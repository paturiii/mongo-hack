"""R-12 Day clustering: stay = anchor; $geoNear greedy clustering into day areas (area_geo polygon stored for Track 1).

Constraints: each day's activities lie within DAY_RADIUS_M of the day seed (so no day spans more than ~4 km radius),
places must be open that weekday (museums never on closed days), meals sit near the day's activities, nightlife on chosen nights.
"""
from __future__ import annotations

from datetime import date

from shared import geo
from shared.agent_protocol import name_similarity
from shared.config import get_db

DAY_RADIUS_M = 3000
MEAL_RADIUS_M = 1500
DAY_TRIP_MIN_M = 12_000
CITY_MAX_M = 7_000
PER_DAY = {"relaxed": 2, "balanced": 3, "moderate": 3, "packed": 4, "fast": 4}


def nights_out(dates: list[date], intensity: str | None) -> set[date]:
    i = (intensity or "").lower()
    if i in ("none", "no", "low", "early_nights", "quiet"):
        return set()
    weekend = [d for d in dates if d.weekday() in (4, 5)]
    if i in ("big_night_out", "high", "every_night", "party", "heavy"):
        n = max(2, len(dates) // 2)
    else:
        n = 1
    chosen = list(dict.fromkeys(weekend + [d for d in dates[1:] if d not in weekend]))[:n]
    return set(chosen)


def _near(center: dict, ids: list[str], radius: float, limit: int = 40) -> list[dict]:
    if not ids:
        return []
    return list(get_db().places.aggregate([
        {"$geoNear": {"near": center, "distanceField": "dist_m", "maxDistance": radius, "query": {"_id": {"$in": ids}}}},
        {"$limit": limit}, {"$project": {"_id": 1, "dist_m": 1}},
    ]))


def is_duplicate(p: dict, chosen: list[dict]) -> bool:
    """Same place under two names (search stub vs OSM node) — close together and similarly named."""
    return any(geo.haversine_m(p["geo"], c["geo"]) < 600 and name_similarity(p["name"], c["name"]) >= 0.6 for c in chosen)


def cluster_days(dates: list[date], anchor: dict, pools: dict[str, list[dict]], profile: dict, events: list[dict],
                 arrival_capacity: int | None = None, day_trip_ok: bool = True) -> list[dict]:
    """Returns [{date, kind, center, activities:[place], lunch, dinner, night:{place|event}}] — places are candidate dicts."""
    per_day = PER_DAY.get(str(profile.get("pace") or "balanced").lower(), 3)
    sights = {p["_id"]: p for p in pools.get("sights", [])}
    food = {p["_id"]: p for p in pools.get("food", [])}
    night = {p["_id"]: p for p in pools.get("nightlife", [])}
    used: set[str] = set()
    plans = [{"date": d, "kind": "city", "activities": [], "lunch": None, "dinner": None, "night": None} for d in dates]

    # Day trip: best-ranked far-away (or explicitly day_trip) sight gets a whole middle day.
    if day_trip_ok and len(dates) >= 3:
        far = [p for p in pools.get("sights", []) if p.get("day_trip") or geo.haversine_m(p["geo"], anchor) > DAY_TRIP_MIN_M]
        if far:
            hero = next((p for p in far if p.get("must_see")), far[0])
            idx = min(len(dates) - 2, max(1, len(dates) // 2))
            dp = plans[idx]
            dp.update(kind="day_trip", center=hero["geo"], day_trip=hero.get("day_trip") or {"destination": hero.get("neighborhood") or hero["name"]})
            if geo.open_on(hero.get("hours"), dp["date"], hero["category"]):
                dp["activities"].append(hero)
                used.add(hero["_id"])
                for n in _near(hero["geo"], [i for i in sights if i not in used], DAY_RADIUS_M):
                    p = sights[n["_id"]]
                    if len(dp["activities"]) >= per_day:
                        break
                    if geo.open_on(p.get("hours"), dp["date"], p["category"]) and not is_duplicate(p, dp["activities"]):
                        dp["activities"].append(p)
                        used.add(p["_id"])
            else:
                dp["kind"] = "city"

    for i, dp in enumerate(plans):
        if dp["kind"] == "day_trip":
            continue
        cap = per_day if i else (arrival_capacity if arrival_capacity is not None else max(1, per_day - 1))
        local = [p for p in pools.get("sights", []) if p["_id"] not in used and geo.haversine_m(p["geo"], anchor) <= CITY_MAX_M
                 and geo.open_on(p.get("hours"), dp["date"], p["category"])]
        if not local or cap <= 0:
            dp["center"] = anchor
            continue
        must = [p for p in local if p.get("must_see")]
        if i == 0:
            seed = min(local[:6], key=lambda p: geo.haversine_m(p["geo"], anchor))
        else:
            seed = must[0] if must else local[0]
        dp["center"] = seed["geo"]
        nearby = _near(seed["geo"], [p["_id"] for p in local], DAY_RADIUS_M)
        rank = {p["_id"]: r for r, p in enumerate(local)}
        picks = [seed["_id"]] + sorted((n["_id"] for n in nearby if n["_id"] != seed["_id"]), key=lambda x: rank.get(x, 999))
        for pid in picks:
            if len(dp["activities"]) >= cap:
                break
            if not is_duplicate(sights[pid], dp["activities"]):
                dp["activities"].append(sights[pid])
            used.add(pid)

    # Rainy-day balance for Track 1: remember which days are mostly outdoor.
    for dp in plans:
        acts = dp["activities"]
        dp["outdoor_share"] = round(sum(1 for p in acts if not p.get("indoor")) / len(acts), 2) if acts else 0
        pts = [p["geo"] for p in acts] or [dp.get("center") or anchor]
        dp["centroid"] = geo.centroid(pts)

    # Meals near the day's activities.
    food_used: set[str] = set()

    def pick_food(center: dict, d: date) -> dict | None:
        for radius in (MEAL_RADIUS_M, 3000, 8000):
            for n in _near(center, [i for i in food if i not in food_used], radius):
                p = food[n["_id"]]
                if geo.open_on(p.get("hours"), d, "food"):
                    food_used.add(p["_id"])
                    return {**p, "dist_m": round(n["dist_m"])}
        return None

    for i, dp in enumerate(plans):
        if dp["activities"] and (i > 0 or len(dp["activities"]) >= 2):
            dp["lunch"] = pick_food(dp["activities"][0]["geo"] if len(dp["activities"]) == 1 else dp["centroid"], dp["date"])
        dinner_center = anchor if dp["kind"] == "day_trip" else (dp["activities"][-1]["geo"] if dp["activities"] else anchor)
        dp["dinner"] = pick_food(dinner_center, dp["date"])

    # Nightlife: dated events first, else best bar/club near dinner.
    chosen = nights_out(dates, profile.get("nightlife_intensity"))
    ev_by_date: dict[str, list[dict]] = {}
    for e in events:
        ev_by_date.setdefault(e["event"]["date"], []).append(e)
    night_used: set[str] = set()
    venues_used: set[str] = set()
    for dp in plans:
        evs = sorted(ev_by_date.get(dp["date"].isoformat(), []), key=lambda e: e["event"].get("kind") == "club_night")
        evs = [e for e in evs if e["event"].get("venue") not in venues_used]
        if evs and dp["date"] in chosen:
            dp["night"] = {"event": evs[0]}
            venues_used.add(evs[0]["event"].get("venue"))
            continue
        if dp["date"] not in chosen:
            continue
        center = (dp["dinner"] or {}).get("geo") or anchor
        for radius in (2000, 5000, 12000):
            hit = next((night[n["_id"]] for n in _near(center, [i for i in night if i not in night_used], radius)), None)
            if hit:
                night_used.add(hit["_id"])
                dp["night"] = {"place": hit}
                break

    for dp in plans:
        pts = [p["geo"] for p in dp["activities"]] + [x["geo"] for x in (dp["lunch"], dp["dinner"]) if x and x.get("geo")]
        dp["area_geo"] = geo.area_polygon(pts) if pts else None
        dp["radius_m"] = round(max((geo.haversine_m(dp["centroid"], p["geo"]) for p in dp["activities"]), default=0))
    return plans
