"""R-06 StayFinder: 3 neighborhoods matching the traveler's vibe (from traveler consensus) → Booking.com / Airbnb deep links."""
from __future__ import annotations

from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from shared import geo
from shared.agent_protocol import Hit, Query, ResearchAgent, norm_name
from shared.llm import structured
from track3_research.link_resolver import save_link

from .common import FORUM_DOMAINS, NON_BLOG, persist_opinions, results_block


class Area(BaseModel):
    neighborhood: str = Field(description="neighborhood name as locals write it")
    why: str = Field(description="<=30 words: why it fits THIS traveler, from the sources")
    tags: list[str]
    caveat: str | None = Field(description="main downside mentioned by travelers, or null")
    source_indices: list[int] = Field(description="indices of results supporting this pick (>=1)")


class AreaPicks(BaseModel):
    areas: list[Area] = Field(description="exactly 3, best first")


class StayFinder(ResearchAgent):
    kind = "stay"

    def plan(self) -> list[Query]:
        prof = self.params.get("profile") or {}
        vibe = " ".join((prof.get("vibe") or [])[:2]) or "first time"
        c = self.city["name"]
        return [
            Query(f"where to stay in {c} {vibe} neighborhood reddit", include_domains=["reddit.com"], k=6),
            Query(f"best area to stay in {c} first visit {vibe}", include_domains=FORUM_DOMAINS, k=5),
            Query(f"{c} best neighborhoods to stay for {vibe} travelers blog", exclude_domains=NON_BLOG, k=5),
        ]

    def extract(self, hits: list[Hit]) -> list[dict]:
        block, flat = results_block(hits)
        self._flat = flat
        prof = self.params.get("profile") or {}
        picks = structured(AreaPicks,
                           "Pick the 3 neighborhoods to stay in for this traveler, strictly based on the travelers' comments in the results. "
                           "Prefer central, walkable areas with good transit unless the traveler says otherwise.",
                           f"City: {self.city['name']}\nTraveler profile: {prof}\n\nResults:\n{block}", tier="smart")
        return [a.model_dump() for a in picks.areas[:3]]

    def link(self, items: list[dict]) -> list[dict]:
        hoods = list(self.db.neighborhoods.find({"city": self.city["key"]}, {"name": 1, "geo": 1}))
        for it in items:
            nn = norm_name(it["neighborhood"])
            match = next((h for h in hoods if norm_name(h["name"]) == nn or nn in norm_name(h["name"]) or norm_name(h["name"]) in nn), None)
            if match:
                it["geo"] = match["geo"]
            else:
                hit = geo.geocode(f"{it['neighborhood']}, {self.city['name']}", near=self.city["geo"], radius_km=25)
                it["geo"] = hit["geo"] if hit else None
        return [it for it in items if it.get("geo")]

    def persist(self, items: list[dict]) -> list[str]:
        dates = self.params.get("dates") or {}
        start, end = str(dates.get("start", ""))[:10], str(dates.get("end", ""))[:10]
        adults = int(self.params.get("travelers") or 2)
        refs = []
        ops_items = []
        for rank, it in enumerate(items):
            src_urls = [self._flat[i].url for i in it["source_indices"] if 0 <= i < len(self._flat)]
            if not src_urls:
                continue
            area_ref = f"area:{self.city['key']}:{norm_name(it['neighborhood'])}"
            meta = {"stay_area": {"name": it["neighborhood"], "geo": it["geo"], "why": it["why"], "tags": it["tags"], "caveat": it.get("caveat"),
                                  "rank": rank, "sources": src_urls[:3]}}
            ss = f"{it['neighborhood']}, {self.city['name']}"
            booking = "https://www.booking.com/searchresults.html?" + urlencode(
                {"ss": ss, "checkin": start, "checkout": end, "group_adults": adults, "no_rooms": 1, "group_children": 0})
            airbnb = f"https://www.airbnb.com/s/{quote(ss.replace(', ', '--').replace(' ', '-'))}/homes?" + urlencode(
                {"checkin": start, "checkout": end, "adults": adults})
            for provider, url, label in (("booking.com", booking, f"Stays in {it['neighborhood']} · Booking.com"),
                                         ("airbnb", airbnb, f"Homes in {it['neighborhood']} · Airbnb")):
                lid, ok = save_link(trip_id=self.trip_id, item_ref=area_ref, kind="stay", provider=provider, url=url, label=label,
                                    for_date=start or None, party_size=adults, extra=meta)
                if ok:
                    refs.append(lid)
            for u in src_urls[:2]:
                ops_items.append({"place_name": None, "summary": f"Stay in {it['neighborhood']}: {it['why']}", "tags": it["tags"][:4] + ["where to stay"],
                                  "stance": "tip", "sentiment": 0.5, "source_url": u, "source_title": "", "ad_like": False})
        persist_opinions(ops_items, self.city, "where to stay")
        self.stats["areas"] = [i["neighborhood"] for i in items]
        return refs
