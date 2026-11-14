"""Durable notification outbox. Failures stay pending, never falsely marked sent."""

import base64
import hashlib
import html
import secrets
import shutil
from datetime import timedelta
from pathlib import Path

from pymongo import ReturnDocument

from shared.config import ROOT
from shared.http import CachedHTTP, now
from shared.models import Alert


def create_alerts(db, disruption):
    trip = db.trips.find_one({"_id": disruption["trip_id"]})
    title = disruption["kind"].replace("_", " ").capitalize()
    if disruption.get("fixture"):
        title = "Demo: " + title
    body = (
        disruption["evidence"][0]["summary"]
        + ". Review the options to confirm a change. Your plan has not changed yet."
    )
    for channel in ("board", "email", "voice"):
        doc = Alert(
            _id=f"alert_{disruption['_id']}_{channel}",
            user_id=trip["user_id"],
            trip_id=trip["_id"],
            disruption_id=disruption["_id"],
            channel=channel,
            title=title,
            body=body,
            status="pending",
            created_at=now(),
        ).model_dump(by_alias=True)
        db.alerts.update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
    db.disruptions.update_one(
        {"_id": disruption["_id"], "status": "open"}, {"$set": {"status": "notified"}}
    )


def deliver_one(db, settings, alert, *, live=False):
    token = secrets.token_hex(16)
    locked = db.alerts.find_one_and_update(
        {
            "_id": alert["_id"],
            "status": "pending",
            "$or": [{"lease_until": {"$exists": False}}, {"lease_until": {"$lte": now()}}],
        },
        {"$set": {"lease_until": now() + timedelta(seconds=120), "lease_token": token}},
        return_document=ReturnDocument.AFTER,
    )
    if not locked:
        return False
    patch = {"status": "sent", "sent_at": now(), "delivery_mode": "live" if live else "fixture"}
    try:
        channel = alert["channel"]
        disruption = db.disruptions.find_one({"_id": alert["disruption_id"]})
        if channel == "board":
            patch["delivery_mode"] = "board"
        elif channel == "voice":
            target = ROOT / "media" / "voice" / (alert["_id"] + ".mp3")
            target.parent.mkdir(parents=True, exist_ok=True)
            if live:
                if not settings.get("ELEVENLABS_API_KEY") or not settings.get(
                    "ELEVENLABS_VOICE_ID"
                ):
                    raise ValueError("ElevenLabs configuration missing")
                response = CachedHTTP(db).request(
                    "POST",
                    "https://api.elevenlabs.io/v1/text-to-speech/"
                    + settings.get("ELEVENLABS_VOICE_ID"),
                    headers={"xi-api-key": settings.get("ELEVENLABS_API_KEY")},
                    params={"output_format": "mp3_44100_128"},
                    json_body={
                        "text": "Heads up. " + " ".join(alert["body"].split()[:38]),
                        "model_id": settings.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
                    },
                    ttl=86400,
                )
                target.write_bytes(base64.b64decode(response["base64"]))
            else:
                source = ROOT / "fixtures" / "voice" / "briefing.mp3"
                if not source.exists():
                    raise ValueError("Fixture voice recording missing")
                shutil.copyfile(source, target)
            patch["audio_url"] = "/media/voice/" + target.name
        elif channel == "email" and live:
            sender = settings.get("RESEND_FROM_EMAIL")
            user = db.users.find_one({"_id": alert["user_id"]}) or {}
            recipient = (
                settings.get("DISRUPTION_TEST_EMAIL")
                if disruption.get("fixture")
                else user.get("email")
            )
            if not sender or not recipient or not settings.get("RESEND_API_KEY"):
                raise ValueError("Email sender/recipient configuration missing")
            if not disruption.get("fixture") and not user.get("notify", {}).get("email"):
                raise ValueError("Traveler has not opted into email")
            board = db.boards.find_one({"trip_id": alert["trip_id"]})
            board_url = (
                settings.get("WEB_BASE_URL", "http://localhost:3000") + "/b/" + board["share_slug"]
            )
            option_links = "".join(
                '<li><a href="'
                + html.escape(
                    board_url + "?disruption=" + disruption["_id"] + "&option=" + option["id"],
                    quote=True,
                )
                + '">'
                + html.escape(option["label"])
                + "</a></li>"
                for option in disruption["options"]
            )
            response = CachedHTTP(db).request(
                "POST",
                "https://api.resend.com/emails",
                headers={
                    "Authorization": "Bearer " + settings.get("RESEND_API_KEY"),
                    "Idempotency-Key": alert["_id"],
                },
                json_body={
                    "from": sender,
                    "to": [recipient],
                    "subject": "Wander: " + alert["title"],
                    "html": "<p>"
                    + html.escape(alert["body"])
                    + "</p><ul>"
                    + option_links
                    + "</ul><p>Open your board and confirm a choice. Email links do not change your plan.</p>",
                },
                ttl=86400,
            )
            patch["provider_id"] = response["id"]
        db.alerts.update_one(
            {"_id": alert["_id"], "lease_token": token},
            {"$set": patch, "$unset": {"lease_until": "", "lease_token": "", "last_error": ""}},
        )
        return True
    except Exception as exc:
        db.alerts.update_one(
            {"_id": alert["_id"], "lease_token": token},
            {
                "$set": {
                    "last_error": type(exc).__name__,
                    "retry_at": now() + timedelta(seconds=30),
                },
                "$inc": {"attempts": 1},
                "$unset": {"lease_until": "", "lease_token": ""},
            },
        )
        return False


def deliver_pending(db, settings, *, live=False, disruption_id=None):
    query = {
        "status": "pending",
        "$or": [{"retry_at": {"$exists": False}}, {"retry_at": {"$lte": now()}}],
    }
    if disruption_id:
        query["disruption_id"] = disruption_id
    for alert in db.alerts.find(query).limit(30):
        deliver_one(db, settings, alert, live=live)


def audio_etag(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
