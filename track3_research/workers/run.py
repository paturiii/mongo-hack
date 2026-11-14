"""R-01 research job workers: change stream + startup sweep, atomic claim, retries/backoff, stale-claim reaper.

    python -m track3_research.workers.run [--concurrency 8] [--worker-id w1] [--exit-when-idle 10] [--dry-run]

--dry-run swaps every agent for a no-op that records which worker executed the job (used by the
exactly-once test in scripts/test_workers.py).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument

from shared.config import get_db

log = logging.getLogger("wander.workers")

MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=2)
BACKOFF_BASE_S = 5


def now() -> datetime:
    return datetime.now(UTC)


def claim(db, worker_id: str, trip_id: str | None = None) -> dict | None:
    flt: dict = {"status": "queued", "$or": [{"not_before": {"$exists": False}}, {"not_before": {"$lte": now()}}]}
    if trip_id:
        flt["trip_id"] = trip_id
    return db.research_jobs.find_one_and_update(
        flt,
        {"$set": {"status": "claimed", "claimed_by": worker_id, "claimed_at": now()}, "$inc": {"attempts": 1}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def reap_stale(db) -> int:
    cutoff = now() - STALE_AFTER
    failed = db.research_jobs.update_many(
        {"status": "claimed", "claimed_at": {"$lt": cutoff}, "attempts": {"$gte": MAX_ATTEMPTS}},
        {"$set": {"status": "failed", "error": "stale claim after max attempts", "finished_at": now()}},
    ).modified_count
    requeued = db.research_jobs.update_many(
        {"status": "claimed", "claimed_at": {"$lt": cutoff}, "attempts": {"$lt": MAX_ATTEMPTS}},
        {"$set": {"status": "queued", "error": "stale claim requeued"}, "$unset": {"claimed_by": "", "claimed_at": ""}},
    ).modified_count
    if failed or requeued:
        log.info("reaper: requeued=%d failed=%d", requeued, failed)
    return requeued + failed


def execute(job: dict, dry_run: bool = False) -> dict:
    if dry_run:
        time.sleep(0.05)
        return {"result_refs": [], "stats": {"dry_run": True}}
    from track3_research.agents import agent_for

    return agent_for(job["kind"])(job).run()


def finish(db, job: dict, worker_id: str, result: dict | None, error: str | None, dry_run: bool = False) -> None:
    mine = {"_id": job["_id"], "status": "claimed", "claimed_by": worker_id}
    if error is None:
        upd: dict = {"$set": {"status": "done", "result_refs": result.get("result_refs", []), "stats": result.get("stats", {}), "finished_at": now(), "error": None}}
        if dry_run:
            upd["$push"] = {"executions": worker_id}
        db.research_jobs.update_one(mine, upd)
    elif job["attempts"] < MAX_ATTEMPTS:
        delay = BACKOFF_BASE_S * (2 ** (job["attempts"] - 1))
        db.research_jobs.update_one(mine, {"$set": {"status": "queued", "error": error[-1500:], "not_before": now() + timedelta(seconds=delay)}, "$unset": {"claimed_by": "", "claimed_at": ""}})
    else:
        db.research_jobs.update_one(mine, {"$set": {"status": "failed", "error": error[-1500:], "finished_at": now()}})


def process_one(db, job: dict, worker_id: str, dry_run: bool = False) -> None:
    t0 = time.time()
    try:
        result = execute(job, dry_run)
        finish(db, job, worker_id, result, None, dry_run)
        log.info("done %s %s in %.1fs refs=%d", job["_id"], job["kind"], time.time() - t0, len(result.get("result_refs", [])))
    except Exception:  # noqa: BLE001
        err = traceback.format_exc()
        log.warning("job %s %s failed (attempt %d): %s", job["_id"], job["kind"], job["attempts"], err.strip().splitlines()[-1])
        finish(db, job, worker_id, None, err)


def _watch_thread(db, loop: asyncio.AbstractEventLoop, wake: asyncio.Event, stop: threading.Event) -> None:
    pipeline = [{"$match": {"$or": [
        {"operationType": "insert"},
        {"operationType": "update", "updateDescription.updatedFields.status": "queued"},
    ]}}]
    while not stop.is_set():
        try:
            with db.research_jobs.watch(pipeline, max_await_time_ms=1000) as stream:
                while not stop.is_set():
                    if stream.try_next() is not None:
                        loop.call_soon_threadsafe(wake.set)
        except Exception as exc:  # noqa: BLE001
            log.warning("change stream error, retrying: %s", exc)
            time.sleep(2)


async def run(worker_id: str, concurrency: int = 8, exit_when_idle: float | None = None, dry_run: bool = False, trip_id: str | None = None) -> int:
    db = get_db()
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    stop = threading.Event()
    threading.Thread(target=_watch_thread, args=(db, loop, wake, stop), daemon=True).start()
    sem = asyncio.Semaphore(concurrency)
    running: set[asyncio.Task] = set()
    processed = 0
    last_reap = 0.0
    idle_since = time.time()
    log.info("worker %s up (concurrency=%d dry_run=%s)", worker_id, concurrency, dry_run)
    try:
        while True:
            if time.time() - last_reap > 30:
                await asyncio.to_thread(reap_stale, db)
                last_reap = time.time()
            claimed_any = False
            while not sem.locked():
                job = await asyncio.to_thread(claim, db, worker_id, trip_id)
                if job is None:
                    break
                claimed_any = True
                processed += 1
                await sem.acquire()

                async def go(j=job):
                    try:
                        await asyncio.to_thread(process_one, db, j, worker_id, dry_run)
                    finally:
                        sem.release()

                t = asyncio.create_task(go())
                running.add(t)
                t.add_done_callback(running.discard)
            if claimed_any or running:
                idle_since = time.time()
            elif exit_when_idle is not None and time.time() - idle_since > exit_when_idle:
                break
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=2.0)
            except TimeoutError:
                pass
        if running:
            await asyncio.gather(*running)
    finally:
        stop.set()
    log.info("worker %s exiting, processed=%d", worker_id, processed)
    return processed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    ap.add_argument("--exit-when-idle", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--trip-id", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args.worker_id, args.concurrency, args.exit_when_idle, args.dry_run, args.trip_id))


if __name__ == "__main__":
    main()
