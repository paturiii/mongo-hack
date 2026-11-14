"""Load fixtures into Atlas (Mongo is still required). Upserts by _id; never wipes collections.

    python scripts/load_fixtures.py --city lisbon                    # Track 2 pack + Track 1 demo trip + cities
    python scripts/load_fixtures.py --city lisbon --only places,opinions
    python scripts/load_fixtures.py --city lisbon --embed            # add Voyage embeddings (Track 2 pack)

Two independent fixture sets with non-overlapping ids:
- fixtures/lisbon/          Track 2 pack (APP-02): every §6.3 collection, trip_fixture_lisbon / b_fixture_lisbon
- fixtures/track1/lisbon/   Track 1 demo trip: trip_lisbon / b_lisbon / lisbon-fixture-demo (`load(db)`)
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import json_util

from shared.config import ROOT, Settings
from shared.http import now

FILE_TO_COLL = {
    "user": "users", "session": "sessions", "questions": "questions", "trip": "trips", "research_jobs": "research_jobs",
    "places": "places", "opinions": "opinions", "links": "links", "board": "boards", "memories": "memories",
    "refine_requests": "refine_requests", "watches": "watches", "signals": "signals", "disruptions": "disruptions",
    "alerts": "alerts",
}
EMBED_TEXT = {
    "places": lambda p: f"{p['name']} · {p['category']} · {p.get('neighborhood', '')} · {', '.join(p.get('consensus', {}).get('tags', []))}",
    "opinions": lambda o: o["summary"],
    "memories": lambda m: m.get("text") or f"{m.get('key')}: {m.get('value')}",
}


def load(db, city="lisbon"):
    """Track 1 demo trip (isolated ids). Refuses to overwrite non-fixture documents."""
    folder = ROOT / "fixtures" / "track1" / city
    for path in folder.glob("*.json"):
        collection = {"trip": "trips", "board": "boards", "user": "users"}.get(path.stem, path.stem)
        documents = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(documents, dict):
            documents = [documents]
        for doc in documents:
            if collection == "users":
                doc["created_at"] = now()
                doc["password_hash"] = "!fixture-login-disabled"
            old = db[collection].find_one({"_id": doc["_id"]})
            if old and not old.get("fixture"):
                raise RuntimeError("Refusing to overwrite non-fixture document in " + collection)
            doc["fixture"] = True
            doc["updated_at"] = now()
            db[collection].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    db.places.create_index([("geo", "2dsphere")])
    db.auth_sessions.create_index("expires_at", expireAfterSeconds=0)
    print("  track1 demo trip: loaded (synthetic trip, real places; no personal data)")


def load_pack(db, city="lisbon", only=frozenset(), embed=False):
    """Track 2 fixtures pack (APP-02) + seeded cities for autocomplete."""
    for f in sorted((ROOT / "fixtures" / city).glob("*.json")):
        if only and f.stem not in only:
            continue
        coll = FILE_TO_COLL[f.stem]
        docs = json_util.loads(f.read_text())
        docs = docs if isinstance(docs, list) else [docs]
        if embed and f.stem in EMBED_TEXT:
            from shared.embed import embed as vectors
            for x, v in zip(docs, vectors([EMBED_TEXT[f.stem](x) for x in docs])):
                x["embedding"] = v
        if coll == "signals":  # time-series: no _id upsert, dedupe on hash
            for x in docs:
                if not db.signals.find_one({"hash": x["hash"]}):
                    db.signals.insert_one(x)
        else:
            for x in docs:
                db[coll].replace_one({"_id": x["_id"]}, x, upsert=True)
        print(f"  {coll}: {len(docs)}{' (embedded)' if embed and f.stem in EMBED_TEXT else ''}")
    if not only or "cities" in only:
        cities = json_util.loads((ROOT / "fixtures" / "cities.json").read_text())
        for c in cities:
            db.cities.update_one({"name": c["name"], "country": c["country"]}, {"$set": c}, upsert=True)
        print(f"  cities: {len(cities)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="lisbon", choices=["lisbon"])
    ap.add_argument("--only", default="")
    ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    only = frozenset(s.strip() for s in a.only.split(",") if s.strip())
    settings = Settings.load()
    client, db = settings.connect()
    try:
        print(f"load_fixtures {a.city} → {settings.get('MONGODB_DB')}")
        load_pack(db, a.city, only, a.embed)
        if not only or "track1" in only:
            load(db, a.city)
        print("done")
    finally:
        client.close()


if __name__ == "__main__":
    main()
