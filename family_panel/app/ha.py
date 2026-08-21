"""Home Assistant REST client.

The panel never talks to vendor APIs; HA is the universal adapter. Runs
against HA_BASE_URL + long-lived token, or — when shipped as an HA add-on —
the supervisor internal API (SUPERVISOR_TOKEN present).

State polling caches into the settings table so /api/ha/tiles can keep
serving the last-known state (greyed out) while HA is unreachable.
"""
import os

import requests

import db

TIMEOUT = 10


class NotConfigured(Exception):
    pass


def _api_base() -> tuple[str, str]:
    sup = os.environ.get("SUPERVISOR_TOKEN")
    if sup:
        return "http://supervisor/core/api", sup
    base = os.environ.get("HA_BASE_URL", "").rstrip("/")
    token = os.environ.get("HA_TOKEN", "")
    if not base or not token:
        raise NotConfigured("HA_BASE_URL/HA_TOKEN not set")
    return base + "/api", token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


SOLAR_KEYS = ("pv_power", "load_power", "grid_power", "battery_power", "battery_level")


def solar_entities(tile: dict) -> dict:
    """The solar tile's several sensors, from either shape it can be written in.

    Hand-written config.yaml (the non-add-on path) nests them under `entities`.
    The add-on's Configuration tab writes them flat on the tile instead, because
    the Supervisor makes any nested schema object mandatory — nesting there
    would force every unrelated tile to carry an empty `entities: {}`.
    """
    nested = tile.get("entities")
    if isinstance(nested, dict) and nested:
        return nested
    return {k: tile[k] for k in SOLAR_KEYS if tile.get(k)}


def wanted_entities(tiles: list[dict]) -> set[str]:
    """Every entity any tile needs. Most tiles have one `entity`; the solar
    tile maps several sensors through `entities`/flat solar keys."""
    out = set()
    for t in tiles:
        if t.get("entity"):
            out.add(t["entity"])
        for v in solar_entities(t).values():
            if v:
                out.add(v)
    return out


def refresh(tiles: list[dict]) -> None:
    """Poll /api/states once and cache the configured entities' states."""
    if not tiles:
        raise NotConfigured("no tiles configured")
    base, token = _api_base()
    wanted = wanted_entities(tiles)
    r = requests.get(f"{base}/states", headers=_headers(token), timeout=TIMEOUT)
    r.raise_for_status()
    states = {s["entity_id"]: {"state": s.get("state"),
                               "attributes": s.get("attributes", {})}
              for s in r.json() if s.get("entity_id") in wanted}
    db.set_json_setting("ha:states", {"at": db.utc_now_iso(), "states": states})


def tile_shape(tile: dict, cached: dict | None) -> dict:
    """One tile's JSON per the API contract; attrs vary by tile type."""
    ttype = tile.get("type", "sensor")
    out = {
        "entity": tile["entity"],
        "label": tile.get("label", tile["entity"]),
        "type": ttype,
        "allow_action": bool(tile.get("allow_action")),
        "state": None,
        "attrs": {},
    }
    if not cached:
        return out
    state = cached.get("state")
    attrs = cached.get("attributes", {})
    out["state"] = state
    if ttype == "sensor":
        out["attrs"] = {"value": state,
                        "unit": attrs.get("unit_of_measurement", "")}
    elif ttype == "climate":
        out["attrs"] = {"current_temp": attrs.get("current_temperature"),
                        "setpoint": attrs.get("temperature"),
                        "hvac_mode": state}
    elif ttype == "media":
        out["attrs"] = {"title": attrs.get("media_title"),
                        "artist": attrs.get("media_artist"),
                        "volume": attrs.get("volume_level"),
                        "playing": state == "playing",
                        # Sonos favourites (radio, saved playlists) surface as
                        # the entity's source_list — the panel's "play something"
                        "source_list": attrs.get("source_list") or [],
                        "source": attrs.get("source"),
                        "entity_picture": attrs.get("entity_picture")}
    elif ttype == "fan":
        out["attrs"] = {"percentage": attrs.get("percentage"),
                        "oscillating": attrs.get("oscillating"),
                        "preset_mode": attrs.get("preset_mode"),
                        "on": state == "on"}
    elif ttype == "camera_snapshot":
        out["attrs"] = {"snapshot_url": f"/api/ha/camera/{tile['entity']}"}
    elif ttype == "presence":
        out["attrs"] = {"home": state == "home"}
    # cover / switch carry everything in state
    return out


