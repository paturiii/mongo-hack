"""`understand` node: free-text trip request → structured partial trip (LLM_MODEL_SMART, structured output)."""
import logging
from datetime import date, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from shared import llm
from shared.db import db

log = logging.getLogger("wander.intake")


class Leg(BaseModel):
    kind: Literal["flight", "train", "bus"]
    carrier: str | None
    number: str | None
    from_city: str | None
    to_city: str | None
    date: str | None = Field(description="YYYY-MM-DD only if the user gave the exact day")


class Understanding(BaseModel):
    destination_city: str | None
    destination_country: str | None
    origin_city: str | None
    start_date: str | None = Field(description="YYYY-MM-DD only if an exact day is stated, else null")
    end_date: str | None = Field(description="YYYY-MM-DD only if stated or derivable from start + length, else null")
    month_hint: str | None = Field(description="Month/season mentioned without exact days, e.g. 'March'")
    nights: int | None
    travelers: int | None
    interests: list[Literal["food", "nightlife", "history", "art", "nature", "beaches"]]
    vibe: list[str]
    dietary: list[str]
    budget_band: Literal["budget", "mid", "splurge"] | None
    pace: Literal["relaxed", "balanced", "packed"] | None
    nightlife_intensity: Literal["early", "drinks", "big_night_out", "every_night"] | None
    must_sees: list[str]
    dealbreakers: list[str]
    legs: list[Leg]


SYSTEM = """You turn a traveler's message into structured trip fields. Today is {today}.
Rules:
- Only fill a field if the message states it or it is an unambiguous inference ("me and my girlfriend" = 2 travelers,
  "clubbing" = nightlife interest, "foodie" = food). Otherwise null / empty list. NEVER guess dates.
- start_date/end_date only when the exact day is given. "in March" or "5 days in March" -> month_hint="March",
  nights=5 (days-1 is fine), dates null. Dates without a year are the next occurrence after today.
- A flight/train number (e.g. TP1351) becomes a leg; carrier is the letters. Its from/to are only what the user says.
- origin_city is where they start the trip ("from London")."""


def understand(prompt: str) -> Understanding:
    return llm.structured(Understanding, SYSTEM.format(today=date.today().isoformat()), prompt, smart=True)


def geocode(city: str | None, country: str | None = None) -> dict | None:
    """City name → {name, country, geo}. Seeded `cities` first, then OSM Nominatim (no key)."""
    if not city:
        return None
    hit = db().cities.find_one({"name": {"$regex": f"^{city.strip()}$", "$options": "i"}}, sort=[("population", -1)])
    if hit:
        return {"name": hit["name"], "country": hit["country"], "geo": hit["geo"]}
    try:
        r = httpx.get("https://nominatim.openstreetmap.org/search",
                      params={"q": f"{city}, {country or ''}", "format": "json", "limit": 1, "featuretype": "city"},
                      headers={"User-Agent": "WanderBot/0.1 (hackathon project)"}, timeout=6)
        res = r.json()
        if res:
            return {"name": city.strip(), "country": country,
                    "geo": {"type": "Point", "coordinates": [float(res[0]["lon"]), float(res[0]["lat"])]}}
    except Exception as e:  # noqa: BLE001 - geocode is best-effort
        log.info("geocode fallback for %s: %s", city, type(e).__name__)
    return {"name": city.strip(), "country": country, "geo": None}


def to_known(u: Understanding) -> dict:
    """Map extraction onto slot ids (only confidently-known values)."""
    known: dict = {}
    if u.start_date and (u.end_date or u.nights):
        end = u.end_date or (date.fromisoformat(u.start_date) + timedelta(days=u.nights)).isoformat()
        known["dates"] = {"start": u.start_date, "end": end}
    for k in ("travelers", "budget_band", "pace", "nightlife_intensity"):
        if getattr(u, k) is not None:
            known[k] = getattr(u, k)
    for k in ("interests", "dietary", "must_sees", "dealbreakers"):
        if getattr(u, k):
            known[k] = getattr(u, k)
    origin = u.origin_city or next((leg.from_city for leg in u.legs if leg.from_city), None)
    if origin:
        known["origin"] = origin
    if u.vibe:
        known["vibe"] = u.vibe
    return known
