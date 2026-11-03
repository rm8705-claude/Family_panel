"""The acceptance-critical timezone behaviour.

- All-day Google (ICS) events must land on the correct Brisbane date — the
  classic off-by-one trap when a DATE value gets stored as midnight UTC.
- Timed UTC events must convert to Australia/Brisbane (+10:00, no DST).
- Weekly recurrences with exceptions must expand correctly for ±8 weeks.
"""
from datetime import timedelta

import db
import jobs


def make_ics(*vevents: str) -> bytes:
    body = "\n".join(vevents)
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//test//EN\n"
        f"{body}\nEND:VCALENDAR\n"
    ).encode()


def vevent(uid: str, lines: str) -> str:
    return f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:20260101T000000Z\n{lines}\nEND:VEVENT"


def agenda(client, from_d, to_d):
    r = client.get(f"/api/agenda?from={from_d}&to={to_d}")
    assert r.status_code == 200
    return r.get_json()["events"]


def near_date(days_ahead: int):
    return db.local_today() + timedelta(days=days_ahead)


def test_all_day_event_lands_on_correct_brisbane_date(client):
    d = near_date(10)
    nxt = d + timedelta(days=1)
    ics = make_ics(vevent("allday1", (
        f"DTSTART;VALUE=DATE:{d:%Y%m%d}\n"
        f"DTEND;VALUE=DATE:{nxt:%Y%m%d}\n"
        "SUMMARY:Pupil free day")))
    jobs.store_feed_events("Test", "kid", ics)

    events = agenda(client, d.isoformat(), d.isoformat())
    assert len(events) == 1
    ev = events[0]
    assert ev["all_day"] is True
    assert ev["date"] == d.isoformat()          # NOT the day before (UTC-10h trap)
    assert ev["end_date"] == d.isoformat()      # single day, inclusive
    # And it must NOT bleed onto the previous local date
    prev = (d - timedelta(days=1)).isoformat()
    assert agenda(client, prev, prev) == []


def test_multi_day_all_day_end_date_inclusive(client):
    d = near_date(20)
    ics = make_ics(vevent("camp1", (
        f"DTSTART;VALUE=DATE:{d:%Y%m%d}\n"
        f"DTEND;VALUE=DATE:{d + timedelta(days=3):%Y%m%d}\n"   # exclusive per RFC
        "SUMMARY:School camp")))
    jobs.store_feed_events("Test", "kid", ics)
    events = agenda(client, d.isoformat(), d.isoformat())
    assert events[0]["end_date"] == (d + timedelta(days=2)).isoformat()


def test_timed_utc_event_converts_to_brisbane(client):
    # 21:30Z == 07:30 next day in Brisbane (+10, no DST)
    d = near_date(5)
    utc_day = d - timedelta(days=1)
    ics = make_ics(vevent("timed1", (
        f"DTSTART:{utc_day:%Y%m%d}T213000Z\n"
        f"DTEND:{utc_day:%Y%m%d}T220000Z\n"
        "SUMMARY:School drop-off")))
    jobs.store_feed_events("Test", "kid", ics)
    events = agenda(client, d.isoformat(), d.isoformat())
    assert len(events) == 1
    assert events[0]["date"] == d.isoformat()
    assert events[0]["start"] == "07:30"
    assert events[0]["end"] == "08:00"
    assert events[0]["all_day"] is False


def test_weekly_recurrence_with_exception_over_8_weeks(client):
    # Weekly swim lesson anchored ~7 weeks back, with one EXDATE, checked
    # across a ±8 week window.
    anchor = near_date(-49)
    skipped = anchor + timedelta(days=28)
    ics = make_ics(vevent("swim1", (
        f"DTSTART;TZID=Australia/Brisbane:{anchor:%Y%m%d}T153000\n"
        f"DTEND;TZID=Australia/Brisbane:{anchor:%Y%m%d}T161500\n"
        "RRULE:FREQ=WEEKLY;COUNT=30\n"
        f"EXDATE;TZID=Australia/Brisbane:{skipped:%Y%m%d}T153000\n"
        "SUMMARY:Swim lesson")))
    jobs.store_feed_events("Test", "kid", ics)

    lo = anchor - timedelta(days=7)
    hi = anchor + timedelta(days=56)
    events = agenda(client, lo.isoformat(), hi.isoformat())
    dates = [e["date"] for e in events]
    expected = [(anchor + timedelta(days=7 * i)).isoformat()
                for i in range(9) if (anchor + timedelta(days=7 * i)) != skipped]
    assert dates == expected
    assert skipped.isoformat() not in dates
    assert all(e["start"] == "15:30" for e in events)


def test_recurrence_moved_instance(client):
    # One instance moved via RECURRENCE-ID must appear at its new time only.
    anchor = near_date(7)
    moved_from = anchor + timedelta(days=7)
    ics = make_ics(
        vevent("gym1", (
            f"DTSTART;TZID=Australia/Brisbane:{anchor:%Y%m%d}T090000\n"
            f"DTEND;TZID=Australia/Brisbane:{anchor:%Y%m%d}T100000\n"
            "RRULE:FREQ=WEEKLY;COUNT=3\n"
            "SUMMARY:Gymnastics")),
        vevent("gym1", (
            f"RECURRENCE-ID;TZID=Australia/Brisbane:{moved_from:%Y%m%d}T090000\n"
            f"DTSTART;TZID=Australia/Brisbane:{moved_from:%Y%m%d}T140000\n"
            f"DTEND;TZID=Australia/Brisbane:{moved_from:%Y%m%d}T150000\n"
            "SUMMARY:Gymnastics (moved)")))
    jobs.store_feed_events("Test", "kid", ics)
    events = agenda(client, moved_from.isoformat(), moved_from.isoformat())
    assert len(events) == 1
    assert events[0]["start"] == "14:00"


def test_resync_is_idempotent(client):
    d = near_date(3)
    ics = make_ics(vevent("idem1", (
        f"DTSTART;VALUE=DATE:{d:%Y%m%d}\n"
        f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}\n"
        "SUMMARY:Bin night")))
    jobs.store_feed_events("Test", "family", ics)
    jobs.store_feed_events("Test", "family", ics)
    assert len(agenda(client, d.isoformat(), d.isoformat())) == 1


def test_local_event_roundtrip_and_midnight_cross(client):
    d = near_date(2)
    r = client.post("/api/events", json={
        "title": "Dinner with the Smiths", "person_id": "family",
        "date": d.isoformat(), "start": "19:00", "end": "23:30"})
    assert r.status_code == 201
    ev = r.get_json()
    assert ev["date"] == d.isoformat() and ev["start"] == "19:00"
    assert ev["editable"] is True

    # end <= start rolls to next local day
    r2 = client.post("/api/events", json={
        "title": "Night shift", "date": d.isoformat(),
        "start": "22:00", "end": "06:00"})
    ev2 = r2.get_json()
    assert ev2["end_date"] == (d + timedelta(days=1)).isoformat()

    # patch: flip to all-day, then delete
    r3 = client.patch(f"/api/events/{ev['id']}", json={"all_day": True})
    assert r3.get_json()["all_day"] is True
    assert r3.get_json()["date"] == d.isoformat()
    assert client.delete(f"/api/events/{ev['id']}").status_code == 200
    assert client.delete(f"/api/events/{ev['id']}").status_code == 404
