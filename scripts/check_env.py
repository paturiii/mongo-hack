"""Check configuration without exposing credentials. Run from the repository root."""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from shared.config import REQUIRED, Settings


def provider_checks(settings):
    """Read-only authentication probes. Never print provider response bodies."""
    probes = [
        (
            "LLM",
            settings.get("LLM_BASE_URL").rstrip("/") + "/models",
            {"Authorization": "Bearer " + settings.get("LLM_API_KEY")},
        ),
        (
            "ElevenLabs",
            "https://api.elevenlabs.io/v2/voices?page_size=1",
            {"xi-api-key": settings.get("ELEVENLABS_API_KEY")},
        ),
        (
            "Resend",
            "https://api.resend.com/domains",
            {"Authorization": "Bearer " + settings.get("RESEND_API_KEY")},
        ),
    ]

    def probe(item):
        name, url, headers = item
        try:
            response = httpx.get(url, headers=headers, timeout=15)
            if (
                name == "Resend"
                and response.status_code == 401
                and "restricted to only send emails" in response.text
            ):
                return name, "send-only key recognized (domain listing not permitted)"
            return name, response.status_code
        except httpx.HTTPError:
            return name, "connection failed"

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, probes))
        for name, status in results:
            ok = status == 200 or str(status).startswith("send-only key recognized")
            print(f"{'OK' if ok else 'FAIL'} {name} read-only API probe: {status}")
        return all(
            status == 200 or str(status).startswith("send-only key recognized")
            for _, status in results
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", action="store_true")
    parser.add_argument("--config-only", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any Track 1 collection or validator is missing",
    )
    args = parser.parse_args()
    settings = Settings.load()
    missing = []
    for key in REQUIRED:
        value = settings.get(key)
        valid = bool(value) and "<" not in value and value.lower() not in {"changeme", "your_key"}
        print(f"{'OK' if valid else 'FAIL'} {key}: {'set' if valid else 'missing/placeholder'}")
        if not valid:
            missing.append(key)
    for key in (
        "SEARCH_API_KEY",
        "RESEND_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "RESEND_FROM_EMAIL",
        "DISRUPTION_TEST_EMAIL",
        "LANGSMITH_API_KEY",
        "LANGSMITH_TRACING",
        "LANGSMITH_PROJECT",
    ):
        print(
            f"{'OK' if settings.get(key) else 'WARN'} {key}: "
            f"{'set' if settings.get(key) else 'not configured'}"
        )
    print("OK flight monitoring: simulator (live flight credentials optional)")
    if missing:
        return 1
    if not args.config_only:
        try:
            client, db = settings.connect()
            print("OK Atlas ping")
            info = client.server_info()
            print("OK MongoDB version: " + info["version"])
            collections = {item["name"]: item for item in db.list_collections()}
            incomplete = False
            for name in (
                "watches",
                "signals",
                "disruptions",
                "alerts",
                "refine_requests",
                "price_snapshots",
                "places",
                "boards",
            ):
                item = collections.get(name)
                status = "OK" if item else "WARN"
                print(f"{status} collection {name}: {'exists' if item else 'not initialized'}")
                incomplete |= not bool(item)
                if item and name not in {"signals", "price_snapshots"}:
                    validator = item.get("options", {}).get("validator", {})
                    print(f"{'OK' if validator else 'WARN'} {name} schema validator")
                    incomplete |= not bool(validator)
            if "places" in collections:
                indexes = list(db.places.list_search_indexes())
                ready = [item for item in indexes if item.get("queryable")]
                print(
                    f"{'OK' if ready else 'WARN'} places Atlas search/vector indexes: {len(ready)} ready"
                )
            client.close()
            if args.strict and incomplete:
                return 1
        except Exception as exc:
            # Exception strings from drivers can include credentials or server details.
            print(f"FAIL MongoDB verification ({type(exc).__name__}); check Atlas access and URI")
            return 1
    if args.providers and not provider_checks(settings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
