"""Route-level checks for the screensaver endpoints in app.py."""
import pytest

import db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    db.init_db({"people": []})
    import app
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c


def test_manifest_route_shape(client, monkeypatch):
    import photos
    monkeypatch.setattr(photos, "manifest", lambda: {
        "photos": [{"id": 2, "added_at": "2026-08-19T00:00:00Z"},
                   {"id": 1, "added_at": "2026-08-18T00:00:00Z"}],
        "count": 2})
    r = client.get("/api/photos")
    assert r.status_code == 200
    body = r.get_json()
    assert body["available"] is True
    assert body["interval_s"] == 45 and body["idle_minutes"] == 3
    assert [p["id"] for p in body["photos"]] == [2, 1]


def test_manifest_route_empty(client, monkeypatch):
    import photos
    monkeypatch.setattr(photos, "manifest", lambda: {"photos": [], "count": 0})
    assert client.get("/api/photos").get_json()["available"] is False


def test_image_route(client, monkeypatch, tmp_path):
    import photos
    f = tmp_path / "x.jpg"
    f.write_bytes(b"\xff\xd8\xff\xdbfake")
    monkeypatch.setattr(photos, "image_path",
                        lambda pid: str(f) if pid == 5 else None)
    r = client.get("/api/photos/5/image")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert client.get("/api/photos/9/image").status_code == 404
