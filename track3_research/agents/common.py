"""Shared bits for Track 3 agents: source classes (§5.2), source typing, opinion extraction + persistence."""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from shared.agent_protocol import Hit, link_place, norm_name, now, stable_id, trim_quote
from shared.config import get_db
from shared.embed import embed
from shared.llm import structured

log = logging.getLogger("wander.agents")

SourceType = Literal["reddit", "forum", "blog", "review", "video", "news"]
Category = Literal["sight", "food", "bar", "club", "museum", "stay", "activity", "transit_hub", "beach", "indoor", "neighborhood"]

FORUM_DOMAINS = ["tripadvisor.com", "tripadvisor.co.uk", "community.ricksteves.com", "flyertalk.com", "fodors.com"]
REVIEW_DOMAINS = ["tripadvisor.com", "tripadvisor.co.uk", "yelp.com", "thefork.com", "thefork.pt", "foursquare.com", "google.com"]
NON_BLOG = ["reddit.com", "tripadvisor.com", "tripadvisor.co.uk", "booking.com", "expedia.com", "youtube.com", "getyourguide.com",
            "viator.com", "tiqets.com", "agoda.com", "hotels.com", "airbnb.com", "klook.com", "yelp.com", "facebook.com", "instagram.com",
            "tiktok.com", "pinterest.com"]

SOURCE_CLASSES: dict[str, dict] = {
    "reddit": {"weight": 1.0, "include": ["reddit.com"], "tpl": ["{city} {theme} reddit recommendations", "r/{city_slug} {theme} locals advice"]},
    "forum": {"weight": 0.9, "include": FORUM_DOMAINS, "tpl": ["{city} {theme} forum advice", "{city} {theme} travel forum tips"]},
    "blog": {"weight": 0.7, "exclude": NON_BLOG, "tpl": ["{city} {theme} travel blog local tips", "{city} {theme} honest guide blog"]},
    "review": {"weight": 0.8, "include": REVIEW_DOMAINS, "tpl": ["{city} {theme} reviews", "best rated {theme} {city} reviews"]},
    "video": {"weight": 0.5, "include": ["youtube.com"], "tpl": ["{city} {theme} guide", "{city} {theme} vlog tips"]},
    "news": {"weight": 0.9, "topic": "news", "recency": 30, "tpl": ["{city} {theme} opening closed renovation", "{city} {theme} news"]},
}
NEWS_FACT_TAGS = {"closed", "closure", "renovation", "new opening", "opening", "strike", "reopened", "temporarily closed"}

THEME_PHRASES = {
    "hidden gems": "hidden gems locals love",
    "things to skip": "tourist traps overrated things to skip",
    "neighborhoods": "best neighborhoods to explore",
    "food": "food where to eat",
    "nightlife": "nightlife bars clubs",
}


def source_type_for(url: str) -> SourceType:
    host = urlparse(url).netloc.lower()
    if "reddit.com" in host:
        return "reddit"
    if "youtube.com" in host or "youtu.be" in host:
        return "video"
    if any(d in host for d in ("tripadvisor.", "ricksteves.com", "flyertalk.com", "fodors.com", "lonelyplanet.com/thorntree")):
        return "forum" if "/ShowTopic" in url or "ricksteves" in host or "flyertalk" in host or "fodors" in host else "review"
    if any(d in host for d in ("yelp.", "thefork.", "foursquare.", "google.")):
        return "review"
    return "blog"


def source_name_for(url: str) -> str:
    p = urlparse(url)
    m = re.search(r"/r/([A-Za-z0-9_]+)", p.path)
    if "reddit.com" in p.netloc and m:
        return f"r/{m.group(1)}"
    return p.netloc.lower().removeprefix("www.")


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            d = datetime.strptime(s[:31], fmt)
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


class ExtractedOpinion(BaseModel):
    source_index: int = Field(description="index of the search result this came from")
    place_name: str | None = Field(description="specific named venue/sight/neighborhood/dish-spot, or null for a general city tip")
    place_category: Category | None
    summary: str = Field(description="paraphrase in <=35 words, concrete and specific")
    quote: str | None = Field(description="verbatim excerpt of at most 20 words from the text, or null")
    stance: Literal["pro", "con", "tip"]
    sentiment: float = Field(description="-1 (very negative) .. 1 (very positive)")
    tags: list[str] = Field(description="2-5 short lowercase tags e.g. 'go early', 'tourist trap', 'book ahead', 'local favorite', 'views', 'fado', 'cheap eats'")
    ad_like: bool = Field(description="true if this reads like sponsored/affiliate/listicle copy")


