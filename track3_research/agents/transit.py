"""R-07 TransitFinder: local transit passes + day trips by train (national rail / official operator links)."""
from __future__ import annotations

from urllib.parse import quote, urlparse

from pydantic import BaseModel, Field

from shared.agent_protocol import Hit, Query, ResearchAgent, link_place
from shared.llm import structured
from track3_research.link_resolver import save_link

from .common import persist_opinions, results_block


class Pass(BaseModel):
    name: str
    summary: str = Field(description="<=30 words: what it covers, price, whether worth it")
    price_eur: float | None
    official_url: str | None = Field(description="official operator URL if it appears in the results, else null")
    tags: list[str]
    source_index: int


class DayTrip(BaseModel):
    destination: str = Field(description="town, e.g. Sintra")
    highlight: str = Field(description="main sight to visit there, e.g. Pena Palace")
    mode: str = Field(description="train|bus|ferry")
    from_station: str
    duration_min: int
    operator: str
    operator_url: str = Field(description="official operator homepage, e.g. https://www.cp.pt")
    summary: str = Field(description="<=30 words practical tip from travelers")
    tags: list[str]
    source_index: int


class TransitFacts(BaseModel):
    passes: list[Pass] = Field(description="max 2")
    day_trips: list[DayTrip] = Field(description="max 3, best first")


class TransitFinder(ResearchAgent):
    kind = "transit"

    def plan(self) -> list[Query]:
        c = self.city["name"]
        return [
            Query(f"{c} public transport card pass worth it tourists reddit", include_domains=["reddit.com"], k=5),
            Query(f"{c} metro tram bus ticket card official prices", k=5),
            Query(f"best day trips from {c} by train reddit", include_domains=["reddit.com"], k=5),
            Query(f"day trip from {c} by train how to get there station tickets", k=5),
        ]

    def extract(self, hits: list[Hit]) -> list[dict]:
        block, flat = results_block(hits)
        self._flat = flat
        facts = structured(TransitFacts, "Extract transit passes and train/bus day trips that travelers recommend. Use only facts in the results "
                           "plus well-known official operator homepages.", f"City: {self.city['name']}\n\nResults:\n{block}", tier="fast")
        self._facts = facts
        return []

    def persist(self, items: list[dict]) -> list[str]:
        refs: list[str] = []
        flat, f = self._flat, self._facts
        ops = []
        for ps in f.passes[:2]:
            if not (0 <= ps.source_index < len(flat)):
                continue
            url = ps.official_url if ps.official_url and ps.official_url.startswith("http") else None
            if url:
                lid, ok = save_link(trip_id=self.trip_id, item_ref=f"transit_pass:{self.city['key']}", kind="train", provider=urlparse(url).netloc,
                                    url=url, label=f"{ps.name} (local transit pass)", is_official=True,
                                    price_hint={"amount": ps.price_eur, "currency": "EUR", "as_of": None} if ps.price_eur else None,
                                    extra={"transit_pass": True, "summary": ps.summary, "source_url": flat[ps.source_index].url})
                if ok:
                    refs.append(lid)
            ops.append({"place_name": None, "summary": f"{ps.name}: {ps.summary}", "tags": ps.tags + ["transit pass"], "stance": "tip",
                        "sentiment": 0.4, "source_url": flat[ps.source_index].url, "source_title": flat[ps.source_index].title, "ad_like": False})
        for dt in f.day_trips[:3]:
            if not (0 <= dt.source_index < len(flat)):
                continue
            src = flat[dt.source_index].url
            dest_id = link_place(self.city, dt.highlight, "sight", extra={"day_trip": {"destination": dt.destination, "mode": dt.mode, "duration_min": dt.duration_min,
                                                                                         "from_station": dt.from_station, "operator": dt.operator}})
            if dest_id:
                self.db.places.update_one({"_id": dest_id}, {"$set": {"day_trip": {"destination": dt.destination, "mode": dt.mode, "duration_min": dt.duration_min,
                                                                                  "from_station": dt.from_station, "operator": dt.operator}}})
            for provider, url, official, label in (
                (dt.operator, dt.operator_url, True, f"{dt.operator}: {dt.from_station} → {dt.destination} ({dt.mode}, ~{dt.duration_min} min)"),
                ("rome2rio", f"https://www.rome2rio.com/map/{quote(self.city['name'])}/{quote(dt.destination)}", False, f"Routes {self.city['name']} → {dt.destination}"),
            ):
                if not url or not url.startswith("http"):
                    continue
                lid, ok = save_link(trip_id=self.trip_id, item_ref=f"place:{dest_id}" if dest_id else f"daytrip:{dt.destination}", kind="train" if dt.mode == "train" else "bus",
                                    provider=provider, url=url, label=label, is_official=official, extra={"day_trip": dt.destination, "source_url": src})
                if ok:
                    refs.append(lid)
            ops.append({"place_name": dt.highlight, "place_category": "sight", "summary": f"Day trip to {dt.destination}: {dt.summary}",
                        "tags": dt.tags + ["day trip"], "stance": "tip", "sentiment": 0.6, "source_url": src, "source_title": flat[dt.source_index].title, "ad_like": False})
        persist_opinions(ops, self.city, "transit")
        self.stats["day_trips"] = [d.destination for d in f.day_trips]
        self.stats["passes"] = [p.name for p in f.passes]
        return refs
