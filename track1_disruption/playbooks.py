from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from shared.http import CachedHTTP


def replacements(db, settings, trip, payload):
    origin = trip["destination"]["geo"]
    query = {"business_status": "OPERATIONAL"}
    if payload["kind"] == "weather":
        query["indoor"] = True
    else:
        original = db.places.find_one({"_id": payload.get("place_id")})
        if original:
            origin = original["geo"]
            query["category"] = original["category"]
            query["_id"] = {"$ne": original["_id"]}
    candidates = list(
        db.places.aggregate(
            [
                {
                    "$geoNear": {
                        "near": origin,
                        "distanceField": "distance_m",
                        "spherical": True,
                        "maxDistance": 8000,
                        "query": query,
                    }
                },
                {"$limit": 20},
                {"$sort": {"consensus.score": -1}},
            ]
        )
    )
    if candidates and settings.mode != "fixtures":
        data = CachedHTTP(db).request(
            "POST",
            "https://api.voyageai.com/v1/rerank",
            headers={"Authorization": "Bearer " + settings.get("VOYAGE_API_KEY")},
            json_body={
                "model": settings.get("VOYAGE_RERANK_MODEL"),
                "query": "Suitable alternatives for "
                + ", ".join(trip.get("profile", {}).get("interests", [])),
                "documents": [
                    row["name"] + " " + str(row.get("consensus", {})) for row in candidates
                ],
                "top_k": min(3, len(candidates)),
            },
        )
        candidates = [candidates[row["index"]] for row in data["data"]]
    return candidates[:3]


def options(db, settings, trip, payload, impact):
    kind = payload["kind"]
    destination = trip["destination"]["name"]
    search_url = "https://www.google.com/search?" + urlencode(
        {"q": destination + " official transport updates"}
    )
    constraints = {}
    if kind == "flight_delay":
        leg = next(leg for leg in trip["legs"] if leg["number"] == payload["number"])
        shifted = datetime.fromisoformat(str(leg["arr_at"])) + timedelta(
            minutes=payload.get("minutes", 180) + 60
        )
        constraints = {"shift_start_to": shifted.isoformat()}
        label, instruction = (
            "Shift arrival-day plans",
            "Move affected arrival-day activities after arrival and transfer. Move dinner to tomorrow if needed; keep other days unchanged.",
        )
        search_url = "https://www.google.com/travel/flights?" + urlencode(
            {"q": trip["origin"]["name"] + " to " + destination}
        )
    elif kind == "flight_cancel":
        label, instruction = (
            "Replan around a new arrival",
            "Find alternative flights, train or bus, and replan only the affected day after the traveler confirms their new arrival.",
        )
        search_url = "https://www.omio.com/search?" + urlencode(
            {"departure": trip["origin"]["name"], "arrival": destination}
        )
    elif kind == "weather":
        label, instruction = (
            "Choose indoor alternatives",
            "Replace only affected outdoor slots with open indoor alternatives matching my interests.",
        )
        constraints = {"indoor_only": True}
    elif kind == "strike":
        label, instruction = (
            "Avoid the affected area",
            "Use walking or alternative transit and reorder only the affected slots, avoiding the strike area.",
        )
        constraints = {"avoid_area_geo": payload["area_geo"]}
        search_url = "https://www.rome2rio.com/s/" + quote(destination)
    elif kind in {"closure", "event_cancel"}:
        label, instruction = (
            "Find an open alternative",
            "Replace the affected closed venue or cancelled event with a nearby, similar open alternative.",
        )
    elif kind == "dead_link":
        label, instruction = (
            "Replace the unavailable link",
            "Find and validate a working official link for the affected booking.",
        )
        link = db.links.find_one({"_id": payload["link_id"]}) or {}
        search_url = "https://www.google.com/search?" + urlencode(
            {"q": link.get("label", destination) + " official booking"}
        )
    else:
        label, instruction = (
            "Compare alternative prices",
            "Compare the affected booking against nearby alternatives, preserving dates and preferences.",
        )
        search_url = "https://www.google.com/travel/search?" + urlencode({"q": destination})
    suggested = (
        replacements(db, settings, trip, payload)
        if kind in {"weather", "closure", "event_cancel"}
        else []
    )
    if suggested:
        instruction += " Candidates: " + ", ".join(row["name"] for row in suggested) + "."
        search_url = "https://www.google.com/maps/search/?" + urlencode(
            {"api": 1, "query": suggested[0]["name"] + " " + destination}
        )
    return [
        {
            "id": "replan",
            "label": label,
            "actions": [
                {"type": "replan", "instruction": instruction, "constraints": constraints},
                {"type": "link", "url": search_url},
            ],
        },
        {
            "id": "review",
            "label": "Review alternatives myself",
            "actions": [
                {"type": "link", "url": search_url},
                {
                    "type": "notice",
                    "instruction": "Contact your stay about late check-in if your arrival changes; no changes have been made automatically.",
                },
            ],
        },
        {
            "id": "keep",
            "label": "Keep my plan",
            "actions": [
                {
                    "type": "notice",
                    "instruction": "Keep the existing plan and acknowledge this alert.",
                },
            ],
        },
    ]
