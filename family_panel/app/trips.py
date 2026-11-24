"""Leave-by trips: "Leave in 12 min for work".

A trip is a place the household has to BE by a time on given weekdays —
work, school, daycare drop-off, the daycare pick-up, swimming, soccer. Each
one is {id, label, destination, time "HH:MM", days [0=Mon..6=Sun], person,
minutes}. `minutes` is the usual drive, used whenever live traffic isn't
available. They live in the settings table and are edited on the panel.

Live drive time comes from Google Maps (the Routes API, traffic-aware), from
the house (the weather lat/lon) to the trip's address, with the key in the
add-on option google_maps_api_key. Only trips coming up in the next couple
of hours are ever looked up, and each destination is cached for five
minutes, so it's a handful of requests a morning — well inside Google's
free monthly allowance.
"""
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta

import requests

import db

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


ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
# What the panel last heard from Google, for the Trips sheet's status line.
traffic_status = {"ok": None, "error": None, "at": None}


def _api_key(config: dict) -> str:
    return str(config.get("google_maps_api_key") or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


def _place(dest: str) -> dict:
    """A "lat,lon" pair goes as a point; anything else as an address Google
    looks up itself (street address, or a place name like a swim centre)."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", dest or "")
    if m:
        return {"location": {"latLng": {"latitude": float(m.group(1)), "longitude": float(m.group(2))}}}
    return {"address": dest}


def travel_minutes(config: dict, destination: str,
                   depart: datetime | None = None) -> tuple[float | None, str | None]:
    """(minutes, route name) by car from Google Maps (Routes API): in traffic
    right now, or Google's predicted traffic when `depart` is a future time.
    (None, None) with no key or no answer."""
    ckey = destination if depart is None else f"{destination}@{depart.strftime('%Y-%m-%d %H:%M')}"
    hit = _cache.get(ckey)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1], hit[2]
    key = _api_key(config)
    if not key:
        traffic_status.update(ok=None, error="No google_maps_api_key in the add-on options",
                              at=db.utc_now_iso())
        return None, None
    w = config.get("weather") or {}
    body = {
        "origin": {"location": {"latLng": {"latitude": float(w.get("lat", -27.48)),
                                           "longitude": float(w.get("lon", 152.98))}}},
        "destination": _place(destination),
        "travelMode": "DRIVE",
        # The most accurate setting — the one the Google Maps app's own times
        # match. (Plain TRAFFIC_AWARE trades accuracy for speed.)
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "regionCode": "AU",
        "units": "METRIC",
    }
    if depart is not None and depart > datetime.now(db.LOCAL_TZ) + timedelta(minutes=2):
        body["departureTime"] = depart.astimezone(db.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mins, name = None, None
    try:
        r = requests.post(ROUTES_URL, json=body, timeout=20, headers={
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "routes.duration,routes.description,routes.distanceMeters",
        })
        if r.status_code >= 400:
            try:
                msg = (r.json().get("error") or {}).get("message") or r.text
            except ValueError:
                msg = r.text
            raise RuntimeError(f"Google said {r.status_code}: {str(msg)[:200]}")
        routes = (r.json() or {}).get("routes") or []
        if not (routes and routes[0].get("duration")):
            raise RuntimeError(f"Google found no driving route to “{destination}” — "
                               "check the address (suburb and state help)")
        mins = float(str(routes[0]["duration"]).rstrip("s")) / 60
        name = routes[0].get("description") or None
        traffic_status.update(ok=True, error=None, at=db.utc_now_iso())
    except Exception as e:                  # noqa: BLE001 - fall back to usual minutes
        log.info("google routes %s: %s", destination, e)
        traffic_status.update(ok=False, error=str(e)[:240], at=db.utc_now_iso())
    _cache[ckey] = (time.time(), mins, name)
    return mins, name


def drive_to(config: dict, destination: str, arrive: datetime | None = None) -> dict:
    """For the event sheet's Drive time key. With an arrive-by in the future,
    the drive is Google's prediction for leaving at about the right time:
    guess a departure, ask, and re-ask once from the corrected departure."""
    now = datetime.now(db.LOCAL_TZ)
    if arrive is None or arrive <= now + timedelta(minutes=5):
        mins, route = travel_minutes(config, destination)
        leave = (arrive - timedelta(minutes=mins)) if (arrive and mins is not None) else None
        predicted = False
    else:
        mins, route = travel_minutes(config, destination, arrive - timedelta(minutes=30))
        if mins is not None:
            m2, r2 = travel_minutes(config, destination, arrive - timedelta(minutes=round(mins)))
            if m2 is not None:
                mins, route = m2, r2 or route
        leave = (arrive - timedelta(minutes=mins)) if mins is not None else None
        predicted = True
    return {
        "minutes": None if mins is None else round(mins),
        "route": route,
        "predicted": predicted,
        "leave_at": leave.strftime("%H:%M") if leave else None,
        "leave_date": leave.strftime("%Y-%m-%d") if leave else None,
        "leave_in_min": int((leave - now).total_seconds() // 60) if leave else None,
        "error": None if mins is not None else traffic_status.get("error"),
    }


# ------------------------------------------------ trips from the calendar
# Any timed event today with a real place in its location becomes a trip of
# its own: be there by the event's start, leaving from home. "Zoom", a link,
# "Home" or "TBC" are not places, so those are skipped. Only events starting
# in the next few hours are ever looked up, and only ones Google can find a
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


SOON_MIN = 10   # leaving within this long: use the traffic on the roads right now


def _drive_for(config: dict, dest: str, arrive: datetime, guess: float,
               now: datetime) -> tuple[float | None, str | None, bool]:
    """(minutes, route, predicted) for getting to `dest` by `arrive`.

    The countdown shows up to two hours ahead, and the traffic at 6:30 is not
    the traffic at 8:00 — so until it's nearly time to go, ask Google for its
    forecast at the time you'd actually set off: a first guess from the usual
    drive, then once more from the departure that guess gives. Close to
    leaving, it's the traffic right now."""
    first_leave = arrive - timedelta(minutes=guess)
    if first_leave - now <= timedelta(minutes=SOON_MIN):
        m, r = travel_minutes(config, dest)
        return m, r, False
    m1, r1 = travel_minutes(config, dest, first_leave)
    if m1 is None:
        return None, None, False
    leave = arrive - timedelta(minutes=round(m1))
    if leave - now <= timedelta(minutes=SOON_MIN):
        m, r = travel_minutes(config, dest)
        return (m, r, False) if m is not None else (m1, r1, True)
    m2, r2 = travel_minutes(config, dest, leave)
    return (m2, r2 or r1, True) if m2 is not None else (m1, r1, True)


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
        live, route, predicted = _drive_for(config, t["destination"], arrive,
                                            usual if not cal else 20.0, now)
        if cal and live is None:
            continue                        # nowhere Google can route to
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
            "predicted": bool(predicted and live is not None),
            "usual_min": None if cal else round(usual),
            "from_calendar": cal,
            "leave_at": leave.strftime("%H:%M"),
            "leave_in_min": int((leave - now).total_seconds() // 60),
        })
    out.sort(key=lambda x: x["leave_in_min"])
    return out
