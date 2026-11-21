"""BOM warnings for the house's spot, and leave-by trips with Waze times."""
from datetime import datetime

import pytest

import bom
import db
import trips


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
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest: (28.0, "M3"))
    now = datetime(2026, 11, 20, 7, 50, tzinfo=db.LOCAL_TZ)     # a Friday
    up = trips.upcoming({}, now)
    assert [u["label"] for u in up] == ["Work"]
    assert up[0]["drive_min"] == 28 and up[0]["live"] is True
    assert up[0]["leave_at"] == "08:02" and up[0]["leave_in_min"] == 12
    # long after leaving, it's gone
    assert trips.upcoming({}, datetime(2026, 11, 20, 8, 25, tzinfo=db.LOCAL_TZ)) == []


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
    assert seen["body"]["routingPreference"] == "TRAFFIC_AWARE"
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
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest: (18.0, "Gympie Rd"))
    now = datetime(2026, 11, 21, 9, 0, tzinfo=db.LOCAL_TZ)          # Saturday
    events = [
        _ev("Swimming", "09:45", "Chermside Aquatic Centre, 375 Hamilton Rd", person="kid"),
        _ev("Standup", "09:30", "https://zoom.us/j/123"),             # not a place
        _ev("Lunch", "12:30", "Home"),                                 # not a place
        _ev("Dentist", "15:00", "1 Example St, Brisbane"),            # too far off yet
    ]
    up = trips.upcoming({}, now, events)
    assert [u["label"] for u in up] == ["Swimming"]
    s = up[0]
    assert s["from_calendar"] and s["person"] == "kid"
    assert s["leave_at"] == "09:27" and s["leave_in_min"] == 27 and s["usual_min"] is None


def test_calendar_trip_skipped_without_waze_or_when_turned_off(monkeypatch):
    db.set_json_setting(trips.KEY, [])
    now = datetime(2026, 11, 21, 9, 0, tzinfo=db.LOCAL_TZ)
    ev = [_ev("Soccer", "09:45", "Bishop Park, Nundah")]
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest: (None, None))
    assert trips.upcoming({}, now, ev) == []
    monkeypatch.setattr(trips, "travel_minutes", lambda cfg, dest: (15.0, None))
    assert trips.upcoming({"calendar_trips": False}, now, ev) == []
    # a weekly trip at the same time wins over the event
    trips.save({"label": "Soccer", "destination": "Bishop Park", "time": "09:45",
                "days": [5], "minutes": 15})
    assert [u["from_calendar"] for u in trips.upcoming({}, now, ev)] == [False]


def test_no_route_is_an_error_not_working(monkeypatch):
    trips._cache.clear()

    class R:
        status_code = 200

        def json(self):
            return {}
    monkeypatch.setattr(trips.requests, "post", lambda *a, **k: R())
    assert trips.travel_minutes({"google_maps_api_key": "k"}, "Nowhere") == (None, None)
    assert trips.traffic_status["ok"] is False and "no driving route" in trips.traffic_status["error"]