class OpinionBatch(BaseModel):
    opinions: list[ExtractedOpinion]


EXTRACT_SYS = (
    "You extract traveler opinions from search snippets for a travel planner. Only use what the text says. "
    "One item per distinct claim about a specific place (or a general city tip). Prefer concrete, actionable claims "
    "(timing, price, crowding, booking, what to order, what to skip). Skip generic filler. Max 3 items per result."
)


def results_block(hits: list[Hit]) -> tuple[str, list]:
    flat = []
    for h in hits:
        for r in h.results:
            if r.url not in {x.url for x in flat}:
                flat.append(r)
    lines = [f"[{i}] {r.title}\nURL: {r.url}\n{r.content[:1400]}" for i, r in enumerate(flat)]
    return "\n\n".join(lines), flat


def extract_opinions(hits: list[Hit], city_name: str, theme: str) -> list[dict]:
    block, flat = results_block(hits)
    if not flat:
        return []
    batch = structured(OpinionBatch, EXTRACT_SYS, f"City: {city_name}\nTheme: {theme}\n\nResults:\n{block}", tier="fast")
    out = []
    for o in batch.opinions:
        if not (0 <= o.source_index < len(flat)):
            continue
        r = flat[o.source_index]
        out.append({**o.model_dump(), "source_url": r.url, "source_title": r.title, "published_date": r.published_date})
    return out


def opinion_weight(source_type: str, tags: list[str], ad_like: bool) -> float:
    w = SOURCE_CLASSES.get(source_type, {"weight": 0.6})["weight"]
    if source_type == "news" and not (set(tags) & NEWS_FACT_TAGS):
        w = 0.3
    if ad_like:
        w *= 0.5
    return round(w, 3)


def persist_opinions(items: list[dict], city: dict, theme: str, ttl_days: int = 30, forced_type: str | None = None) -> list[str]:
    """Link each opinion to a place (no geocoding here), embed, upsert on (source_url, place, summary)."""
    if not items:
        return []
    db = get_db()
    fetched = now()
    docs = []
    for it in items:
        summary = (it.get("summary") or "").strip()[:600]
        if not summary:
            continue
        url = it["source_url"]
        stype = forced_type or source_type_for(url)
        tags = sorted({t.strip().lower()[:40] for t in it.get("tags", []) if t.strip()})[:6]
        place_id = None
        cat = it.get("place_category") or "sight"
        if it.get("place_name") and cat != "neighborhood":
            place_id = link_place(city, it["place_name"], cat, geocode=False)
        posted = parse_date(it.get("published_date"))
        sentiment = max(-1.0, min(1.0, float(it.get("sentiment", 0))))
        docs.append({
            "_id": stable_id("op_", url, norm_name(it.get("place_name") or ""), norm_name(summary)[:80]),
            "place_id": place_id, "place_name": it.get("place_name"), "city": city["key"], "source_type": stype,
            "source_name": source_name_for(url), "source_url": url, "source_title": (it.get("source_title") or "")[:300],
            "summary": summary, "stance": it.get("stance", "tip"), "sentiment": sentiment,
            "tags": tags, "ad_like": bool(it.get("ad_like")), "posted_at": posted,
            "weight": opinion_weight(stype, tags, bool(it.get("ad_like"))),
            "fetched_at": fetched, "expires_at": fetched + timedelta(days=ttl_days),
        })
        q = trim_quote(it.get("quote"))
        if q:
            docs[-1]["quote"] = q[:200]
    ids = []
    for d in docs:
        _id = d.pop("_id")
        db.opinions.update_one({"_id": _id}, {"$set": d, "$addToSet": {"themes": theme}}, upsert=True)
        ids.append(_id)
    return ids


def opinion_text(d: dict, city_name: str) -> str:
    return f"{d.get('place_name') or city_name}: {d['summary']} [{', '.join(d.get('tags', []))}]"


def embed_opinion_backlog(city_key: str | None = None, limit: int = 2000) -> int:
    """Opinions are persisted fast and embedded in batches afterwards (Voyage budget is shared)."""
    db = get_db()
    flt: dict = {"embedding": {"$exists": False}}
    if city_key:
        flt["city"] = city_key
    docs = list(db.opinions.find(flt, {"place_name": 1, "summary": 1, "tags": 1, "city": 1}).limit(limit))
    if not docs:
        return 0
    vecs = embed([opinion_text(d, d["city"]) for d in docs])
    from pymongo import UpdateOne

    db.opinions.bulk_write([UpdateOne({"_id": d["_id"]}, {"$set": {"embedding": v}}) for d, v in zip(docs, vecs)], ordered=False)
    return len(docs)
