"""SQLite helpers for Family Panel.

Plain sqlite3, WAL mode, schema created/migrated on boot. All timestamps are
stored as UTC ISO-8601 strings; conversion to Australia/Brisbane happens at the
API layer (see app.py / tz helpers below).
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Australia/Brisbane")
UTC = timezone.utc

DB_PATH = os.environ.get(
    "PANEL_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "panel.db")
)

_local = threading.local()

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS people(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, colour TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS ics_cache(
    uid TEXT NOT NULL, feed TEXT NOT NULL, person_id TEXT,
    title TEXT, location TEXT,
    start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, all_day INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(uid, feed));

CREATE TABLE IF NOT EXISTS events_local(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, person_id TEXT,
    start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT, notes TEXT,
    countdown INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual','magic_import')),
    import_id INTEGER NULL);

CREATE TABLE IF NOT EXISTS magic_imports(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT, from_addr TEXT, subject TEXT,
    body_excerpt TEXT, extracted_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','dismissed')));

CREATE TABLE IF NOT EXISTS chores(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, person_id TEXT NOT NULL,
    days TEXT NOT NULL,                       -- "MO,TU,WE,TH,FR"
    slot TEXT NOT NULL CHECK(slot IN ('morning','afternoon','evening')),
    stars INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS chore_done(
    chore_id INTEGER NOT NULL, date TEXT NOT NULL, done_at TEXT,
    PRIMARY KEY(chore_id, date));

CREATE TABLE IF NOT EXISTS star_redemptions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL, stars INTEGER NOT NULL, note TEXT, date TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS lists(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'custom' CHECK(kind IN ('grocery','todo','custom')));

CREATE TABLE IF NOT EXISTS list_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL, text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, created_at TEXT);

CREATE TABLE IF NOT EXISTS recipes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, notes TEXT, ingredients TEXT,    -- ingredients = JSON array
    source_url TEXT,                                     -- imported recipes link home
    meta TEXT);                                          -- JSON: servings/prep/cook/image

CREATE TABLE IF NOT EXISTS meal_plan(
    date TEXT NOT NULL,
    slot TEXT NOT NULL CHECK(slot IN ('dinner','lunch','other')),
    recipe_id INTEGER NULL, free_text TEXT,
    PRIMARY KEY(date, slot));

CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_ics_start ON ics_cache(start_utc);
CREATE INDEX IF NOT EXISTS idx_local_start ON events_local(start_utc);
CREATE INDEX IF NOT EXISTS idx_items_list ON list_items(list_id);
"""


def connect() -> sqlite3.Connection:
    """One connection per thread (Flask + APScheduler both hit the DB)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        _local.conn = conn
    return conn


def init_db(config: dict) -> None:
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        cur = conn.execute("SELECT value FROM settings WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            conn.execute("INSERT INTO settings(key, value) VALUES('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
        else:
            version = int(row["value"])
            if version < 2:
                # v2: imported-recipe columns (CREATE above already has them on
                # fresh databases; existing ones need the ALTERs)
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(recipes)")}
                if "source_url" not in cols:
                    conn.execute("ALTER TABLE recipes ADD COLUMN source_url TEXT")
                if "meta" not in cols:
                    conn.execute("ALTER TABLE recipes ADD COLUMN meta TEXT")
            conn.execute("UPDATE settings SET value=? WHERE key='schema_version'",
                         (str(SCHEMA_VERSION),))

        # Mirror people from config: config is the source of truth for id/name/colour.
        for p in config.get("people", []):
            conn.execute(
                "INSERT INTO people(id, name, colour) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, colour=excluded.colour",
                (p["id"], p["name"], p["colour"]))

        # Ensure the default lists exist exactly once.
        if not conn.execute("SELECT 1 FROM lists WHERE kind='grocery'").fetchone():
            conn.execute("INSERT INTO lists(name, kind) VALUES('Groceries','grocery')")
        if not conn.execute("SELECT 1 FROM lists WHERE kind='todo'").fetchone():
            conn.execute("INSERT INTO lists(name, kind) VALUES('To do','todo')")


# ---------------------------------------------------------------- settings kv

def get_setting(key: str, default=None):
    row = connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value) -> None:
    conn = connect()
    with conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_json_setting(key: str, default=None):
    raw = get_setting(key)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def set_json_setting(key: str, value) -> None:
    set_setting(key, json.dumps(value))


def mark_sync(job: str, ok: bool = True, detail: str = "") -> None:
    """Record a job run for /api/status."""
    set_json_setting(f"sync:{job}", {
        "at": utc_now_iso(), "ok": ok, "detail": detail})


# ---------------------------------------------------------------- time helpers

def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_iso(dt: datetime) -> str:
    """Any aware datetime -> canonical UTC ISO string."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s: str) -> datetime:
    """Parse the canonical stored form back to an aware UTC datetime."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def local_date_to_utc_iso(d: date) -> str:
    """Brisbane-local midnight of a calendar date, stored as UTC.

    This is how all-day events are stored so that converting back to local
    time always lands on the correct Brisbane date (the classic all-day
    off-by-one trap: naively storing midnight UTC shifts the date +10 h).
    """
    return to_utc_iso(datetime.combine(d, time.min, tzinfo=LOCAL_TZ))


def utc_iso_to_local(s: str) -> datetime:
    return parse_utc(s).astimezone(LOCAL_TZ)


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def event_local_shape(row, source: str, feed: str | None = None) -> dict:
    """Common JSON shape for one event (ICS or local), localised for the UI."""
    start_l = utc_iso_to_local(row["start_utc"])
    end_l = utc_iso_to_local(row["end_utc"])
    all_day = bool(row["all_day"])
    out = {
        "id": (f"ics:{feed}:{row['uid']}" if source == "ics" else row["id"]),
        "source": source,
        "title": row["title"],
        "person_id": row["person_id"],
        "location": row["location"],
        "all_day": all_day,
        "date": start_l.strftime("%Y-%m-%d"),
        "end_date": (end_l - timedelta(seconds=1)).strftime("%Y-%m-%d") if all_day
                    else end_l.strftime("%Y-%m-%d"),
        "start": None if all_day else start_l.strftime("%H:%M"),
        "end": None if all_day else end_l.strftime("%H:%M"),
        "start_utc": row["start_utc"],
        "end_utc": row["end_utc"],
    }
    if source == "local":
        out["notes"] = row["notes"]
        out["countdown"] = bool(row["countdown"])
        out["editable"] = True
    else:
        out["feed"] = feed
        out["editable"] = False
    return out
