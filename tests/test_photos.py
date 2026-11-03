"""Photo screensaver backend: folder scan, shared-album scrape, cache
pipeline, pruning. Real Pillow images are built in tmp_path rather than
using fixture binaries so orientation/size assertions are exact."""
import os
import sqlite3
from io import BytesIO

import pytest
import requests
from PIL import Image

import db
import photos

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="module", autouse=True)
def _ensure_shared_schema():
    """conftest.py's autouse clean_tables fixture assumes db.py's schema
    already exists on the shared session db; normally some other test
    file's `client` fixture creates it first by importing app. When this
    file runs alone nothing else does that, so make sure it happens here
    too, before any per-test db.DB_PATH monkeypatching below."""
    db.init_db({})


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point photos.py's own storage at a throwaway db path per test, the
    same trick conftest.py uses for the whole suite (redirect the db path
    before anything reads it) — photos.py reads db.DB_PATH at call time so
    this is enough, no need to touch conftest's shared session db."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))


def make_jpeg_bytes(size=(400, 300), color=(10, 20, 30), orientation=None):
    img = Image.new("RGB", size, color)
    buf = BytesIO()
    if orientation is not None:
        exif = img.getexif()
        exif[0x0112] = orientation      # Orientation tag
        img.save(buf, "JPEG", exif=exif.tobytes())
    else:
        img.save(buf, "JPEG")
    return buf.getvalue()


# ------------------------------------------------------------------ folder source

def test_folder_scan_cache_manifest_roundtrip(tmp_path):
    folder = tmp_path / "photos_in"
    folder.mkdir()

    # plain landscape, well under the downscale threshold
    (folder / "landscape.jpg").write_bytes(make_jpeg_bytes(size=(1000, 600)))

    # portrait shot: raw pixels are landscape-shaped (800x600) but tagged
    # with EXIF orientation 6 ("rotate 90 CW to display correctly") — the
    # way a phone held upright actually stores the sensor's raw frame
    (folder / "portrait.jpg").write_bytes(
        make_jpeg_bytes(size=(800, 600), orientation=6))

    # oversized: real jpeg header + padding past the 25MB cap, so the scan's
    # size filter (checked via os.stat, before Pillow ever opens it) trips
    (folder / "huge.jpg").write_bytes(
        make_jpeg_bytes(size=(200, 200)) + b"\0" * (26 * 1024 * 1024))

    # not an image extension at all
    (folder / "notes.txt").write_text("shopping list, not a photo")

    config = {"photos": {"folder": str(folder), "shared_album_url": None}}
    result = photos.refresh(config)

    assert result["ok"] is True
    assert result["count"] == 2                  # huge + txt excluded
    assert result["detail"] == "folder 2"

    man = photos.manifest()
    assert man["count"] == 2
    ids = [p["id"] for p in man["photos"]]

    # image_path round trip + proof the rotation was actually applied:
    # one cached image should be portrait-shaped even though its source
    # pixels were landscape-shaped before the EXIF transpose
    dims = set()
    for pid in ids:
        path = photos.image_path(pid)
        assert path and os.path.exists(path)
        with Image.open(path) as im:
            dims.add(im.size)
    assert (1000, 600) in dims             # landscape untouched
    assert (600, 800) in dims              # portrait: 800x600 raw, rotated


def test_downscale_caps_long_edge(tmp_path):
    folder = tmp_path / "photos_in"
    folder.mkdir()
    (folder / "big.jpg").write_bytes(make_jpeg_bytes(size=(3000, 2000)))

    config = {"photos": {"folder": str(folder)}}
    result = photos.refresh(config)
    assert result["count"] == 1

    pid = photos.manifest()["photos"][0]["id"]
    with Image.open(photos.image_path(pid)) as im:
        assert max(im.size) <= 1920
        assert im.size == (1920, 1280)     # aspect ratio preserved


def test_prune_removes_row_and_cache_file(tmp_path):
    folder = tmp_path / "photos_in"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(make_jpeg_bytes(color=(1, 1, 1)))
    (folder / "b.jpg").write_bytes(make_jpeg_bytes(color=(2, 2, 2)))

    config = {"photos": {"folder": str(folder)}}
    photos.refresh(config)
    assert photos.manifest()["count"] == 2

    b_path = photos._cache_path("file:b.jpg")
    assert os.path.exists(b_path)
    conn = sqlite3.connect(db.DB_PATH)
    b_id = conn.execute(
        "SELECT id FROM photos WHERE skey='file:b.jpg'").fetchone()[0]
    conn.close()

    os.remove(folder / "b.jpg")
    result = photos.refresh(config)

    assert result["count"] == 1
    assert photos.manifest()["count"] == 1
    assert photos.image_path(b_id) is None
    assert not os.path.exists(b_path)


# ------------------------------------------------------------------ missing config

def test_refresh_with_no_sources_configured():
    for config in (None, {}, {"photos": {}},
                   {"photos": {"folder": None, "shared_album_url": None}}):
        result = photos.refresh(config)
        assert result == {"ok": True, "detail": "nothing configured", "count": 0}


# ------------------------------------------------------------------ shared album

