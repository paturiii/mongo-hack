"""R-06 PlaceFinder: places by category + bbox → normalized `places` (geo, hours, indoor, neighborhood) + Voyage embedding.

Provider: OSM Overpass (keyless, PLACES_PROVIDER=osm). Mirrors are rotated because the main instance is often at capacity.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta

from pymongo import UpdateOne

from shared import geo, http
from shared.agent_protocol import Hit, Query, ResearchAgent, name_tokens, norm_name, now, stable_id
from shared.embed import embed

log = logging.getLogger("wander.places")

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

GROUPS: dict[str, dict] = {
    "sights": {"radius_km": 9, "selectors": [
        'nwr["tourism"~"^(attraction|museum|gallery|viewpoint)$"]["name"]',
        'nwr["historic"~"^(monument|castle|monastery|palace|archaeological_site|tower|fort)$"]["name"]',
        'nwr["amenity"="place_of_worship"]["name"]["wikidata"]',
        'node["place"~"^(suburb|neighbourhood|quarter)$"]["name"]',
    ]},
    "food": {"radius_km": 7, "selectors": [
        'nwr["amenity"~"^(restaurant|cafe)$"]["name"]["opening_hours"]',
        'nwr["amenity"~"^(restaurant|cafe)$"]["name"]["website"]',
        'nwr["amenity"="marketplace"]["name"]',
    ]},
    "nightlife": {"radius_km": 7, "selectors": [
        'nwr["amenity"~"^(bar|pub|nightclub)$"]["name"]',
        'nwr["amenity"="music_venue"]["name"]',
    ]},
    "nature": {"radius_km": 35, "selectors": [
        'nwr["natural"="beach"]["name"]',
        'nwr["leisure"~"^(park|garden|nature_reserve)$"]["name"]["wikidata"]',
        'nwr["tourism"="viewpoint"]["name"]',
    ]},
}


def classify(t: dict) -> tuple[str, bool] | None:
    tour, hist, amen, nat, leis, plc = (t.get(k) for k in ("tourism", "historic", "amenity", "natural", "leisure", "place"))
    if plc in ("suburb", "neighbourhood", "quarter"):
        return "neighborhood", False
    if tour in ("museum", "gallery"):
        return "museum", True
    if amen == "nightclub":
        return "club", True
    if amen in ("bar", "pub", "music_venue"):
        return "bar", True
    if amen in ("restaurant", "cafe", "marketplace"):
        return "food", True
    if nat == "beach":
        return "beach", False
    if leis in ("park", "garden", "nature_reserve"):
        return "activity", False
    if hist in ("monastery", "palace") or amen == "place_of_worship":
        return "sight", True
    if tour in ("attraction", "viewpoint") or hist:
        return "sight", False
    return None


def bbox_around(center: dict, radius_km: float) -> tuple[float, float, float, float]:
    lng, lat = center["coordinates"]
    d_lat = radius_km / 111.0
    d_lng = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    return lat - d_lat, lng - d_lng, lat + d_lat, lng + d_lng


def overpass(query: str) -> list[dict]:
    def call():
        last = None
        for url in OVERPASS:
            try:
                r = http.request("POST", url, data={"data": query}, timeout=90, retries=2, backoff=3)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                    return r.json().get("elements", [])
                last = f"{url} {r.status_code}"
            except Exception as exc:  # noqa: BLE001
                last = f"{url} {exc}"
        raise RuntimeError(f"all overpass mirrors failed: {last}")

    return http.cached("overpass", {"q": query}, call, ttl=timedelta(days=14)) or []


class PlaceFinder(ResearchAgent):
    kind = "places"

    def plan(self) -> list[Query]:
        group = GROUPS[self.params.get("theme", "sights")]
        radius = float(self.params.get("radius_km", group["radius_km"]))
        s, w, n, e = bbox_around(self.city["geo"], radius)
        bb = f"({s:.4f},{w:.4f},{n:.4f},{e:.4f})"
        body = "".join(f"{sel}{bb};" for sel in group["selectors"])
        return [Query(f"[out:json][timeout:80];({body});out center tags 2500;", meta={"radius": radius})]

    def search(self, queries: list[Query]) -> list[Hit]:
        self._elements = [el for q in queries for el in overpass(q.q)]
        self.stats["osm_elements"] = len(self._elements)
        return []

    def extract(self, hits: list[Hit]) -> list[dict]:
        items = []
        for el in self._elements:
            t = el.get("tags") or {}
            cls = classify(t)
            name = t.get("name:en") or t.get("name")
            if not cls or not name:
                continue
            lat, lng = (el.get("lat"), el.get("lon")) if el["type"] == "node" else ((el.get("center") or {}).get("lat"), (el.get("center") or {}).get("lon"))
            if lat is None:
                continue
            cat, indoor = cls
            cuisine = (t.get("cuisine") or "").replace(";", ", ").replace("_", " ")
            items.append({
                "source": "osm", "source_id": f"{el['type']}/{el['id']}", "city": self.city["key"], "name": name,
                "name_norm": norm_name(name), "name_tokens": name_tokens(name), "category": cat, "indoor": indoor,
                "geo": geo.point(lng, lat), "address": " ".join(filter(None, [t.get("addr:street"), t.get("addr:housenumber")])) or None,
                "neighborhood": t.get("addr:suburb") or t.get("addr:city_district"), "hours": t.get("opening_hours"),
                "website": t.get("website") or t.get("contact:website"), "wikidata": t.get("wikidata"),
                "cuisine": cuisine or None, "osm_kind": t.get("tourism") or t.get("historic") or t.get("amenity") or t.get("natural") or t.get("leisure"),
                "notability": (2 if t.get("wikidata") else 0) + (1 if t.get("wikipedia") else 0) + (0.5 if t.get("website") else 0),
                "business_status": "CLOSED_TEMPORARILY" if t.get("disused") else "OPERATIONAL",
            })
        return items

    def link(self, items: list[dict]) -> list[dict]:
        """Neighborhood nodes go to the Track 3 `neighborhoods` collection; every place gets the nearest one."""
        new_hoods = [i for i in items if i["category"] == "neighborhood"]
        if new_hoods:
            self.db.neighborhoods.bulk_write([UpdateOne(
                {"_id": stable_id("nb_", h["source_id"])},
                {"$set": {"city": h["city"], "name": h["name"], "name_norm": h["name_norm"], "name_tokens": h["name_tokens"], "geo": h["geo"], "source_id": h["source_id"]}},
                upsert=True) for h in new_hoods], ordered=False)
        hoods = list(self.db.neighborhoods.find({"city": self.city["key"]}, {"name": 1, "geo": 1}))
        items = [i for i in items if i["category"] != "neighborhood"]
        if hoods:
            for it in items:
                if not it.get("neighborhood"):
                    nearest = min(hoods, key=lambda h: geo.haversine_m(h["geo"], it["geo"]))
                    if geo.haversine_m(nearest["geo"], it["geo"]) < 2500:
                        it["neighborhood"] = nearest["name"]
        return items

    def persist(self, items: list[dict]) -> list[str]:
        ts = now()
        ops, ids = [], []
        for it in items:
            it = {k: v for k, v in it.items() if v is not None}
            pid = stable_id("pl_", "osm", it["source_id"])
            ids.append(pid)
            it["updated_at"] = ts
            ops.append(UpdateOne({"_id": pid}, {"$set": it, "$setOnInsert": {
                "created_at": ts, "consensus": {"score": 0, "mentions": 0, "by_source": {}, "tags": [], "opinion_ids": []}}}, upsert=True))
        for i in range(0, len(ops), 500):
            self.db.places.bulk_write(ops[i : i + 500], ordered=False)
        self.stats["embedded"] = embed_places(self.db, {"_id": {"$in": ids}, "embedding": {"$exists": False}, "notability": {"$gte": 2}}, self.city["name"])
        return ids


def place_text(p: dict, city_name: str) -> str:
    c = p.get("consensus") or {}
    bits = [f"{p['name']} — {p.get('osm_kind') or p['category']}"]
    if p.get("cuisine"):
        bits.append(p["cuisine"])
    if p.get("neighborhood"):
        bits.append(p["neighborhood"])
    bits.append(city_name)
    text = ", ".join(bits)
    if c.get("tags"):
        text += ". Travelers: " + ", ".join(c["tags"][:8])
    if c.get("blurb"):
        text += ". " + c["blurb"]
    return text[:700]


def embed_places(db, flt: dict, city_name: str, limit: int = 400) -> int:
    """Embed (or re-embed) places matching flt; also refreshes search_text for Atlas Search."""
    docs = list(db.places.find(flt, {"name": 1, "category": 1, "cuisine": 1, "neighborhood": 1, "osm_kind": 1, "consensus": 1,
                                     "search_text": 1, "has_emb": {"$gt": ["$embedding", None]}}).limit(limit))
    if not docs:
        return 0
    texts = [place_text(p, city_name) for p in docs]
    stale = [(p, t) for p, t in zip(docs, texts) if not (p.get("has_emb") and p.get("search_text") == t)]
    fresh = [p["_id"] for p, t in zip(docs, texts) if p.get("has_emb") and p.get("search_text") == t]
    if fresh:
        db.places.update_many({"_id": {"$in": fresh}}, {"$set": {"embedded_at": now()}})
    if not stale:
        return 0
    vecs = embed([t for _, t in stale])
    db.places.bulk_write([UpdateOne({"_id": p["_id"]}, {"$set": {"embedding": v, "search_text": t, "embedded_at": now()}}) for (p, t), v in zip(stale, vecs)], ordered=False)
    return len(stale)
