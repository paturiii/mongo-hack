"""Idempotent setup for the collections Track 3 writes: validators, indexes, Atlas Search/Vector indexes.

    python -m track3_research.setup_db [--wait]
"""
from __future__ import annotations

import argparse
import json
import time

from pymongo import ASCENDING, DESCENDING, GEOSPHERE
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.operations import SearchIndexModel

from shared.config import ROOT, get_db

VALIDATED = ["research_jobs", "places", "opinions", "links", "boards"]

INDEXES: dict[str, list[tuple[list, dict]]] = {
    "research_jobs": [
        ([("status", ASCENDING), ("created_at", ASCENDING)], {}),
        ([("trip_id", ASCENDING), ("kind", ASCENDING)], {}),
        ([("status", ASCENDING), ("claimed_at", ASCENDING)], {}),
    ],
    "places": [
        ([("geo", GEOSPHERE)], {}),
        ([("city", ASCENDING), ("name_norm", ASCENDING)], {}),
        ([("source", ASCENDING), ("source_id", ASCENDING)], {"unique": True}),
        ([("city", ASCENDING), ("category", ASCENDING), ("consensus.score", DESCENDING)], {}),
        ([("city", ASCENDING), ("name_tokens", ASCENDING)], {}),
    ],
    "neighborhoods": [([("geo", GEOSPHERE)], {}), ([("city", ASCENDING), ("name_norm", ASCENDING)], {})],
    "opinions": [
        ([("city", ASCENDING), ("place_id", ASCENDING)], {}),
        ([("city", ASCENDING), ("themes", ASCENDING)], {}),
        ([("expires_at", ASCENDING)], {"expireAfterSeconds": 0}),
    ],
    "links": [
        ([("trip_id", ASCENDING), ("url", ASCENDING), ("item_ref", ASCENDING)], {"unique": True}),
        ([("trip_id", ASCENDING), ("kind", ASCENDING)], {}),
    ],
    "boards": [
        ([("trip_id", ASCENDING)], {"unique": True}),
        ([("share_slug", ASCENDING)], {"unique": True}),
    ],
    "board_versions": [([("trip_id", ASCENDING), ("version", ASCENDING)], {"unique": True})],
    "search_cache": [([("expires_at", ASCENDING)], {"expireAfterSeconds": 0})],
    "trips": [([("status", ASCENDING)], {})],
    "refine_requests": [([("status", ASCENDING), ("created_at", ASCENDING)], {})],
}


def ensure_collections(db, force_validators: bool = False) -> None:
    """Validators are owned by Track 2 (APP-01, scripts/setup_db.py). We only install ours where none exists yet,
    so running this never overrides the team's contract; --force-validators replaces them."""
    info = {c["name"]: c.get("options", {}) for c in db.list_collections()}
    for name in VALIDATED:
        schema = json.loads((ROOT / "shared" / "schemas" / f"{name}.json").read_text())
        opts = {"validator": {"$jsonSchema": schema}, "validationLevel": "strict", "validationAction": "error"}
        if name not in info:
            try:
                db.create_collection(name, **opts)
            except CollectionInvalid:
                pass
        elif force_validators or not info[name].get("validator"):
            db.command({"collMod": name, **opts})
        else:
            print(f"  = {name}: keeping existing validator")
    existing = set(info)
    for name in INDEXES:
        if name not in existing and name not in VALIDATED:
            try:
                db.create_collection(name)
            except CollectionInvalid:
                pass


def ensure_indexes(db) -> None:
    for coll, specs in INDEXES.items():
        for keys, opts in specs:
            try:
                db[coll].create_index(keys, **opts)
            except OperationFailure as exc:
                print(f"  ! index {coll} {keys}: {exc.details.get('errmsg') if exc.details else exc}")


def ensure_search_indexes(db, wait: bool) -> None:
    pending = []
    for path in sorted((ROOT / "infra" / "indexes").glob("*.json")):
        spec = json.loads(path.read_text())
        col = db[spec["collection"]]
        current = {i["name"]: i for i in col.list_search_indexes()}
        if spec["name"] in current:
            if current[spec["name"]].get("latestDefinition") != spec["definition"]:
                try:
                    col.update_search_index(spec["name"], spec["definition"])
                except OperationFailure as exc:
                    print(f"  ! update {spec['name']}: {exc}")
        else:
            col.create_search_index(SearchIndexModel(name=spec["name"], type=spec["type"], definition=spec["definition"]))
            print(f"  + search index {spec['collection']}.{spec['name']}")
        pending.append((col, spec["name"]))
    if not wait:
        return
    for _ in range(120):
        states = {n: next((i.get("queryable") for i in c.list_search_indexes() if i["name"] == n), False) for c, n in pending}
        if all(states.values()):
            print("  search indexes queryable:", ", ".join(states))
            return
        time.sleep(5)
    print("  ! search indexes still building:", states)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true", help="block until Atlas Search indexes are queryable")
    ap.add_argument("--force-validators", action="store_true")
    args = ap.parse_args()
    db = get_db()
    ensure_collections(db, args.force_validators)
    ensure_indexes(db)
    ensure_search_indexes(db, args.wait)
    print("track3 setup ok:", db.name)


if __name__ == "__main__":
    main()
