"""Assist proxy: conversation REST, the voice pipeline websocket, TTS fetch.

Everything external is faked — no HA, no network.
"""
import json
import time

import pytest

import assist
import db

# This module touches no DB, but conftest's autouse teardown expects the
# schema to exist — it normally arrives via `import app` in the other files.
db.init_db({"people": []})

CONFIG = {"ha": {"base_url": "http://ha.local:8123", "token": "tok-123"}}


class FakeResponse:
    def __init__(self, payload=None, content=b"", headers=None, raw=None):
        self._payload = payload
        self._raw = raw
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        pass

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)       # blows up on malformed bodies
        return self._payload


def conversation_reply(speech="The back door is closed."):
    return {"response": {"speech": {"plain": {"speech": speech}}},
            "conversation_id": "abc"}


# ----------------------------------------------------------------- text

def test_text_query_extracts_speech(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json, timeout=timeout)
        return FakeResponse(conversation_reply())

    monkeypatch.setattr(assist, "_post", fake_post)
    out = assist.text_query(CONFIG, "is the back door closed?")

    assert out == {"ok": True, "response_text": "The back door is closed."}
    assert seen["url"] == "http://ha.local:8123/api/conversation/process"
    assert seen["headers"]["Authorization"] == "Bearer tok-123"
    assert seen["body"] == {"text": "is the back door closed?", "language": "en"}
    assert seen["timeout"] == 10


