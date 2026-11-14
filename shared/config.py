"""Env loading + mandatory Atlas connection (MASTERPLAN §1.1), with secret-safe errors.

Two call styles, same .env:
- module constants + require_mongo()  (Track 2: `from shared import config`)
- Settings.load().connect()           (Track 1: `from shared.config import Settings`)
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

FAIL_MSG = "MONGODB_URI missing/unreachable — see MASTERPLAN §1"
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


def require_mongo() -> None:
    """Exit with a clear message if Mongo is not configured or reachable. No fallback by design."""
    if not MONGODB_URI:
        sys.exit(FAIL_MSG)
    try:
        MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000).admin.command("ping")
    except Exception as e:
        sys.exit(f"{FAIL_MSG} ({type(e).__name__})")


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
