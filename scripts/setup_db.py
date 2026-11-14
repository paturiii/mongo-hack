"""Idempotently initialize only Track 1-owned collections and supporting indexes."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import ROOT, Settings

SCHEMAS = {
    "watches": {
        "required": ["_id", "trip_id", "kind", "target", "next_check_at", "active"],
        "properties": {
            "trip_id": {"bsonType": "string"},
            "active": {"bsonType": "bool"},
            "next_check_at": {"bsonType": "date"},
        },
    },
    "disruptions": {
        "required": ["trip_id", "kind", "severity", "evidence", "affected", "options", "status"],
        "properties": {
            "severity": {"bsonType": "int", "minimum": 1, "maximum": 5},
            "status": {"enum": ["open", "notified", "resolved", "dismissed"]},
            "evidence": {"bsonType": "array", "minItems": 1},
        },
    },
    "alerts": {
        "required": ["user_id", "trip_id", "disruption_id", "channel", "status", "created_at"],
        "properties": {
            "channel": {"enum": ["board", "email", "voice"]},
            "status": {"enum": ["pending", "sent", "seen"]},
        },
    },
    "signal_receipts": {"required": ["_id", "status", "created_at"]},
    "disruption_audit": {"required": ["disruption_id", "event", "created_at"]},
}


def setup(db):
    existing = set(db.list_collection_names())
    for name, schema in SCHEMAS.items():
        validator = {"$jsonSchema": {"bsonType": "object", **schema}}
        contract = ROOT / "shared" / "schemas" / (name + ".json")
        if contract.exists():
            validator = json.loads(contract.read_text())
        if name not in existing:
            db.create_collection(name, validator=validator)
        else:
            db.command("collMod", name, validator=validator)
    for name, meta, ttl in (("signals", "meta", 14 * 86400), ("price_snapshots", "meta", None)):
        if name not in existing:
            options = {"timeseries": {"timeField": "ts", "metaField": meta}}
            if ttl:
                options["expireAfterSeconds"] = ttl
            db.create_collection(name, **options)
        else:
            info = next(db.list_collections(filter={"name": name}))
            if not info.get("options", {}).get("timeseries"):
                raise RuntimeError(
                    f"{name} must be a time-series collection; refusing to replace data"
                )
    db.watches.create_index([("active", 1), ("next_check_at", 1), ("lease_until", 1)])
    db.watches.create_index([("trip_id", 1), ("kind", 1), ("target_key", 1)], unique=True)
    db.alerts.create_index([("disruption_id", 1), ("channel", 1)], unique=True)
    db.disruptions.create_index([("trip_id", 1), ("status", 1)])
    db.signal_receipts.create_index([("status", 1), ("created_at", 1)])
    db.search_cache.create_index("expires_at", expireAfterSeconds=0)
    db.disruption_audit.create_index([("disruption_id", 1), ("event", 1)], unique=True)
    print("OK Track 1 collections, validators, time-series and indexes initialized")


if __name__ == "__main__":
    client, db = Settings.load().connect()
    try:
        setup(db)
    finally:
        client.close()
