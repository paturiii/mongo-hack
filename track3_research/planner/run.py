"""Planner + refine service: change streams on trips (ready_for_research) and refine_requests (queued), plus a startup sweep.

    python -m track3_research.planner.run                       # serve forever
    python -m track3_research.planner.run --trip trip_x --once  # plan one trip (and its queued refines) then exit
    python -m track3_research.planner.run --refine rr_x         # run one refine request
    --inline-workers  also process this trip's research jobs in-process (no separate workers needed)
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from shared.config import get_db
from track3_research.planner.graph import run_planner
from track3_research.planner.refine import run_refine

log = logging.getLogger("wander.planner.run")


def _plan(trip_id: str, inline: bool) -> None:
    try:
        out = run_planner(trip_id, inline_workers=inline)
        log.info("planned %s → %s timings=%s", trip_id, out.get("result"), out.get("timings"))
    except Exception:
        log.exception("planner failed for %s", trip_id)


def _refine(rid: str) -> None:
    try:
        out = run_refine(rid)
        log.info("refined %s → %s", rid, out.get("result"))
    except Exception:
        log.exception("refine failed for %s", rid)


def serve(trip_filter: str | None, inline: bool, once: bool) -> None:
    db = get_db()
    pool = ThreadPoolExecutor(3)
    busy: set[str] = set()
    lock = threading.Lock()

    def submit(key: str, fn, *args):
        with lock:
            if key in busy:
                return None
            busy.add(key)

        def wrapped():
            try:
                fn(*args)
            finally:
                with lock:
                    busy.discard(key)

        return pool.submit(wrapped)

    def sweep() -> list:
        futs = []
        tq: dict = {"status": "ready_for_research"}
        rq: dict = {"status": "queued"}
        if trip_filter:
            tq["_id"] = trip_filter
            rq["trip_id"] = trip_filter
        for t in db.trips.find(tq, {"_id": 1}):
            futs.append(submit(t["_id"], _plan, t["_id"], inline))
        for f in futs:
            if f:
                f.result()
        for r in db.refine_requests.find(rq, {"_id": 1, "trip_id": 1}).sort("created_at", 1):
            if db.boards.find_one({"trip_id": r["trip_id"]}, {"_id": 1}):
                f = submit(f"refine:{r['trip_id']}", _refine, r["_id"])
                if f and once:
                    f.result()
        return futs

    sweep()
    if once:
        pool.shutdown(wait=True)
        return
    pipeline = [{"$match": {"$or": [
        {"ns.coll": "trips", "fullDocument.status": "ready_for_research"},
        {"ns.coll": "refine_requests", "fullDocument.status": "queued"},
    ]}}]
    log.info("planner service watching trips + refine_requests")
    while True:
        try:
            with db.watch(pipeline, full_document="updateLookup", max_await_time_ms=5000) as stream:
                for ev in stream:
                    doc = ev.get("fullDocument") or {}
                    if trip_filter and trip_filter not in (doc.get("_id"), doc.get("trip_id")):
                        continue
                    if ev["ns"]["coll"] == "trips":
                        submit(doc["_id"], _plan, doc["_id"], inline)
                    else:
                        submit(f"refine:{doc['trip_id']}", _refine, doc["_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("change stream error, re-sweeping: %s", exc)
            time.sleep(2)
            sweep()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trip")
    ap.add_argument("--refine")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--inline-workers", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "openai", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if a.refine:
        print(json.dumps(run_refine(a.refine).get("result"), default=str))
        return
    serve(a.trip, a.inline_workers, a.once)


if __name__ == "__main__":
    main()
