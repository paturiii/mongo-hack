import hashlib
import json
from datetime import UTC, datetime

from shared.http import now
from shared.models import Watch


def interval(date, kind):
    try:
        remaining = (datetime.fromisoformat(str(date)).replace(tzinfo=UTC) - now()).total_seconds()
    except ValueError:
        remaining = 0
    if kind in {"link", "price"}:
        return 86400
    if remaining > 7 * 86400:
        return 21600
    if remaining > 86400 or kind == "flight":
        return 3600
    return 300


def build_watchlist(db, board):
    trip = db.trips.find_one({"_id": board["trip_id"]})
    if not trip:
        return []
    targets = []
    for leg in trip.get("legs", []):
        if leg["kind"] in {"flight", "train"}:
            targets.append((leg["kind"], {**leg, "date": str(leg["dep_at"])[:10]}))
    for day in board.get("days", []):
        area = {
            "date": day["date"],
            "area_geo": day["area_geo"],
            "city": trip["destination"]["name"],
        }
        targets.extend((kind, area) for kind in ("weather", "area"))
        for slot in day.get("slots", []):
            item = slot["item"]
            if item.get("place_id"):
                targets.append(("venue", {"place_id": item["place_id"], "date": day["date"]}))
            if item.get("event_id"):
                targets.append(("event", {"event_id": item["event_id"], "date": day["date"]}))
    for link in db.links.find({"trip_id": trip["_id"]}):
        if link["_id"] not in {
            booking["link_id"] for booking in board.get("bookings", [])
        } and not any(
            link["_id"] in slot["item"].get("link_ids", [])
            for day in board.get("days", [])
            for slot in day.get("slots", [])
        ):
            continue
        targets.append(("link", {"link_id": link["_id"], "date": link.get("for_date", "")}))
        if link.get("price_hint"):
            targets.append(("price", {"link_id": link["_id"], "date": link.get("for_date", "")}))
    ids = []
    for kind, target in targets:
        key = hashlib.sha256(json.dumps(target, sort_keys=True, default=str).encode()).hexdigest()[
            :24
        ]
        watch_id = f"watch_{trip['_id']}_{kind}_{key}"
        seconds = interval(target.get("date", ""), kind)
        doc = Watch(
            _id=watch_id,
            trip_id=trip["_id"],
            kind=kind,
            target=target,
            next_check_at=now(),
            interval_s=seconds,
            active=True,
        ).model_dump(by_alias=True)
        db.watches.update_one(
            {"_id": watch_id},
            {
                "$set": {"active": True, "board_version": board["version"], "interval_s": seconds},
                "$setOnInsert": {k: v for k, v in doc.items() if k not in {"active", "interval_s"}}
                | {"target_key": key},
            },
            upsert=True,
        )
        ids.append(watch_id)
    db.watches.update_many(
        {"trip_id": trip["_id"], "_id": {"$nin": ids}}, {"$set": {"active": False}}
    )
    return ids
