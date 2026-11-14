"""R-07 Getting there: origin → destination flight/train search links (or the user's booked leg), arrival airport, transfer tips."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from shared.agent_protocol import Hit, Query, ResearchAgent, link_place
from shared.llm import structured
from track3_research.link_resolver import save_link

from .common import persist_opinions, results_block


class Airports(BaseModel):
    origin_city_code: str = Field(description="IATA metro/city code for the origin, e.g. LON")
    origin_airport: str = Field(description="main IATA airport code at origin, e.g. LHR")
    dest_airport: str = Field(description="main IATA airport code at destination, e.g. LIS")
    dest_airport_name: str = Field(description="full airport name, e.g. Humberto Delgado Airport (Lisbon Airport)")
    rail_reasonable: bool = Field(description="true if a train/bus under ~10h is a realistic alternative")
    dest_timezone: str = Field(description="IANA timezone of the destination, e.g. Europe/Lisbon")
    transfer_minutes: int = Field(description="typical airport → city-center transfer time by public transport, minutes")


class TransferTip(BaseModel):
    mode: str
    summary: str = Field(description="<=30 words: how, duration, cost, gotchas")
    tags: list[str]
    source_index: int


class TransferTips(BaseModel):
    tips: list[TransferTip]


def _d(v) -> date | None:
    s = str(v or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


class GettingThereFinder(ResearchAgent):
    kind = "getting_there"

    def plan(self) -> list[Query]:
        o = self.params.get("origin")  # trip.origin dict {name, geo} or a bare city string
        origin = (o.get("name") if isinstance(o, dict) else o) or "London"
        self._airports = structured(Airports, "Resolve airports for a trip. Use real IATA codes.",
                                    f"Origin: {origin}\nDestination: {self.city['name']}, {self.city.get('country') or ''}", tier="fast")
        a = self._airports
        return [
            Query(f"{a.dest_airport_name} to city center metro taxi uber tips reddit", include_domains=["reddit.com"], k=5),
            Query(f"{self.city['name']} airport to city center best way", k=5),
        ]

    def extract(self, hits: list[Hit]) -> list[dict]:
        block, flat = results_block(hits)
        self._flat = flat
        if not flat:
            return []
        tips = structured(TransferTips, "Extract concrete airport-to-city transfer options travelers describe (metro, bus, taxi, ride-hail). Max 4.",
                          f"Airport: {self._airports.dest_airport_name}\n\nResults:\n{block}", tier="fast")
        return [t.model_dump() for t in tips.tips if 0 <= t.source_index < len(flat)]

    def persist(self, items: list[dict]) -> list[str]:
        a = self._airports
        p = self.params
        o = p.get("origin")
        origin = (o.get("name") if isinstance(o, dict) else o) or "London"
        dates = p.get("dates") or {}
        start, end = _d(dates.get("start")), _d(dates.get("end"))
        adults = int(p.get("travelers") or 2)
        refs: list[str] = []
        airport_id = link_place(self.city, a.dest_airport_name, "transit_hub", extra={"iata": a.dest_airport})
        self.stats["airport_place_id"] = airport_id

        def add(**kw):
            lid, ok = save_link(trip_id=self.trip_id, party_size=adults, **kw)
            if ok:
                refs.append(lid)

        legs = [leg for leg in (p.get("legs") or []) if leg.get("kind") in ("flight", "train", "bus")]
        for leg in legs:
            carrier = (leg.get("carrier") or "").upper()
            number = re.sub(r"^[A-Z]{2}", "", str(leg.get("number") or "").upper()) if carrier else str(leg.get("number") or "")
            code = f"{carrier}{number}"
            dep = str(leg.get("dep_at") or "")[:10]
            add(item_ref=f"leg:{code}", kind="flight" if leg["kind"] == "flight" else leg["kind"], provider="flightaware",
                url=f"https://www.flightaware.com/live/flight/{code}", label=f"Track {code} {leg.get('from', '')}→{leg.get('to', '')} (booked)",
                for_date=dep or None, extra={"leg": {**leg, "code": code}, "booked": True})
        has_outbound = any(str(leg.get("to", "")).upper() == a.dest_airport for leg in legs)
        if start and end:
            o, d = a.origin_city_code.lower(), a.dest_airport.lower()
            if not has_outbound:
                q = f"Flights from {a.origin_city_code} to {a.dest_airport} on {start} through {end}"
                add(item_ref="getting_there:outbound", kind="flight", provider="google_flights",
                    url="https://www.google.com/travel/flights?" + urlencode({"q": q}), label=f"Flights {origin} → {self.city['name']} ({start:%b %d}, round trip)",
                    for_date=start.isoformat())
                add(item_ref="getting_there:outbound", kind="flight", provider="skyscanner",
                    url=f"https://www.skyscanner.net/transport/flights/{o}/{d}/{start:%y%m%d}/{end:%y%m%d}/?adultsv2={adults}",
                    label=f"Compare on Skyscanner ({start:%b %d}–{end:%b %d})", for_date=start.isoformat())
            q = f"One way flights from {a.dest_airport} to {a.origin_city_code} on {end}"
            add(item_ref="getting_there:return", kind="flight", provider="google_flights",
                url="https://www.google.com/travel/flights?" + urlencode({"q": q}), label=f"Return {self.city['name']} → {origin} ({end:%b %d})",
                for_date=end.isoformat())
        if a.rail_reasonable:
            add(item_ref="getting_there:rail", kind="train", provider="rome2rio",
                url=f"https://www.rome2rio.com/map/{quote(origin)}/{quote(self.city['name'])}", label=f"Train/bus options {origin} → {self.city['name']}")
        persist_opinions([{**t, "place_name": a.dest_airport_name, "place_category": "transit_hub", "stance": "tip", "sentiment": 0.3,
                           "source_url": self._flat[t["source_index"]].url, "source_title": self._flat[t["source_index"]].title,
                           "tags": t["tags"] + ["airport transfer"], "ad_like": False} for t in items], self.city, "airport transfer")
        self.stats["airports"] = a.model_dump()
        return refs
