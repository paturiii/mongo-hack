"""R-08 Tickets: official site first, then Tiqets / GetYourGuide. `is_official` badge; "book ahead" from traveler tips."""
from __future__ import annotations

from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, Field

from shared.agent_protocol import Hit, Query, ResearchAgent, link_place
from shared.llm import structured
from shared.search_client import search
from track3_research.link_resolver import save_link

from .common import FORUM_DOMAINS, persist_opinions, results_block

RESELLERS = ("tiqets.", "getyourguide.", "viator.", "klook.", "headout.", "musement.", "tripadvisor.", "civitatis.", "ticketbar.", "tickets-",
             "booking.com", "expedia.", "isango.", "gocity.", "lisbon-tickets", "tourscanner.", "toursbylocals.")


class Monument(BaseModel):
    name: str = Field(description="official name, e.g. 'Jerónimos Monastery'")
    category: str = Field(description="sight|museum")
    book_ahead: bool
    tip: str = Field(description="<=25 words practical tip (timing, queues, combo tickets)")
    tags: list[str]
    source_index: int


class Monuments(BaseModel):
    monuments: list[Monument] = Field(description="8 must-see ticketed sights, best first")


class OfficialPick(BaseModel):
    official_index: int | None = Field(description="index of the result that is the OFFICIAL site of the monument/operator, or null")
    ticket_index: int | None = Field(description="index of the official ticket purchase page if distinct, or null")


class TicketFinder(ResearchAgent):
    kind = "tickets"

    def plan(self) -> list[Query]:
        c = self.city["name"]
        return [
            Query(f"{c} must see monuments book tickets in advance reddit", include_domains=["reddit.com"], k=6),
            Query(f"{c} top sights skip the line tickets worth it", include_domains=FORUM_DOMAINS, k=5),
            Query(f"{c} best museums and monuments tips queues", k=5),
        ]

    def extract(self, hits: list[Hit]) -> list[dict]:
        block, flat = results_block(hits)
        self._flat = flat
        must = ", ".join(self.params.get("must_sees") or [])
        res = structured(Monuments, "List the ticketed monuments/museums travelers say are worth it, with booking tips from the results. "
                         "Include famous day-trip palaces if travelers mention them.", f"City: {self.city['name']}\nTraveler must-sees: {must or 'none'}\n\nResults:\n{block}", tier="fast")
        return [m.model_dump() for m in res.monuments[:8] if 0 <= m.source_index < len(flat)]

    def link(self, items: list[dict]) -> list[dict]:
        def resolve(m: dict) -> dict:
            m["place_id"] = link_place(self.city, m["name"], "museum" if m["category"] == "museum" else "sight")
            for q in (f"{m['name']} {self.city['name']} official website tickets", f"{m['name']} official site opening hours"):
                results = [r for r in search(q, k=6) if not any(x in urlparse(r.url).netloc for x in RESELLERS)]
                if not results:
                    continue
                listing = "\n".join(f"[{i}] {r.title} — {r.url}" for i, r in enumerate(results))
                pick = structured(OfficialPick, "Identify the official website (government, heritage body, foundation, or the venue itself) — never resellers, blogs or tour sites.",
                                  f"Monument: {m['name']} ({self.city['name']})\n{listing}", tier="fast")
                for key in ("ticket_index", "official_index"):
                    i = getattr(pick, key)
                    if i is not None and 0 <= i < len(results):
                        m.setdefault("official_url", results[i].url)
                if m.get("official_url"):
                    break
            return m

        return self.parallel(resolve, items, workers=4)

    def persist(self, items: list[dict]) -> list[str]:
        refs: list[str] = []
        ops = []
        for m in items:
            ref = f"place:{m['place_id']}" if m.get("place_id") else f"monument:{m['name']}"
            src = self._flat[m["source_index"]]
            extra = {"book_ahead": m["book_ahead"], "tip": m["tip"], "source_url": src.url}
            official_ok = False
            if m.get("official_url"):
                lid, official_ok = save_link(trip_id=self.trip_id, item_ref=ref, kind="monument_ticket", provider=urlparse(m["official_url"]).netloc.removeprefix("www."),
                                             url=m["official_url"], label=f"{m['name']} — official tickets", is_official=True, extra=extra)
                if official_ok:
                    refs.append(lid)
            if not official_ok:
                for provider, url in (("tiqets", f"https://www.tiqets.com/en/search?q={quote_plus(m['name'])}"),
                                      ("getyourguide", f"https://www.getyourguide.com/s/?q={quote_plus(m['name'] + ' ' + self.city['name'])}")):
                    lid, ok = save_link(trip_id=self.trip_id, item_ref=ref, kind="monument_ticket", provider=provider, url=url,
                                        label=f"{m['name']} tickets ({provider})", is_official=False, extra=extra)
                    if ok:
                        refs.append(lid)
                        break
            ops.append({"place_name": m["name"], "place_category": "museum" if m["category"] == "museum" else "sight", "summary": m["tip"],
                        "tags": m["tags"] + (["book ahead"] if m["book_ahead"] else []), "stance": "tip", "sentiment": 0.5,
                        "source_url": src.url, "source_title": src.title, "ad_like": False})
        persist_opinions(ops, self.city, "tickets")
        self.stats["official"] = sum(1 for m in items if m.get("official_url"))
        self.stats["monuments"] = [m["name"] for m in items]
        return refs