ALBUM_URL = "https://photos.app.goo.gl/FAKEALBUM123"
FIXTURE_PHOTO_URLS = [
    "https://lh3.googleusercontent.com/pw/fake-album-photo-one",
    "https://lh3.googleusercontent.com/pw/fake-album-photo-two",
    "https://lh3.googleusercontent.com/pw/fake-album-photo-three",
]


class FakeResponse:
    def __init__(self, content=b"", text="", headers=None, status_code=200):
        self.content = content
        self.text = text
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def test_shared_album_import_and_cheap_re_refresh(monkeypatch):
    with open(os.path.join(FIXTURES, "shared_album.html"), encoding="utf-8") as f:
        album_html = f.read()
    tiny_jpeg = make_jpeg_bytes(size=(50, 40))

    calls = []

    def fake_get(url, timeout=None, headers=None, **kwargs):
        calls.append(url)
        if url == ALBUM_URL:
            return FakeResponse(text=album_html,
                                headers={"Content-Type": "text/html"})
        if url.startswith("https://lh3.googleusercontent.com/pw/fake-album-photo-"):
            return FakeResponse(content=tiny_jpeg,
                                headers={"Content-Type": "image/jpeg"})
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(photos, "_get", fake_get)

    config = {"photos": {"folder": None, "shared_album_url": ALBUM_URL}}
    result = photos.refresh(config)

    assert result["ok"] is True
    assert result["count"] == 3
    assert result["detail"] == "album 3"
    assert len(calls) == 4                 # 1 album page + 3 photo downloads

    conn = sqlite3.connect(db.DB_PATH)
    skeys = {r[0] for r in conn.execute(
        "SELECT skey FROM photos WHERE source='album'")}
    conn.close()
    expected = {photos._album_skey(u) for u in FIXTURE_PHOTO_URLS}
    assert skeys == expected
    assert all(s.startswith("g:") for s in skeys)

    # every skey requested with the downscale suffix, not the bare base url
    photo_requests = [u for u in calls if u != ALBUM_URL]
    assert all(u.endswith("=w1920-h1280") for u in photo_requests)

    # second refresh: album page re-fetched (cheap), but nothing already
    # cached is downloaded again
    calls.clear()
    result2 = photos.refresh(config)
    assert result2["count"] == 3
    assert calls == [ALBUM_URL]


# ------------------------------------------------- prune safety on failure
# Regression tests for the cache-wipe bug found in external review: a
# failed album fetch (or an unmounted folder) used to feed an empty
# valid-set into an unconditional prune, deleting every cached photo over a
# transient failure. Pruning is per-source now, and only after success.

def _album_fixture_get(url, timeout=None, headers=None, **kw):
    if url == "https://photos.app.goo.gl/x":
        with open(os.path.join(FIXTURES, "shared_album.html"),
                  encoding="utf-8") as f:
            return FakeResponse(text=f.read(),
                                headers={"Content-Type": "text/html"})
    return FakeResponse(content=make_jpeg_bytes(size=(600, 400)),
                        headers={"Content-Type": "image/jpeg"})


def test_failed_album_fetch_keeps_cache(monkeypatch):
    cfg = {"photos": {"shared_album_url": "https://photos.app.goo.gl/x"}}
    monkeypatch.setattr(photos, "_get", _album_fixture_get)
    first = photos.refresh(cfg)
    assert first["ok"] and first["count"] > 0
    cached_before = first["count"]

    def dead_get(url, **kw):
        raise requests.ConnectionError("dns hiccup at 3am")
    monkeypatch.setattr(photos, "_get", dead_get)
    second = photos.refresh(cfg)
    assert second["ok"] is False
    assert "album failed" in second["detail"]
    # the whole point: nothing was deleted
    assert second["count"] == cached_before
    assert photos.manifest()["count"] == cached_before


def test_missing_folder_keeps_cache_and_says_so(tmp_path):
    folder = tmp_path / "mounted"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(make_jpeg_bytes())
    cfg = {"photos": {"folder": str(folder)}}
    assert photos.refresh(cfg)["count"] == 1

    # simulate the share unmounting
    (folder / "a.jpg").unlink()
    folder.rmdir()
    after = photos.refresh(cfg)
    assert after["count"] == 1                    # cache kept
    assert "folder missing" in after["detail"]    # and it says so
    assert after["ok"] is True                    # routine state, not a fault


def test_unconfiguring_a_source_still_prunes_it(tmp_path, monkeypatch):
    folder = tmp_path / "photos_in"
    folder.mkdir()
    (folder / "a.jpg").write_bytes(make_jpeg_bytes())
    assert photos.refresh({"photos": {"folder": str(folder)}})["count"] == 1

    # owner deliberately removes the folder source from config entirely:
    # that IS the one case where clearing folder-sourced photos is correct
    monkeypatch.setattr(photos, "_get", _album_fixture_get)
    gone = photos.refresh({"photos": {"shared_album_url":
                                      "https://photos.app.goo.gl/x"}})
    conn = sqlite3.connect(db.DB_PATH)
    try:
        n_folder = conn.execute(
            "SELECT COUNT(*) FROM photos WHERE source='folder'").fetchone()[0]
    finally:
        conn.close()
    assert n_folder == 0
    assert gone["count"] >= 1                     # album photos took over
