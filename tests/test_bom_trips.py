"""BOM warnings for the house's spot, and leave-by trips with Waze times."""
from datetime import datetime

import pytest

import bom
import db
import trips

# the older timing tests are about drive times, not the early-arrival buffer
NO_EARLY = {"arrive_early_min": 0}


class Resp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_geohash_matches_the_reference_example():
    assert bom.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"


def test_refresh_keeps_live_warnings_and_fetches_their_text(monkeypatch):
    def get(url, headers=None, timeout=None):
        if url.endswith("/warnings"):
            return Resp({"data": [
                {"id": "IDQ1", "title": "Severe Thunderstorm Warning for Brisbane",
                 "short_title": "Severe Thunderstorm Warning", "warning_group_type": "major",
                 "phase": "new", "issue_time": "2026-11-21T03:00:00Z", "expiry_time": "2999-01-01T00:00:00Z"},
                {"id": "IDQ2", "title": "Old one", "phase": "cancelled"},
                {"id": "IDQ3", "title": "Expired", "expiry_time": "2000-01-01T00:00:00Z"}]})
        return Resp({"data": {"message": "<p>Damaging winds.</p><p>Stay indoors &amp; safe.</p>"}})
    monkeypatch.setattr(bom.requests, "get", get)
    items = bom.refresh({"lat": -27.48, "lon": 152.98})
    assert [i["id"] for i in items] == ["IDQ1"]
    assert items[0]["severity"] == "major"
    assert items[0]["message"] == "Damaging winds.\nStay indoors & safe."
    assert bom.latest()["items"][0]["id"] == "IDQ1"


def test_trip_validation():
    with pytest.raises(ValueError):
        trips.save({"label": "Work", "destination": "x", "time": "8am", "days": [0]})
    with pytest.raises(ValueError):
        trips.save({"label": "Work", "destination": "x", "time": "08:30", "days": []})
    t = trips.save({"label": "Work", "destination": "1 Example St", "time": "08:30",
                    "days": [0, 1, 2, 3, 4], "minutes": 25})
    assert t["id"] and trips.all_trips()[0]["minutes"] == 25
    trips.delete(t["id"])
    assert trips.all_trips() == []


def test_upcoming_uses_live_waze_time(monkeypatch):
    db.set_json_setting(trips.KEY, [])
    trips.save({"label": "Work", "destination": "1 Example St", "time": "08:30",
                "days": [4], "minutes": 20})                   # Fridays
    trips.save({"label": "Soccer", "destination": "Oval", "time": "08:30",
                "days": [5], "minutes": 15})                   # Saturdays: not today
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest, depart=None, **kw: (28.0, "M3"))
    now = datetime(2026, 11, 20, 7, 50, tzinfo=db.LOCAL_TZ)     # a Friday
    up = trips.upcoming(NO_EARLY, now)
    assert [u["label"] for u in up] == ["Work"]
    assert up[0]["drive_min"] == 28 and up[0]["live"] is True
    assert up[0]["leave_at"] == "08:02" and up[0]["leave_in_min"] == 12
    # long after leaving, it's gone
    assert trips.upcoming(NO_EARLY, datetime(2026, 11, 20, 8, 25, tzinfo=db.LOCAL_TZ)) == []


def test_travel_minutes_without_a_key_is_none():
    trips._cache.clear()
    assert trips.travel_minutes({}, "Somewhere") == (None, None)
    assert "google_maps_api_key" in trips.traffic_status["error"]


