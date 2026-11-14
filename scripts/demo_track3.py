"""Track 3 end to end on Atlas: reset demo trip → planner (research + plan) → board v1 → refines → board v2/v3.

    python scripts/demo_track3.py                # full run
    python scripts/demo_track3.py --skip-plan    # only (re)run the refine requests
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.config import get_db

FIX = ROOT / "fixtures" / "lisbon"


def reset(db, trip: dict) -> None:
    tid = trip["_id"]
    db.boards.delete_many({"trip_id": tid})
    db.board_versions.delete_many({"trip_id": tid})
    db.research_jobs.delete_many({"trip_id": tid})
    db.links.delete_many({"trip_id": tid})
    db.refine_requests.delete_many({"trip_id": tid})
    db.planner_checkpoints.delete_many({"thread_id": {"$in": [tid, "refine:rr_t3_beach", "refine:rr_t3_rain"]}})
    db.planner_checkpoint_writes.delete_many({"thread_id": {"$in": [tid, "refine:rr_t3_beach", "refine:rr_t3_rain"]}})
    db.trips.replace_one({"_id": tid}, trip, upsert=True)


def show(board: dict, changed: dict | None = None) -> None:
    print(f"\n=== {board['title']}  (v{board['version']}, /b/{board['share_slug']})\n{board['summary']}")
    for d in board["days"]:
        mark = " *changed*" if changed and d["date"] in changed else ""
        print(f"\n{d['date']} · {d['theme']}{mark}" + (f"  [notice: {d['notice']}]" if d.get("notice") else ""))
        for s in d["slots"]:
            it = s["item"]
            chip = it["opinion_chips"][0]["text"] if it.get("opinion_chips") else ""
            print(f"  {s['time']} {it['name']}  — {it['why'][:90]}" + (f"\n         chip: {chip[:90]}" if chip else ""))
    print(f"\nBookings ({board['checklist_progress']['total']}):")
    for b in board["bookings"]:
        print(f"  [{b['group']}] {b['label'][:70]} · {b['deadline_hint']}{' · OFFICIAL' if b.get('is_official') else ''}")
    print(f"Budget: {board['budget']['text']} ({board['budget']['confidence']} confidence)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-plan", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "openai", "pymongo"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from track3_research.planner.graph import run_planner
    from track3_research.planner.refine import run_refine

    db = get_db()
    trip = json.loads((FIX / "track3_trip.json").read_text())
    t0 = time.time()
    if not a.skip_plan:
        reset(db, trip)
        out = run_planner(trip["_id"], inline_workers=True)
        print(f"\nplanner done in {time.time() - t0:.0f}s timings={out.get('timings')} gaps={out.get('gaps')}")
        show(db.boards.find_one({"trip_id": trip["_id"]}))
    for rr in json.loads((FIX / "track3_refine_requests.json").read_text()):
        rr["created_at"] = datetime.now(UTC)
        db.refine_requests.replace_one({"_id": rr["_id"]}, rr, upsert=True)
        db.planner_checkpoints.delete_many({"thread_id": f"refine:{rr['_id']}"})
        db.planner_checkpoint_writes.delete_many({"thread_id": f"refine:{rr['_id']}"})
        t1 = time.time()
        res = run_refine(rr["_id"])
        print(f"\nrefine {rr['_id']} ({rr['instruction']!r}) → v{res['result']['version']} in {time.time() - t1:.0f}s, changed={res['changed']}")
        show(db.boards.find_one({"trip_id": trip["_id"]}), res["changed"])


if __name__ == "__main__":
    main()
