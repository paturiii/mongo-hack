"""Opt-in functional probes for configured LLM, Voyage, search, email and voice."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ConfigDict

from shared.config import Settings
from shared.http import CachedHTTP
from shared.search_client import news_search as search
from shared.structured import structured


class Probe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


def main():
    settings = Settings.load()
    client, db = settings.connect()
    failures = []

    def run(name, call):
        try:
            assert call()
            print("OK " + name)
        except Exception as exc:
            failures.append(name)
            print("FAIL " + name + " (" + type(exc).__name__ + ")")

    try:
        run(
            "LLM structured output with configured FAST model",
            lambda: structured(db, settings, Probe, "Configuration test: return ok=true.").ok,
        )
        http = CachedHTTP(db)
        run(
            "Voyage embeddings",
            lambda: http.request(
                "POST",
                "https://api.voyageai.com/v1/embeddings",
                headers={"Authorization": "Bearer " + settings.get("VOYAGE_API_KEY")},
                json_body={
                    "model": settings.get("VOYAGE_EMBED_MODEL"),
                    "input": ["Wander service verification"],
                    "input_type": "document",
                },
            )["data"][0]["embedding"],
        )
        run(
            "Voyage reranking",
            lambda: http.request(
                "POST",
                "https://api.voyageai.com/v1/rerank",
                headers={"Authorization": "Bearer " + settings.get("VOYAGE_API_KEY")},
                json_body={
                    "model": settings.get("VOYAGE_RERANK_MODEL"),
                    "query": "indoor museum",
                    "documents": ["Lisbon Story Centre indoor museum", "Outdoor walking tour"],
                    "top_k": 1,
                },
            )["data"],
        )
        run("Search provider", lambda: search(db, settings, "Lisbon transport service updates"))
        run(
            "Real Resend alert accepted",
            lambda: (
                db.alerts.count_documents(
                    {
                        "channel": "email",
                        "delivery_mode": "live",
                        "status": "sent",
                        "provider_id": {"$exists": True},
                    }
                )
                > 0
            ),
        )
        run(
            "Real ElevenLabs briefing saved",
            lambda: (
                db.alerts.count_documents(
                    {
                        "channel": "voice",
                        "delivery_mode": "live",
                        "status": "sent",
                        "audio_url": {"$exists": True},
                    }
                )
                > 0
            ),
        )
        if not settings.get("LANGSMITH_API_KEY"):
            print("WARN LangSmith tracing is disabled: LANGSMITH_API_KEY is not configured")
        return 1 if failures else 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
