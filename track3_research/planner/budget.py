"""R-15 Budget estimator: aggregation over the trip's link price_hints + per-day heuristics → total range with confidence."""
from __future__ import annotations

from shared.config import get_db

STAY_PER_NIGHT = {"budget": 75, "mid": 150, "splurge": 320}
BAND_MULT = {"budget": 0.65, "mid": 1.0, "splurge": 1.9}
TRANSIT_PER_DAY = 8


def estimate(trip: dict, days: list[dict], link_ids: list[str]) -> dict:
    db = get_db()
    travelers = int(trip.get("travelers") or 2)
    band = (trip.get("profile") or {}).get("budget_band") or "mid"
    hinted = {r["_id"]: r for r in db.links.aggregate([
        {"$match": {"_id": {"$in": link_ids}, "price_hint.amount": {"$type": "number"}}},
        {"$group": {"_id": "$kind", "total": {"$sum": "$price_hint.amount"}, "n": {"$sum": 1}}},
    ])}
    per_person = {"food": 0.0, "tickets": 0.0, "nightlife": 0.0}
    priced = total_items = 0
    for d in days:
        for s in d["slots"]:
            cost = (s["item"].get("est_cost") or {}).get("amount")
            if cost is None:
                continue
            total_items += 1
            priced += 1 if s["item"].get("price_from_link") else 0
            k = s["item"].get("kind")
            bucket = "food" if k in ("lunch", "dinner") else "nightlife" if k == "night" else "tickets"
            per_person[bucket] += float(cost)
    nights = max(1, len(days) - 1)
    rooms = max(1, (travelers + 1) // 2)
    stay = STAY_PER_NIGHT.get(band, 150) * nights * rooms
    transit = TRANSIT_PER_DAY * len(days) * travelers
    group = {k: round(v * travelers) for k, v in per_person.items()}
    breakdown = {"stay": stay, **group, "local_transit": transit}
    mid = sum(breakdown.values())
    low, high = round(mid * 0.85 / 50) * 50, round(mid * 1.2 / 50) * 50
    confidence = "medium" if hinted or (total_items and priced / total_items > 0.3) else "low"
    excludes = ["flights"] if "flight" not in hinted else []
    text = f"€{low:,}–{high:,} for {travelers}" + (" (excl. flights)" if excludes else "")
    return {"low": low, "high": high, "currency": "EUR", "travelers": travelers, "confidence": confidence,
            "breakdown": breakdown, "excludes": excludes, "text": text}
