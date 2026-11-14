"""R-04 Consensus scorer.

score = Σ(weight × sentiment × recency_decay) / Σ(weight), recency_decay = 0.5^(age_years/2)
× cross-source boost (2 types 1.2, ≥3 types 1.35, single source 0.8) × ad/listicle penalty (0.7 when most mentions read like ads).
Computed by one aggregation pipeline that $merges into places.consensus; then chips ("Reddit + forums agree: go early").

    python -m track3_research.consensus --city Lisbon
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pymongo import UpdateOne

from shared.agent_protocol import city_ctx, ensure_geo, name_similarity
from shared.config import get_db

SOURCE_LABEL = {"reddit": "Reddit", "forum": "forums", "blog": "blogs", "review": "reviews", "video": "YouTube", "news": "local news"}
FIXTURE_RE = re.compile(r"^\[FIXTURE\]")

_TAG_CANON = [
    (r"\b(go|arrive|get there|visit) (early|before \d+)", "go early"),
    (r"\b(book|reserve|reservation)s?\b.*(ahead|advance|needed|essential|recommended)?", "book ahead"),
    (r"\btourist(y)? trap|overrated|touristy", "tourist trap"),
    (r"\b(long )?(queue|line|wait)s?\b", "expect a queue"),
    (r"\bview(s|point)?\b", "views"),
    (r"\blocal(s)? (favou?rite|spot|haunt)|where locals", "local favorite"),
    (r"\bcheap|budget|good value|affordable", "good value"),
    (r"\bexpensive|pricey|overpriced", "pricey"),
    (r"\bcrowd(ed|s)?\b|busy", "crowded"),
    (r"\bfado\b", "fado"),
    (r"\bwalk[- ]?in\b|no reservations", "walk-in only"),
    (r"\bclosed|closure|renovation", "closed/renovation"),
]


GENERIC_TAGS = {"restaurant", "restaurants", "food", "bar", "bars", "lisbon", "travel", "recommendation", "recommended", "top pick",
                "museum", "sight", "attraction", "nightlife", "club", "portugal", "europe", "city", "tourism", "must see", "must-see",
                "worth it", "great", "good", "popular", "favorite", "best"}


def canon_tag(t: str) -> str:
    t = t.lower().strip()
    for pat, canon in _TAG_CANON:
        if re.search(pat, t):
            return canon
    return t


def real_filter(db, city_key: str) -> dict:
    """Ignore Track 2 fixture opinions once real research exists for the city."""
    real = db.opinions.count_documents({"city": city_key, "summary": {"$not": FIXTURE_RE}}, limit=20)
    return {"summary": {"$not": FIXTURE_RE}} if real >= 20 else {}


def merge_stubs(city: dict) -> int:
    """Fold search-created stubs into provider places (fuzzy name, or $geoNear ≤300 m) and repoint opinions/links."""
    db = get_db()
    merged = 0
    for stub in db.places.find({"city": city["key"], "source": "search"}, {"name": 1, "name_tokens": 1, "geo": 1}):
        target = None
        toks = stub.get("name_tokens") or []
        if toks:
            best, best_s = None, 0.0
            for p in db.places.find({"city": city["key"], "source": {"$nin": ["search", "fixture"]}, "name_tokens": {"$in": toks}}, {"name": 1}).limit(300):
                s = name_similarity(stub["name"], p["name"])
                if s > best_s:
                    best, best_s = p, s
            if best_s >= 0.88:
                target = best
        if target is None and stub.get("geo"):
            for p in db.places.aggregate([
                {"$geoNear": {"near": stub["geo"], "distanceField": "d", "maxDistance": 300, "query": {"city": city["key"], "source": {"$nin": ["search", "fixture"]}}}},
                {"$limit": 20}, {"$project": {"name": 1}}]):
                if name_similarity(stub["name"], p["name"]) >= 0.6:
                    target = p
                    break
        if target is None:
            continue
        db.opinions.update_many({"place_id": stub["_id"]}, {"$set": {"place_id": target["_id"]}})
        db.links.update_many({"item_ref": f"place:{stub['_id']}"}, {"$set": {"item_ref": f"place:{target['_id']}"}})
        db.places.delete_one({"_id": stub["_id"]})
        merged += 1
    return merged


def score_pipeline(city_key: str, extra_match: dict) -> list[dict]:
    year_ms = 365.25 * 24 * 3600 * 1000
    return [
        {"$match": {"city": city_key, "place_id": {"$ne": None}, **extra_match}},
        {"$set": {"_posted": {"$ifNull": ["$posted_at", {"$dateSubtract": {"startDate": "$fetched_at", "unit": "year", "amount": 1}}]}}},
        {"$set": {"_age": {"$max": [0, {"$divide": [{"$subtract": ["$$NOW", "$_posted"]}, year_ms]}]}}},
        {"$set": {"_decay": {"$pow": [0.5, {"$divide": ["$_age", 2]}]}}},
        {"$group": {
            "_id": "$place_id",
            "num": {"$sum": {"$multiply": ["$weight", "$sentiment", "$_decay"]}},
            "den": {"$sum": "$weight"},
            "mentions": {"$sum": 1},
            "types": {"$addToSet": "$source_type"},
            "srcs": {"$push": "$source_type"},
            "tags": {"$push": "$tags"},
            "ads": {"$sum": {"$cond": ["$ad_like", 1, 0]}},
            "opinion_ids": {"$push": "$_id"},
        }},
        {"$set": {
            "base": {"$cond": [{"$gt": ["$den", 0]}, {"$divide": ["$num", "$den"]}, 0]},
            "ntypes": {"$size": "$types"},
            "ad_share": {"$divide": ["$ads", "$mentions"]},
        }},
        {"$set": {
            "boost": {"$switch": {"branches": [{"case": {"$gte": ["$ntypes", 3]}, "then": 1.35}, {"case": {"$eq": ["$ntypes", 2]}, "then": 1.2}], "default": 0.8}},
            "ad_pen": {"$cond": [{"$gt": ["$ad_share", 0.5]}, 0.7, 1.0]},
        }},
        {"$project": {"_id": 1, "consensus": {
            "score": {"$round": [{"$multiply": ["$base", "$boost", "$ad_pen"]}, 4]},
            "base": {"$round": ["$base", 4]},
            "mentions": "$mentions",
            "source_types": "$types",
            "by_source": {"$arrayToObject": {"$map": {"input": "$types", "as": "t", "in": {
                "k": "$$t", "v": {"$size": {"$filter": {"input": "$srcs", "as": "s", "cond": {"$eq": ["$$s", "$$t"]}}}}}}}},
            "tags": {"$reduce": {"input": "$tags", "initialValue": [], "in": {"$setUnion": ["$$value", "$$this"]}}},
            "opinion_ids": "$opinion_ids",
            "updated_at": "$$NOW",
        }}},
        {"$merge": {"into": "places", "on": "_id", "whenMatched": "merge", "whenNotMatched": "discard"}},
    ]


def build_chips(city_key: str, extra_match: dict) -> int:
    """Multi-source agreement chips + ordered tags + blurb (used for Atlas Search text and embeddings)."""
    db = get_db()
    by_place: dict[str, list[dict]] = defaultdict(list)
    for o in db.opinions.find({"city": city_key, "place_id": {"$ne": None}, **extra_match},
                              {"place_id": 1, "source_type": 1, "source_url": 1, "summary": 1, "tags": 1, "weight": 1, "stance": 1, "sentiment": 1}):
        by_place[o["place_id"]].append(o)
    df: Counter = Counter()
    for ops_list in by_place.values():
        df.update({canon_tag(t) for o in ops_list for t in o.get("tags", [])})
    generic = GENERIC_TAGS | {t for t, n in df.items() if len(by_place) >= 20 and n / len(by_place) > 0.2}
    ops = []
    for pid, ops_list in by_place.items():
        tag_types: dict[str, set] = defaultdict(set)
        tag_best: dict[str, dict] = {}
        counts: Counter = Counter()
        for o in ops_list:
            for t in {canon_tag(t) for t in o.get("tags", [])} - generic:
                tag_types[t].add(o["source_type"])
                counts[t] += 1
                if t not in tag_best or o["weight"] > tag_best[t]["weight"]:
                    tag_best[t] = o
        chips = []
        agreed = sorted((t for t in tag_types if len(tag_types[t]) >= 2), key=lambda t: (-len(tag_types[t]), -counts[t]))
        for t in agreed[:2]:
            types = sorted(tag_types[t], key=lambda s: ["reddit", "forum", "review", "blog", "news", "video"].index(s))
            label = " + ".join(SOURCE_LABEL[s] for s in types[:3])
            label = label[0].upper() + label[1:]
            best = tag_best[t]
            chips.append({"text": f"{label} agree: {t}", "source_type": best["source_type"], "url": best["source_url"], "source_types": types, "multi": True})
        for o in sorted(ops_list, key=lambda o: -o["weight"]):
            if len(chips) >= 2:
                break
            if any(c["url"] == o["source_url"] for c in chips):
                continue
            label = SOURCE_LABEL[o["source_type"]]
            text = o["summary"] if len(o["summary"]) <= 110 else o["summary"][:107].rsplit(" ", 1)[0] + "…"
            chips.append({"text": f"{label[0].upper() + label[1:]}: {text}", "source_type": o["source_type"], "url": o["source_url"], "source_types": [o["source_type"]], "multi": False})
        blurb = " ".join(o["summary"] for o in sorted(ops_list, key=lambda o: -o["weight"])[:3])[:500]
        tourist_trap = any(canon_tag(t) == "tourist trap" for o in ops_list if o["stance"] == "con" for t in o.get("tags", []))
        ops.append(UpdateOne({"_id": pid}, {"$set": {
            "consensus.tags": [t for t, _ in counts.most_common(10)], "consensus.chips": chips,
            "consensus.blurb": blurb, "consensus.tourist_trap": tourist_trap}}))
    for i in range(0, len(ops), 500):
        db.places.bulk_write(ops[i : i + 500], ordered=False)
    return len(ops)


def geocode_top_stubs(city: dict, limit: int = 30) -> int:
    db = get_db()
    stubs = db.places.find({"city": city["key"], "source": "search", "geo": {"$exists": False}, "consensus.mentions": {"$gte": 1}},
                           {"_id": 1}).sort([("consensus.mentions", -1), ("consensus.score", -1)]).limit(limit)
    return sum(1 for s in stubs if ensure_geo(s["_id"], city))


def run_consensus(city_name: str, geocode_limit: int = 30, embed: bool = True) -> dict:
    from track3_research.agents.places import embed_places

    db = get_db()
    city = city_ctx(city_name)
    merged = merge_stubs(city)
    match = real_filter(db, city["key"])
    db.places.update_many({"city": city["key"], "consensus.mentions": {"$gt": 0}, "source": {"$ne": "fixture"}},
                          {"$set": {"consensus.score": 0, "consensus.mentions": 0}})
    list(db.opinions.aggregate(score_pipeline(city["key"], match)))
    chips = build_chips(city["key"], match)
    geocoded = geocode_top_stubs(city, geocode_limit) if geocode_limit else 0
    merged += merge_stubs(city) if geocoded else 0
    embedded = 0
    if embed:
        embedded = embed_places(db, {"city": city["key"], "consensus.mentions": {"$gt": 0}, "source": {"$ne": "fixture"},
                                     "$or": [{"embedded_at": {"$exists": False}}, {"$expr": {"$lt": ["$embedded_at", "$consensus.updated_at"]}}]},
                                city["name"], limit=600)
    return {"merged_stubs": merged, "places_scored": chips, "geocoded": geocoded, "embedded": embedded,
            "at": datetime.now(timezone.utc).isoformat()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Lisbon")
    ap.add_argument("--geocode", type=int, default=30)
    a = ap.parse_args()
    print(run_consensus(a.city, a.geocode))


if __name__ == "__main__":
    main()