def test_travel_minutes_asks_google_routes_with_traffic(monkeypatch):
    trips._cache.clear()
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"routes": [{"duration": "1380s", "description": "M3"}]}

    def post(url, json=None, timeout=None, headers=None):
        seen.update(url=url, body=json, headers=headers)
        return R()
    monkeypatch.setattr(trips.requests, "post", post)
    cfg = {"google_maps_api_key": "k", "weather": {"lat": -27.4, "lon": 153.0}}
    assert trips.travel_minutes(cfg, "1 Example St, Brisbane") == (23.0, "M3")
    assert seen["headers"]["X-Goog-Api-Key"] == "k"
    assert seen["body"]["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"
    assert seen["body"]["destination"] == {"address": "1 Example St, Brisbane"}
    assert trips.traffic_status["ok"] is True
    # coordinates go as a point, not an address
    assert trips._place("-27.47, 153.02")["location"]["latLng"]["longitude"] == 153.02


def test_google_error_is_reported(monkeypatch):
    trips._cache.clear()

    class R:
        status_code = 403
        text = "denied"

        def json(self):
            return {"error": {"message": "Routes API has not been used in project 123"}}
    monkeypatch.setattr(trips.requests, "post", lambda *a, **k: R())
    assert trips.travel_minutes({"google_maps_api_key": "k"}, "X") == (None, None)
    assert trips.traffic_status["ok"] is False
    assert "Routes API" in trips.traffic_status["error"]


def test_trips_api_round_trip(client):
    db.set_json_setting(trips.KEY, [])
    r = client.put("/api/trips", json={"label": "Daycare pick-up", "destination": "Daycare",
                                       "time": "17:00", "days": [0, 2], "minutes": 10})
    assert r.status_code == 200
    tid = r.get_json()["trip"]["id"]
    assert client.get("/api/trips").get_json()["trips"][0]["label"] == "Daycare pick-up"
    assert client.delete(f"/api/trips/{tid}").status_code == 200
    assert client.put("/api/trips", json={"label": "x"}).status_code == 400


def _ev(title, start, location, **kw):
    return {"id": title, "title": title, "start": start, "date": "2026-11-21",
            "location": location, "all_day": False, "person_id": kw.get("person")}


def test_calendar_events_with_an_address_become_trips(monkeypatch):
    db.set_json_setting(trips.KEY, [])
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest, depart=None, **kw: (18.0, "Gympie Rd"))
    now = datetime(2026, 11, 21, 9, 0, tzinfo=db.LOCAL_TZ)          # Saturday
    events = [
        _ev("Swimming", "09:45", "Chermside Aquatic Centre, 375 Hamilton Rd", person="kid"),
        _ev("Standup", "09:30", "https://zoom.us/j/123"),             # not a place
        _ev("Lunch", "12:30", "Home"),                                 # not a place
        _ev("Dentist", "15:00", "1 Example St, Brisbane"),            # too far off yet
    ]
    up = trips.upcoming(NO_EARLY, now, events)
    assert [u["label"] for u in up] == ["Swimming"]
    s = up[0]
    assert s["from_calendar"] and s["person"] == "kid"
    assert s["leave_at"] == "09:27" and s["leave_in_min"] == 27 and s["usual_min"] is None


def test_calendar_trip_skipped_without_waze_or_when_turned_off(monkeypatch):
    db.set_json_setting(trips.KEY, [])
    now = datetime(2026, 11, 21, 9, 0, tzinfo=db.LOCAL_TZ)
    ev = [_ev("Soccer", "09:45", "Bishop Park, Nundah")]
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest, depart=None, **kw: (None, None))
    assert trips.upcoming(NO_EARLY, now, ev) == []
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest, depart=None, **kw: (15.0, None))
    assert trips.upcoming({"calendar_trips": False}, now, ev) == []
    # a weekly trip at the same time wins over the event
    trips.save({"label": "Soccer", "destination": "Bishop Park", "time": "09:45",
                "days": [5], "minutes": 15})
    assert [u["from_calendar"] for u in trips.upcoming(NO_EARLY, now, ev)] == [False]


def test_no_route_is_an_error_not_working(monkeypatch):
    trips._cache.clear()

    class R:
        status_code = 200

        def json(self):
            return {}
    monkeypatch.setattr(trips.requests, "post", lambda *a, **k: R())
    assert trips.travel_minutes({"google_maps_api_key": "k"}, "Nowhere") == (None, None)
    assert trips.traffic_status["ok"] is False and "no driving route" in trips.traffic_status["error"]


