"""Env loading + MongoDB fail-fast. Every process imports this first (MASTERPLAN §1.1)."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

FAIL_MSG = "MONGODB_URI missing/unreachable — see MASTERPLAN §1"


def env(name: str, default: str | None = None) -> str:
    return os.environ.get(name, default if default is not None else "")


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

REQUIRED = ["MONGODB_URI", "MONGODB_DB", "LLM_API_KEY", "LLM_MODEL_FAST", "LLM_MODEL_SMART",
            "VOYAGE_API_KEY", "WANDER_MODE"]


def require_mongo() -> None:
    """Exit with a clear message if Mongo is not configured or reachable. No fallback by design."""
    if not MONGODB_URI:
        sys.exit(FAIL_MSG)
    from pymongo import MongoClient
    try:
        MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000).admin.command("ping")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"{FAIL_MSG} ({type(e).__name__})")
