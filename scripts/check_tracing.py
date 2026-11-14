"""Verify authenticated LangSmith access and persisted Track 1 graph traces."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langsmith import Client

from shared.config import Settings

if __name__ == "__main__":
    settings = Settings.load()
    if not settings.get("LANGSMITH_API_KEY"):
        raise SystemExit("FAIL LANGSMITH_API_KEY is missing")
    try:
        client = Client(api_key=settings.get("LANGSMITH_API_KEY"))
        projects = list(
            client.list_projects(name=settings.get("LANGSMITH_PROJECT", "wander"), limit=1)
        )
        if not projects:
            raise SystemExit(
                "FAIL no wander project yet; run a new simulation with tracing enabled"
            )

        async def first_run():
            async for run in client.runs.query(
                project_ids=[str(projects[0].id)], filter='has(tags, "track:1")', page_size=1
            ):
                return run
            return None

        run = asyncio.run(first_run())
        if run is None:
            raise SystemExit("FAIL no persisted track:1 trace yet")
        print("OK authenticated LangSmith access")
        print("OK persisted Track 1 graph trace: " + str(run.id))
    except Exception as exc:  # noqa: BLE001 - Provider exceptions may contain sensitive details.
        print("FAIL LangSmith verification (" + type(exc).__name__ + ")")
        raise SystemExit(1) from None
