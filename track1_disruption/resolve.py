"""Conditional transactional resolution + Track 3 outbox + Track 2 feedback."""

from shared.http import now
from shared.models import RefineRequest


class ResolutionConflict(ValueError):
    pass


def resolve(db, disruption_id, option_id, user_id):
    def transaction(session):
        doc = db.disruptions.find_one({"_id": disruption_id}, session=session)
        if not doc:
            raise KeyError("Disruption not found")
        trip = db.trips.find_one({"_id": doc["trip_id"], "user_id": user_id}, session=session)
        if not trip:
            raise PermissionError("This disruption belongs to another traveler")
        if doc["status"] not in {"open", "notified"}:
            raise ResolutionConflict("This disruption has already been resolved")
        option = next((item for item in doc["options"] if item["id"] == option_id), None)
        if option is None:
            raise ValueError("Unknown response option")
        result = db.disruptions.update_one(
            {"_id": disruption_id, "status": doc["status"]},
            {
                "$set": {
                    "status": "resolved",
                    "chosen_option": option_id,
                    "resolved_at": now(),
                }
            },
            session=session,
        )
        if result.modified_count != 1:
            raise ResolutionConflict("Another request resolved this disruption")
        replans = [action for action in option["actions"] if action["type"] == "replan"]
        request_id = None
        if replans:
            request_id = "ref_" + disruption_id
            request = RefineRequest(
                _id=request_id,
                trip_id=trip["_id"],
                source="disruption",
                disruption_id=disruption_id,
                instruction=" ".join(action["instruction"] for action in replans),
                scope={"days": doc["affected"]["days"], "slot_ids": doc["affected"]["slot_ids"]},
                constraints={
                    key: value
                    for action in replans
                    for key, value in action.get("constraints", {}).items()
                },
                status="queued",
                created_at=now(),
            ).model_dump(by_alias=True)
            db.refine_requests.insert_one(request, session=session)
        db.disruption_audit.insert_one(
            {
                "_id": "audit_" + disruption_id,
                "disruption_id": disruption_id,
                "event": "resolved",
                "option_id": option_id,
                "user_id": user_id,
                "created_at": now(),
            },
            session=session,
        )
        db.memories.insert_one(
            {
                "_id": "feedback_" + disruption_id,
                "user_id": user_id,
                "kind": "feedback",
                "source": "disruption_choice",
                "key": doc["kind"],
                "value": option["label"],
                "text": f"For {doc['kind']}, chose: {option['label']}",
                "confidence": 1.0,
                "updated_at": now(),
            },
            session=session,
        )
        return {
            "disruption_id": disruption_id,
            "status": "resolved",
            "refine_request_id": request_id,
            "board_url": "/b/"
            + (db.boards.find_one({"_id": trip["board_id"]}, session=session) or {}).get(
                "share_slug", ""
            ),
        }

    with db.client.start_session() as session:
        return session.with_transaction(transaction)


def dismiss(db, disruption_id, user_id):
    doc = db.disruptions.find_one({"_id": disruption_id})
    if not doc or not db.trips.find_one({"_id": doc["trip_id"], "user_id": user_id}):
        raise PermissionError("Disruption not available")
    updated = db.disruptions.update_one(
        {"_id": disruption_id, "status": {"$in": ["open", "notified"]}},
        {"$set": {"status": "dismissed", "resolved_at": now()}},
    )
    if not updated.modified_count:
        raise ResolutionConflict("This disruption has already been resolved")
    return {"status": "dismissed"}
