"""link_resolve jobs: (re)validate a trip's links; broken ones are flagged so they never reach a board."""
from __future__ import annotations

from shared.agent_protocol import Hit, Query, ResearchAgent, now
from track3_research.link_resolver import _check


class LinkResolveAgent(ResearchAgent):
    kind = "link_resolve"

    def plan(self) -> list[Query]:
        return []

    def extract(self, hits: list[Hit]) -> list[dict]:
        flt = {"trip_id": self.trip_id} if self.trip_id else {}
        if self.params.get("link_ids"):
            flt = {"_id": {"$in": self.params["link_ids"]}}
        return list(self.db.links.find(flt, {"url": 1}))

    def persist(self, items: list[dict]) -> list[str]:
        def one(link):
            v = _check(link["url"])
            self.db.links.update_one({"_id": link["_id"]}, {"$set": {"validation": {"status": v["status"], "checked_at": now(), "ok": v["ok"]}}})
            return link["_id"] if v["ok"] else None

        ok = [x for x in self.parallel(one, items, workers=8) if x]
        self.stats["ok"], self.stats["broken"] = len(ok), len(items) - len(ok)
        return ok
