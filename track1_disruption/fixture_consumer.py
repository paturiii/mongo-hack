"""Explicit demo substitute for Track 3; never runs outside fixture mode."""

from copy import deepcopy
from datetime import datetime, timedelta

from shared.http import now


def consume(db, settings, request_id):
    if settings.mode != "fixtures":
        raise RuntimeError("Fixture consumer cannot run in seeded/live mode")

    def transaction(session):
        request = db.refine_requests.find_one(
            {"_id": request_id, "status": "queued"}, session=session
        )
        if not request:
            return None
        board = db.boards.find_one({"trip_id": request["trip_id"]}, session=session)
        if not board or not board.get("fixture"):
            raise RuntimeError("Fixture consumer cannot modify a live board")
        updated = deepcopy(board)
        constraints = request["constraints"]
        scope = request["scope"]
        for day in updated["days"]:
            if day["date"] not in scope["days"]:
                continue
            for index, slot in enumerate(day["slots"]):
                if scope["slot_ids"] and slot["slot_id"] not in scope["slot_ids"]:
                    continue
                slot["item"]["why"] = "Demo replan: " + request["instruction"]
                if constraints.get("shift_start_to"):
                    shifted = datetime.fromisoformat(constraints["shift_start_to"])
                    slot["time"] = (shifted + timedelta(minutes=90 * index)).strftime("%H:%M")
                if constraints.get("indoor_only"):
                    place = db.places.find_one(
                        {"indoor": True, "business_status": "OPERATIONAL"}, session=session
                    )
                    if place:
                        slot["item"].update(
                            {
                                "place_id": place["_id"],
                                "name": place["name"],
                                "link_ids": [],
                                "source_url": place.get("source_url"),
                                "source_type": "fixture",
                                "fetched_at": now(),
                            }
                        )
                if constraints.get("avoid_area_geo"):
                    place = db.places.find_one(
                        {
                            "geo": {
                                "$not": {"$geoWithin": {"$geometry": constraints["avoid_area_geo"]}}
                            },
                            "business_status": "OPERATIONAL",
                        },
                        session=session,
                    )
                    if place:
                        slot["item"].update(
                            {
                                "place_id": place["_id"],
                                "name": place["name"],
                                "link_ids": [],
                                "source_url": place.get("source_url"),
                                "source_type": "fixture",
                                "fetched_at": now(),
                            }
                        )
        updated["version"] += 1
        updated["updated_at"] = now()
        updated["fixture_replan"] = True
        result = db.boards.replace_one(
            {"_id": board["_id"], "version": board["version"]}, updated, session=session
        )
        if result.modified_count != 1:
            raise RuntimeError("Board version changed concurrently")
        db.refine_requests.update_one(
            {"_id": request_id, "status": "queued"},
            {
                "$set": {
                    "status": "done",
                    "result_board_version": updated["version"],
                    "fixture_consumer": True,
                }
            },
            session=session,
        )
        return updated["version"]

    with db.client.start_session() as session:
        return session.with_transaction(transaction)
