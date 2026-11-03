"""Route-level checks for the Assist proxy endpoints in app.py."""
import json

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


def test_text_route_passes_through(client, monkeypatch):
    import assist
    monkeypatch.setattr(assist, "text_query",
                        lambda cfg, text: {"ok": True, "response_text": "21 degrees"})
    r = client.post("/api/assist/text", json={"text": "temp inside?"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "response_text": "21 degrees"}


def test_text_route_rejects_empty(client):
    r = client.post("/api/assist/text", json={"text": "   "})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_audio_route_caps_size(client):
    r = client.post("/api/assist/audio", data=b"\x00" * (2 * 1024 * 1024 + 2),
                    content_type="application/octet-stream")
    assert r.status_code == 413


def test_audio_route_passes_rate(client, monkeypatch):
    import assist
    seen = {}
    def fake(cfg, pcm, sample_rate=16000):
        seen["rate"] = sample_rate
        seen["n"] = len(pcm)
        return {"ok": True, "heard": "hi", "response_text": "hello", "tts_path": None}
    monkeypatch.setattr(assist, "audio_query", fake)
    r = client.post("/api/assist/audio?sample_rate=16000", data=b"\x01\x02" * 100,
                    content_type="application/octet-stream")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert seen == {"rate": 16000, "n": 200}


def test_tts_route_rejects_bad_path(client, monkeypatch):
    import assist
    def bomb(cfg, path):
        raise ValueError("That's not a voice reply.")
    monkeypatch.setattr(assist, "tts_fetch", bomb)
    r = client.get("/api/assist/tts?path=/api/states")
    assert r.status_code == 400
