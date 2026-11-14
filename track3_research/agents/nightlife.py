"""R-08 NightlifeFinder: dated events for the trip (Ticketmaster if keyed, plus RA / DICE / Shotgun / Xceed discovery via search)."""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from shared import http
from shared.agent_protocol import Hit, Query, ResearchAgent, link_place
from shared.config import settings
from shared.llm import structured
from shared.search_client import search
from track3_research.link_resolver import save_link

from .common import persist_opinions, results_block

EVENT_DOMAINS = ["ra.co", "dice.fm", "shotgun.live", "xceed.me", "ticketline.sapo.pt", "bandsintown.com", "songkick.com", "eventbrite.com",
                 "ticketmaster.pt", "ticketmaster.com", "blueticket.pt", "everythingisnew.pt", "feverup.com"]


class Event(BaseModel):
    name: str
    venue: str
    date: str = Field(description="YYYY-MM-DD; must be explicitly stated or clearly implied in the text")
    start_time: str | None = Field(description="HH:MM 24h if stated")
    kind: str = Field(description="club|concert|party|fado|festival")
    price_eur: float | None
    source_index: int = Field(description="result index whose URL is the event/ticket page")


class Events(BaseModel):
    events: list[Event]


class Club(BaseModel):
    name: str
    open_days: list[str] = Field(description="weekdays it normally opens, as Mo Tu We Th Fr Sa Su")
    opens_at: str = Field(description="usual opening time HH:MM")
    agenda_index: int | None = Field(description="index of a result that is the club's own site, its RA/DICE/Shotgun venue page or agenda; null if none")
    tip: str = Field(description="<=25 words: door policy, when to arrive, music")
    source_index: int


class Clubs(BaseModel):
    clubs: list[Club] = Field(description="4 clubs/late venues travelers recommend, best first")


LISTING_HINTS = ("page=", "/cities/", "/b/", "/guide", "/events?", "/search", "/discover", "/top-", "/best-")
DAY_CODES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


