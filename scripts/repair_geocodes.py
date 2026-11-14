"""Unset geo on search stubs whose stored point came from a name-mismatched Nominatim hit.

    python scripts/repair_geocodes.py --city Lisbon [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import geo
from shared.agent_protocol import city_ctx, geocode_matches, link_place, name_similarity
from shared.config import get_db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Lisbon")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db, city = get_db(), city_ctx(a.city)
    bad, moved = [], 0
    for p in db.places.find({"city": city["key"], "source": "search", "geo": {"$exists": True}}, {"name": 1, "geo": 1}):
        q = f"{p['name']}, {city['name']}"
        hit = geo.geocode(q, near=city["geo"], radius_km=45)
        if not hit or geo.haversine_m(hit["geo"], p["geo"]) > 50 or geocode_matches(p["name"], hit.get("name") or ""):
            continue
        strict = geo.geocode(q, near=city["geo"], radius_km=45, accept=lambda ns: any(geocode_matches(p["name"], n) for n in ns))
        if strict and geo.haversine_m(strict["geo"], p["geo"]) < 200:
            continue
        print(f"  {p['name']!r} → {hit.get('name')!r}; strict: {strict and strict['name']!r}")
        if a.dry_run:
            continue
        if strict:
            db.places.update_one({"_id": p["_id"]}, {"$set": {"geo": strict["geo"], "neighborhood": strict.get("neighborhood")}})
            moved += 1
        else:
            bad.append(p["_id"])
    if bad:
        db.places.update_many({"_id": {"$in": bad}}, {"$unset": {"geo": "", "neighborhood": ""}})
    print(f"{len(bad)} geocodes cleared, {moved} moved{' (dry run)' if a.dry_run else ''}")

    names = {p["_id"]: p for p in db.places.find({"city": city["key"]}, {"name": 1, "category": 1})}
    relinked = 0
    for o in db.opinions.find({"city": city["key"], "place_id": {"$ne": None}, "place_name": {"$ne": None}}, {"place_id": 1, "place_name": 1, "themes": 1}):
        p = names.get(o["place_id"])
        if not p or name_similarity(o["place_name"], p["name"]) >= 0.6:
            continue
        cat = "food" if "food" in (o.get("themes") or []) or "restaurants" in (o.get("themes") or []) else p.get("category") or "sight"
        print(f"  opinion on {o['place_name']!r} was linked to {p['name']!r}")
        if not a.dry_run:
            pid = link_place(city, o["place_name"], cat, geocode=False)
            db.opinions.update_one({"_id": o["_id"]}, {"$set": {"place_id": pid}})
            relinked += 1
    print(f"{relinked} opinions relinked")


if __name__ == "__main__":
    main()
