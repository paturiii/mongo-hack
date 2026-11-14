"""Idempotent DB setup: $jsonSchema validators, indexes, time-series, Atlas Search/Vector indexes.

    python scripts/setup_db.py            # everything
    python scripts/setup_db.py --no-search

`setup(db)` (collections + validators + indexes, no search indexes) is what Track 1's tests call.
Existing indexes and search indexes owned by other tracks are never overwritten.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import ASCENDING, DESCENDING, GEOSPHERE
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from shared.config import ROOT, Settings

SCHEMAS = ROOT / "shared" / "schemas"
TIMESERIES = {
    "signals": {"timeField": "ts", "metaField": "meta", "expire": 14 * 86400},
    "price_snapshots": {"timeField": "ts", "metaField": "meta", "expire": None},
}
# Track 1 audit/queue collections that have no shared contract file.
EXTRA_VALIDATORS = {
    "signal_receipts": {"required": ["_id", "status", "created_at"]},
    "disruption_audit": {"required": ["disruption_id", "event", "created_at"]},
}
INDEXES = {
    "users": [([("email", ASCENDING)], {"unique": True})],
    "auth_sessions": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0}), ([("user_id", ASCENDING)], {})],
    "sessions": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0}),
                 ([("user_id", ASCENDING), ("created_at", DESCENDING)], {})],
    "questions": [([("session_id", ASCENDING), ("batch", ASCENDING)], {"unique": True})],
    "trips": [([("user_id", ASCENDING)], {}), ([("status", ASCENDING)], {})],
    "research_jobs": [([("status", ASCENDING), ("created_at", ASCENDING)], {}), ([("trip_id", ASCENDING)], {})],
    # Track 3 owns uniqueness rules on places/opinions/links; only shared essentials here.
    "places": [([("geo", GEOSPHERE)], {}), ([("city", ASCENDING), ("category", ASCENDING)], {})],
    "opinions": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0})],
    "links": [([("trip_id", ASCENDING)], {})],
    "search_cache": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0})],
    "boards": [([("share_slug", ASCENDING)], {"unique": True}), ([("trip_id", ASCENDING)], {})],
    "refine_requests": [([("status", ASCENDING), ("created_at", ASCENDING)], {})],
    "memories": [([("user_id", ASCENDING), ("kind", ASCENDING), ("key", ASCENDING)], {})],
    "usage": [([("user_id", ASCENDING), ("day", ASCENDING)], {"unique": True})],
    "cities": [([("name", ASCENDING), ("country", ASCENDING)], {"unique": True})],
    # Track 1 (disruption management)
    "watches": [([("active", ASCENDING), ("next_check_at", ASCENDING), ("lease_until", ASCENDING)], {}),
                ([("trip_id", ASCENDING), ("kind", ASCENDING), ("target_key", ASCENDING)], {"unique": True})],
    "alerts": [([("disruption_id", ASCENDING), ("channel", ASCENDING)], {"unique": True}),
               ([("trip_id", ASCENDING), ("created_at", DESCENDING)], {})],
    "disruptions": [([("trip_id", ASCENDING), ("status", ASCENDING)], {})],
    "signal_receipts": [([("status", ASCENDING), ("created_at", ASCENDING)], {})],
    "disruption_audit": [([("disruption_id", ASCENDING), ("event", ASCENDING)], {"unique": True})],
}


def _validator(path: Path) -> dict:
    doc = json.loads(path.read_text())
    return doc if "$jsonSchema" in doc else {"$jsonSchema": doc}


def ensure_collections(d) -> None:
    existing = set(d.list_collection_names())
    validators = {f.stem: _validator(f) for f in sorted(SCHEMAS.glob("*.json")) if f.stem not in TIMESERIES}
    validators |= {k: {"$jsonSchema": {"bsonType": "object", **v}} for k, v in EXTRA_VALIDATORS.items()}
    for name, validator in validators.items():
        opts = {"validator": validator, "validationLevel": "moderate", "validationAction": "error"}
        if name in existing:
            d.command({"collMod": name, **opts})
        else:
            d.create_collection(name, **opts)
        print(f"  collection {name}: validator ok")
    for name, ts in TIMESERIES.items():
        if name not in existing:
            kw = {"timeseries": {"timeField": ts["timeField"], "metaField": ts["metaField"], "granularity": "minutes"}}
            if ts["expire"]:
                kw["expireAfterSeconds"] = ts["expire"]
            d.create_collection(name, **kw)
        elif not next(d.list_collections(filter={"name": name})).get("options", {}).get("timeseries"):
            raise RuntimeError(f"{name} must be a time-series collection; refusing to replace data")
        print(f"  time-series {name}: ok")
    for name in ("search_cache", "cities"):
        if name not in existing:
            d.create_collection(name)


def ensure_indexes(d) -> None:
    for coll, specs in INDEXES.items():
        for keys, opts in specs:
            try:
                d[coll].create_index(keys, **opts)
            except OperationFailure as e:
                if e.code not in (85, 86):  # same keys, different options/name: someone else's index wins
                    raise
                print(f"  index {coll}{keys}: exists with other options, kept")
        print(f"  indexes {coll}: {len(specs)}")


def ensure_search_indexes(d) -> None:
    for spec in json.loads((ROOT / "infra" / "indexes" / "search_indexes.json").read_text()):
        coll = d[spec["collection"]]
        names = {ix["name"] for ix in coll.list_search_indexes()}
        if spec["name"] in names:  # never overwrite: another track may own/tune it
            print(f"  search index {spec['name']}: exists, kept")
        else:
            coll.create_search_index(SearchIndexModel(definition=spec["definition"], name=spec["name"], type=spec["type"]))
            print(f"  search index {spec['name']}: created (builds async)")


def setup(db) -> None:
    ensure_collections(db)
    ensure_indexes(db)


def main() -> None:
    settings = Settings.load()
    client, d = settings.connect()
    try:
        print(f"setup_db → {settings.get('MONGODB_DB')}")
        setup(d)
        if "--no-search" not in sys.argv:
            try:
                ensure_search_indexes(d)
            except OperationFailure as e:
                print(f"  search indexes skipped: {e.details.get('errmsg', e)}")
        print("done")
    finally:
        client.close()


if __name__ == "__main__":
    main()
