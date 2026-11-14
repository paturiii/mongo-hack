"""Inject a labeled simulated observation and run the actual durable disruption pipeline."""

import argparse
import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import Settings
from track1_disruption.alerts import deliver_pending
from track1_disruption.monitors import recorded
from track1_disruption.run import drain_signals, persist_signal
from track1_disruption.watchlist import build_watchlist

KINDS = {
    "flight_delay": "flight",
    "flight_cancel": "flight",
    "weather": "weather",
    "strike": "area",
    "closure": "venue",
    "event_cancel": "event",
    "dead_link": "link",
    "price_spike": "price",
}


def simulate(db, settings, trip_id, kind, minutes=180, *, live_alerts=False):
    started = time.monotonic()
    board = db.boards.find_one({"trip_id": trip_id})
    if not board:
        raise ValueError("Trip board not found; run scripts/load_fixtures.py first")
    build_watchlist(db, board)
    query = {"trip_id": trip_id, "kind": KINDS[kind], "active": True}
    if kind in {"strike", "weather"}:
        query["target.date"] = board["days"][min(2, len(board["days"]) - 1)]["date"]
    watch = db.watches.find_one(query)
    if not watch:
        raise ValueError("No matching watch on the board")
    payload = copy.deepcopy(recorded(KINDS[kind], watch))
    payload["kind"] = kind
    if kind.startswith("flight_"):
        payload["minutes"] = minutes
        payload["summary"] = (
            f"SIMULATED: {payload.get('carrier', '')}{payload.get('number', '')} "
            + (f"delayed {minutes} minutes" if kind == "flight_delay" else "cancelled")
        )
    payload["evidence"][0]["summary"] = payload.get("summary", "Simulated " + kind)
    hash_value = persist_signal(db, watch, payload)
    drain_signals(db, settings)
    disruption_id = "dis_" + hash_value[:32]
    deliver_pending(db, settings, live=live_alerts, disruption_id=disruption_id)
    doc = db.disruptions.find_one({"_id": disruption_id})
    if not doc:
        receipt = db.signal_receipts.find_one({"_id": hash_value})
        raise RuntimeError(
            "Simulation did not produce a disruption: "
            + str(receipt.get("last_error", "no affected items"))
        )
    return {
        "disruption_id": disruption_id,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "affected": doc["affected"],
        "severity": doc["severity"],
        "status": doc["status"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trip", default="trip_lisbon")
    parser.add_argument("--type", choices=list(KINDS), default="flight_delay")
    parser.add_argument("--minutes", type=int, default=180)
    parser.add_argument("--live-alerts", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    client, db = settings.connect()
    try:
        print(
            simulate(db, settings, args.trip, args.type, args.minutes, live_alerts=args.live_alerts)
        )
    finally:
        client.close()
