"""Web search over Tavily / Exa / Brave, cached in search_cache. Swap provider via SEARCH_PROVIDER.

- search(query, include_domains=..., ...) -> list[SearchResult]   general web search (Track 3 research agents)
- news_search(db, settings, query) -> [{url, summary}]            last-3-days news (Track 1 disruption monitors)
"""
from dataclasses import asdict, dataclass
from datetime import timedelta
from urllib.parse import urlparse

from shared import http
from shared.config import settings as app_settings
from shared.http import CachedHTTP


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
        "api_key": app_settings.search_api_key,
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
        {"url": r["url"], "title": r.get("title") or "", "content": r.get("content") or "",
         "published_date": r.get("published_date"), "score": r.get("score", 0)}
        for r in data.get("results", [])
    ]


def _exa(query, include_domains, exclude_domains, recency_days, k, topic) -> list[dict]:
    body = {"query": query, "numResults": k, "contents": {"highlights": {"numSentences": 4}}}
    if include_domains:
        body["includeDomains"] = include_domains
    if exclude_domains:
        body["excludeDomains"] = exclude_domains
    data = http.post_json("https://api.exa.ai/search", json=body, headers={"x-api-key": app_settings.search_api_key},
                          timeout=45)
    return [
        {"url": r["url"], "title": r.get("title") or "", "content": " ".join(r.get("highlights") or [])[:2000],
         "published_date": r.get("publishedDate"), "score": r.get("score") or 0}
        for r in data.get("results", [])
    ]


def _brave(query, include_domains, exclude_domains, recency_days, k, topic) -> list[dict]:
    q = query
    if include_domains:
        q += " (" + " OR ".join(f"site:{d}" for d in include_domains) + ")"
    params = {"q": q, "count": k}
    if recency_days:
        params["freshness"] = "pm" if recency_days <= 31 else "py"
    data = http.get_json("https://api.search.brave.com/res/v1/web/search", params=params,
                         headers={"X-Subscription-Token": app_settings.search_api_key})
    return [
        {"url": r["url"], "title": r.get("title") or "", "content": r.get("description") or "",
         "published_date": r.get("age"), "score": 0}
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
    payload = {"p": app_settings.search_provider, "q": query, "inc": include_domains, "exc": exclude_domains,
               "days": recency_days, "k": k, "topic": topic}

    def live() -> list[dict]:
        if not app_settings.search_api_key:
            return []
        return _PROVIDERS[app_settings.search_provider](query, include_domains, exclude_domains, recency_days, k, topic)

    ttl = timedelta(days=3) if topic == "news" else timedelta(days=7)
    rows = http.cached("search", payload, live, ttl=ttl) or []
    return [SearchResult(**r) for r in rows]


def as_dicts(results: list[SearchResult]) -> list[dict]:
    return [asdict(r) for r in results]


def news_search(db, settings, query):
    """Recent news (3 days) for disruption monitors. `settings` is Track 1's Settings (.get)."""
    cached_http = CachedHTTP(db)
    provider, key = settings.get("SEARCH_PROVIDER", "tavily"), settings.get("SEARCH_API_KEY")
    if provider == "tavily":
        result = cached_http.request(
            "POST",
            "https://api.tavily.com/search",
            json_body={"api_key": key, "query": query, "topic": "news", "days": 3, "max_results": 5},
        )
        return [{"url": row["url"], "summary": row.get("content", "")[:2000]} for row in result.get("results", [])]
    if provider == "exa":
        result = cached_http.request(
            "POST",
            "https://api.exa.ai/search",
            headers={"x-api-key": key},
            json_body={
                "query": query,
                "numResults": 5,
                "startPublishedDate": (http.now() - timedelta(days=3)).isoformat(),
                "contents": {"summary": True},
            },
        )
        return [{"url": row["url"], "summary": row.get("summary", "")[:2000]} for row in result.get("results", [])]
    if provider == "brave":
        result = cached_http.request(
            "GET",
            "https://api.search.brave.com/res/v1/news/search",
            headers={"X-Subscription-Token": key},
            params={"q": query, "count": 5, "freshness": "pw"},
        )
        return [{"url": row["url"], "summary": row.get("description", "")[:2000]} for row in result.get("results", [])]
    raise ValueError("Unsupported SEARCH_PROVIDER")
