"""Config load for Family Panel: .env (secrets) + config.yaml (structure)."""
import os

import yaml
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULTS = {
    "people": [],
    "ics_feeds": [],
    "weather": {"lat": -27.48, "lon": 152.98},
    "ha_tiles": [],
    "screen": {"sleep": "21:00", "wake": "05:45"},
    "sync_minutes": {"ics": 5, "imap": 5, "weather": 30, "ha": 0.25},
}


def load_config(path: str | None = None) -> dict:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    path = path or os.environ.get("PANEL_CONFIG", os.path.join(BASE_DIR, "config.yaml"))
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for k, v in loaded.items():
            cfg[k] = v
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return cfg


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def running_as_ha_addon() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))