# entity domain -> allowed action -> (service domain, service, payload builder)
ACTIONS = {
    "cover": {
        "open": ("cover", "open_cover", lambda v: {}),
        "close": ("cover", "close_cover", lambda v: {}),
        "stop": ("cover", "stop_cover", lambda v: {}),
    },
    "climate": {
        "set_temperature": ("climate", "set_temperature",
                            lambda v: {"temperature": float(v)}),
        "turn_on": ("climate", "turn_on", lambda v: {}),
        "turn_off": ("climate", "turn_off", lambda v: {}),
    },
    "switch": {
        "turn_on": ("switch", "turn_on", lambda v: {}),
        "turn_off": ("switch", "turn_off", lambda v: {}),
    },
    "media_player": {
        "play": ("media_player", "media_play", lambda v: {}),
        "pause": ("media_player", "media_pause", lambda v: {}),
        "next": ("media_player", "media_next_track", lambda v: {}),
        "previous": ("media_player", "media_previous_track", lambda v: {}),
        "volume_set": ("media_player", "volume_set",
                       lambda v: {"volume_level": max(0.0, min(1.0, float(v)))}),
        # v = a name from the tile's source_list (a Sonos favourite)
        "select_source": ("media_player", "select_source",
                          lambda v: {"source": str(v)}),
    },
    "fan": {
        "turn_on": ("fan", "turn_on", lambda v: {}),
        "turn_off": ("fan", "turn_off", lambda v: {}),
        "set_percentage": ("fan", "set_percentage",
                           lambda v: {"percentage": max(0, min(100, int(v)))}),
        "oscillate_on": ("fan", "oscillate", lambda v: {"oscillating": True}),
        "oscillate_off": ("fan", "oscillate", lambda v: {"oscillating": False}),
    },
}


def call_action(entity: str, action: str, value=None) -> None:
    domain = entity.split(".", 1)[0]
    table = ACTIONS.get(domain, {})
    if action not in table:
        raise ValueError(f"Action '{action}' not allowed for {domain}")
    service_domain, service, payload = table[action]
    base, token = _api_base()
    body = {"entity_id": entity, **payload(value)}
    r = requests.post(f"{base}/services/{service_domain}/{service}",
                      headers=_headers(token), json=body, timeout=TIMEOUT)
    r.raise_for_status()


def camera_snapshot(entity: str) -> tuple[bytes, str]:
    base, token = _api_base()
    r = requests.get(f"{base}/camera_proxy/{entity}",
                     headers=_headers(token), timeout=TIMEOUT)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "image/jpeg")


def solar_shape(tile: dict, states: dict) -> dict:
    """The solar/battery power-flow tile (Sungrow SH + SBR via the mkaiser
    HACS integration's sensors — but any integration exposing equivalent
    power sensors works).

    Sign conventions in attrs (normalise the integration's signs with the
    tile's invert_* flags):
      battery_w  positive = charging, negative = discharging
      grid_w     positive = exporting, negative = importing
    Power values are watts; set power_unit: kW on the tile if the sensors
    report kilowatts and they'll be scaled up.
    """
    ents = solar_entities(tile)
    scale = 1000.0 if str(tile.get("power_unit", "W")).lower() == "kw" else 1.0

    def num(key, scaled=True):
        ent = ents.get(key)
        raw = (states.get(ent) or {}).get("state") if ent else None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return v * scale if scaled else v

    pv = num("pv_power")
    load = num("load_power")
    grid = num("grid_power")
    battery_w = num("battery_power")
    battery_pct = num("battery_level", scaled=False)
    if grid is not None and tile.get("invert_grid"):
        grid = -grid
    if battery_w is not None and tile.get("invert_battery"):
        battery_w = -battery_w

    # derive whichever of load/grid is missing when the rest are known
    if load is None and None not in (pv, grid, battery_w):
        load = pv - grid - battery_w

    return {
        "entity": tile.get("entity") or "solar",
        "label": tile.get("label", "Solar"),
        "type": "solar",
        "allow_action": False,           # read-only by design
        "state": "ok" if pv is not None or battery_pct is not None else None,
        "attrs": {
            "pv_w": pv, "load_w": load, "grid_w": grid,
            "battery_w": battery_w, "battery_pct": battery_pct,
        },
    }


def cached_states() -> dict | None:
    return db.get_json_setting("ha:states")
