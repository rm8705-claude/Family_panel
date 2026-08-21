"""Home Assistant Assist, proxied for the wall panel.

The panel is a plain browser page and holds no HA credentials, so every
Assist call goes through here and the long-lived token never leaves the
server. Typed questions use the REST conversation API; spoken ones run a
full HA assist pipeline (stt -> intent -> tts) over the websocket API,
which is the only transport that takes streamed audio.

Every failure comes back as a short human sentence — these strings are
rendered on a kitchen wall, not into a log.
"""
import json
import time

import requests
import websocket

import ha

TIMEOUT = 10            # conversation REST call
PIPELINE_TIMEOUT = 30   # whole stt -> intent -> tts run
TTS_TIMEOUT = 15
MAX_TTS_BYTES = 10 * 1024 * 1024
AUDIO_CHUNK = 4096

# The panel hands us a path it was told to fetch, so this must stay an
# allowlist: without it /api/assist/tts?path=... would be an authenticated
# open proxy into every HA endpoint (states, services, config).
TTS_PATH_PREFIXES = ("/api/tts_proxy/", "/api/tts/")

# Seams: tests monkeypatch these rather than the requests/websocket modules.
_post = requests.post
_get = requests.get
_ws_connect = websocket.create_connection


def _resolve(config: dict | None) -> tuple[str, str]:
    """(base url, token) — base has no trailing slash and no /api suffix.

    An explicit `ha: {base_url, token}` block in config wins; otherwise
    ha.py's own rules apply (supervisor token when running as an HA add-on,
    else HA_BASE_URL/HA_TOKEN). Raises ha.NotConfigured.
    """
    block = ((config or {}).get("ha") or {}) if isinstance(config, dict) else {}
    base = str(block.get("base_url") or "").rstrip("/")
    token = str(block.get("token") or "")
    if base and token:
        return base, token
    api, api_token = ha._api_base()
    return (api[:-4] if api.endswith("/api") else api), api_token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _ws_url(base: str) -> str:
    host = base.split("://", 1)[-1]
    scheme = "wss://" if base.startswith("https://") else "ws://"
    # Add-on builds reach core through the supervisor proxy, which publishes
    # the socket at /core/websocket instead of core's own /api/websocket.
    if host.endswith("/core"):
        return f"{scheme}{host}/websocket"
    return f"{scheme}{host}/api/websocket"


