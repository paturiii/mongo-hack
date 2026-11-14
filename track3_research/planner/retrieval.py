"""R-11 Hybrid retrieval: $rankFusion(Atlas Search + Vector Search) per category → Voyage rerank → consensus/pref boosts.

Falls back to manual reciprocal-rank fusion if $rankFusion is unavailable on the tier (MASTERPLAN §13).
"""
from __future__ import annotations

import logging
import math

from pymongo.errors import OperationFailure

from shared import geo
from shared.agent_protocol import city_ctx, ensure_geo, geocode_matches, link_place, name_similarity
from shared.config import get_db
from shared.embed import embed, rerank
from track3_research.agents.places import place_text

log = logging.getLogger("wander.retrieval")

PROJECT = {"name": 1, "category": 1, "geo": 1, "hours": 1, "indoor": 1, "neighborhood": 1, "consensus": 1, "notability": 1, "source": 1,
           "source_id": 1, "website": 1, "wikidata": 1, "day_trip": 1, "cuisine": 1, "osm_kind": 1, "price_band": 1, "walk_in_only": 1}


def _vec_stage(city_key: str, qvec: list[float], cats: list[str], k: int, indoor: bool | None) -> dict:
    flt: dict = {"city": city_key, "category": {"$in": cats}}
    if indoor is not None:
        flt["indoor"] = indoor
    return {"$vectorSearch": {"index": "places_vector", "path": "embedding", "queryVector": qvec, "numCandidates": max(200, k * 6), "limit": k, "filter": flt}}


def _text_stage(city_key: str, query: str, cats: list[str], indoor: bool | None) -> dict:
    filters = [{"equals": {"path": "city", "value": city_key}}, {"in": {"path": "category", "value": cats}}]
    if indoor is not None:
        filters.append({"equals": {"path": "indoor", "value": indoor}})
    return {"$search": {"index": "places_search", "compound": {
        "filter": filters,
        "should": [
            {"text": {"query": query, "path": "search_text"}},
            {"text": {"query": query, "path": "consensus.tags", "score": {"boost": {"value": 2}}}},
            {"text": {"query": query, "path": "name", "score": {"boost": {"value": 1.5}}}},
        ],
        "minimumShouldMatch": 1,
    }}}


def fused_candidates(city_key: str, query: str, qvec: list[float], cats: list[str], k: int = 50, indoor: bool | None = None) -> list[dict]:
    db = get_db()
    post = [{"$match": {"source": {"$ne": "fixture"}}}, {"$limit": k}, {"$project": {**PROJECT, "fusion": {"$meta": "score"}}}]
    try:
        return list(db.places.aggregate([
            {"$rankFusion": {"input": {"pipelines": {
                "vector": [_vec_stage(city_key, qvec, cats, k, indoor)],
                "text": [_text_stage(city_key, query, cats, indoor), {"$limit": k}],
            }}, "combination": {"weights": {"vector": 1.0, "text": 1.0}}}},
            *post,
        ]))
    except OperationFailure as exc:
        log.warning("$rankFusion unavailable (%s); manual RRF fallback", exc.code)
        vec = list(db.places.aggregate([_vec_stage(city_key, qvec, cats, k, indoor), {"$project": {"_id": 1}}]))
        txt = list(db.places.aggregate([_text_stage(city_key, query, cats, indoor), {"$limit": k}, {"$project": {"_id": 1}}]))
        rrf: dict = {}
        for lst in (vec, txt):
            for r, d in enumerate(lst):
                rrf[d["_id"]] = rrf.get(d["_id"], 0) + 1 / (60 + r + 1)
        ids = sorted(rrf, key=lambda i: -rrf[i])[:k]
        docs = {d["_id"]: d for d in db.places.find({"_id": {"$in": ids}, "source": {"$ne": "fixture"}}, PROJECT)}
        return [{**docs[i], "fusion": rrf[i]} for i in ids if i in docs]


def _matches_any(p: dict, terms: list[str]) -> bool:
    hay = " ".join([p["name"], " ".join((p.get("consensus") or {}).get("tags", [])), p.get("cuisine") or "", p.get("osm_kind") or ""]).lower()
    return any(t and t.lower() in hay for t in terms)