def test_drive_to_a_future_event_uses_predicted_traffic(monkeypatch):
    from datetime import timedelta
    asked = []

    def tm(cfg, dest, depart=None, **kw):
        asked.append(depart)
        return (25.0, "Gympie Rd")
    monkeypatch.setattr(trips, "travel_minutes", tm)
    arrive = (datetime.now(db.LOCAL_TZ) + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    d = trips.drive_to(NO_EARLY, "Bishop Park, Nundah", arrive)
    assert d["predicted"] is True and d["minutes"] == 25 and d["leave_at"] == "08:35"
    assert asked[0] == arrive - timedelta(minutes=30)          # first guess
    assert asked[1] == arrive - timedelta(minutes=25)          # corrected departure


def test_drive_endpoint_rejects_non_places(client):
    assert client.get("/api/trips/drive?dest=https://zoom.us/j/1").status_code == 400


def test_countdown_uses_the_forecast_for_when_you_leave(monkeypatch):
    """At 6:40 for an 8:30 arrive-by, the drive is Google's forecast for
    leaving ~8:00, not the empty roads at 6:40; close to leaving, it's now."""
    db.set_json_setting(trips.KEY, [])
    trips.save({"label": "Work", "destination": "1 Example St", "time": "08:30",
                "days": [4], "minutes": 20})
    asked = []

    def tm(cfg, dest, depart=None, **kw):
        asked.append(depart)
        return (15.0, "M3") if depart is None else (34.0, "M3")      # empty now, busy at 8
    monkeypatch.setattr(trips, "travel_minutes", tm)
    early = trips.upcoming(NO_EARLY, datetime(2026, 11, 20, 6, 40, tzinfo=db.LOCAL_TZ))[0]
    assert early["predicted"] and early["drive_min"] == 34 and early["leave_at"] == "07:56"
    assert asked[0].strftime("%H:%M") == "08:10" and asked[1].strftime("%H:%M") == "07:56"
    asked.clear()
    late = trips.upcoming(NO_EARLY, datetime(2026, 11, 20, 8, 5, tzinfo=db.LOCAL_TZ))[0]
    assert not late["predicted"] and late["drive_min"] == 15 and asked == [None]


def test_countdown_carries_the_live_time_and_route_line(monkeypatch):
    db.set_json_setting(trips.KEY, [])
    trips.save({"label": "Work", "destination": "1 Example St", "time": "08:30",
                "days": [4], "minutes": 20})
    trips._polylines["1 Example St"] = "abc123"

    def tm(cfg, dest, depart=None, **kw):
        return (15.0, "M3") if depart is None else (34.0, "M3")
    monkeypatch.setattr(trips, "travel_minutes", tm)
    t = trips.upcoming(NO_EARLY, datetime(2026, 11, 20, 6, 40, tzinfo=db.LOCAL_TZ))[0]
    assert t["drive_min"] == 34 and t["now_min"] == 15 and t["polyline"] == "abc123"


def test_mapconfig_prefers_the_browser_key(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "google_maps_api_key", "server-key")
    assert client.get("/api/trips/mapconfig").get_json()["key"] == "server-key"
    monkeypatch.setitem(app_module.CONFIG, "google_maps_browser_key", "browser-key")
    d = client.get("/api/trips/mapconfig").get_json()
    assert d["key"] == "browser-key" and "lat" in d["home"]


def test_drive_carries_distance_and_traffic_delay(monkeypatch):
    trips._cache.clear()

    class R:
        status_code = 200

        def json(self):
            return {"routes": [{"duration": "1380s", "staticDuration": "960s", "distanceMeters": 18420,
                                "description": "M3", "polyline": {"encodedPolyline": "xyz"}}]}
    monkeypatch.setattr(trips.requests, "post", lambda *a, **k: R())
    d = trips.drive_to({"google_maps_api_key": "k"}, "Chermside Westfield")
    assert d["minutes"] == 23 and d["no_traffic_min"] == 16 and d["distance_km"] == 18.4
    assert d["polyline"] == "xyz" and d["route"] == "M3"


def test_voice_can_stop_at_speech_to_text(monkeypatch):
    import assist
    sent = {}

    class WS:
        def send(self, m):
            import json as _j
            try:
                sent.update(_j.loads(m))
            except Exception:
                pass

        def close(self):
            pass
    monkeypatch.setattr(assist, "_resolve", lambda c: ("http://ha", "t"))
    monkeypatch.setattr(assist, "_ws_connect", lambda *a, **k: WS())
    monkeypatch.setattr(assist, "_authenticate", lambda ws, tok: None)
    monkeypatch.setattr(assist, "_run_pipeline", lambda ws, pcm, dl: {"ok": True, "heard": "Chermside"})
    assert assist.audio_query({}, b"\x00\x01" * 10, stt_only=True)["heard"] == "Chermside"
    assert sent["end_stage"] == "stt"


def test_home_comes_from_zone_home_then_override(monkeypatch):
    import ha
    trips._home.update(at=0.0, latlon=None, error=None)
    monkeypatch.setattr(ha, "_api_base", lambda: ("http://ha/api", "t"))

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"attributes": {"latitude": -27.39, "longitude": 153.03}}
    monkeypatch.setattr(trips.requests, "get", lambda *a, **k: R())
    h = trips.home({"weather": {"lat": -27.48, "lon": 152.98}})
    assert h == {"lat": -27.39, "lon": 153.03, "source": "zone.home"}
    assert trips.home({"home_override": "-27.5, 153.1"})["source"] == "home_override"
    # weather follows home while it's the shipped default, but a set one stays
    assert trips.weather_spot({"weather": {"lat": -27.48, "lon": 152.98}})["lat"] == -27.39
    assert trips.weather_spot({"weather": {"lat": -33.86, "lon": 151.2}})["lat"] == -33.86
    trips._home.update(at=0.0, latlon=None, error=None)