class NightlifeFinder(ResearchAgent):
    kind = "nightlife"

    def plan(self) -> list[Query]:
        dates = self.params.get("dates") or {}
        self._start, self._end = _d(dates.get("start")), _d(dates.get("end"))
        c = self.city["name"]
        month = self._start.strftime("%B %Y") if self._start else ""
        qs = [
            Query(f"{c} club events {month}", include_domains=["ra.co", "dice.fm", "shotgun.live", "xceed.me"], k=8),
            Query(f"{c} concerts {month}", include_domains=["bandsintown.com", "songkick.com", "ticketline.sapo.pt", "everythingisnew.pt", "blueticket.pt"], k=8),
            Query(f"{c} live music fado shows {month} tickets", include_domains=EVENT_DOMAINS, k=8),
        ]
        if self._start:
            d = self._start
            while self._end and d <= self._end:
                qs.append(Query(f"{c} party {d:%A %d %B %Y}", include_domains=["ra.co", "dice.fm", "shotgun.live", "xceed.me", "eventbrite.com"], k=5))
                d += timedelta(days=2)
        qs.append(Query(f"best clubs in {c} reddit door policy what time", include_domains=["reddit.com"], k=6, meta={"clubs": True}))
        qs.append(Query(f"{c} best nightclubs guide opening nights", k=6, meta={"clubs": True}))
        return qs

    def _club_nights(self, hits: list[Hit]) -> list[dict]:
        """Regular club nights on trip dates when each club normally opens, linked to the venue's own agenda page."""
        block, flat = results_block([h for h in hits if h.query.meta.get("clubs")])
        if not flat or not self._start:
            return []
        res = structured(Clubs, "Pick clubs/late-night venues travelers recommend, with their usual opening nights.",
                         f"City: {self.city['name']}\n\nResults:\n{block}", tier="fast")
        out = []
        for cl in res.clubs[:4]:
            agenda = flat[cl.agenda_index].url if cl.agenda_index is not None and 0 <= cl.agenda_index < len(flat) else None
            if not agenda or "reddit.com" in agenda:
                found = search(f"{cl.name} {self.city['name']} club agenda official", k=5)
                agenda = next((r.url for r in found if "reddit.com" not in r.url and not any(h in r.url for h in LISTING_HINTS)), None)
            if not agenda:
                continue
            d = self._start
            while d <= self._end:
                if DAY_CODES[d.weekday()] in cl.open_days:
                    out.append({"name": f"Club night at {cl.name}", "venue": cl.name, "date": d.isoformat(), "start_time": cl.opens_at, "kind": "club_night",
                                "price_eur": None, "url": agenda, "provider": urlparse(agenda).netloc.removeprefix("www."), "tip": cl.tip,
                                "tip_url": flat[cl.source_index].url if 0 <= cl.source_index < len(flat) else None})
                d += timedelta(days=1)
        return out

    def _ticketmaster(self) -> list[dict]:
        if not (settings.ticketmaster_api_key and self._start and self._end):
            return []
        params = {"apikey": settings.ticketmaster_api_key, "city": self.city["name"], "size": 50,
                  "startDateTime": f"{self._start}T00:00:00Z", "endDateTime": f"{self._end}T23:59:59Z", "classificationName": "music"}
        data = http.cached("ticketmaster", params, lambda: http.get_json("https://app.ticketmaster.com/discovery/v2/events.json", params=params)) or {}
        out = []
        for e in data.get("_embedded", {}).get("events", []):
            venue = (e.get("_embedded", {}).get("venues") or [{}])[0]
            loc = venue.get("location") or {}
            out.append({"name": e["name"], "venue": venue.get("name", ""), "date": e["dates"]["start"].get("localDate"),
                        "start_time": (e["dates"]["start"].get("localTime") or "")[:5] or None, "kind": "concert", "price_eur": None,
                        "url": e["url"], "provider": "ticketmaster", "status": e["dates"].get("status", {}).get("code"),
                        "geo": {"type": "Point", "coordinates": [float(loc["longitude"]), float(loc["latitude"])]} if loc.get("longitude") else None})
        return out

    def extract(self, hits: list[Hit]) -> list[dict]:
        block, flat = results_block([h for h in hits if not h.query.meta.get("clubs")])
        events = self._ticketmaster()
        if flat and self._start:
            res = structured(Events, "Extract concrete events (club nights, concerts, fado shows) with an explicit date. Ignore anything without a date.",
                             f"City: {self.city['name']}\nTrip dates: {self._start} to {self._end}\n\nResults:\n{block}", tier="fast")
            for e in res.events:
                if not (0 <= e.source_index < len(flat)):
                    continue
                url = flat[e.source_index].url
                years = set(re.findall(r"20\d\d", url))
                if any(h in url for h in LISTING_HINTS) or (years and str(self._start.year) not in years):
                    continue
                events.append({**e.model_dump(), "url": url, "provider": urlparse(url).netloc.removeprefix("www.")})
        events += self._club_nights(hits)
        in_range = []
        seen = set()
        for e in events:
            d = _d(e.get("date"))
            if not d or not (self._start <= d <= self._end) or e.get("status") in ("cancelled", "postponed"):
                continue
            key = (e["name"].lower()[:40], str(d))
            if key not in seen:
                seen.add(key)
                in_range.append(e)
        self.stats["events_found"] = len(events)
        return in_range

    def persist(self, items: list[dict]) -> list[str]:
        refs = []
        adults = int(self.params.get("travelers") or 2)
        for e in items:
            cat = "club" if e["kind"] in ("club", "party", "festival") else "bar"
            venue_id = link_place(self.city, e["venue"], cat, geo_hint=e.get("geo")) if e.get("venue") else None
            label = f"{e['name']} @ {e['venue']} · {_d(e['date']):%a %b %d}" + (f" {e['start_time']}" if e.get("start_time") else "")
            lid, ok = save_link(trip_id=self.trip_id, item_ref=f"event:{e['name'][:60]}:{e['date']}", kind="club_ticket", provider=e["provider"], url=e["url"],
                                label=label, is_official=e["provider"] in ("ticketmaster", "ra.co", "dice.fm", "shotgun.live"), for_date=str(e["date"])[:10],
                                party_size=adults, price_hint={"amount": e["price_eur"], "currency": "EUR", "as_of": None} if e.get("price_eur") else None,
                                extra={"event": {"name": e["name"], "venue": e["venue"], "venue_place_id": venue_id, "date": str(e["date"])[:10],
                                                 "start_time": e.get("start_time"), "kind": e["kind"]}})
            if ok:
                refs.append(lid)
        tips = {e["venue"]: e for e in items if e.get("tip") and e.get("tip_url")}
        persist_opinions([{"place_name": v, "place_category": "club", "summary": e["tip"], "tags": ["door policy", "club night"], "stance": "tip",
                           "sentiment": 0.5, "source_url": e["tip_url"], "source_title": "", "ad_like": False} for v, e in tips.items()], self.city, "nightlife")
        self.stats["events_in_dates"] = len(items)
        self.stats["dated_listings"] = sum(1 for e in items if e["kind"] != "club_night")
        return refs
