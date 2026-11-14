"""Env + MongoDB fail-fast (MASTERPLAN §1.1). Every process imports this first."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

MONGO_FAIL_MSG = "MONGODB_URI missing/unreachable — see MASTERPLAN §1"


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = field(default_factory=lambda: _env("MONGODB_URI"))
    mongodb_db: str = field(default_factory=lambda: _env("MONGODB_DB", "wander"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model_fast: str = field(default_factory=lambda: _env("LLM_MODEL_FAST"))
    llm_model_smart: str = field(default_factory=lambda: _env("LLM_MODEL_SMART"))
    voyage_api_key: str = field(default_factory=lambda: _env("VOYAGE_API_KEY"))
    voyage_embed_model: str = field(default_factory=lambda: _env("VOYAGE_EMBED_MODEL", "voyage-3.5"))
    voyage_rerank_model: str = field(default_factory=lambda: _env("VOYAGE_RERANK_MODEL", "rerank-2.5"))
    wander_mode: str = field(default_factory=lambda: _env("WANDER_MODE", "fixtures"))
    search_provider: str = field(default_factory=lambda: _env("SEARCH_PROVIDER", "tavily").lower())
    search_api_key: str = field(default_factory=lambda: _env("SEARCH_API_KEY"))
    places_provider: str = field(default_factory=lambda: _env("PLACES_PROVIDER", "osm").lower())
    places_api_key: str = field(default_factory=lambda: _env("PLACES_API_KEY"))
    ticketmaster_api_key: str = field(default_factory=lambda: _env("TICKETMASTER_API_KEY"))
    langsmith_project: str = field(default_factory=lambda: _env("LANGSMITH_PROJECT", "wander"))
    research_timeout_s: int = field(default_factory=lambda: int(_env("RESEARCH_TIMEOUT_S", "90")))

    embed_dims: int = 1024


settings = Settings()

if not os.environ.get("LANGSMITH_API_KEY"):
    os.environ["LANGSMITH_TRACING"] = "false"


@lru_cache(maxsize=1)
def get_client():
    from pymongo import MongoClient

    if not settings.mongodb_uri:
        sys.exit(MONGO_FAIL_MSG)
    try:
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True, appname="wander")
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"{MONGO_FAIL_MSG} ({type(exc).__name__})")
    return client


def get_db():
    return get_client()[settings.mongodb_db]


def require(*names: str) -> None:
    missing = [n for n in names if not _env(n)]
    if missing:
        sys.exit(f"Missing required env: {', '.join(missing)} — see MASTERPLAN §1.2")
