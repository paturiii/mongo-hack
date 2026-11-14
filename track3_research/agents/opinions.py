"""R-03 Opinion Scouts: one agent, configured per source_type (reddit, forum, blog, review, video, news)."""
from __future__ import annotations

from shared.agent_protocol import Hit, Query, ResearchAgent

from .common import SOURCE_CLASSES, THEME_PHRASES, extract_opinions, persist_opinions

DEFAULT_SOURCES = ["reddit", "forum", "blog", "review", "video", "news"]


class OpinionScout(ResearchAgent):
    kind = "opinions"

    def plan(self) -> list[Query]:
        theme = self.params.get("theme", "things to do")
        phrase = THEME_PHRASES.get(theme, theme)
        per_source = int(self.params.get("queries_per_source", 1))
        out = []
        for st in self.params.get("source_types") or DEFAULT_SOURCES:
            sc = SOURCE_CLASSES[st]
            for tpl in sc["tpl"][:per_source]:
                q = tpl.format(city=self.city["name"], city_slug=self.city["slug"].replace("-", ""), theme=phrase)
                out.append(Query(q, include_domains=sc.get("include"), exclude_domains=sc.get("exclude"),
                                 recency_days=sc.get("recency"), topic=sc.get("topic", "general"), k=int(self.params.get("k", 6)),
                                 meta={"source_type": st}))
        return out

    def extract(self, hits: list[Hit]) -> list[dict]:
        theme = self.params.get("theme", "things to do")

        def one(h: Hit) -> list[dict]:
            items = extract_opinions([h], self.city["name"], theme)
            st = h.query.meta["source_type"]
            for it in items:
                it["forced_type"] = "news" if st == "news" else None
            return items

        items = [x for batch in self.parallel(one, [h for h in hits if h.results]) for x in batch]
        self.stats["extracted"] = len(items)
        return items

    def persist(self, items: list[dict]) -> list[str]:
        theme = self.params.get("theme", "things to do")
        ttl = int(self.params.get("ttl_days", 30))
        refs: list[str] = []
        for forced in {it.get("forced_type") for it in items}:
            group = [it for it in items if it.get("forced_type") == forced]
            refs += persist_opinions(group, self.city, theme, ttl_days=ttl, forced_type=forced)
        return refs