def score_candidates(cands: list[dict], query: str, profile: dict, top_k: int) -> list[dict]:
    if not cands:
        return []
    texts = [place_text(p, "") for p in cands]
    rr = dict(rerank(query, texts))
    n = len(cands)
    out = []
    for i, p in enumerate(cands):
        c = p.get("consensus") or {}
        mentions = c.get("mentions", 0) or 0
        cons = ((c.get("score", 0) or 0) + 1) / 2 * min(1.0, math.log1p(mentions) / math.log(6)) if mentions else 0.0
        notab = min(1.0, (p.get("notability") or 0) / 3)
        s = 0.5 * rr.get(i, 0) + 0.3 * cons + 0.1 * notab + 0.1 * (1 - i / n)
        if c.get("tourist_trap"):
            s -= 0.15
        if _matches_any(p, profile.get("dealbreakers") or []):
            s -= 1.0
        if _matches_any(p, (profile.get("interests") or []) + (profile.get("vibe") or [])):
            s += 0.05
        if any(name_similarity(p["name"], m) >= 0.75 for m in profile.get("must_sees") or []):
            s += 0.6
            p = {**p, "must_see": True}
        out.append({**p, "rerank": round(rr.get(i, 0), 4), "final": round(s, 4)})
    out.sort(key=lambda p: -p["final"])
    return out[:top_k]


def sourced(p: dict) -> bool:
    """Every board item needs ≥1 opinion source, or provider evidence (notable OSM entry with wikidata/website)."""
    c = p.get("consensus") or {}
    return (c.get("mentions", 0) or 0) > 0 or (p.get("source") == "osm" and (p.get("wikidata") or p.get("website")))


CATEGORY_QUERIES = {
    "sights": (["sight", "museum", "activity"], "{interests} {vibe} must-see sights, museums, viewpoints and historic places locals and travelers love in {city}"),
    "food": (["food"], "authentic local {dietary} food, {budget} restaurants and tascas travelers rave about in {city}"),
    "nightlife": (["bar", "club"], "{nightlife} nightlife: bars, live music, fado and clubs with good vibes in {city}"),
    "beach": (["beach", "activity"], "best beaches and coastal spots for a beach day near {city}"),
    "indoor": (["museum", "sight", "food", "bar"], "indoor rainy-day museums, palaces, markets and covered spots in {city}"),
}


def retrieve(city_name: str, profile: dict, needs: dict[str, int], overrides: dict[str, str] | None = None,
             indoor: bool | None = None, exclude_ids: set | None = None) -> dict[str, list[dict]]:
    """needs = {category_key: how many to return}. Returns ranked, geocoded, sourced candidates per key."""
    city = city_ctx(city_name)
    sight_interests = [i for i in profile.get("interests") or [] if i.lower() not in ("food", "nightlife", "drinks", "eating")]
    fill = {"city": city["name"], "interests": ", ".join(sight_interests), "vibe": ", ".join(profile.get("vibe") or []),
            "dietary": ", ".join(profile.get("dietary") or []), "budget": profile.get("budget_band") or "",
            "nightlife": str(profile.get("nightlife_intensity") or "").replace("_", " ")}
    queries = {}
    for key in needs:
        cats, tpl = CATEGORY_QUERIES[key]
        queries[key] = (cats, (overrides or {}).get(key) or " ".join(tpl.format(**fill).split()))
    qvecs = dict(zip(queries, embed([q for _, q in queries.values()], input_type="query")))
    out: dict[str, list[dict]] = {}
    for key, (cats, q) in queries.items():
        cands = [c for c in fused_candidates(city["key"], q, qvecs[key], cats, k=50, indoor=indoor) if sourced(c) and c["_id"] not in (exclude_ids or set())]
        if key == "sights":
            have = {c["_id"] for c in cands}
            for m in profile.get("must_sees") or []:
                pid = link_place(city, m, "sight", create=False, geocode=False)
                doc = get_db().places.find_one({"_id": pid, "source": {"$ne": "fixture"}}, PROJECT) if pid else None
                if doc and not doc.get("geo") and not ensure_geo(pid, city):
                    hit = geo.geocode(m, near=city["geo"], radius_km=45,
                                      accept=lambda ns, m=m, d=doc: any(geocode_matches(m, n) or geocode_matches(d["name"], n) for n in ns))
                    if hit:
                        get_db().places.update_one({"_id": pid}, {"$set": {"geo": hit["geo"]}})
                if doc and not doc.get("geo"):
                    doc["geo"] = (get_db().places.find_one({"_id": pid}, {"geo": 1}) or {}).get("geo")
                if doc and doc.get("geo") and pid not in have and pid not in (exclude_ids or set()):
                    cands.append({**doc, "fusion": 0})
        ranked = score_candidates(cands, q, profile, top_k=max(needs[key] * 2, 10))
        keep = []
        for p in ranked:
            if not p.get("geo") and ensure_geo(p["_id"], city):
                p["geo"] = get_db().places.find_one({"_id": p["_id"]}, {"geo": 1}).get("geo")
            if p.get("geo"):
                keep.append(p)
        out[key] = keep
        log.info("retrieve %s: %d fused → %d kept (query=%r)", key, len(cands), len(keep), q[:80])
    return out
