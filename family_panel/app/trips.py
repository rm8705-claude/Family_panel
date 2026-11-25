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
    early = t.get("early")
    try:
        early = None if early in (None, "") else max(0, min(60, int(early)))
    except (TypeError, ValueError):
        early = None
    return {"id": str(t.get("id") or uuid.uuid4().hex[:10]), "label": label, "early": early,
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
# The route line Google last drew to each destination, for the traffic map.
_polylines: dict = {}
# Distance and the no-traffic drive for each destination, for the Maps sheet.
_extras: dict = {}
# What the panel last heard from Google, for the Trips sheet's status line.
traffic_status = {"ok": None, "error": None, "at": None}


# ------------------------------------------------------------------- home
# Where "home" is, for trips, Maps and the traffic map — and for the weather
# and BOM warnings while their own lat/lon is still the shipped default:
#   1. home_override (add-on option) — "lat,lon", for a home that isn't
#      Home Assistant's
#   2. Home Assistant's zone.home — the house HA already knows, re-read hourly
#   3. the weather lat/lon from the add-on options
DEFAULT_LATLON = (-27.48, 152.98)
_home = {"at": 0.0, "latlon": None, "error": None}
HOME_CACHE_S = 3600
_LATLON = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*")


def _ha_home() -> tuple[float, float] | None:
    if _home["latlon"] and time.time() - _home["at"] < HOME_CACHE_S:
        return _home["latlon"]
    try:
        import ha
        base, token = ha._api_base()
        r = requests.get(f"{base}/states/zone.home", headers=ha._headers(token), timeout=10)
        r.raise_for_status()
        a = (r.json() or {}).get("attributes") or {}
        latlon = (float(a["latitude"]), float(a["longitude"]))
        _home.update(at=time.time(), latlon=latlon, error=None)
    except Exception as e:                  # noqa: BLE001 - keep the last good one
        _home.update(error=str(e)[:200], at=time.time() - HOME_CACHE_S + 300)  # retry in 5 min
    return _home["latlon"]


def home(config: dict) -> dict:
    """{lat, lon, source} — see the order above."""
    m = _LATLON.fullmatch(str(config.get("home_override") or ""))
    if m:
        return {"lat": float(m.group(1)), "lon": float(m.group(2)), "source": "home_override"}
    ha_home = _ha_home()
    if ha_home:
        return {"lat": ha_home[0], "lon": ha_home[1], "source": "zone.home"}
    w = config.get("weather") or {}
    return {"lat": float(w.get("lat", DEFAULT_LATLON[0])), "lon": float(w.get("lon", DEFAULT_LATLON[1])),
            "source": "weather"}


def weather_spot(config: dict) -> dict:
    """The weather/BOM location: the add-on's weather lat/lon if someone set
    it, otherwise home — so a fresh install isn't forecasting for Toowong."""
    w = dict(config.get("weather") or {})
    lat, lon = w.get("lat"), w.get("lon")
    untouched = lat is None or lon is None or (round(float(lat), 4), round(float(lon), 4)) == DEFAULT_LATLON
    if untouched:
        h = home(config)
        if h["source"] != "weather":
            w.update(lat=h["lat"], lon=h["lon"])
    return w


def map_config(config: dict) -> dict:
    """What the tablet needs to draw the traffic map: a key for Google's Maps
    JavaScript API (google_maps_browser_key if set, else the same key) and
    the house's spot. The map runs in the browser, so this key does reach
    the tablet — a separate browser key restricted to the panel's address
    is the tidy way to do it."""
    key = str(config.get("google_maps_browser_key") or "").strip() or _api_key(config)
    return {"key": key or None, "home": home(config)}


def _api_key(config: dict) -> str:
    return str(config.get("google_maps_api_key") or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


def _place(dest: str) -> dict:
    """A "lat,lon" pair goes as a point; anything else as an address Google
    looks up itself (street address, or a place name like a swim centre)."""
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", dest or "")
    if m:
        return {"location": {"latLng": {"latitude": float(m.group(1)), "longitude": float(m.group(2))}}}
    return {"address": dest}


def _pkey(destination: str, origin: str | None) -> str:
    return destination if not origin else f"{origin}>{destination}"


def travel_minutes(config: dict, destination: str, depart: datetime | None = None,
                   origin: str | None = None) -> tuple[float | None, str | None]:
    """(minutes, route name) by car from Google Maps (Routes API): in traffic
    right now, or Google's predicted traffic when `depart` is a future time.
    From home, or from `origin` (an address or "lat,lon") when given.
    (None, None) with no key or no answer."""
    origin = (origin or "").strip() or None
    pkey = _pkey(destination, origin)
    ckey = pkey if depart is None else f"{pkey}@{depart.strftime('%Y-%m-%d %H:%M')}"
    hit = _cache.get(ckey)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1], hit[2]
    key = _api_key(config)
    if not key:
        traffic_status.update(ok=None, error="No google_maps_api_key in the add-on options",
                              at=db.utc_now_iso())
        return None, None
    h = home(config)
    body = {
        "origin": _place(origin) if origin else
                  {"location": {"latLng": {"latitude": h["lat"], "longitude": h["lon"]}}},
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
            "X-Goog-FieldMask": "routes.duration,routes.staticDuration,routes.description,"
                                "routes.distanceMeters,routes.polyline.encodedPolyline",
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
        poly = ((routes[0].get("polyline") or {}).get("encodedPolyline"))
        if poly:
            _polylines[pkey] = poly
        static = routes[0].get("staticDuration")
        _extras[pkey] = {
            "distance_km": round(float(routes[0].get("distanceMeters") or 0) / 1000, 1) or None,
            "no_traffic_min": round(float(str(static).rstrip("s")) / 60) if static else None,
        }
        traffic_status.update(ok=True, error=None, at=db.utc_now_iso())
    except Exception as e:                  # noqa: BLE001 - fall back to usual minutes
        log.info("google routes %s: %s", destination, e)
        traffic_status.update(ok=False, error=str(e)[:240], at=db.utc_now_iso())
    _cache[ckey] = (time.time(), mins, name)
    return mins, name


def drive_to(config: dict, destination: str, arrive: datetime | None = None,
             origin: str | None = None) -> dict:
    early = early_min(config) if arrive is not None else 0
    target = arrive - timedelta(minutes=early) if arrive is not None else None
    out = _drive_to(config, destination, target, origin)
    out.update(early_min=early, target=target.strftime("%H:%M") if target else None)
    return out


def _drive_to(config: dict, destination: str, arrive: datetime | None = None,
              origin: str | None = None) -> dict:
    """For the event sheet's Drive time key. With an arrive-by in the future,
    the drive is Google's prediction for leaving at about the right time:
    guess a departure, ask, and re-ask once from the corrected departure."""
    now = datetime.now(db.LOCAL_TZ)
    if arrive is None or arrive <= now + timedelta(minutes=5):
        mins, route = travel_minutes(config, destination, origin=origin)
        leave = (arrive - timedelta(minutes=mins)) if (arrive and mins is not None) else None
        predicted = False
    else:
        mins, route = travel_minutes(config, destination, arrive - timedelta(minutes=30), origin=origin)
        if mins is not None:
            m2, r2 = travel_minutes(config, destination, arrive - timedelta(minutes=round(mins)),
                                    origin=origin)
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
        "polyline": _polylines.get(_pkey(destination, (origin or "").strip() or None)),
        **(_extras.get(_pkey(destination, (origin or "").strip() or None))
           or {"distance_km": None, "no_traffic_min": None}),
        "from": (origin or "").strip() or "Home",
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


def early_min(config: dict, trip: dict | None = None) -> int:
    """How long before the time to aim to arrive: the trip's own setting, else
    the arrive_early_min add-on option, else 10 minutes — a rule of thumb for
    parking, the walk in and the kids' shoes."""
    for v in ((trip or {}).get("early"), config.get("arrive_early_min")):
        if v not in (None, ""):
            try:
                return max(0, min(60, int(v)))
            except (TypeError, ValueError):
                pass
    return 10


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
        early = early_min(config, t)
        # aim to be there `early` minutes before the trip / event time
        arrive = now.replace(hour=h, minute=m, second=0, microsecond=0) - timedelta(minutes=early)
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
            "target": arrive.strftime("%H:%M"), "early_min": early,
            "drive_min": round(drive), "live": live is not None, "route": route,
            "predicted": bool(predicted and live is not None),
            # beside a forecast, what the same drive takes in traffic right now
            "now_min": (round(n) if (n := (travel_minutes(config, t["destination"])[0]
                                           if predicted and live is not None else live)) is not None
                        else None),
            "polyline": _polylines.get(t["destination"]),
            "usual_min": None if cal else round(usual),
            "from_calendar": cal,
            "leave_at": leave.strftime("%H:%M"),
            "leave_in_min": int((leave - now).total_seconds() // 60),
        })
    out.sort(key=lambda x: x["leave_in_min"])
    return out


# ------------------------------------------------- spoken on the Sonos
# "Work: leave in 10 minutes — 23 minutes in traffic", then "Time to leave
# for work" at the leave time, over whatever is playing (ha.announce, which
# keeps to the night volume cap and the sonos_tts engine). Each trip is said
# once per point per day. One switch turns the lot off (the speaker key in
# the rail, and Settings > Trips); never inside the screen's sleep hours.
ANNOUNCE_KEY = "announce:leave"
ANNOUNCE_POINTS = (10, 0)            # minutes before leaving
ANNOUNCE_WINDOW = 3                  # say it if we're up to this late for a point


def announce_enabled() -> bool:
    return (db.get_json_setting(ANNOUNCE_KEY) or {}).get("enabled", True)


def set_announce(enabled: bool) -> None:
    st = db.get_json_setting(ANNOUNCE_KEY) or {}
    st["enabled"] = bool(enabled)
    db.set_json_setting(ANNOUNCE_KEY, st)


def _in_sleep(config: dict, now: datetime) -> bool:
    sc = config.get("screen") or {}
    try:
        s = _mins(sc.get("sleep", "21:00"))
        w = _mins(sc.get("wake", "05:45"))
    except (ValueError, AttributeError):
        return False
    n = now.hour * 60 + now.minute
    return (s <= n < w) if s < w else (n >= s or n < w)


def announce_speakers(config: dict) -> list[str]:
    own = [e.strip() for e in str(config.get("leave_announce_speakers") or "").split(",") if e.strip()]
    media = [t["entity"] for t in config.get("ha_tiles", []) or []
             if t.get("type") == "media" and t.get("allow_action") and t.get("entity")]
    return [e for e in own if e in media] if own else media


def announce_message(config: dict, t: dict, point: int) -> str:
    who = next((p.get("name") for p in config.get("people", []) or []
                if p.get("id") == t.get("person")), None)
    lead = f"{who}, " if who else ""
    if point == 0:
        msg = f"{lead}time to leave for {t['label']}."
    else:
        msg = f"{lead}{t['label']}: leave in {point} minutes."
    if t.get("live"):
        msg += f" {t['drive_min']} minutes in traffic."
    return msg


def announce_due(config: dict, events: list[dict] | None = None, now: datetime | None = None,
                 send=None) -> list[str]:
    """Say whatever is due now; returns what was said."""
    now = now or datetime.now(db.LOCAL_TZ)
    if not announce_enabled() or _in_sleep(config, now):
        return []
    speakers = announce_speakers(config)
    if not speakers:
        return []
    if send is None:
        import ha
        send = ha.announce
    st = db.get_json_setting(ANNOUNCE_KEY) or {}
    today = now.strftime("%Y-%m-%d")
    done = set(st.get("done", [])) if st.get("date") == today else set()
    said = []
    for t in upcoming(config, now, events):
        n = t["leave_in_min"]
        for point in ANNOUNCE_POINTS:
            key = f"{t['id']}@{point}"
            if key in done or not (point - ANNOUNCE_WINDOW <= n <= point):
                continue
            msg = announce_message(config, t, point)
            send(config, speakers, msg)
            done.add(key)
            said.append(msg)
            break                     # one line per trip per run
    st.update(date=today, done=sorted(done))
    db.set_json_setting(ANNOUNCE_KEY, st)
    return said