def test_lookup_from_somewhere_else(monkeypatch):
    trips._cache.clear()
    seen = {}

    class R:
        status_code = 200

        def json(self):
            return {"routes": [{"duration": "600s", "distanceMeters": 5000}]}

    def post(url, json=None, timeout=None, headers=None):
        seen.update(json)
        return R()
    monkeypatch.setattr(trips.requests, "post", post)
    d = trips.drive_to({"google_maps_api_key": "k"}, "Chermside", origin="Nundah QLD")
    assert seen["origin"] == {"address": "Nundah QLD"} and d["from"] == "Nundah QLD" and d["minutes"] == 10


def test_leave_time_aims_ten_minutes_early_by_default(monkeypatch):
    db.set_json_setting(trips.KEY, [])
    trips.save({"label": "Work", "destination": "1 Example St", "time": "08:30",
                "days": [4], "minutes": 20})
    trips.save({"label": "Soccer", "destination": "Oval", "time": "08:45",
                "days": [4], "minutes": 15, "early": 20})
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest, depart=None, **kw: (20.0, None))
    up = {u["label"]: u for u in trips.upcoming({}, datetime(2026, 11, 20, 7, 30, tzinfo=db.LOCAL_TZ))}
    assert up["Work"]["leave_at"] == "08:00" and up["Work"]["target"] == "08:20"
    assert up["Work"]["early_min"] == 10 and up["Work"]["arrive"] == "08:30"
    assert up["Soccer"]["leave_at"] == "08:05" and up["Soccer"]["early_min"] == 20     # its own setting
    no = trips.upcoming({"arrive_early_min": 0}, datetime(2026, 11, 20, 7, 30, tzinfo=db.LOCAL_TZ))
    assert {u["label"]: u["leave_at"] for u in no}["Work"] == "08:10"
