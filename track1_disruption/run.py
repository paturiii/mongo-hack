"""Atomic watch leases, durable signal receipts, retries, streams + startup sweeps."""

import argparse
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from shared.config import Settings
from shared.http import now
from shared.models import Signal
from track1_disruption.alerts import deliver_pending
from track1_disruption.digest import deliver as deliver_digests
from track1_disruption.digest import enqueue
from track1_disruption.fixture_consumer import consume
from track1_disruption.graph import process_signal, resume_choice, signal_hash
from track1_disruption.monitors import poll
from track1_disruption.watchlist import build_watchlist, interval

log = logging.getLogger("wander.disruption")


def recover_resolutions(db, settings):
    for doc in db.disruptions.find(
        {"status": "resolved", "handoff_recovered": {"$ne": True}}
    ).limit(20):
        try:
            resume_choice(db, settings, doc["_id"], doc["chosen_option"])
            if settings.mode == "fixtures":
                request = db.refine_requests.find_one(
                    {"disruption_id": doc["_id"], "status": "queued"}
                )
                if request:
                    consume(db, settings, request["_id"])
            db.disruptions.update_one({"_id": doc["_id"]}, {"$set": {"handoff_recovered": True}})
        except Exception as exc:
            log.warning("Resolution recovery deferred (%s)", type(exc).__name__)


def claim_watch(db, worker_id):
    return db.watches.find_one_and_update(
        {
            "active": True,
            "next_check_at": {"$lte": now()},
            "$or": [
                {"lease_until": {"$exists": False}},
                {"lease_until": {"$lte": now()}},
            ],
        },
        {
            "$set": {
                "claimed_by": worker_id,
                "lease_token": secrets.token_hex(16),
                "lease_until": now() + timedelta(seconds=120),
            },
            "$inc": {"attempts": 1},
        },
        sort=[("next_check_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def persist_signal(db, watch, payload):
    hash_value = signal_hash(watch["trip_id"], watch["_id"], payload)
    signal = Signal(
        ts=now(),
        meta={
            "watch_id": watch["_id"],
            "trip_id": watch["trip_id"],
            "kind": watch["kind"],
            "provider": "fixture" if payload.get("fixture") else "monitor",
        },
        payload=payload,
        hash=hash_value,
    ).model_dump()
    try:
        db.signal_receipts.insert_one(
            {"_id": hash_value, "status": "queued", "signal": signal, "created_at": now()}
        )
    except DuplicateKeyError:
        return hash_value
    return hash_value


def drain_signals(db, settings):
    while True:
        token = secrets.token_hex(16)
        row = db.signal_receipts.find_one_and_update(
            {
                "status": {"$in": ["queued", "claimed"]},
                "$or": [{"lease_until": {"$exists": False}}, {"lease_until": {"$lte": now()}}],
            },
            {
                "$set": {
                    "status": "claimed",
                    "lease_token": token,
                    "lease_until": now() + timedelta(seconds=120),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not row:
            return
        try:
            # Unique receipt is the authoritative dedupe key. Time-series has no unique index.
            if not db.signals.find_one({"hash": row["_id"]}):
                db.signals.insert_one(dict(row["signal"]))
            process_signal(db, settings, row["signal"])
            db.signal_receipts.update_one(
                {"_id": row["_id"], "lease_token": token},
                {
                    "$set": {"status": "done", "completed_at": now()},
                    "$unset": {"lease_token": "", "lease_until": ""},
                },
            )
        except Exception as exc:
            db.signal_receipts.update_one(
                {"_id": row["_id"], "lease_token": token},
                {
                    "$set": {
                        "status": "queued",
                        "last_error": type(exc).__name__,
                        "lease_until": now() + timedelta(seconds=30),
                    },
                    "$inc": {"failures": 1},
                },
            )
            log.warning("Signal pipeline deferred (%s)", type(exc).__name__)


def run_watch(db, settings, watch):
    ownership = {"_id": watch["_id"], "lease_token": watch["lease_token"]}
    try:
        payload = poll(db, settings, watch)
        if payload:
            persist_signal(db, watch, payload)
        seconds = interval(watch["target"].get("date", ""), watch["kind"])
        db.watches.update_one(
            ownership,
            {
                "$set": {
                    "next_check_at": now() + timedelta(seconds=seconds),
                    "interval_s": seconds,
                    "last_checked_at": now(),
                    "attempts": 0,
                },
                "$unset": {"lease_until": "", "lease_token": "", "last_error": ""},
            },
        )
    except Exception as exc:
        attempts = watch.get("attempts", 1)
        db.watches.update_one(
            ownership,
            {
                "$set": {
                    "next_check_at": now()
                    + timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 6))),
                    "last_error": type(exc).__name__,
                },
                "$unset": {"lease_until": "", "lease_token": ""},
            },
        )
        log.warning("Watch deferred (%s)", type(exc).__name__)


def sweep_boards(db):
    # Startup/recovery sweep also deactivates watches for trips no longer traveling.
    for board in db.boards.find():
        trip = db.trips.find_one({"_id": board["trip_id"]})
        if trip and trip.get("status") in {"planned", "traveling"}:
            build_watchlist(db, board)
        else:
            db.watches.update_many({"trip_id": board["trip_id"]}, {"$set": {"active": False}})


def watch_boards(db, stop):
    while not stop.is_set():
        try:
            with db.boards.watch(full_document="updateLookup", max_await_time_ms=1000) as stream:
                while not stop.is_set():
                    event = stream.try_next()
                    if event and event.get("fullDocument"):
                        build_watchlist(db, event["fullDocument"])
        except Exception as exc:
            log.warning("Board stream reconnecting (%s)", type(exc).__name__)
            stop.wait(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--live-alerts",
        action="store_true",
        help="Actually deliver email/TTS; fixture records are default",
    )
    args = parser.parse_args()
    settings = Settings.load()
    client, db = settings.connect()
    stop = threading.Event()
    logging.basicConfig(level=logging.INFO)
    try:
        sweep_boards(db)
        enqueue(db)
        deliver_digests(db, settings, live=args.live_alerts)
        if not args.once:
            threading.Thread(target=watch_boards, args=(db, stop), daemon=True).start()
        with ThreadPoolExecutor(max_workers=8) as pool:
            last_sweep = time.monotonic()
            while True:
                batch = []
                for _ in range(8):
                    watch = claim_watch(db, str(threading.get_ident()))
                    if not watch:
                        break
                    batch.append(pool.submit(run_watch, db, settings, watch))
                for future in batch:
                    future.result()
                drain_signals(db, settings)
                deliver_pending(db, settings, live=args.live_alerts)
                recover_resolutions(db, settings)
                if args.once and not batch:
                    break
                if time.monotonic() - last_sweep > 60:
                    sweep_boards(db)
                    enqueue(db)
                    deliver_digests(db, settings, live=args.live_alerts)
                    last_sweep = time.monotonic()
                if not batch:
                    stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        client.close()


if __name__ == "__main__":
    main()