def test_text_query_malformed_and_timeout(monkeypatch):
    monkeypatch.setattr(assist, "_post",
                        lambda *a, **k: FakeResponse(raw="<html>nope</html>"))
    bad = assist.text_query(CONFIG, "hi")
    assert bad["ok"] is False and bad["error"] and "Traceback" not in bad["error"]

    # right JSON, wrong shape -> still a human error, no KeyError
    monkeypatch.setattr(assist, "_post",
                        lambda *a, **k: FakeResponse({"response": {}}))
    assert assist.text_query(CONFIG, "hi")["ok"] is False

    def boom(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(assist, "_post", boom)
    out = assist.text_query(CONFIG, "hi")
    assert out["ok"] is False and out["error"] == "Home Assistant didn't answer"


# ----------------------------------------------------------------- voice

class FakeWS:
    """Replays a scripted server side and records what we sent."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []            # text frames (parsed)
        self.binary = []          # raw binary frames
        self.closed = False
        self.url = None
        self.connect_timeout = None

    def recv(self):
        if not self.script:
            raise AssertionError("client read past the end of the script")
        return json.dumps(self.script.pop(0))

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def send_binary(self, payload):
        self.binary.append(bytes(payload))

    def close(self):
        self.closed = True


def event(etype, data=None):
    return {"id": 1, "type": "event", "event": {"type": etype, "data": data or {}}}


FULL_RUN = [
    {"type": "auth_required", "ha_version": "2026.8.0"},
    {"type": "auth_ok", "ha_version": "2026.8.0"},
    {"id": 1, "type": "result", "success": True, "result": None},
    event("run-start", {"pipeline": "01", "language": "en",
                        "runner_data": {"stt_binary_handler_id": 7,
                                        "timeout": 300}}),
    event("stt-start", {}),
    event("stt-end", {"stt_output": {"text": "turn on the hallway light"}}),
    event("intent-start", {}),
    event("intent-end", {"intent_output": {"response": {"speech": {"plain": {
        "speech": "Turned on hallway light."}}}, "conversation_id": "x"}}),
    event("tts-start", {}),
    event("tts-end", {"tts_output": {"url": "/api/tts_proxy/deadbeef.mp3",
                                     "mime_type": "audio/mpeg"}}),
    event("run-end", {}),
]


def install_ws(monkeypatch, script):
    made = {}

    def connect(url, timeout=None, **kwargs):
        ws = FakeWS(script)
        ws.url, ws.connect_timeout = url, timeout
        made["ws"] = ws
        return ws

    monkeypatch.setattr(assist, "_ws_connect", connect)
    return made


def test_audio_query_happy_path(monkeypatch):
    made = install_ws(monkeypatch, FULL_RUN)
    pcm = bytes(10000)                      # ~3 chunks of 4096

    out = assist.audio_query(CONFIG, pcm)

    assert out == {"ok": True,
                   "heard": "turn on the hallway light",
                   "response_text": "Turned on hallway light.",
                   "tts_path": "/api/tts_proxy/deadbeef.mp3"}

    ws = made["ws"]
    assert ws.url == "ws://ha.local:8123/api/websocket"
    assert ws.sent[0] == {"type": "auth", "access_token": "tok-123"}
    run = ws.sent[1]
    assert run["type"] == "assist_pipeline/run" and run["id"] == 1
    assert run["start_stage"] == "stt" and run["end_stage"] == "tts"
    assert run["input"] == {"sample_rate": 16000}

    # every audio frame is prefixed with the handler id byte; the last frame
    # is the bare prefix (end of speech) and carries no audio
    assert ws.binary and all(f[:1] == b"\x07" for f in ws.binary)
    assert ws.binary[-1] == b"\x07"
    assert b"".join(f[1:] for f in ws.binary) == pcm
    assert all(len(f) == 1 + 4096 for f in ws.binary[:-2])
    assert ws.closed is True


def test_audio_query_wss_for_https_base(monkeypatch):
    made = install_ws(monkeypatch, FULL_RUN)
    assist.audio_query({"ha": {"base_url": "https://ha.example.com/", "token": "t"}},
                       b"\x00\x01")
    assert made["ws"].url == "wss://ha.example.com/api/websocket"


def test_audio_query_error_event(monkeypatch):
    script = FULL_RUN[:5] + [event("error", {"code": "stt-no-text-recognized",
                                             "message": "No text recognized"})]
    install_ws(monkeypatch, script)
    out = assist.audio_query(CONFIG, bytes(64))
    assert out["ok"] is False
    assert out["error"] == "Voice pipeline error: No text recognized"


def test_audio_query_auth_invalid(monkeypatch):
    install_ws(monkeypatch, [{"type": "auth_required"},
                             {"type": "auth_invalid",
                              "message": "Invalid access token"}])
    out = assist.audio_query(CONFIG, bytes(64))
    assert out["ok"] is False and "token" in out["error"]


def test_audio_query_dropped_connection_after_auth_does_not_spin(monkeypatch):
    """A FakeWS whose recv() raises after auth must fail fast with a clear
    error, not spin in _run_pipeline's loop until PIPELINE_TIMEOUT."""
    class DropsAfterAuth(FakeWS):
        def recv(self):
            if self.script:
                return super().recv()
            raise OSError("connection closed")

    ws = DropsAfterAuth(FULL_RUN[:2])   # auth_required, auth_ok — then nothing
    monkeypatch.setattr(assist, "_ws_connect", lambda url, timeout=None, **k: ws)

    start = time.monotonic()
    out = assist.audio_query(CONFIG, bytes(64))
    elapsed = time.monotonic() - start

    assert out == {"ok": False, "error": "Voice pipeline error: the connection "
                                         "to Home Assistant dropped"}
    assert elapsed < 1        # no spin to the 30s pipeline deadline


def test_audio_query_socket_and_empty_audio(monkeypatch):
    def refuse(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(assist, "_ws_connect", refuse)
    out = assist.audio_query(CONFIG, bytes(64))
    assert out == {"ok": False, "error": "Home Assistant didn't answer"}

    install_ws(monkeypatch, FULL_RUN)
    assert assist.audio_query(CONFIG, b"")["ok"] is False


# ----------------------------------------------------------------- tts fetch

def test_tts_fetch_allowlist(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, headers=headers, timeout=timeout)
        return FakeResponse(content=b"ID3audio",
                            headers={"Content-Type": "audio/mpeg"})

    monkeypatch.setattr(assist, "_get", fake_get)
    data, mime = assist.tts_fetch(CONFIG, "/api/tts_proxy/deadbeef.mp3")

    assert (data, mime) == (b"ID3audio", "audio/mpeg")
    assert seen["url"] == "http://ha.local:8123/api/tts_proxy/deadbeef.mp3"
    assert seen["headers"]["Authorization"] == "Bearer tok-123"
    assert seen["timeout"] == 15


def test_tts_fetch_rejects_other_paths(monkeypatch):
    monkeypatch.setattr(assist, "_get", lambda *a, **k: pytest.fail("must not fetch"))
    for path in ("/api/states", "/api/services/homeassistant/restart",
                 "http://evil/api/tts_proxy/x.mp3", None):
        with pytest.raises(ValueError):
            assist.tts_fetch(CONFIG, path)


def test_tts_fetch_size_cap(monkeypatch):
    monkeypatch.setattr(assist, "_get", lambda *a, **k: FakeResponse(
        content=b"x" * (assist.MAX_TTS_BYTES + 1)))
    with pytest.raises(ValueError):
        assist.tts_fetch(CONFIG, "/api/tts_proxy/big.mp3")


# ----------------------------------------------------------------- available

def test_available(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HA_BASE_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    assert assist.available({}) is False
    assert assist.available(None) is False
    assert assist.available({"ha": {"base_url": "http://ha.local:8123"}}) is False
    assert assist.available(CONFIG) is True

    # falls back to ha.py's env resolution when config carries no ha block
    monkeypatch.setenv("HA_BASE_URL", "http://ha.local:8123/")
    monkeypatch.setenv("HA_TOKEN", "env-token")
    assert assist.available({}) is True
    assert assist._resolve({}) == ("http://ha.local:8123", "env-token")
