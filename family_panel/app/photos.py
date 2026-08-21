"""Photo screensaver backend: two sources feeding one local image cache.

Source 1 is a folder — a NAS share, a USB stick, whatever's mounted where
config points. Plain recursive scan.

Source 2 is a Google Photos "shared album" link (photos.app.goo.gl/...).
This is a scrape of the public album page, not an API call: the Photos
Library API stopped letting third-party apps read a user's library in
March 2025, so the only door left open is the same one a Google Calendar's
"secret iCal address" uses — a long, guessable-only-by-brute-force link
that anyone holding it can read. Handing that link to the panel is the
household's own call, same trust model as the ICS feeds elsewhere here.

Storage is self-managed: a `photos` table in the shared sqlite db (own
table, untouched by db.py's schema/migrations) plus a cache directory
beside the db file. Every cached image, from either source, is normalised
through Pillow (EXIF-rotated, downscaled, re-encoded) before it reaches
the wall — screensaver frames should never depend on a source's raw
format, orientation or resolution.
"""
import hashlib
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from io import BytesIO

import requests
from PIL import Image, ImageOps

import db

log = logging.getLogger("family-panel.photos")

FETCH_TIMEOUT = 20
MAX_FOLDER_FILES = 500
MAX_FOLDER_BYTES = 25 * 1024 * 1024
MAX_ALBUM_PHOTOS = 500
MAX_IMAGE_BYTES = 20 * 1024 * 1024
FOLDER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
THUMB_SIZE = (1920, 1920)
JPEG_QUALITY = 82

# Module-level so tests can monkeypatch the network layer without reaching
# into requests itself.
_get = requests.get

# A desktop-browser UA — the shared-album page serves a script-free
# fallback to unrecognised clients that doesn't carry the photo data.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Matches the ["<url>",width,height entries Google Photos embeds inside its
# AF_initDataCallback blobs. Deliberately loose (not a full JS/JSON parser —
# that page is not valid JSON) since only the base URL + dims are needed.
PHOTO_URL_RE = re.compile(r'\["(https://lh3\.googleusercontent\.com/[^"]+)",(\d+),(\d+)')


# ------------------------------------------------------------------ storage

