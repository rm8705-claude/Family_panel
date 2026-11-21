"""Leave-by trips: "Leave in 12 min for work".

A trip is a place the household has to BE by a time on given weekdays —
work, school, daycare drop-off, the daycare pick-up, swimming, soccer. Each
one is {id, label, destination, time "HH:MM", days [0=Mon..6=Sun], person,
minutes}. `minutes` is the usual drive, used whenever live traffic isn't
available. They live in the settings table and are edited on the panel.

Live drive time comes from Home Assistant's Waze Travel Time integration:
the waze_travel_time.get_travel_times service (?return_response), from the
house (the weather lat/lon) to the trip's address. Only trips coming up in
the next couple of hours are ever looked up, and each destination is cached
for five minutes, so Waze sees a handful of requests a morning.
"""
import logging
import time
import uuid
from datetime import datetime, timedelta

import requests

import db
import ha

log = logging.getLogger("family-panel.trips")

KEY = "trips"
LOOKAHEAD_MIN = 120          # start showing a trip this long before arrive-by
AFTER_LEAVE_MIN = 10         # keep "Leave now" up this long past the leave time
CACHE_S = 300
_cache: dict = {}


def all_trips() -> list[dict]:
    return db.get_json_setting(KEY) or []


def _clean(t: dict) -> dict:
    hhmm = str(t.get("time") or "").strip()
    try:
        datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        raise ValueError("Time must be HH:MM, e.g. 08:30")
    label = str(t.get("label") or "").strip()[:40]
    dest = str(t.get("destination") or "").strip()[:200]
    if not label or not dest:
        raise ValueError("A trip needs a name and an address")
    days = sorted({int(d) for d in (t.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
    if not days:
        raise ValueError("Pick at least one day")
    try:
        minutes = max(1, min(240, int(t.get("minutes") or 20)))
    except (TypeError, ValueError):
        minutes = 20
    return {"id": str(t.get("id") or uuid.uuid4().hex[:10]), "label": label,
            "destination": dest, "time": hhmm, "days": days,
            "person": (str(t.get("person")) if t.get("person") else None),
            "minutes": minutes}


def save(trip: dict) -> dict:
    t = _clean(trip)
    trips = [x for x in all_trips() if x.get("id") != t["id"]] + [t]
    trips.sort(key=lambda x: (x["time"], x["label"]))
    db.set_json_setting(KEY, trips)
    return t


def delete(trip_id: str) -> None:
    db.set_json_setting(KEY, [x for x in all_trips() if x.get("id") != trip_id])


def _origin(config: dict) -> str:
    w = config.get("weather") or {}
    return f"{float(w.get('lat', -27.48))},{float(w.get('lon', 152.98))}"


def travel_minutes(config: dict, destination: str) -> tuple[float | None, str | None]:
    """(minutes, route name) by car right now, or (None, None) when Waze
    isn't set up or doesn't answer."""
    hit = _cache.get(destination)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1], hit[2]
    try:
        base, token = ha._api_base()
        r = requests.post(f"{base}/services/waze_travel_time/get_travel_times?return_response",
                          headers=ha._headers(token), timeout=20,
                          json={"origin": _origin(config), "destination": destination,
                                "region": "au", "units": "metric", "vehicle_type": "car",
                                "realtime": True})
        r.raise_for_status()
        data = r.json() or {}
        routes = (data.get("service_response", data) or {}).get("routes") or []
        best = min(routes, key=lambda x: float(x.get("duration") or 1e9)) if routes else None
        mins = float(best["duration"]) if best and best.get("duration") is not None else None
        name = best.get("name") if best else None
    except Exception as e:                  # noqa: BLE001 - fall back to usual minutes
        log.info("waze %s: %s", destination, e)
        mins, name = None, None
    _cache[destination] = (time.time(), mins, name)
    return mins, name


def upcoming(config: dict, now: datetime | None = None) -> list[dict]:
    """Today's trips still worth showing, soonest leave time first."""
    now = now or datetime.now(db.LOCAL_TZ)
    out = []
    for t in all_trips():
        if now.weekday() not in (t.get("days") or []):
            continue
        h, m = (int(x) for x in t["time"].split(":"))
        arrive = now.replace(hour=h, minute=m, second=0, microsecond=0)
        usual = float(t.get("minutes") or 20)
        # cheap first pass on the usual drive: is it anywhere near due?
        if not (arrive - timedelta(minutes=usual + LOOKAHEAD_MIN) <= now
                <= arrive - timedelta(minutes=usual) + timedelta(minutes=AFTER_LEAVE_MIN + 30)):
            continue
        live, route = travel_minutes(config, t["destination"])
        drive = live if live is not None else usual
        leave = arrive - timedelta(minutes=drive)
        if now > leave + timedelta(minutes=AFTER_LEAVE_MIN):
            continue
        out.append({
            "id": t["id"], "label": t["label"], "destination": t["destination"],
            "person": t.get("person"), "arrive": t["time"],
            "drive_min": round(drive), "live": live is not None, "route": route,
            "usual_min": round(usual),
            "leave_at": leave.strftime("%H:%M"),
            "leave_in_min": int((leave - now).total_seconds() // 60),
        })
    out.sort(key=lambda x: x["leave_in_min"])
    return out
