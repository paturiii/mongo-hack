from shared.http import CachedHTTP


def search(db, settings, query):
    http = CachedHTTP(db)
    provider, key = settings.get("SEARCH_PROVIDER", "tavily"), settings.get("SEARCH_API_KEY")
    if provider == "tavily":
        result = http.request(
            "POST",
            "https://api.tavily.com/search",
            json_body={
                "api_key": key,
                "query": query,
                "topic": "news",
                "days": 3,
                "max_results": 5,
            },
        )
        return [
            {"url": row["url"], "summary": row.get("content", "")[:2000]}
            for row in result.get("results", [])
        ]
    if provider == "exa":
        from datetime import timedelta

        from shared.http import now

        result = http.request(
            "POST",
            "https://api.exa.ai/search",
            headers={"x-api-key": key},
            json_body={
                "query": query,
                "numResults": 5,
                "startPublishedDate": (now() - timedelta(days=3)).isoformat(),
                "contents": {"summary": True},
            },
        )
        return [
            {"url": row["url"], "summary": row.get("summary", "")[:2000]}
            for row in result.get("results", [])
        ]
    if provider == "brave":
        result = http.request(
            "GET",
            "https://api.search.brave.com/res/v1/news/search",
            headers={"X-Subscription-Token": key},
            params={"q": query, "count": 5, "freshness": "pw"},
        )
        return [
            {"url": row["url"], "summary": row.get("description", "")[:2000]}
            for row in result.get("results", [])
        ]
    raise ValueError("Unsupported SEARCH_PROVIDER")
