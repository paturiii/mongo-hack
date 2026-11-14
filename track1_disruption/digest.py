"""T-48h digest checks, stored in a durable outbox for email delivery."""

import hashlib
import html
import secrets
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument

from shared.http import CachedHTTP, now


def checks(db, trip):
    board = db.boards.find_one({"trip_id": trip["_id"]})
    if not board:
        return []
    checks = []
    weather = db.disruptions.find_one(
        {"trip_id": trip["_id"], "kind": "weather", "status": {"$in": ["open", "notified"]}}
    )
    checks.append(
        {
            "label": "Weather outlook",
            "summary": weather["evidence"][0]["summary"]
            if weather
            else "Review the forecast before packing.",
            "source_url": "https://open-meteo.com/",
            "source_type": "weather",
            "fetched_at": now(),
        }
    )
    strike = db.disruptions.find_one(
        {"trip_id": trip["_id"], "kind": "strike", "status": {"$in": ["open", "notified"]}}
    )
    checks.append(
        {
            "label": "Transport and strikes",
            "summary": strike["evidence"][0]["summary"]
            if strike
            else "Check operator service notices before leaving.",
            "source_url": "https://www.metrolisboa.pt/",
            "source_type": "operator",
            "fetched_at": now(),
        }
    )
    outstanding = [row for row in board["bookings"] if row["status"] == "todo"]
    for booking in outstanding:
        checks.append(
            {
                "label": "Unbooked timed ticket",
                "summary": f"{booking['label']}: check availability; no sell-out claim is inferred.",
                "source_url": booking["url"],
                "source_type": "booking",
                "fetched_at": now(),
            }
        )
    return checks


def enqueue(db, *, at=None, force_fixture=False):
    at = at or now()
    queued = []
    for trip in db.trips.find({"status": "planned"}):
        start_value = trip["dates"]["start"]
        start = (
            start_value
            if isinstance(start_value, datetime)
            else datetime.fromisoformat(str(start_value))
        )
        start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        remaining = (start - at).total_seconds()
        if not (0 < remaining <= 48 * 3600 or force_fixture and trip.get("fixture")):
            continue
        key = hashlib.sha256((trip["_id"] + start.date().isoformat()).encode()).hexdigest()
        document = {
            "_id": "digest_" + key,
            "trip_id": trip["_id"],
            "user_id": trip["user_id"],
            "checks": checks(db, trip),
            "status": "pending",
            "created_at": now(),
            "fixture": trip.get("fixture", False),
        }
        db.pretrip_digests.update_one(
            {"_id": document["_id"]}, {"$setOnInsert": document}, upsert=True
        )
        queued.append(document["_id"])
    return queued


def deliver(db, settings, *, live=False):
    for doc in db.pretrip_digests.find({"status": "pending"}):
        token = secrets.token_hex(16)
        claimed = db.pretrip_digests.find_one_and_update(
            {
                "_id": doc["_id"],
                "status": "pending",
                "$or": [{"lease_until": {"$exists": False}}, {"lease_until": {"$lte": now()}}],
            },
            {"$set": {"lease_until": now() + timedelta(minutes=2), "lease_token": token}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            continue
        try:
            result = {"status": "sent", "sent_at": now(), "delivery_mode": "fixture"}
            if live:
                user = db.users.find_one({"_id": doc["user_id"]}) or {}
                recipient = (
                    settings.get("DISRUPTION_TEST_EMAIL")
                    if doc.get("fixture")
                    else user.get("email")
                )
                if (
                    not recipient
                    or not settings.get("RESEND_FROM_EMAIL")
                    or not user.get("notify", {}).get("email")
                ):
                    raise ValueError("Digest email configuration/opt-in missing")
                rows = "".join(
                    "<li><strong>"
                    + html.escape(row["label"])
                    + "</strong>: "
                    + html.escape(row["summary"])
                    + ' <a href="'
                    + html.escape(row["source_url"], quote=True)
                    + '">Source</a></li>'
                    for row in doc["checks"]
                )
                response = CachedHTTP(db).request(
                    "POST",
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": "Bearer " + settings.get("RESEND_API_KEY"),
                        "Idempotency-Key": doc["_id"],
                    },
                    json_body={
                        "from": settings.get("RESEND_FROM_EMAIL"),
                        "to": [recipient],
                        "subject": "Wander: "
                        + ("Demo " if doc.get("fixture") else "")
                        + "pre-trip checks",
                        "html": "<p>Your pre-trip checklist:</p><ul>" + rows + "</ul>",
                    },
                    ttl=86400,
                )
                result.update(provider_id=response["id"], delivery_mode="live")
            db.pretrip_digests.update_one(
                {"_id": doc["_id"], "lease_token": token},
                {
                    "$set": result,
                    "$unset": {"lease_token": "", "lease_until": "", "last_error": ""},
                },
            )
        except Exception as exc:
            db.pretrip_digests.update_one(
                {"_id": doc["_id"], "lease_token": token},
                {
                    "$set": {
                        "last_error": type(exc).__name__,
                        "lease_until": now() + timedelta(minutes=5),
                    },
                    "$unset": {"lease_token": ""},
                },
            )
