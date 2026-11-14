"""Idempotent DB setup: $jsonSchema validators, indexes, time-series, Atlas Search/Vector indexes (APP-01).

    python scripts/setup_db.py            # everything
    python scripts/setup_db.py --no-search
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import ASCENDING, DESCENDING, GEOSPHERE
from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from shared import config
from shared.db import db

ROOT = config.ROOT
SCHEMAS = ROOT / "shared" / "schemas"
TIMESERIES = {
    "signals": {"timeField": "ts", "metaField": "meta", "expire": 14 * 86400},
    "price_snapshots": {"timeField": "ts", "metaField": "meta", "expire": None},
}
INDEXES = {
    "users": [([("email", ASCENDING)], {"unique": True})],
    "auth_sessions": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0}), ([("user_id", ASCENDING)], {})],
    "sessions": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0}), ([("user_id", ASCENDING), ("created_at", DESCENDING)], {})],
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
    "watches": [([("active", ASCENDING), ("next_check_at", ASCENDING)], {})],
    "disruptions": [([("trip_id", ASCENDING), ("status", ASCENDING)], {})],
    "alerts": [([("trip_id", ASCENDING), ("created_at", DESCENDING)], {})],
    "usage": [([("user_id", ASCENDING), ("day", ASCENDING)], {"unique": True})],
    "cities": [([("name", ASCENDING), ("country", ASCENDING)], {"unique": True})],
}


def ensure_collections(d) -> None:
    existing = set(d.list_collection_names())
    for f in sorted(SCHEMAS.glob("*.json")):
        name, schema = f.stem, json.loads(f.read_text())
        opts = {"validator": {"$jsonSchema": schema}, "validationLevel": "moderate", "validationAction": "error"}
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


def main() -> None:
    d = db()
    print(f"setup_db → {config.MONGODB_DB}")
    ensure_collections(d)
    ensure_indexes(d)
    if "--no-search" not in sys.argv:
        try:
            ensure_search_indexes(d)
        except OperationFailure as e:
            print(f"  search indexes skipped: {e.details.get('errmsg', e)}")
    print("done")


if __name__ == "__main__":
    main()
