"""DEV STUB standing in for Track 3, so Track 2 can be demoed alone (fixtures mode).

Watches `trips` for status=ready_for_research, fakes research_jobs progress, then writes a board cloned from
fixtures/lisbon/board.json. Also bumps the board version for queued `refine_requests` so the live board visibly
updates. Never run this against the shared DB while Track 3's planner is running.

    MONGODB_DB=wander_dev python scripts/dev_stub_planner.py
"""
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import json_util

from shared import config
from shared.db import db

KINDS = ["opinions", "opinions", "opinions", "places", "places", "stay", "getting_there", "transit", "tickets",
         "nightlife", "restaurants"]


def now() -> datetime:
    return datetime.now(UTC)


def plan(trip: dict, delay: float) -> str:
    d = db()
    if not d.trips.find_one_and_update({"_id": trip["_id"], "status": "ready_for_research"},
                                       {"$set": {"status": "researching"}}):
        return ""
    city = (trip.get("destination") or {}).get("name") or "Lisbon"
    jobs = [{"_id": f"job_{secrets.token_hex(5)}", "trip_id": trip["_id"], "kind": k, "params": {"city": city},
             "status": "queued", "attempts": 0, "created_at": now()} for k in KINDS]
    d.research_jobs.insert_many(jobs)
    for j in jobs:
        time.sleep(delay)
        d.research_jobs.update_one({"_id": j["_id"]}, {"$set": {"status": "done"}})
    board = json_util.loads((config.ROOT / "fixtures" / "lisbon" / "board.json").read_text())
    dates = trip.get("dates") or {}
    board.update({
        "_id": f"b_{trip['_id'].removeprefix('trip_')}", "trip_id": trip["_id"], "user_id": trip["user_id"], "version": 1,
        "share_slug": f"{city.lower().replace(' ', '-')}-{secrets.token_hex(3)}",
        "title": f"{city} · {str(dates.get('start', ''))[:10]} → {str(dates.get('end', ''))[:10]}".strip(" ·→"),
        "summary": f"[DEV STUB] Sample plan for {city} from fixtures — Track 3 replaces this with a real plan.",
        "updated_at": now(),
    })
    d.boards.replace_one({"_id": board["_id"]}, board, upsert=True)
    d.trips.update_one({"_id": trip["_id"]}, {"$set": {"status": "planned", "board_id": board["_id"]}})
    print(f"planned {trip['_id']} → /b/{board['share_slug']}", flush=True)
    return board["share_slug"]


def refine(rr: dict) -> None:
    d = db()
    if not d.refine_requests.find_one_and_update({"_id": rr["_id"], "status": "queued"}, {"$set": {"status": "claimed"}}):
        return
    b = d.boards.find_one({"trip_id": rr["trip_id"]})
    if not b:
        d.refine_requests.update_one({"_id": rr["_id"]}, {"$set": {"status": "failed"}})
        return
    d.boards.update_one({"_id": b["_id"]}, {"$inc": {"version": 1}, "$set": {
        "summary": f"[DEV STUB] Updated for: “{rr['instruction']}”", "updated_at": now()}})
    d.refine_requests.update_one({"_id": rr["_id"]}, {"$set": {"status": "done", "result_board_version": b["version"] + 1}})
    print(f"refined {b['share_slug']} v{b['version'] + 1}", flush=True)


def main() -> None:
    delay = float(sys.argv[sys.argv.index("--delay") + 1]) if "--delay" in sys.argv else 0.4
    if config.MONGODB_DB == "wander" and "--shared" not in sys.argv:
        sys.exit("Refusing to run the dev stub on the shared 'wander' DB (Track 3 owns planning there). "
                 "Set MONGODB_DB=wander_dev, or pass --shared if you really mean it.")
    print(f"dev stub planner on {config.MONGODB_DB}", flush=True)
    d = db()
    for t in d.trips.find({"status": "ready_for_research"}):
        plan(t, delay)
    for rr in d.refine_requests.find({"status": "queued", "source": "user"}):
        refine(rr)
    with d.watch([{"$match": {"$or": [
        {"ns.coll": "trips", "fullDocument.status": "ready_for_research"},
        {"ns.coll": "refine_requests", "operationType": "insert"},
    ]}}], full_document="updateLookup") as stream:
        for ch in stream:
            doc = ch.get("fullDocument") or {}
            if ch["ns"]["coll"] == "trips":
                plan(doc, delay)
            else:
                refine(doc)


if __name__ == "__main__":
    main()
