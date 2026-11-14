"""§5.2 research agent protocol: PLAN → SEARCH → EXTRACT → LINK → SCORE → PERSIST.

Track 3 builds every research agent on this; Track 1 reuses it for news/closure monitors.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from shared import geo
from shared.config import get_db
from shared.search_client import SearchResult, search

log = logging.getLogger("wander.agent")

QUOTE_MAX_WORDS = 20


def now() -> datetime:
    return datetime.now(UTC)


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


_STOP = {"the", "a", "o", "de", "da", "do", "dos", "das", "of", "restaurante", "restaurant", "bar", "cafe", "museu", "museum", "lisboa", "lisbon"}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def _tokens(s: str) -> set[str]:
    return {t for t in norm_name(s).split() if t not in _STOP and len(t) > 1}


def name_similarity(a: str, b: str) -> float:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    core_a, core_b = ta - GENERIC_NAME_WORDS, tb - GENERIC_NAME_WORDS
    if core_a and core_a == core_b:
        return max(ratio, 0.9)
    extra = ta ^ tb
    if ta and tb and (ta <= tb or tb <= ta) and (min(len(ta), len(tb)) >= 2 or extra <= GENERIC_NAME_WORDS):
        overlap = len(ta & tb) / max(len(ta), len(tb))
        ratio = max(ratio, 0.75 + 0.25 * overlap)
    return ratio


GENERIC_NAME_WORDS = {"praia", "beach", "restaurante", "restaurant", "museu", "museum", "bar", "cafe", "pastelaria", "confeitaria",
                      "tasca", "taberna", "igreja", "church", "palacio", "palace", "castelo", "castle", "mosteiro", "monastery",
                      "miradouro", "viewpoint", "jardim", "garden", "parque", "park", "mercado", "market", "the", "of", "club", "clube", "cervejaria", "marisqueira", "padaria", "wine",
                      "elevador", "funicular"}


def geocode_matches(name: str, hit_name: str) -> bool:
    """Stricter than name_similarity: the geocoder hit must not add distinctive words ("Castro" ≠ "Castro de Leceia")."""
    if not hit_name:
        return False
    ts, th = _tokens(name) - GENERIC_NAME_WORDS, _tokens(hit_name) - GENERIC_NAME_WORDS
    if ts == th or (th and th <= ts):
        return True
    return difflib.SequenceMatcher(None, norm_name(name), norm_name(hit_name)).ratio() >= 0.85


def trim_quote(q: str | None) -> str | None:
    if not q:
        return None
    words = q.strip().strip('"').split()
    return " ".join(words[:QUOTE_MAX_WORDS]) or None


def stable_id(prefix: str, *parts: Any) -> str:
    return prefix + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ---------- cities ----------
def city_ctx(name: str, country: str | None = None) -> dict:
    """{key, slug, name, country, geo, bbox}. `key` is the value stored in places/opinions.city (display name,
    matching Track 2 fixtures). Uses the shared `cities` collection when present, else Nominatim (cached)."""
    name = name.strip()
    known = get_db().cities.find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    hit = geo.geocode(f"{name}, {country}" if country else name)
    if not hit and not known:
        raise ValueError(f"could not geocode city {name!r}")
    canonical = known["name"] if known else name
    center = known["geo"] if known and known.get("geo") else hit["geo"]
    return {"key": canonical, "slug": slugify(canonical), "name": canonical, "country": country or (known or {}).get("country"),
            "geo": center, "bbox": hit["bbox"] if hit else None}


# ---------- entity linking (§5.2 LINK) ----------
_INDOOR = {"museum": True, "food": True, "bar": True, "club": True, "indoor": True, "stay": True,
           "sight": False, "beach": False, "activity": False, "transit_hub": True, "neighborhood": False}


def name_tokens(name: str) -> list[str]:
    return sorted(_tokens(name))


def ensure_geo(place_id: str, city: dict) -> bool:
    db = get_db()
    p = db.places.find_one({"_id": place_id}, {"geo": 1, "name": 1, "neighborhood": 1})
    if not p:
        return False
    if p.get("geo"):
        return True
    def accept(names: list[str]) -> bool:
        return any(geocode_matches(p["name"], n) for n in names)

    hit = (geo.geocode(f"{p['name']}, {city['name']}", near=city["geo"], radius_km=45, accept=accept)
           or geo.geocode(p["name"], near=city["geo"], radius_km=45, accept=accept))
    if not hit or geo.haversine_m(hit["geo"], city["geo"]) > 60_000:
        return False
    upd = {"geo": hit["geo"], "updated_at": now()}
    if hit.get("neighborhood") and not p.get("neighborhood"):
        upd["neighborhood"] = hit["neighborhood"]
    db.places.update_one({"_id": place_id}, {"$set": upd})
    return True


def link_place(city: dict, name: str, category: str = "sight", geo_hint: dict | None = None, create: bool = True,
               extra: dict | None = None, geocode: bool = True) -> str | None:
    """Fuzzy name + city match, then $geoNear ≤300 m; else create a stub (geocoded unless geocode=False;
    ungeocoded stubs are merged into provider places or geocoded later). Returns place _id."""
    pid = _match_or_create(city, name, category, geo_hint, create, extra, geocode)
    if pid and geocode:
        ensure_geo(pid, city)
    return pid


def _match_or_create(city, name, category, geo_hint, create, extra, geocode) -> str | None:
    db = get_db()
    name = (name or "").strip()
    if not name or len(name) > 120:
        return None
    nn = norm_name(name)
    exact = db.places.find_one({"city": city["key"], "name_norm": nn, "source": {"$ne": "fixture"}}, {"_id": 1}, sort=[("source", -1)])
    if exact:
        return exact["_id"]
    best_id, best = None, 0.0
    toks = name_tokens(name)
    if toks:
        cands = db.places.find({"city": city["key"], "name_tokens": {"$in": toks}, "source": {"$ne": "fixture"}}, {"_id": 1, "name": 1, "source": 1}).limit(400)
        for p in cands:
            s = name_similarity(name, p["name"]) + (0.01 if p.get("source") != "search" else 0)
            if s > best:
                best_id, best = p["_id"], s
    if best >= 0.88:
        return best_id
    if geo_hint:
        near = db.places.aggregate([
            {"$geoNear": {"near": geo_hint, "distanceField": "d", "maxDistance": 300, "query": {"city": city["key"], "source": {"$ne": "fixture"}}}},
            {"$limit": 15}, {"$project": {"name": 1, "d": 1}},
        ])
        for p in near:
            if name_similarity(name, p["name"]) >= 0.6:
                return p["_id"]
    if not create:
        return None
    hit = {"geo": geo_hint} if geo_hint else None
    pid = stable_id("pl_", city["key"], "search", nn)
    doc = {
        "source": "search", "source_id": f"{city['key']}:{nn}", "city": city["key"], "name": name, "name_norm": nn,
        "name_tokens": name_tokens(name),
        "category": category, "indoor": _INDOOR.get(category, False), "business_status": "OPERATIONAL",
        "consensus": {"score": 0, "mentions": 0, "by_source": {}, "tags": [], "opinion_ids": []},
        "created_at": now(),
    }
    if hit:
        doc["geo"] = hit["geo"]
        if hit.get("neighborhood"):
            doc["neighborhood"] = hit["neighborhood"]
        if hit.get("address"):
            doc["address"] = hit["address"]
    if extra:
        doc.update(extra)
    db.places.update_one({"_id": pid}, {"$setOnInsert": doc, "$set": {"updated_at": now()}}, upsert=True)
    return pid


@dataclass
class Query:
    q: str
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    recency_days: int | None = None
    k: int = 6
    topic: str = "general"
    meta: dict = field(default_factory=dict)


@dataclass
class Hit:
    query: Query
    results: list[SearchResult]


class ResearchAgent:
    """Subclass and implement plan/extract/persist (and optionally search/link/score)."""

    kind = "base"
    max_parallel = 6

    def __init__(self, job: dict):
        self.job = job
        self.params: dict = job.get("params") or {}
        self.trip_id: str | None = job.get("trip_id")
        self.db = get_db()
        self.city = city_ctx(self.params["city"], self.params.get("country")) if self.params.get("city") else None
        self.stats: dict[str, Any] = {}

    # PLAN
    def plan(self) -> list[Query]:
        raise NotImplementedError

    # SEARCH (cached by search_client)
    def search(self, queries: list[Query]) -> list[Hit]:
        def one(q: Query) -> Hit:
            try:
                return Hit(q, search(q.q, q.include_domains, q.exclude_domains, q.recency_days, q.k, q.topic))
            except Exception as exc:  # noqa: BLE001
                log.warning("search failed %r: %s", q.q, exc)
                return Hit(q, [])

        with ThreadPoolExecutor(self.max_parallel) as ex:
            hits = list(ex.map(one, queries))
        self.stats["search_results"] = sum(len(h.results) for h in hits)
        return hits

    # EXTRACT
    def extract(self, hits: list[Hit]) -> list[dict]:
        raise NotImplementedError

    # LINK
    def link(self, items: list[dict]) -> list[dict]:
        return items

    # SCORE
    def score(self, items: list[dict]) -> list[dict]:
        return items

    # PERSIST → result_refs
    def persist(self, items: list[dict]) -> list[str]:
        raise NotImplementedError

    def run(self) -> dict:
        queries = self.plan()
        self.stats["queries"] = len(queries)
        hits = self.search(queries)
        items = self.extract(hits)
        items = self.link(items)
        items = self.score(items)
        refs = self.persist(items)
        self.stats["persisted"] = len(refs)
        return {"result_refs": refs, "stats": self.stats}

    def parallel(self, fn, items: list, workers: int | None = None) -> list:
        with ThreadPoolExecutor(workers or self.max_parallel) as ex:
            return list(ex.map(fn, items))
