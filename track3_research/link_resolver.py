"""R-09 LinkResolver + Validator: normalize, strip tracking params, dedupe, HEAD/GET check. Only ok links reach boards.

    python -m track3_research.link_resolver --report [--trip trip_x] [--revalidate]
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from shared import http
from shared.agent_protocol import now, stable_id
from shared.config import get_db

TRACKING = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "aff", "aid_src", "affiliate", "_ga", "si"}
BOT_PROTECTED = ("booking.com", "airbnb.", "skyscanner.", "google.", "tripadvisor.", "thefork.", "opentable.", "getyourguide.",
                 "tiqets.", "ra.co", "dice.fm", "flightaware.", "omio.", "rome2rio.", "resy.", "ticketmaster.", "viator.",
                 "expedia.", "kayak.", "reddit.com", "sevenrooms.", "covermanager.", "shotgun.live", "xceed.me", "eventbrite.")


def normalize_url(url: str) -> str:
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    netloc = p.netloc.lower()
    path = p.path or "/"
    return urlunparse((p.scheme.lower() or "https", netloc, path, "", urlencode(q, doseq=True), ""))


def _check(url: str) -> dict:
    status, final, note = None, url, ""
    try:
        r = http.client().head(url, timeout=12)
        status, final = r.status_code, str(r.url)
        if status in (400, 403, 404, 405, 406, 429, 501, 503) or status >= 500:
            with http.client().stream("GET", url, timeout=15) as g:
                status, final = g.status_code, str(g.url)
                headers = g.headers
        else:
            headers = r.headers
    except httpx.HTTPError as exc:
        try:
            with http.client().stream("GET", url, timeout=15) as g:
                status, final, headers = g.status_code, str(g.url), g.headers
        except httpx.HTTPError as exc2:
            return {"status": "unreachable", "ok": False, "http_status": None, "note": type(exc2).__name__ or str(exc)}
    host = urlparse(final).netloc.lower()
    if status < 400:
        return {"status": "ok", "ok": True, "http_status": status, "final_url": final}
    waf = any(h in headers for h in ("cf-ray", "x-amz-cf-id", "x-akamai-transformed", "x-datadome", "x-px")) or "cloudflare" in headers.get("server", "").lower()
    if status == 429 or (status in (401, 403, 405, 202, 503, 999) and (waf or any(d in host for d in BOT_PROTECTED))):
        return {"status": "bot_protected", "ok": True, "http_status": status, "final_url": final, "note": "provider blocks automated checks; URL well-formed"}
    return {"status": f"http_{status}", "ok": False, "http_status": status, "final_url": final, "note": note}


def validate_url(url: str) -> dict:
    res = http.cached("linkcheck", {"url": url}, lambda: _check(url), ttl=timedelta(hours=24))
    return res or {"status": "unreachable", "ok": False}


def save_link(*, trip_id: str | None, item_ref: str | None, kind: str, provider: str, url: str, label: str,
              is_official: bool = False, price_hint: dict | None = None, for_date: str | None = None,
              party_size: int | None = None, extra: dict | None = None, validate: bool = True) -> tuple[str, bool]:
    """Upsert on (trip_id, url, item_ref). Returns (link_id, ok)."""
    url = normalize_url(url)
    v = validate_url(url) if validate else {"status": "unchecked", "ok": True}
    lid = stable_id("lnk_", trip_id, url, item_ref)
    doc = {"trip_id": trip_id, "item_ref": item_ref, "kind": kind, "provider": provider, "url": url, "label": label[:200],
           "is_official": bool(is_official), "price_hint": price_hint, "for_date": for_date, "party_size": party_size,
           "validation": {"status": v["status"], "checked_at": now(), "ok": bool(v["ok"])}}
    if extra:
        doc.update(extra)
    get_db().links.update_one({"_id": lid}, {"$set": doc, "$setOnInsert": {"created_at": now()}}, upsert=True)
    return lid, bool(v["ok"])


def report(trip_id: str | None, revalidate: bool) -> dict:
    db = get_db()
    flt = {"trip_id": trip_id} if trip_id else {}
    counts = {"ok": 0, "broken": 0}
    by_status: dict[str, int] = {}
    for link in db.links.find(flt):
        if revalidate:
            v = _check(link["url"])
            db.links.update_one({"_id": link["_id"]}, {"$set": {"validation": {"status": v["status"], "checked_at": now(), "ok": v["ok"]}}})
        else:
            v = link.get("validation") or {"ok": False, "status": "unchecked"}
        counts["ok" if v["ok"] else "broken"] += 1
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    on_boards = set()
    for b in db.boards.find(flt, {"bookings.link_id": 1, "days.slots.item.link_ids": 1}):
        on_boards |= {x["link_id"] for x in b.get("bookings", [])}
        for d in b.get("days", []):
            for s in d.get("slots", []):
                on_boards |= set(s["item"].get("link_ids", []))
    broken_on_boards = db.links.count_documents({"_id": {"$in": list(on_boards)}, "validation.ok": {"$ne": True}}) if on_boards else 0
    return {**counts, "by_status": by_status, "links_on_boards": len(on_boards), "broken_on_boards": broken_on_boards}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--trip")
    ap.add_argument("--revalidate", action="store_true")
    a = ap.parse_args()
    r = report(a.trip, a.revalidate)
    print(f"links ok={r['ok']} broken={r['broken']} statuses={r['by_status']}")
    print(f"on boards={r['links_on_boards']} broken_on_boards={r['broken_on_boards']}")


if __name__ == "__main__":
    main()
