"""Bureau of Meteorology warnings for the house's own location.

Source: the JSON API behind the BOM's own app (api.weather.bom.gov.au), which
answers "what warnings cover this spot" for a 6-character geohash — the same
one the panel's weather lat/lon already describes, so there is nothing to set
up. It is not a published, supported API; if it ever moves, the panel just
shows no warnings and /api/status says why (job "bom").

refresh() runs on the scheduler; the cleaned list is cached in the settings
table so the panel keeps showing a warning through a network blip.
"""
import html
import logging
import re
from datetime import datetime

import requests

import db

log = logging.getLogger("family-panel.bom")

API = "https://api.weather.bom.gov.au/v1"
TIMEOUT = 15
# The BOM turns away requests without a browser-ish agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (family-panel)", "Accept": "application/json"}
CACHE_KEY = "bom:warnings"
_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat: float, lon: float, length: int = 6) -> str:
    lat_r, lon_r = [-90.0, 90.0], [-180.0, 180.0]
    out, bits, ch, even = [], 0, 0, True
    while len(out) < length:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            ch = (ch << 1) | 1
            rng[0] = mid
        else:
            ch <<= 1
            rng[1] = mid
        even = not even
        bits += 1
        if bits == 5:
            out.append(_B32[ch])
            bits, ch = 0, 0
    return "".join(out)


def _text(message: str) -> str:
    """The warning body arrives as HTML; the panel shows plain paragraphs."""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", message or "")
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if ln)[:4000]


def _expired(w: dict) -> bool:
    exp = w.get("expiry_time")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(db.UTC)
    except ValueError:
        return False


def refresh(weather_cfg: dict) -> list[dict]:
    lat = float(weather_cfg.get("lat", -27.48))
    lon = float(weather_cfg.get("lon", 152.98))
    gh = geohash(lat, lon)
    r = requests.get(f"{API}/locations/{gh}/warnings", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    raw = (r.json() or {}).get("data") or []
    old = {w["id"]: w for w in (latest() or {}).get("items", [])}
    items = []
    for w in raw:
        if not isinstance(w, dict) or not w.get("id"):
            continue
        if str(w.get("phase") or "").lower() == "cancelled" or _expired(w):
            continue
        item = {
            "id": w["id"],
            "title": w.get("title") or w.get("short_title") or "Weather warning",
            "short": w.get("short_title") or w.get("title") or "Warning",
            "type": w.get("type"),
            "severity": "major" if str(w.get("warning_group_type")).lower() == "major" else "minor",
            "phase": w.get("phase"),
            "issued": w.get("issue_time"),
            "expires": w.get("expiry_time"),
            "message": None,
        }
        # The full text is a second call per warning; only for ones not
        # already held at this issue time.
        prev = old.get(w["id"])
        if prev and prev.get("issued") == item["issued"] and prev.get("message"):
            item["message"] = prev["message"]
        else:
            try:
                d = requests.get(f"{API}/warnings/{w['id']}", headers=HEADERS, timeout=TIMEOUT)
                d.raise_for_status()
                item["message"] = _text(((d.json() or {}).get("data") or {}).get("message"))
            except Exception as e:          # noqa: BLE001 - the title still stands
                log.info("bom warning %s text: %s", w["id"], e)
        items.append(item)
    items.sort(key=lambda i: (i["severity"] != "major", i.get("issued") or ""))
    db.set_json_setting(CACHE_KEY, {"at": db.utc_now_iso(), "geohash": gh, "items": items})
    return items


def latest() -> dict | None:
    return db.get_json_setting(CACHE_KEY)
