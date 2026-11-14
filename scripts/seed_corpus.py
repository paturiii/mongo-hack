"""R-05 Demo corpus pre-seeder: Opinion Scouts for ~10 themes × all source types (long TTL), places, consensus.

    python scripts/seed_corpus.py --city lisbon|tokyo|mexico-city [--themes food,nightlife] [--no-places]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import get_db

CITIES = {"lisbon": ("Lisbon", "Portugal"), "tokyo": ("Tokyo", "Japan"), "mexico-city": ("Mexico City", "Mexico")}
THEMES = ["food", "nightlife", "hidden gems", "things to skip", "neighborhoods", "museums", "viewpoints", "day trips", "markets", "coffee and bakeries"]
CITY_THEMES = {"lisbon": ["fado"], "tokyo": ["izakaya and bars"], "mexico-city": ["tacos and street food"]}
SOURCES = ["reddit", "forum", "blog", "review", "video", "news"]


def seed(slug: str, themes: list[str], places: bool) -> dict:
    from track3_research.agents.common import embed_opinion_backlog
    from track3_research.agents.opinions import OpinionScout
    from track3_research.agents.places import PlaceFinder
    from track3_research.consensus import run_consensus

    name, country = CITIES[slug]
    t0 = time.time()
    if places:
        for g in ("sights", "food", "nightlife"):
            try:
                PlaceFinder({"_id": f"job_seed_{slug}_places_{g}", "kind": "places", "params": {"city": name, "country": country, "theme": g}}).run()
            except Exception as exc:  # noqa: BLE001
                logging.warning("places %s/%s failed: %s", slug, g, exc)

    db = get_db()
    fresh_after = datetime.now(UTC) - timedelta(days=14)

    def one(theme: str) -> int:
        have = db.opinions.count_documents({"city": name, "themes": theme, "fetched_at": {"$gt": fresh_after}, "summary": {"$not": {"$regex": "^\\[FIXTURE"}}})
        if have >= 15:
            logging.info("%s/%s: %d fresh opinions already, skipping", slug, theme, have)
            return 0
        job = {"_id": f"job_seed_{slug}_{theme.replace(' ', '_')}", "kind": "opinions",
               "params": {"city": name, "country": country, "theme": theme, "source_types": SOURCES, "ttl_days": 365}}
        try:
            return len(OpinionScout(job).run()["result_refs"])
        except Exception as exc:  # noqa: BLE001
            logging.warning("opinions %s/%s failed: %s", slug, theme, exc)
            return 0

    with ThreadPoolExecutor(3) as ex:
        counts = dict(zip(themes, ex.map(one, themes)))
    cons = run_consensus(name, geocode_limit=20)
    embedded = embed_opinion_backlog(name)
    real = {"city": name, "summary": {"$not": {"$regex": "^\\[FIXTURE"}}}
    by_type = {r["_id"]: r["n"] for r in db.opinions.aggregate([{"$match": real}, {"$group": {"_id": "$source_type", "n": {"$sum": 1}}}])}
    return {"city": name, "per_theme": counts, "opinions": sum(by_type.values()), "by_source_type": by_type, "consensus": cons,
            "opinions_embedded": embedded, "secs": round(time.time() - t0)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, choices=list(CITIES))
    ap.add_argument("--themes", default=None, help="comma list; default: 10 core themes + city extras")
    ap.add_argument("--no-places", action="store_true")
    a = ap.parse_args()
    a.themes = a.themes or ",".join(THEMES + CITY_THEMES.get(a.city, []))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    r = seed(a.city, [t.strip() for t in a.themes.split(",") if t.strip()], not a.no_places)
    ok = r["opinions"] >= 150 and len(r["by_source_type"]) >= 3
    print(r)
    print(f"{r['city']}: {r['opinions']} opinions across {len(r['by_source_type'])} source types -> {'PASS' if ok else 'FAIL'} (AC: >=150, >=3 types)")


if __name__ == "__main__":
    main()
