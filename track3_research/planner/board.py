"""R-13 Board composition: items with opinion chips + sources + validated links, bookings by urgency, and the
versioned write (boards vN + board_versions archive + trips update) in one MongoDB transaction."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from shared.agent_protocol import slugify
from shared.config import get_client, get_db
from shared.llm import structured
from track3_research.link_resolver import save_link

log = logging.getLogger("wander.board")

COST = {"museum": 14, "sight": 10, "activity": 0, "beach": 0, "lunch": 20, "dinner": 32, "bar": 15, "club": 22, "event": 25}
BAND_MULT = {"budget": 0.65, "mid": 1.0, "splurge": 1.9}
GROUP = {"flight": "Getting there", "transfer": "Getting there", "stay": "Stay", "train": "Getting around", "bus": "Getting around",
         "monument_ticket": "Tickets", "tour": "Tickets", "club_ticket": "Nights out", "restaurant": "Eat", "rebook": "Getting there"}


def links_by_ref(trip_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for l in get_db().links.find({"trip_id": trip_id, "validation.ok": True}):
        out.setdefault(l.get("item_ref") or "", []).append(l)
    for v in out.values():
        v.sort(key=lambda l: (not l.get("is_official"), l["provider"]))
    return out


def _chips(p: dict) -> list[dict]:
    c = p.get("consensus") or {}
    chips = [{"text": x["text"], "source_type": x["source_type"], "url": x["url"]} for x in (c.get("chips") or [])[:2]]
    if not chips and c.get("opinion_ids"):
        o = get_db().opinions.find_one({"_id": {"$in": c["opinion_ids"]}}, sort=[("weight", -1)])
        if o:
            chips = [{"text": o["summary"][:110], "source_type": o["source_type"], "url": o["source_url"]}]
    return chips


def _provider_source(p: dict) -> dict | None:
    if p.get("source") == "osm" and p.get("source_id"):
        return {"source_type": "provider", "provider": "openstreetmap", "url": f"https://www.openstreetmap.org/{p['source_id']}"}
    return None


def place_item(p: dict, kind: str, refs: dict, band: str, for_date: str | None = None) -> dict:
    links = refs.get(f"place:{p['_id']}", [])
    if for_date:
        links = [l for l in links if not l.get("for_date") or l["kind"] != "club_ticket" or l["for_date"] == for_date]
    chips = _chips(p)
    sources = [{"source_type": c["source_type"], "url": c["url"]} for c in chips]
    for l in links:
        if l.get("is_official"):
            sources.append({"source_type": "provider", "provider": l["provider"], "url": l["url"]})
            break
    ps = _provider_source(p)
    if ps:
        sources.append(ps)
    cost_key = kind if kind in ("lunch", "dinner") else ("event" if kind == "event" else p["category"])
    priced = next((l["price_hint"]["amount"] for l in links if (l.get("price_hint") or {}).get("amount")), None)
    amount = priced if priced is not None else round(COST.get(cost_key, 0) * (BAND_MULT.get(band, 1) if kind in ("lunch", "dinner") else 1))
    c = p.get("consensus") or {}
    return {
        "place_id": p["_id"], "name": p["name"], "why": "", "opinion_chips": chips, "link_ids": [l["_id"] for l in links[:2]],
        "sources": sources, "est_cost": {"amount": amount, "currency": "EUR", "per": "person"} if amount else None,
        "price_from_link": priced is not None, "geo": p.get("geo"), "category": p["category"], "indoor": bool(p.get("indoor")),
        "neighborhood": p.get("neighborhood"), "kind": kind, "tags": (c.get("tags") or [])[:5],
        "timed_ticket": any(l["kind"] == "monument_ticket" and l.get("book_ahead") for l in links),
        "walk_in_only": bool(p.get("walk_in_only")), "consensus_score": c.get("score"),
    }


def event_item(link: dict, refs: dict) -> dict:
    ev = link["event"]
    venue = get_db().places.find_one({"_id": ev.get("venue_place_id")}) if ev.get("venue_place_id") else None
    chips = _chips(venue) if venue else []
    return {
        "place_id": ev.get("venue_place_id"), "name": ev["name"], "why": "", "opinion_chips": chips, "link_ids": [link["_id"]],
        "sources": [{"source_type": "provider", "provider": link["provider"], "url": link["url"]}] + [{"source_type": c["source_type"], "url": c["url"]} for c in chips],
        "est_cost": {"amount": (link.get("price_hint") or {}).get("amount") or COST["event"], "currency": "EUR", "per": "person"},
        "price_from_link": bool((link.get("price_hint") or {}).get("amount")), "geo": (venue or {}).get("geo"), "category": "club",
        "indoor": True, "kind": "night", "event_id": link["_id"], "event": ev, "tags": ["club night"] if ev.get("kind") == "club_night" else [ev.get("kind")],
        "timed_ticket": ev.get("kind") != "club_night",
    }


def transfer_links(trip_id: str, airport: dict | None, stay_area: dict, city: str, party: int) -> list[str]:
    origin = (airport or {}).get("name") or f"{city} Airport"
    dest = f"{stay_area['name']}, {city}"
    ids = []
    for provider, url, label in (
        ("google_maps", "https://www.google.com/maps/dir/?" + urlencode({"api": 1, "origin": origin, "destination": dest, "travelmode": "transit"}),
         f"Airport → {stay_area['name']} by public transport (Google Maps)"),
        ("rome2rio", f"https://www.rome2rio.com/map/{origin.replace(' ', '-')}/{dest.replace(', ', '-').replace(' ', '-')}", f"Airport → {stay_area['name']} options (Rome2Rio)"),
    ):
        lid, ok = save_link(trip_id=trip_id, item_ref="transfer:airport", kind="transfer", provider=provider, url=url, label=label, party_size=party)
        if ok:
            ids.append(lid)
    return ids


def _opinion_sources(city: str, theme: str, n: int = 2) -> tuple[list[dict], list[dict]]:
    ops = list(get_db().opinions.find({"city": city, "themes": theme}).sort("weight", -1).limit(n))
    chips = [{"text": o["summary"][:110], "source_type": o["source_type"], "url": o["source_url"]} for o in ops]
    return chips, [{"source_type": o["source_type"], "url": o["source_url"]} for o in ops]


def build_slots(day_slots: list[dict], ctx: dict) -> list[dict]:
    """Turn scheduled slots into board slots (item docs)."""
    refs, band = ctx["refs"], ctx["band"]
    out = []
    for s in day_slots:
        k = s["kind"]
        item = None
        if s.get("place"):
            item = place_item(s["place"], k if k in ("lunch", "dinner", "night") else "activity", refs, band, ctx.get("date"))
        elif s.get("event"):
            item = event_item(s["event"], refs)
        elif k == "arrival":
            leg = ctx["arrival"]["leg"] or {}
            ls = [l for l in ctx["flight_links"] if l.get("booked")]
            item = {"place_id": ctx.get("airport_id"), "name": f"Land at {leg.get('to', 'airport')} · {(ls[0]['leg']['code'] if ls else leg.get('number', ''))}".strip(" ·"),
                    "kind": "arrival", "link_ids": [l["_id"] for l in ls[:1]], "opinion_chips": [], "est_cost": None,
                    "sources": [{"source_type": "provider", "provider": l["provider"], "url": l["url"]} for l in ls[:1]] or [{"source_type": "user", "url": "https://www.flightaware.com/"}],
                    "leg": leg}
        elif k == "transfer":
            chips, srcs = _opinion_sources(ctx["city"], "airport transfer")
            item = {"place_id": None, "name": f"Airport → {ctx['stay']['name']}", "kind": "transfer", "link_ids": ctx["transfer_ids"], "opinion_chips": chips,
                    "sources": srcs or [{"source_type": "provider", "url": u} for u in ctx["transfer_urls"][:1]], "est_cost": {"amount": 2, "currency": "EUR", "per": "person"}}
        elif k == "checkin":
            st = ctx["stay"]
            item = {"place_id": None, "name": f"Check in · {st['name']}", "kind": "checkin", "link_ids": st["link_ids"][:2], "geo": st["geo"],
                    "opinion_chips": [{"text": f"Travelers: {st['why'][:100]}", "source_type": "reddit" if "reddit" in st["sources"][0] else "forum", "url": st["sources"][0]}],
                    "sources": [{"source_type": "reddit" if "reddit" in u else "blog", "url": u} for u in st["sources"][:2]], "est_cost": None}
        elif k in ("day_trip_out", "day_trip_back"):
            dt = ctx["day_trip"] or {}
            hero = ctx["day_trip_hero"]
            ls = [l for l in refs.get(f"place:{hero['_id']}", []) if l["kind"] in ("train", "bus")] if hero else []
            chips, srcs = _opinion_sources(ctx["city"], "transit")
            out_leg = k == "day_trip_out"
            name = (f"{(dt.get('mode') or 'train').title()} {dt.get('from_station') or ctx['city']} → {dt.get('destination')}" if out_leg
                    else f"{(dt.get('mode') or 'train').title()} back to {ctx['city']}")
            sources = srcs or [{"source_type": "provider", "provider": l["provider"], "url": l["url"]} for l in ls[:1]]
            item = {"place_id": None, "name": name, "kind": "transit", "link_ids": [l["_id"] for l in ls[:2]], "opinion_chips": chips[:1],
                    "sources": sources, "est_cost": {"amount": 5, "currency": "EUR", "per": "person"}}
        if not item or not item.get("sources"):
            continue
        item.setdefault("why", "")
        out.append({"slot_id": s["slot_id"], "time": s["time"], "item": item})
    return out


# ---------- LLM polish: titles, themes, whys ----------
class SlotWhy(BaseModel):
    slot_id: str
    why: str = Field(description="<=22 words, grounded ONLY in the chips/tags given; mention timing or booking tips when present")


class DayTheme(BaseModel):
    date: str
    theme: str = Field(description="3-6 words, e.g. 'Alfama lanes & fado night'")


class Polish(BaseModel):
    title: str
    summary: str = Field(description="<=45 words")
    days: list[DayTheme]
    whys: list[SlotWhy]


def polish(days: list[dict], trip: dict, city: str, budget_text: str, only_dates: set[str] | None = None) -> None:
    lines = []
    for d in days:
        if only_dates and d["date"] not in only_dates:
            continue
        lines.append(f"## {d['date']} ({d.get('kind', 'city')})")
        for s in d["slots"]:
            it = s["item"]
            chips = " | ".join(c["text"] for c in it.get("opinion_chips", []))
            lines.append(f"- {s['slot_id']} {s['time']} {it['name']} [{it.get('kind')}/{it.get('category', '')}] tags={it.get('tags', [])} chips={chips}")
    prof = trip.get("profile") or {}
    try:
        res = structured(Polish, ("You write concise, specific copy for a travel itinerary board. Never invent facts beyond the chips/tags. No emoji. "
                          "Write for the traveler: never mention chips, tags, missing data, scheduling gaps or what is not on the plan. "
                          "If a stop has no chips, write a short neutral line from its category/neighborhood."),
                         f"City: {city}\nTravelers: {trip.get('travelers')} · profile: {prof}\nBudget: {budget_text}\n\n" + "\n".join(lines), tier="smart")
    except Exception as exc:  # noqa: BLE001
        log.warning("polish failed: %s", exc)
        res = None
    whys = {w.slot_id: w.why for w in (res.whys if res else [])}
    themes = {t.date: t.theme for t in (res.days if res else [])}
    for d in days:
        if only_dates and d["date"] not in only_dates:
            continue
        d["theme"] = themes.get(d["date"]) or d.get("theme") or ", ".join(dict.fromkeys(
            s["item"].get("neighborhood") for s in d["slots"] if s["item"].get("neighborhood"))) or "Explore"
        for s in d["slots"]:
            it = s["item"]
            fallback = it["opinion_chips"][0]["text"] if it.get("opinion_chips") else (
                f"Notable {it.get('category', 'stop')} in {it['neighborhood']}" if it.get("neighborhood") else f"Notable {it.get('category', 'stop')} on today's route")
            why = whys.get(s["slot_id"]) or it.get("why") or fallback
            it["why"] = fallback if why.strip().lower() == it["name"].strip().lower() else why
    if res and not only_dates:
        trip["_title"], trip["_summary"] = res.title, res.summary


# ---------- bookings ----------
def _deadline(link: dict, start: date) -> tuple[int, str]:
    k = link["kind"]
    fd = link.get("for_date")
    if link.get("booked"):
        return 9, "Booked — tracking live"
    if k == "flight":
        return 0, "Book now — fares climb in the last 6 weeks"
    if k == "stay":
        return 1, f"Book by {(start - timedelta(days=42)):%b %d} for the best choice"
    if k == "monument_ticket":
        return (2, f"Timed entry — book by {(date.fromisoformat(fd) if fd else start) - timedelta(days=7):%b %d}") if link.get("book_ahead") else (6, "Buy online or on the day")
    if k == "restaurant":
        return (7, "Walk-in only — arrive early") if link.get("walk_in_only") else (3, f"Reserve by {(date.fromisoformat(fd) if fd else start) - timedelta(days=7):%b %d}")
    if k == "club_ticket":
        ev = link.get("event") or {}
        if ev.get("kind") == "club_night":
            return 5, f"Check the line-up the week before {ev.get('date', '')}"
        return 4, f"Buy before {ev.get('date', fd or '')} — popular nights sell out"
    return 8, "No booking needed — pay on the day"


def build_bookings(link_ids: list[str], start: date, previous: list[dict] | None) -> list[dict]:
    prev_status = {b["link_id"]: b["status"] for b in (previous or [])}
    docs = {l["_id"]: l for l in get_db().links.find({"_id": {"$in": link_ids}, "validation.ok": True})}
    out = []
    for lid in dict.fromkeys(link_ids):
        l = docs.get(lid)
        if not l:
            continue
        rank, hint = _deadline(l, start)
        out.append({"link_id": lid, "kind": l["kind"], "label": l["label"], "url": l["url"], "provider": l["provider"], "is_official": l.get("is_official", False),
                    "group": GROUP.get(l["kind"], "Other"), "for_date": l.get("for_date"), "deadline_hint": hint, "_rank": rank,
                    "status": prev_status.get(lid) or ("user_marked_booked" if l.get("booked") else "todo")})
    out.sort(key=lambda b: (b["_rank"], b.get("for_date") or ""))
    for b in out:
        b.pop("_rank")
    return out


# ---------- transactional write ----------
def write_board(trip: dict, board: dict, *, plan_status: bool, reason: str) -> dict:
    """boards vN + archive v(N-1) in board_versions + trips.board_id/status — atomically."""
    client, db = get_client(), get_db()
    now = datetime.now(UTC)
    result: dict = {}

    def txn(session):
        prev = db.boards.find_one({"trip_id": trip["_id"]}, session=session)
        doc = {**board, "trip_id": trip["_id"], "user_id": trip.get("user_id") or "anonymous", "updated_at": now, "reason": reason}
        if prev:
            archived = {**prev, "_id": f"{prev['_id']}@v{prev['version']}", "board_id": prev["_id"], "archived_at": now}
            db.board_versions.replace_one({"_id": archived["_id"]}, archived, upsert=True, session=session)
            doc.update(_id=prev["_id"], share_slug=prev["share_slug"], version=int(prev["version"]) + 1)
            r = db.boards.replace_one({"_id": prev["_id"], "version": prev["version"]}, doc, session=session)
            if r.matched_count != 1:
                raise RuntimeError("board version conflict")
        else:
            city = slugify((trip.get("destination") or {}).get("name") or "trip")
            doc.update(_id=f"b_{secrets.token_hex(6)}", share_slug=f"{city}-{secrets.token_hex(4)}", version=1)
            db.boards.insert_one(doc, session=session)
        upd: dict = {"$set": {"board_id": doc["_id"], "plan_updated_at": now}}
        flt: dict = {"_id": trip["_id"]}
        if plan_status:
            flt["status"] = {"$in": ["researching", "ready_for_research"]}
            upd["$set"]["status"] = "planned"
        r = db.trips.update_one(flt, upd, session=session)
        if plan_status and r.matched_count != 1:
            raise RuntimeError("trip status moved on; refusing to overwrite")
        result.update(board_id=doc["_id"], version=doc["version"], share_slug=doc["share_slug"])

    with client.start_session() as s:
        s.with_transaction(txn)
    return result
