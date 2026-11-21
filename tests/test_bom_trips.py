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


def test_travel_minutes_falls_back_when_waze_missing(monkeypatch):
    trips._cache.clear()
    monkeypatch.setattr(trips.ha, "_api_base", lambda: ("http://ha/api", "t"))

    def boom(*a, **k):
        raise ConnectionError("no waze")
    monkeypatch.setattr(trips.requests, "post", boom)
    assert trips.travel_minutes({}, "Somewhere") == (None, None)


def test_trips_api_round_trip(client):
    db.set_json_setting(trips.KEY, [])
    r = client.put("/api/trips", json={"label": "Daycare pick-up", "destination": "Daycare",
                                       "time": "17:00", "days": [0, 2], "minutes": 10})
    assert r.status_code == 200
    tid = r.get_json()["trip"]["id"]
    assert client.get("/api/trips").get_json()["trips"][0]["label"] == "Daycare pick-up"
    assert client.delete(f"/api/trips/{tid}").status_code == 200
    assert client.put("/api/trips", json={"label": "x"}).status_code == 400
