"""Web search over Tavily / Exa / Brave, cached in search_cache (TTL 7d). Swap provider via SEARCH_PROVIDER."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from urllib.parse import urlparse

from shared import http
from shared.config import settings


@dataclass
class SearchResult:
    url: str
    title: str
    content: str
    published_date: str | None = None
    score: float = 0.0

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")


def _tavily(query: str, include_domains, exclude_domains, recency_days, k, topic) -> list[dict]:
    body = {
        "api_key": settings.search_api_key,
        "query": query,
        "max_results": k,
        "search_depth": "advanced",
        "chunks_per_source": 3,
        "topic": topic,
    }
    if include_domains:
        body["include_domains"] = include_domains
    if exclude_domains:
        body["exclude_domains"] = exclude_domains
    if recency_days:
        body["days"] = recency_days
        if topic != "news":
            body["time_range"] = "year" if recency_days > 31 else "month"
            body.pop("days")
    data = http.post_json("https://api.tavily.com/search", json=body, timeout=45)
    return [
        {"url": r["url"], "title": r.get("title") or "", "content": r.get("content") or "", "published_date": r.get("published_date"), "score": r.get("score", 0)}
        for r in data.get("results", [])
    ]


def _exa(query, include_domains, exclude_domains, recency_days, k, topic) -> list[dict]:
    body = {"query": query, "numResults": k, "contents": {"highlights": {"numSentences": 4}}}
    if include_domains:
        body["includeDomains"] = include_domains
    if exclude_domains:
        body["excludeDomains"] = exclude_domains
    data = http.post_json("https://api.exa.ai/search", json=body, headers={"x-api-key": settings.search_api_key}, timeout=45)
    return [
        {"url": r["url"], "title": r.get("title") or "", "content": " ".join(r.get("highlights") or [])[:2000], "published_date": r.get("publishedDate"), "score": r.get("score") or 0}
        for r in data.get("results", [])
    ]


def _brave(query, include_domains, exclude_domains, recency_days, k, topic) -> list[dict]:
    q = query
    if include_domains:
        q += " (" + " OR ".join(f"site:{d}" for d in include_domains) + ")"
    params = {"q": q, "count": k}
    if recency_days:
        params["freshness"] = "pm" if recency_days <= 31 else "py"
    data = http.get_json("https://api.search.brave.com/res/v1/web/search", params=params, headers={"X-Subscription-Token": settings.search_api_key})
    return [
        {"url": r["url"], "title": r.get("title") or "", "content": r.get("description") or "", "published_date": r.get("age"), "score": 0}
        for r in data.get("web", {}).get("results", [])
    ]


_PROVIDERS = {"tavily": _tavily, "exa": _exa, "brave": _brave}


def search(
    query: str,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    recency_days: int | None = None,
    k: int = 6,
    topic: str = "general",
) -> list[SearchResult]:
    payload = {"p": settings.search_provider, "q": query, "inc": include_domains, "exc": exclude_domains, "days": recency_days, "k": k, "topic": topic}

    def live() -> list[dict]:
        if not settings.search_api_key:
            return []
        return _PROVIDERS[settings.search_provider](query, include_domains, exclude_domains, recency_days, k, topic)

    ttl = timedelta(days=3) if topic == "news" else timedelta(days=7)
    rows = http.cached("search", payload, live, ttl=ttl) or []
    return [SearchResult(**r) for r in rows]


def as_dicts(results: list[SearchResult]) -> list[dict]:
    return [asdict(r) for r in results]