def _cache_dir() -> str:
    d = os.path.join(os.path.dirname(db.DB_PATH), "photo_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(skey: str) -> str:
    h = hashlib.sha1(skey.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_cache_dir(), f"{h}.jpg")


def _connect() -> sqlite3.Connection:
    # Read db.DB_PATH at call time (not cached at import) so it can be
    # pointed at a throwaway db per-test the same way the rest of the
    # panel's tests redirect PANEL_DB.
    os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
    return sqlite3.connect(db.DB_PATH, timeout=15)


def ensure_tables() -> None:
    conn = _connect()
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS photos("
                "id INTEGER PRIMARY KEY, skey TEXT UNIQUE, source TEXT,"
                " added_at TEXT, cached TEXT)")
    finally:
        conn.close()


def _count() -> int:
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    finally:
        conn.close()


def _prune_source(source: str, valid_skeys: set) -> None:
    """Drop DB rows + cache files for ONE source's skeys that are no longer
    present in that source (folder file deleted, album photo removed, source
    unconfigured entirely).

    Deliberately scoped per source, and only ever called for a source whose
    refresh SUCCEEDED (or that is genuinely unconfigured): a failed album
    fetch or an unmounted folder returns an empty valid-set, and pruning
    against that would wipe the whole cache over a 3 am DNS hiccup — the
    exact opposite of the offline-first rule everything else follows."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT skey, cached FROM photos WHERE source=?", (source,)
        ).fetchall()
        stale = [r for r in rows if r[0] not in valid_skeys]
        if not stale:
            return
        with conn:
            conn.executemany("DELETE FROM photos WHERE skey=?",
                             [(r[0],) for r in stale])
        for _skey, cached in stale:
            try:
                if cached and os.path.exists(cached):
                    os.remove(cached)
            except OSError:
                pass
    finally:
        conn.close()


# ------------------------------------------------------------------ image pipeline

def _normalize_and_save(skey: str, img) -> bool:
    """Common pipeline for every cached image regardless of source: correct
    EXIF orientation (phone photos routinely carry rotation as a tag, not
    baked into the pixels), drop alpha/palette weirdness, cap size, and
    re-encode to a predictable JPEG. Returns False (never raises) on
    anything unreadable — a corrupt file must not break the whole refresh."""
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    img.thumbnail(THUMB_SIZE)
    img.save(_cache_path(skey), "JPEG", quality=JPEG_QUALITY)
    return True


def _cache_from_path(skey: str, path: str) -> bool:
    try:
        with Image.open(path) as img:
            return _normalize_and_save(skey, img)
    except Exception as e:
        log.info("photo not cached (%s): %s", skey, e)
        return False


def _cache_from_bytes(skey: str, data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as img:
            return _normalize_and_save(skey, img)
    except Exception as e:
        log.info("photo not cached (%s): %s", skey, e)
        return False


# ------------------------------------------------------------------ source: folder

def _scan_folder(folder: str) -> list:
    """Recursive scan for image files. Returns (skey, added_at, abspath)
    tuples; caller decides sort order. A missing/unmounted folder returns
    None — a routine state (NAS asleep, USB unplugged), not a fault, but the
    caller must know the difference between "folder empty" and "folder gone"
    or it would prune the cache of a folder that is merely unmounted."""
    if not folder or not os.path.isdir(folder):
        return None
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if len(found) >= MAX_FOLDER_FILES:
                return found
            if os.path.splitext(name)[1].lower() not in FOLDER_EXTS:
                continue
            abspath = os.path.join(root, name)
            try:
                st = os.stat(abspath)
            except OSError:
                continue
            if st.st_size > MAX_FOLDER_BYTES:
                continue
            rel = os.path.relpath(abspath, folder)
            added_at = datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            found.append((f"file:{rel}", added_at, abspath))
    return found


def _refresh_folder(folder: str):
    """Returns (valid_skeys, skipped_count, error_note_or_None)."""
    valid = set()
    skipped = 0
    found = _scan_folder(folder)
    if found is None:
        # unmounted/missing: keep whatever is cached, say so in the detail
        return None, 0, None
    conn = _connect()
    try:
        existing = {r[0] for r in conn.execute(
            "SELECT skey FROM photos WHERE source='folder'")}
        for skey, added_at, abspath in found:
            valid.add(skey)
            if skey in existing:
                continue
            if _cache_from_path(skey, abspath):
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO photos"
                        "(skey, source, added_at, cached) VALUES(?,?,?,?)",
                        (skey, "folder", added_at, _cache_path(skey)))
            else:
                skipped += 1
    finally:
        conn.close()
    return valid, skipped, None


# ------------------------------------------------------------------ source: shared album

def _album_skey(base_url: str) -> str:
    return "g:" + hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:16]


def _extract_photo_urls(html: str) -> list:
    seen = set()
    out = []
    for m in PHOTO_URL_RE.finditer(html):
        url = m.group(1)
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= MAX_ALBUM_PHOTOS:
            break
    return out


def _fetch_album_html(url: str) -> str:
    r = _get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": UA},
             allow_redirects=True)
    r.raise_for_status()
    return r.text


def _download_album_photo(base_url: str):
    # =w1920-h1280 asks Google's image server for a downscaled variant
    # server-side, rather than pulling the (often much larger) original
    # over the household's connection just to shrink it locally anyway.
    r = _get(f"{base_url}=w1920-h1280", timeout=FETCH_TIMEOUT,
             headers={"User-Agent": UA})
    r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        return None
    data = r.content
    if len(data) > MAX_IMAGE_BYTES:
        return None
    return data


def _refresh_album(url: str):
    """Returns (valid_skeys, skipped_count, error_note_or_None)."""
    try:
        html = _fetch_album_html(url)
    except Exception as e:
        return set(), 0, str(e)

    urls = _extract_photo_urls(html)
    valid = set()
    skipped = 0
    conn = _connect()
    try:
        existing = {r[0] for r in conn.execute(
            "SELECT skey FROM photos WHERE source='album'")}
        for base_url in urls:
            skey = _album_skey(base_url)
            valid.add(skey)
            if skey in existing:
                continue          # already cached — 6-hourly refresh stays cheap
            try:
                data = _download_album_photo(base_url)
            except Exception as e:
                log.info("album photo download failed (%s): %s", skey, e)
                data = None
            if data is None or not _cache_from_bytes(skey, data):
                skipped += 1
                continue
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO photos"
                    "(skey, source, added_at, cached) VALUES(?,?,?,?)",
                    (skey, "album", db.utc_now_iso(), _cache_path(skey)))
    finally:
        conn.close()
    return valid, skipped, None


# ------------------------------------------------------------------ public API

def refresh(config: dict) -> dict:
    """Sync both configured sources into the local cache. Never raises —
    this feeds a scheduled job, and a network hiccup or a bad config value
    must degrade to a status note, not take the job down."""
    ensure_tables()
    cfg = (config or {}).get("photos") or {}
    folder = cfg.get("folder")
    album_url = cfg.get("shared_album_url")

    if not folder and not album_url:
        # both sources deliberately unconfigured: clearing the cache is the
        # one case where an empty valid-set is the truth, not a failure
        _prune_source("folder", set())
        _prune_source("album", set())
        return {"ok": True, "detail": "nothing configured", "count": 0}

    ok = True
    parts = []

    if folder:
        try:
            f_valid, f_skipped, f_note = _refresh_folder(folder)
        except Exception as e:
            f_valid, f_skipped, f_note = set(), 0, str(e)
        if f_valid is None:
            parts.append("folder missing (cache kept)")
        elif f_note:
            ok = False
            parts.append(f"folder failed: {f_note}")
        else:
            _prune_source("folder", f_valid)
            suffix = f" ({f_skipped} skipped)" if f_skipped else ""
            parts.append(f"folder {len(f_valid)}{suffix}")
    else:
        _prune_source("folder", set())

    if album_url:
        try:
            a_valid, a_skipped, a_note = _refresh_album(album_url)
        except Exception as e:
            a_valid, a_skipped, a_note = set(), 0, str(e)
        if a_note:
            ok = False
            parts.append(f"album failed: {a_note}")
        else:
            _prune_source("album", a_valid)
            suffix = f" ({a_skipped} skipped)" if a_skipped else ""
            parts.append(f"album {len(a_valid)}{suffix}")
    else:
        _prune_source("album", set())

    return {"ok": ok, "detail": " · ".join(parts), "count": _count()}


def manifest() -> dict:
    ensure_tables()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, added_at FROM photos ORDER BY added_at DESC, id DESC"
        ).fetchall()
    finally:
        conn.close()
    photos = [{"id": r[0], "added_at": r[1]} for r in rows]
    return {"photos": photos, "count": len(photos)}


def image_path(photo_id: int):
    ensure_tables()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT cached FROM photos WHERE id=?", (photo_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row[0] or not os.path.exists(row[0]):
        return None
    return row[0]