def _dig(obj, *keys):
    """Walk nested dicts, returning None the moment the shape disagrees."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _text_of(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def available(config: dict | None) -> bool:
    """True when an HA base URL and token can be resolved."""
    try:
        base, token = _resolve(config)
    except Exception:
        return False
    return bool(base and token)


# ----------------------------------------------------------------- text

def text_query(config: dict | None, text: str) -> dict:
    """Ask Assist a typed question.

    -> {"ok": True, "response_text": str} | {"ok": False, "error": str}
    """
    asked = (text or "").strip()
    if not asked:
        return {"ok": False, "error": "There was nothing to ask."}
    try:
        base, token = _resolve(config)
    except Exception:
        return {"ok": False, "error": "Home Assistant isn't set up yet."}

    try:
        r = _post(f"{base}/api/conversation/process",
                  headers=_headers(token),
                  json={"text": asked, "language": "en"},
                  timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {"ok": False, "error": "Home Assistant didn't answer"}

    speech = _text_of(_dig(data, "response", "speech", "plain", "speech"))
    if not speech:
        return {"ok": False, "error": "Assist answered with nothing to show"}
    return {"ok": True, "response_text": speech}


# ----------------------------------------------------------------- voice

def audio_query(config: dict | None, pcm_bytes: bytes, sample_rate: int = 16000) -> dict:
    """Run one spoken question through the HA assist pipeline.

    `pcm_bytes` is raw 16-bit signed little-endian mono PCM at `sample_rate`
    (no WAV header — the pipeline is told the rate in the run message).

    -> {"ok": True, "heard": str, "response_text": str, "tts_path": str|None}
       | {"ok": False, "error": str}
    """
    try:
        base, token = _resolve(config)
    except Exception:
        return {"ok": False, "error": "Home Assistant isn't set up yet."}
    if not pcm_bytes:
        return {"ok": False, "error": "I didn't hear anything"}

    deadline = time.monotonic() + PIPELINE_TIMEOUT
    ws = None
    try:
        try:
            ws = _ws_connect(_ws_url(base), timeout=PIPELINE_TIMEOUT)
        except Exception:
            return {"ok": False, "error": "Home Assistant didn't answer"}

        auth_error = _authenticate(ws, token)
        if auth_error:
            return {"ok": False, "error": auth_error}

        ws.send(json.dumps({
            "id": 1,
            "type": "assist_pipeline/run",
            "start_stage": "stt",
            "end_stage": "tts",
            "input": {"sample_rate": int(sample_rate)},
        }))
        return _run_pipeline(ws, pcm_bytes, deadline)
    except Exception:
        return {"ok": False, "error": "Voice pipeline error: the connection to "
                                      "Home Assistant dropped"}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


_DEAD_SOCKET_ERROR = ("Voice pipeline error: the connection to Home Assistant "
                      "dropped")


def _authenticate(ws, token: str) -> str | None:
    """HA greets with auth_required, then answers auth_ok / auth_invalid."""
    msg = _recv_json(ws)
    if msg is False:
        return _DEAD_SOCKET_ERROR
    if _dig(msg, "type") == "auth_required":
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        msg = _recv_json(ws)
        if msg is False:
            return _DEAD_SOCKET_ERROR
    if _dig(msg, "type") != "auth_ok":
        return "Home Assistant refused the panel's token"
    return None


def _run_pipeline(ws, pcm_bytes: bytes, deadline: float) -> dict:
    heard = response_text = tts_path = None
    while True:
        if time.monotonic() > deadline:
            return {"ok": False,
                    "error": "Voice pipeline error: Home Assistant took too long"}
        msg = _recv_json(ws)
        if msg is False:                 # dead socket — don't spin to deadline
            return {"ok": False, "error": _DEAD_SOCKET_ERROR}
        if _dig(msg, "type") != "event":
            continue                     # the run's result ack, pongs, etc.
        event = msg.get("event") or {}
        etype = event.get("type")
        data = event.get("data") or {}

        if etype == "error":
            return {"ok": False, "error": _pipeline_error(data)}
        if etype == "run-start":
            handler = _dig(data, "runner_data", "stt_binary_handler_id")
            if not isinstance(handler, int):
                return {"ok": False, "error": "Voice pipeline error: Home Assistant "
                                              "opened no audio channel"}
            _stream_audio(ws, handler, pcm_bytes)
        elif etype == "stt-end":
            heard = _text_of(_dig(data, "stt_output", "text"))
        elif etype == "intent-end":
            response_text = _text_of(
                _dig(data, "intent_output", "response", "speech", "plain", "speech"))
        elif etype == "tts-end":
            tts_path = _text_of(_dig(data, "tts_output", "url"))
        elif etype == "run-end":
            break

    return {"ok": True, "heard": heard or "", "response_text": response_text or "",
            "tts_path": tts_path}


def _stream_audio(ws, handler_id: int, pcm_bytes: bytes) -> None:
    """Send the recording as binary frames.

    Each frame carries the run's stt_binary_handler_id as a single leading
    byte — that prefix is how HA routes the audio to this pipeline run, and
    it is repeated on every frame, not sent once. A frame holding only the
    prefix byte (empty audio) is the end-of-speech signal.
    """
    prefix = bytes([handler_id & 0xFF])
    for start in range(0, len(pcm_bytes), AUDIO_CHUNK):
        ws.send_binary(prefix + pcm_bytes[start:start + AUDIO_CHUNK])
    ws.send_binary(prefix)


def _pipeline_error(data: dict) -> str:
    message = _text_of(data.get("message")) or _text_of(data.get("code")) \
        or "Home Assistant gave up"
    return f"Voice pipeline error: {message}"


def _recv_json(ws):
    """One websocket text frame as a dict.

    Returns False (not None) when recv() raised or came back empty — that's
    a dead socket, distinct from a frame that arrived but merely wasn't JSON.
    Callers must check `is False` before treating a falsy result as "keep
    looping": conflating the two spins a core until the pipeline deadline
    on every dropped connection.
    """
    try:
        raw = ws.recv()
    except Exception:
        return False
    if not raw:
        return False
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception:
        return None


# ----------------------------------------------------------------- tts audio

def tts_fetch(config: dict | None, path: str) -> tuple[bytes, str]:
    """Fetch the audio a pipeline produced. Raises ValueError with a human
    message on a rejected path, missing config, or an HA that won't answer."""
    if not isinstance(path, str) or not path.startswith(TTS_PATH_PREFIXES):
        raise ValueError("That isn't a Home Assistant audio address")
    try:
        base, token = _resolve(config)
    except Exception:
        raise ValueError("Home Assistant isn't set up yet")

    try:
        r = _get(f"{base}{path}",
                 headers={"Authorization": f"Bearer {token}"},
                 timeout=TTS_TIMEOUT)
        r.raise_for_status()
        content = r.content
    except Exception:
        raise ValueError("Home Assistant didn't answer")

    if len(content) > MAX_TTS_BYTES:
        raise ValueError("That answer's audio was too big to play")
    return content, r.headers.get("Content-Type", "audio/mpeg")
