"""APScheduler background jobs: ICS sync, IMAP poll, weather, HA poll.

Every job is idempotent and safe to re-run; failures are recorded via
db.mark_sync so /api/status always reflects reality. Logs go to stdout
(journald picks them up under systemd). A failed fetch never clears the
previous cache — the panel keeps rendering stale data offline.
"""
import logging
from datetime import timedelta

import requests
from apscheduler.schedulers.background import BackgroundScheduler

import db

log = logging.getLogger("family-panel.jobs")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

_scheduler: BackgroundScheduler | None = None

# Expansion window for recurring events: enough history to scroll back,
# ~13 months forward so "N sleeps" countdowns to next year's events work.
ICS_PAST_DAYS = 60
ICS_FUTURE_DAYS = 400

FETCH_TIMEOUT = 30


# ------------------------------------------------------------------ ICS sync

def store_feed_events(feed_name: str, person_id: str | None, ics_bytes: bytes) -> int:
    """Parse one ICS payload, expand recurrences, replace this feed's cache rows.

    Pure parse+store (no network) so tests can drive it with fixture files.
    Occurrence rows get uid = "<UID>@<start-utc>" because a recurring event's
    expanded instances share the calendar UID and the PK is (uid, feed).
    """
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics_bytes)
    today = db.local_today()
    window_start = today - timedelta(days=ICS_PAST_DAYS)
    window_end = today + timedelta(days=ICS_FUTURE_DAYS)

    occurrences = recurring_ical_events.of(cal).between(window_start, window_end)

    rows = []
    for ev in occurrences:
        dtstart = ev.get("DTSTART")
        if dtstart is None:
            continue
        start = dtstart.dt
        dtend = ev.get("DTEND")
        end = dtend.dt if dtend is not None else None

        if hasattr(start, "hour"):           # timed event
            all_day = 0
            if start.tzinfo is None:         # floating time: treat as local
                start = start.replace(tzinfo=db.LOCAL_TZ)
            if end is None:
                end = start + timedelta(hours=1)
            elif end.tzinfo is None:
                end = end.replace(tzinfo=db.LOCAL_TZ)
            start_utc = db.to_utc_iso(start)
            end_utc = db.to_utc_iso(end)
        else:                                # DATE value: all-day
            all_day = 1
            if end is None:
                end = start + timedelta(days=1)   # DTEND is exclusive per RFC 5545
            start_utc = db.local_date_to_utc_iso(start)
            end_utc = db.local_date_to_utc_iso(end)

        uid = str(ev.get("UID", "no-uid"))
        rows.append((f"{uid}@{start_utc}", feed_name, person_id,
                     str(ev.get("SUMMARY", "(untitled)")),
                     str(ev["LOCATION"]) if ev.get("LOCATION") else None,
                     start_utc, end_utc, all_day))

    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM ics_cache WHERE feed=?", (feed_name,))
        conn.executemany(
            "INSERT OR REPLACE INTO ics_cache"
            "(uid, feed, person_id, title, location, start_utc, end_utc, all_day)"
            " VALUES(?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def sync_one_feed(feed: dict) -> None:
    name = feed["name"]
    headers = {"User-Agent": "family-panel/1.0"}
    etag = db.get_setting(f"etag:feed:{name}")
    lastmod = db.get_setting(f"lastmod:feed:{name}")
    if etag:
        headers["If-None-Match"] = etag
    if lastmod:
        headers["If-Modified-Since"] = lastmod

    resp = requests.get(feed["url"], headers=headers, timeout=FETCH_TIMEOUT)
    if resp.status_code == 304:
        db.mark_sync(f"feed:{name}", True, "not modified")
        return
    resp.raise_for_status()

    n = store_feed_events(name, feed.get("person"), resp.content)
    if resp.headers.get("ETag"):
        db.set_setting(f"etag:feed:{name}", resp.headers["ETag"])
    if resp.headers.get("Last-Modified"):
        db.set_setting(f"lastmod:feed:{name}", resp.headers["Last-Modified"])
    db.mark_sync(f"feed:{name}", True, f"{n} events")


def sync_ics(config: dict) -> None:
    feeds = config.get("ics_feeds", []) or []
    ok = True
    for feed in feeds:
        try:
            sync_one_feed(feed)
        except Exception as e:          # one bad feed must not kill the rest
            ok = False
            log.warning("ICS feed %s failed: %s", feed.get("name"), e)
            db.mark_sync(f"feed:{feed.get('name')}", False, str(e)[:200])
    db.mark_sync("ics", ok, f"{len(feeds)} feeds")


# ------------------------------------------------------------------ other jobs
# Lazy imports so a missing optional config (e.g. no IMAP creds) can no-op
# cleanly and app.py stays importable in tests without these modules loaded.

def poll_imap(config: dict) -> None:
    import magic_import
    try:
        n, gave_up = magic_import.poll_inbox()
        detail = f"{n} new"
        if gave_up:
            detail += f", {gave_up} gave up"
        db.mark_sync("imap", True, detail)
    except magic_import.NotConfigured:
        db.mark_sync("imap", True, "not configured")
    except Exception as e:
        log.warning("IMAP poll failed: %s", e)
        db.mark_sync("imap", False, str(e)[:200])


def fetch_weather(config: dict) -> None:
    import weather
    try:
        weather.refresh(config.get("weather", {}))
        db.mark_sync("weather", True)
    except Exception as e:
        log.warning("weather fetch failed: %s", e)
        db.mark_sync("weather", False, str(e)[:200])


def poll_ha(config: dict) -> None:
    import ha
    try:
        ha.refresh(config.get("ha_tiles", []) or [])
        db.mark_sync("ha", True)
    except ha.NotConfigured:
        db.mark_sync("ha", True, "not configured")
    except Exception as e:
        # expected whenever HA is down; tiles grey out via /api/ha/tiles
        db.mark_sync("ha", False, str(e)[:200])


def sync_photos(config: dict) -> None:
    import photos
    try:
        r = photos.refresh(config)
        db.mark_sync("photos", r["ok"], r["detail"])
    except Exception as e:
        log.warning("photo sync failed: %s", e)
        db.mark_sync("photos", False, str(e)[:200])


# ------------------------------------------------------------------ scheduler

def start(config: dict) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone="UTC")
    mins = config.get("sync_minutes", {})

    def add(name, fn, minutes):
        seconds = max(int(float(minutes) * 60), 5)
        sched.add_job(fn, "interval", args=[config], seconds=seconds,
                      id=name, max_instances=1, coalesce=True,
                      next_run_time=None)

    add("ics", sync_ics, mins.get("ics", 5))
    add("imap", poll_imap, mins.get("imap", 5))
    add("weather", fetch_weather, mins.get("weather", 30))
    add("ha", poll_ha, mins.get("ha", 0.25))
    # photos change rarely; 6-hourly keeps a shared album fresh same-day
    # without hammering Google's share pages
    add("photos", sync_photos, mins.get("photos", 360))
    sched.start()

    # First run of everything straight away (in the scheduler's thread pool).
    for job in sched.get_jobs():
        job.modify(next_run_time=__import__("datetime").datetime.now(db.UTC))

    log.info("scheduler started (intervals min: %s)", mins)
    _scheduler = sched
    return sched
