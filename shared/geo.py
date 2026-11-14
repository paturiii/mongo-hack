"""Geocoding (Nominatim, cached + 1 req/s), distances, day-area polygons, opening-hours checks."""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import date, timedelta

from shared import http

_nominatim_lock = threading.Lock()
_last_call = [0.0]


def point(lng: float, lat: float) -> dict:
    return {"type": "Point", "coordinates": [round(float(lng), 6), round(float(lat), 6)]}


def _nominatim(params: dict) -> list[dict]:
    def call():
        with _nominatim_lock:
            wait = 1.1 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            try:
                r = http.request("GET", "https://nominatim.openstreetmap.org/search", params={**params, "format": "jsonv2", "addressdetails": 1}, timeout=20)
                return r.json() if r.status_code == 200 else []
            finally:
                _last_call[0] = time.time()

    return http.cached("geocode", params, call, ttl=timedelta(days=90)) or []


def geocode(query: str, near: dict | None = None, radius_km: float = 40, accept=None) -> dict | None:
    """Return {geo, name, names, bbox, neighborhood, address} or None. `near` biases + bounds results to a radius.
    `accept(names) -> bool` picks the first of the top results whose OSM names (incl. name:en, alt_name) pass."""
    params: dict = {"q": query, "limit": 3}
    if accept:
        params["namedetails"] = 1
    if near:
        lng, lat = near["coordinates"]
        d_lat = radius_km / 111.0
        d_lng = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
        params["viewbox"] = f"{lng - d_lng:.4f},{lat + d_lat:.4f},{lng + d_lng:.4f},{lat - d_lat:.4f}"
        params["bounded"] = 1
    rows = _nominatim(params)
    if not rows:
        return None
    for r in rows:
        names = [r.get("name") or ""] + [n for k, v in (r.get("namedetails") or {}).items() if k.split(":")[0] in
                                         ("name", "alt_name", "old_name", "official_name", "short_name", "int_name", "brand")
                                         for n in v.split(";")]
        if not accept or accept([n for n in names if n]):
            break
    else:
        return None
    addr = r.get("address") or {}
    return {
        "geo": point(float(r["lon"]), float(r["lat"])),
        "names": names,
        "name": r.get("name") or query,
        "bbox": [float(x) for x in r.get("boundingbox", [])] or None,  # [s, n, w, e]
        "neighborhood": addr.get("suburb") or addr.get("neighbourhood") or addr.get("city_district") or addr.get("quarter"),
        "address": r.get("display_name"),
        "osm": f"{r.get('osm_type')}/{r.get('osm_id')}",
        "type": r.get("type"),
    }


def haversine_m(a: dict, b: dict) -> float:
    (lng1, lat1), (lng2, lat2) = a["coordinates"], b["coordinates"]
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def centroid(points: list[dict]) -> dict:
    xs = [p["coordinates"][0] for p in points]
    ys = [p["coordinates"][1] for p in points]
    return point(sum(xs) / len(xs), sum(ys) / len(ys))


def area_polygon(points: list[dict], buffer_m: float = 350) -> dict:
    """Convex hull of the points, each buffered by a small square, as a closed CCW GeoJSON Polygon."""
    pts: list[tuple[float, float]] = []
    for p in points:
        lng, lat = p["coordinates"]
        d_lat = buffer_m / 111_000
        d_lng = buffer_m / (111_000 * max(0.2, math.cos(math.radians(lat))))
        pts += [(lng - d_lng, lat - d_lat), (lng + d_lng, lat - d_lat), (lng + d_lng, lat + d_lat), (lng - d_lng, lat + d_lat)]
    pts = sorted(set(pts))
    if len(pts) < 3:
        return {"type": "Polygon", "coordinates": [[list(p) for p in pts + pts[:1]]]}

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    ring = [[round(x, 6), round(y, 6)] for x, y in hull]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


# ---------- opening hours (OSM opening_hours subset) ----------
_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_RULE = re.compile(r"^((?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*[-,]\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))*)\s*(.*)$")


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_M = "(?:" + "|".join(_MONTHS) + ")"
_MONTH_RULE = re.compile(rf"^({_M}(?:\s*\d{{1,2}})?(?:\s*-\s*{_M}?(?:\s*\d{{1,2}})?)?)\s+(.*)$")


def _month_spec_covers(spec: str, day: date) -> bool:
    """'Oct-Apr', 'Jan 1', 'Dec 24-26', 'Jul 1-Aug 31' → does it include `day`?"""
    parts = [p.strip() for p in spec.split("-")]

    def parse(p: str, default_month: int | None) -> tuple[int, int | None]:
        m = re.match(rf"^({_M})?\s*(\d{{1,2}})?$", p)
        mon = _MONTHS.index(m.group(1)) + 1 if m and m.group(1) else default_month
        return mon or 1, int(m.group(2)) if m and m.group(2) else None

    a_mon, a_day = parse(parts[0], None)
    if len(parts) == 1:
        return day.month == a_mon and (a_day is None or day.day == a_day)
    b_mon, b_day = parse(parts[1], a_mon)
    lo, hi = (a_mon, a_day or 1), (b_mon, b_day or 31)
    cur = (day.month, day.day)
    return lo <= cur <= hi if lo <= hi else (cur >= lo or cur <= hi)


def _expand_days(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-")
            i, j = _DAYS.index(a), _DAYS.index(b)
            k = i
            while True:
                out.add(k)
                if k == j:
                    break
                k = (k + 1) % 7
        elif part in _DAYS:
            out.add(_DAYS.index(part))
    return out


def open_on(hours: str | None, day: date, category: str | None = None) -> bool:
    """Best-effort: True if the place is open at some point on `day`. Unknown hours → open,
    except museums which default to closed on Mondays (the common European pattern)."""
    if not hours:
        return not (category == "museum" and day.weekday() == 0)
    h = hours.strip()
    if h in ("24/7",):
        return True
    wd = day.weekday()
    state: bool | None = None
    matched_any_rule = False
    for rule in re.split(r"\s*;\s*", h):
        rule = rule.strip()
        mm = _MONTH_RULE.match(rule)
        if mm:
            if not _month_spec_covers(mm.group(1), day):
                matched_any_rule = matched_any_rule or not mm.group(2).lower().startswith(("off", "closed"))
                continue
            rule = mm.group(2).strip()
        m = _RULE.match(rule)
        if not m:
            if re.match(r"^\d{1,2}:\d{2}", rule):
                matched_any_rule, state = True, True
            elif mm and rule.lower().startswith(("off", "closed")):
                state = False
            continue
        matched_any_rule = True
        days, rest = _expand_days(m.group(1)), m.group(2).strip().lower()
        if wd in days:
            state = not rest.startswith(("off", "closed"))
    if state is None:
        if not matched_any_rule:
            return not (category == "museum" and wd == 0)
        return False
    return state
