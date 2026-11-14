"""Env loading + mandatory Atlas connection (MASTERPLAN §1.1), with secret-safe errors.

One .env, three call styles (one per track, all supported):
- Track 2: module constants + require_mongo()        `from shared import config`
- Track 1: Settings.load().connect()                  `from shared.config import Settings`
- Track 3: settings (attribute object) + get_db()     `from shared.config import settings, get_db`
"""
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

FAIL_MSG = "MONGODB_URI missing/unreachable — see MASTERPLAN §1"
MONGO_FAIL_MSG = FAIL_MSG
REQUIRED = (
    "MONGODB_URI",
    "MONGODB_DB",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL_FAST",
    "LLM_MODEL_SMART",
    "VOYAGE_API_KEY",
    "WANDER_MODE",
)

if not os.environ.get("LANGSMITH_API_KEY"):
    os.environ["LANGSMITH_TRACING"] = "false"  # no key → don't try to trace


def env(name: str, default: str | None = None) -> str:
    return os.environ.get(name, default if default is not None else "")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# ---------------------------------------------------------------- Track 2: module constants
MONGODB_URI = env("MONGODB_URI")
MONGODB_DB = env("MONGODB_DB", "wander")
WANDER_MODE = env("WANDER_MODE", "fixtures")

LLM_BASE_URL = env("LLM_BASE_URL") or None
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL_FAST = env("LLM_MODEL_FAST")
LLM_MODEL_SMART = env("LLM_MODEL_SMART")

VOYAGE_API_KEY = env("VOYAGE_API_KEY")
VOYAGE_EMBED_MODEL = env("VOYAGE_EMBED_MODEL", "voyage-3.5")
VOYAGE_RERANK_MODEL = env("VOYAGE_RERANK_MODEL", "rerank-2.5")
EMBED_DIMS = 1024

ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_AGENT_ID = env("ELEVENLABS_AGENT_ID")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID")

WEB_ORIGIN = env("WEB_ORIGIN", "http://localhost:3000")
DAILY_LLM_QUOTA = int(env("WANDER_DAILY_LLM_QUOTA", "300"))


def require_mongo() -> None:
    """Exit with a clear message if Mongo is not configured or reachable. No fallback by design."""
    if not MONGODB_URI:
        sys.exit(FAIL_MSG)
    try:
        MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000).admin.command("ping")
    except Exception as e:
        sys.exit(f"{FAIL_MSG} ({type(e).__name__})")


# ---------------------------------------------------------------- Track 1: Settings.load().connect()
@dataclass(frozen=True)
class Settings:
    values: dict[str, str]

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        return cls(dict(os.environ))

    def get(self, key, default=""):
        return self.values.get(key, default).strip()

    @property
    def mode(self):
        return self.get("WANDER_MODE")

    def validate(self):
        missing = [key for key in REQUIRED if not self.get(key)]
        if "MONGODB_URI" in missing:
            raise RuntimeError(FAIL_MSG)
        if missing:
            raise RuntimeError("Missing environment variables: " + ", ".join(missing))
        if not self.get("MONGODB_URI").startswith("mongodb+srv://"):
            raise RuntimeError("MONGODB_URI must identify MongoDB Atlas (mongodb+srv://)")
        if self.mode not in {"fixtures", "seeded", "live"}:
            raise RuntimeError("WANDER_MODE must be fixtures, seeded, or live")

    def connect(self):
        self.validate()
        client = None
        try:
            client = MongoClient(
                self.get("MONGODB_URI"),
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                tz_aware=True,
            )
            client.admin.command("ping")
        except Exception:
            if client is not None:
                client.close()
            raise RuntimeError(FAIL_MSG) from None
        return client, client[self.get("MONGODB_DB")]


# ---------------------------------------------------------------- Track 3: settings + get_db()
@dataclass(frozen=True)
class AppSettings:
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


settings = AppSettings()


@lru_cache(maxsize=1)
def get_client():
    if not settings.mongodb_uri:
        sys.exit(FAIL_MSG)
    try:
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000, tz_aware=True, appname="wander")
        client.admin.command("ping")
    except Exception as exc:
        sys.exit(f"{FAIL_MSG} ({type(exc).__name__})")
    return client


def get_db():
    return get_client()[settings.mongodb_db]


def require(*names: str) -> None:
    missing = [n for n in names if not _env(n)]
    if missing:
        sys.exit(f"Missing required env: {', '.join(missing)} — see MASTERPLAN §1.2")
