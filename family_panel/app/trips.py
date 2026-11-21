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
import re
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


# ------------------------------------------------ trips from the calendar
# Any timed event today with a real place in its location becomes a trip of
# its own: be there by the event's start, leaving from home. "Zoom", a link,
# "Home" or "TBC" are not places, so those are skipped. Only events starting
# in the next few hours are ever sent to Waze, and only ones Waze can find a
# route to are shown — an event has no "usual drive" to fall back on.
# calendar_trips: false (add-on option) turns this off.
_NOT_A_PLACE = re.compile(
    r"(https?://|www\.|zoom|teams|meet\.google|google meet|webex|skype|facetime|online|"
    r"virtual|video call|phone|^call\b|\btbc\b|\btba\b|\btbd\b|^home$|^at home$|^house$)", re.I)
EVENT_LOOKAHEAD_MIN = 180


def is_place(location) -> bool:
    loc = str(location or "").strip()
    return len(loc) >= 4 and not _NOT_A_PLACE.search(loc)


def _event_trips(config: dict, events: list[dict], manual: list[dict], now: datetime) -> list[dict]:
    if config.get("calendar_trips") is False:
        return []
    out, seen = [], set()
    for e in events or []:
        if e.get("all_day") or not e.get("start") or e.get("date") != now.strftime("%Y-%m-%d"):
            continue
        if not is_place(e.get("location")):
            continue
        h, m = (int(x) for x in e["start"].split(":"))
        arrive = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if not (arrive - timedelta(minutes=EVENT_LOOKAHEAD_MIN) <= now <= arrive):
            continue
        key = (e["location"].strip().lower(), e["start"])
        if key in seen:                     # the same event on two calendars
            continue
        seen.add(key)
        # A weekly trip set up by hand for the same time wins over the event.
        if any(abs((h * 60 + m) - _mins(t["time"])) <= 15 for t in manual
               if now.weekday() in (t.get("days") or [])):
            continue
        out.append({"id": "ev:" + str(e.get("id")), "label": e.get("title") or "Event",
                    "destination": e["location"].strip(), "time": e["start"],
                    "person": e.get("person_id"), "minutes": None, "from_calendar": True})
    return out


def _mins(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def upcoming(config: dict, now: datetime | None = None, events: list[dict] | None = None) -> list[dict]:
    """Today's trips still worth showing, soonest leave time first: the
    weekly ones set up on the panel, plus calendar events with an address."""
    now = now or datetime.now(db.LOCAL_TZ)
    out = []
    manual = all_trips()
    todays = [t for t in manual if now.weekday() in (t.get("days") or [])]
    for t in todays + _event_trips(config, events, manual, now):
        h, m = (int(x) for x in t["time"].split(":"))
        arrive = now.replace(hour=h, minute=m, second=0, microsecond=0)
        cal = bool(t.get("from_calendar"))
        usual = float(t.get("minutes") or 20)
        # cheap first pass on the usual drive: is it anywhere near due?
        if not cal and not (arrive - timedelta(minutes=usual + LOOKAHEAD_MIN) <= now
                            <= arrive - timedelta(minutes=usual) + timedelta(minutes=AFTER_LEAVE_MIN + 30)):
            continue
        live, route = travel_minutes(config, t["destination"])
        if cal and live is None:
            continue                        # nowhere Waze can route to
        if cal and live > LOOKAHEAD_MIN:
            continue                        # interstate, or a mis-read address
        drive = live if live is not None else usual
        if cal and arrive - timedelta(minutes=drive + LOOKAHEAD_MIN) > now:
            continue
        leave = arrive - timedelta(minutes=drive)
        if now > leave + timedelta(minutes=AFTER_LEAVE_MIN):
            continue
        out.append({
            "id": t["id"], "label": t["label"], "destination": t["destination"],
            "person": t.get("person"), "arrive": t["time"],
            "drive_min": round(drive), "live": live is not None, "route": route,
            "usual_min": None if cal else round(usual),
            "from_calendar": cal,
            "leave_at": leave.strftime("%H:%M"),
            "leave_in_min": int((leave - now).total_seconds() // 60),
        })
    out.sort(key=lambda x: x["leave_in_min"])
    return out
