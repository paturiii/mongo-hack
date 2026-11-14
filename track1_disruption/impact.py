"""Date-scoped, exact/geo impact analysis against the current board."""

from datetime import UTC, datetime


def affected(db, trip, board, payload):
    kind = payload["kind"]
    date = payload.get("date")
    place_ids = None
    if payload.get("area_geo"):
        place_ids = {
            row["_id"]
            for row in db.places.find(
                {"geo": {"$geoWithin": {"$geometry": payload["area_geo"]}}}, {"_id": 1}
            )
        }
    if kind.startswith("flight_"):
        leg = next(
            (
                leg
                for leg in trip.get("legs", [])
                if leg.get("number") == payload.get("number")
                and leg.get("carrier") == payload.get("carrier")
                and str(leg.get("dep_at", ""))[:10] == date
            ),
            None,
        )
        if not leg:
            return {"days": [], "slot_ids": [], "link_ids": []}, 1
        date = str(leg["arr_at"])[:10]
    days, slots, links = [], [], set()
    for day in board.get("days", []):
        if day["date"] != date:
            continue
        for slot in day.get("slots", []):
            item = slot["item"]
            include = kind.startswith("flight_")
            if kind == "weather":
                place = db.places.find_one({"_id": item.get("place_id")}) or {}
                include = place.get("indoor") is False and (
                    place_ids is None or item.get("place_id") in place_ids
                )
            elif kind == "strike":
                include = place_ids is not None and item.get("place_id") in place_ids
            elif kind == "closure":
                include = item.get("place_id") == payload.get("place_id")
            elif kind == "event_cancel":
                include = item.get("event_id") == payload.get("event_id")
            elif kind in {"dead_link", "price_spike"}:
                include = payload.get("link_id") in item.get("link_ids", [])
            if include:
                slots.append(slot["slot_id"])
                links.update(item.get("link_ids", []))
                if day["date"] not in days:
                    days.append(day["date"])
    if kind in {"dead_link", "price_spike"} and any(
        row["link_id"] == payload.get("link_id") for row in board.get("bookings", [])
    ):
        links.add(payload["link_id"])
        if date and date not in days:
            days.append(date)
    try:
        imminent = (
            datetime.fromisoformat(date).replace(tzinfo=UTC) - datetime.now(UTC)
        ).total_seconds() <= 2 * 86400
    except (ValueError, TypeError):
        imminent = False
    # Read timed bookings from the stored board rather than trusting incoming signals.
    scoring = next(
        db.boards.aggregate(
            [
                {"$match": {"_id": board["_id"]}},
                {
                    "$project": {
                        "bookings_at_risk": {
                            "$size": {
                                "$filter": {
                                    "input": {
                                        "$reduce": {
                                            "input": "$days",
                                            "initialValue": [],
                                            "in": {"$concatArrays": ["$$value", "$$this.slots"]},
                                        }
                                    },
                                    "as": "slot",
                                    "cond": {
                                        "$and": [
                                            {"$in": ["$$slot.slot_id", {"$literal": slots}]},
                                            {
                                                "$or": [
                                                    {"$eq": ["$$slot.item.timed_ticket", True]},
                                                    {
                                                        "$ne": [
                                                            {
                                                                "$ifNull": [
                                                                    "$$slot.item.event_id",
                                                                    None,
                                                                ]
                                                            },
                                                            None,
                                                        ]
                                                    },
                                                ]
                                            },
                                        ]
                                    },
                                }
                            }
                        }
                    }
                },
                {
                    "$project": {
                        "severity": {
                            "$min": [
                                5,
                                {
                                    "$add": [
                                        1,
                                        int(bool(slots or links)),
                                        int(imminent),
                                        int(len(slots) >= 3),
                                        {"$cond": [{"$gt": ["$bookings_at_risk", 0]}, 1, 0]},
                                    ]
                                },
                            ]
                        }
                    }
                },
            ]
        ),
        {"severity": 1},
    )
    severity = scoring["severity"]
    if kind in {"flight_cancel", "event_cancel"} and slots:
        severity = max(4, severity)
    return {"days": days, "slot_ids": slots, "link_ids": sorted(links)}, severity
