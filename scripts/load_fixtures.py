"""Load fixtures into Atlas (Mongo is still required). Upserts by _id, so it is safe to re-run.

    python scripts/load_fixtures.py --city lisbon
    python scripts/load_fixtures.py --city lisbon --only places,opinions
    python scripts/load_fixtures.py --city lisbon --embed     # add Voyage embeddings
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import json_util

from shared import config
from shared.db import db

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


def load_cities(d) -> None:
    cities = json_util.loads((config.ROOT / "fixtures" / "cities.json").read_text())
    for c in cities:
        d.cities.update_one({"name": c["name"], "country": c["country"]}, {"$set": c}, upsert=True)
    print(f"  cities: {len(cities)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="lisbon")
    ap.add_argument("--only", default="")
    ap.add_argument("--embed", action="store_true")
    a = ap.parse_args()
    only = {s.strip() for s in a.only.split(",") if s.strip()}
    folder = config.ROOT / "fixtures" / a.city
    d = db()
    print(f"load_fixtures {a.city} → {config.MONGODB_DB}")
    for f in sorted(folder.glob("*.json")):
        if only and f.stem not in only:
            continue
        coll = FILE_TO_COLL[f.stem]
        docs = json_util.loads(f.read_text())
        docs = docs if isinstance(docs, list) else [docs]
        if a.embed and f.stem in EMBED_TEXT:
            from shared.embed import embed
            vecs = embed([EMBED_TEXT[f.stem](x) for x in docs])
            for x, v in zip(docs, vecs):
                x["embedding"] = v
        if coll == "signals":  # time-series: no _id upsert, dedupe on hash
            for x in docs:
                if not d.signals.find_one({"hash": x["hash"]}):
                    d.signals.insert_one(x)
        else:
            for x in docs:
                d[coll].replace_one({"_id": x["_id"]}, x, upsert=True)
        print(f"  {coll}: {len(docs)}{' (embedded)' if a.embed and f.stem in EMBED_TEXT else ''}")
    if not only or "cities" in only:
        load_cities(d)
    print("done")


if __name__ == "__main__":
    main()
