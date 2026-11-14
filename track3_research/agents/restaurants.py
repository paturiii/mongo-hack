"""R-08 RestaurantFinder: consensus food picks → OpenTable / TheFork / Resy / official links; "walk-in only" otherwise."""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field

from shared.agent_protocol import Hit, Query, ResearchAgent, link_place, name_similarity, norm_name
from shared.llm import structured
from shared.search_client import search
from track3_research.link_resolver import save_link

from .common import FORUM_DOMAINS, NON_BLOG, persist_opinions, results_block

RESERVATION_DOMAINS = ("thefork.", "opentable.", "resy.", "sevenrooms.", "covermanager.", "quandoo.", "tablein.", "zomato.", "exploretock.", "umai.io")
RESERVATION_SITES = ["thefork.com", "thefork.pt", "opentable.com", "resy.com", "sevenrooms.com", "covermanager.com", "umai.io", "quandoo.com"]


class Restaurant(BaseModel):
    name: str
    neighborhood: str | None
    why: str = Field(description="<=25 words: what to order / why travelers love it")
    walk_in_only: bool = Field(description="true if travelers say no reservations are taken")
    price_band: str = Field(description="budget|mid|splurge")
    tags: list[str]
    source_index: int


class Restaurants(BaseModel):
    restaurants: list[Restaurant] = Field(description="10 picks, best first; match the traveler's budget and diet")


class BookingPick(BaseModel):
    reservation_index: int | None = Field(description="index of a reservation page for THIS restaurant (TheFork/OpenTable/Resy/SevenRooms/official booking), or null")
    official_index: int | None = Field(description="index of the restaurant's own official website, or null")


class RestaurantFinder(ResearchAgent):
    kind = "restaurants"

    def plan(self) -> list[Query]:
        prof = self.params.get("profile") or {}
        diet = " ".join(prof.get("dietary") or [])
        band = prof.get("budget_band") or ""
        c = self.city["name"]
        return [
            Query(f"{c} best restaurants {band} {diet} reservation reddit", include_domains=["reddit.com"], k=6),
            Query(f"{c} restaurant recommendations locals {diet}", include_domains=FORUM_DOMAINS, k=5),
            Query(f"{c} where to eat dinner {band} honest blog {diet}", exclude_domains=NON_BLOG, k=5),
        ]

    def extract(self, hits: list[Hit]) -> list[dict]:
        block, flat = results_block(hits)
        self._flat = flat
        prof = self.params.get("profile") or {}
        res = structured(Restaurants, "Pick restaurants that multiple travelers praise in the results. Real, currently operating places only.",
                         f"City: {self.city['name']}\nTraveler: budget={prof.get('budget_band')}, dietary={prof.get('dietary')}, interests={prof.get('interests')}\n\nResults:\n{block}",
                         tier="fast")
        picks = [{**r.model_dump(), "source_url": flat[r.source_index].url, "source_title": flat[r.source_index].title}
                 for r in res.restaurants[:10] if 0 <= r.source_index < len(flat)]
        have = {norm_name(p["name"]) for p in picks}
        for p in self.db.places.find({"city": self.city["key"], "category": "food", "source": {"$ne": "fixture"}, "consensus.mentions": {"$gte": 1},
                                      "consensus.score": {"$gt": 0.3}}, {"name": 1, "consensus": 1, "neighborhood": 1}).sort("consensus.score", -1).limit(20):
            if len(picks) >= 10:
                break
            chips = (p.get("consensus") or {}).get("chips") or []
            if norm_name(p["name"]) in have or not chips:
                continue
            have.add(norm_name(p["name"]))
            picks.append({"name": p["name"], "neighborhood": p.get("neighborhood"), "why": chips[0]["text"][:160], "walk_in_only": False,
                          "price_band": prof.get("budget_band") or "mid", "tags": (p["consensus"].get("tags") or [])[:3], "source_index": -1,
                          "source_url": chips[0]["url"], "source_title": "", "from_consensus": True})
        return picks

    def link(self, items: list[dict]) -> list[dict]:
        def resolve(r: dict) -> dict:
            r["place_id"] = link_place(self.city, r["name"], "food")
            platforms = search(f"{r['name']} {self.city['name']}", include_domains=RESERVATION_SITES, k=5)
            for x in platforms:
                if name_similarity(r["name"], x.title.split("|")[0].split(" - ")[0].split(" in ")[0]) >= 0.7 or norm_name(r["name"]) in norm_name(x.title):
                    r["reservation_url"] = x.url
                    break
            results = search(f"{r['name']} {self.city['name']} restaurant reservation", k=6)
            if not results:
                return r
            listing = "\n".join(f"[{i}] {x.title} — {x.url}" for i, x in enumerate(results))
            pick = structured(BookingPick, "Find where to reserve THIS exact restaurant. Ignore listicles and other restaurants.",
                              f"Restaurant: {r['name']} ({self.city['name']})\n{listing}", tier="fast")
            if pick.reservation_index is not None and 0 <= pick.reservation_index < len(results) and not r.get("reservation_url"):
                r["reservation_url"] = results[pick.reservation_index].url
            if pick.official_index is not None and 0 <= pick.official_index < len(results):
                r["official_url"] = results[pick.official_index].url
            return r

        return self.parallel(resolve, items, workers=4)

    def persist(self, items: list[dict]) -> list[str]:
        refs: list[str] = []
        ops = []
        adults = int(self.params.get("travelers") or 2)
        reservable = 0
        for r in items:
            ref = f"place:{r['place_id']}" if r.get("place_id") else f"restaurant:{r['name']}"
            got = False
            for url, official in ((r.get("reservation_url"), False), (r.get("official_url"), True)):
                if not url or got:
                    continue
                host = urlparse(url).netloc.removeprefix("www.")
                is_res = any(d in host for d in RESERVATION_DOMAINS)
                lid, ok = save_link(trip_id=self.trip_id, item_ref=ref, kind="restaurant", provider=host, url=url,
                                    label=f"Reserve {r['name']}" if (is_res or not official) else f"{r['name']} — official site",
                                    is_official=official, party_size=adults, extra={"walk_in_only": r["walk_in_only"], "source_url": r["source_url"]})
                if ok:
                    refs.append(lid)
                    got = True
                    reservable += 1
            tags = r["tags"] + (["walk-in only"] if r["walk_in_only"] or not got else ["book ahead"])
            ops.append({"place_name": r["name"], "place_category": "food", "summary": r["why"], "tags": tags, "stance": "pro", "sentiment": 0.7,
                        "source_url": r["source_url"], "source_title": r.get("source_title", ""), "ad_like": False})
            if r.get("place_id"):
                self.db.places.update_one({"_id": r["place_id"]}, {"$set": {"price_band": r["price_band"], "walk_in_only": r["walk_in_only"] or not got}})
        persist_opinions(ops, self.city, "restaurants")
        self.stats["reservable"] = reservable
        return refs
