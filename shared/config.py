"""Environment loading and mandatory Atlas connection, with secret-safe errors."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
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
            raise RuntimeError("MONGODB_URI missing/unreachable — see MASTERPLAN §1")
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
            raise RuntimeError("MONGODB_URI missing/unreachable — see MASTERPLAN §1") from None
        return client, client[self.get("MONGODB_DB")]
