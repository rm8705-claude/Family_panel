"""Chores that repeat fortnightly or monthly, and pasting a list of them."""
import sqlite3
from datetime import date

import app as app_module
import db

PIN = {"X-Admin-Pin": "0000"}
PEOPLE = [{"id": "rohan", "name": "Rohan", "colour": "#4A3575"},
          {"id": "sam", "name": "Sam", "colour": "#B1A1D4"}]


def row(**kw):
    base = {"days": "SA", "repeat": "weekly", "anchor": None}
    base.update(kw)
    return base


class Row(dict):
    def keys(self):
        return super().keys()


def due(d, **kw):
    return app_module.chore_due(Row(row(**kw)), d)


def test_weekly_fortnightly_monthly():
    sat1, sat2, sat3 = date(2026, 11, 7), date(2026, 11, 14), date(2026, 11, 21)
    assert due(sat1) and due(sat2) and not due(date(2026, 11, 8))
    # anchored on the week of 2 Nov: on, off, on
    assert due(sat1, repeat="fortnightly", anchor="2026-11-02")
    assert not due(sat2, repeat="fortnightly", anchor="2026-11-02")
    assert due(sat3, repeat="fortnightly", anchor="2026-11-02")
    # the partner chore a week later takes the other weeks
    assert due(sat2, repeat="fortnightly", anchor="2026-11-09")
    # monthly: first Saturday only
    assert due(sat1, repeat="monthly") and not due(sat2, repeat="monthly")


def test_paste_a_list(client, monkeypatch):
    monkeypatch.setitem(app_module.CONFIG, "people", PEOPLE)
    text = "\n".join([
        "Take out bins | Rohan | Mon | evening | weekly",
        "Change kids' sheets | sam | Sun | morning | fortnightly next",
        "Cobwebs | Rohan | Sun | afternoon | monthly",
        "Clean shower | Sam | Thursday",
        "# a comment",
        "Mystery | Nobody | Mon",
        "Bad days | Rohan | Funday",
    ])
    r = client.post("/api/chores/bulk", json={"text": text, "dry_run": True}, headers=PIN).get_json()
    assert r["created"] == 0 and r["errors"] == 2
    ok = [x["chore"] for x in r["rows"] if x["ok"]]
    assert ok[0]["days"] == "MO" and ok[0]["stars"] == 0
    assert ok[1]["repeat"] == "fortnightly" and ok[1]["person_id"] == "sam"
    assert date.fromisoformat(ok[1]["anchor"]).weekday() == 0
    assert ok[3]["slot"] == "evening" and ok[3]["days"] == "TH"
    assert client.get("/api/chores").get_json()["chores"] == []      # dry run saved nothing

    r = client.post("/api/chores/bulk", json={"text": text}, headers=PIN).get_json()
    assert r["created"] == 4
    saved = {c["title"]: c for c in client.get("/api/chores").get_json()["chores"]}
    assert saved["Cobwebs"]["repeat"] == "monthly"
    # needs the PIN
    assert client.post("/api/chores/bulk", json={"text": text}).status_code == 403


def test_today_list_skips_off_weeks(client, monkeypatch):
    monkeypatch.setitem(app_module.CONFIG, "people", PEOPLE)
    client.post("/api/chores", headers=PIN, json={
        "title": "Mop", "person_id": "rohan", "days": "SA", "slot": "afternoon",
        "repeat": "fortnightly", "anchor": "2026-11-02", "stars": 0})
    on = client.get("/api/chores/today?date=2026-11-07").get_json()
    off = client.get("/api/chores/today?date=2026-11-14").get_json()
    titles = lambda d: [c["title"] for p in d["people"] for c in p["chores"]]   # noqa: E731
    assert titles(on) == ["Mop"] and titles(off) == []


def test_old_database_gains_the_repeat_columns(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO settings VALUES('schema_version', '2');
        CREATE TABLE chores(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            person_id TEXT NOT NULL, days TEXT NOT NULL, slot TEXT NOT NULL, stars INTEGER NOT NULL DEFAULT 1);
        INSERT INTO chores(title, person_id, days, slot) VALUES('Old', 'rohan', 'MO', 'evening');
        CREATE TABLE recipes(id INTEGER PRIMARY KEY, title TEXT, source_url TEXT, meta TEXT);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(db._local, "conn", None)      # a fresh connection to the old file
    db.init_db({"people": []})
    db._local.conn.close()
    c = sqlite3.connect(path)
    cols = {r[1] for r in c.execute("PRAGMA table_info(chores)")}
    assert {"repeat", "anchor"} <= cols
    assert c.execute("SELECT repeat FROM chores").fetchone()[0] == "weekly"
