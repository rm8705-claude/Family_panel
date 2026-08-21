#!/usr/bin/with-contenv bashio
# ==============================================================================
# Family Panel add-on entrypoint.
#
# 1. Reads /data/options.json (the add-on Configuration tab) via bashio.
# 2. Writes it out as /data/config.yaml in the shape config.py expects.
# 3. Exports the .env-equivalent variables the app reads directly.
# 4. Starts gunicorn with exactly one worker.
#
# Background jobs (ICS/IMAP/weather/HA sync) start at app-import time when
# PANEL_JOBS=1 (exported below), so they run under gunicorn. Exactly one
# worker is required so APScheduler can't double-fire.
# ==============================================================================
set -e

bashio::log.info "Family Panel: reading add-on configuration..."

# ---- 1 & 2: options.json -> /data/config.yaml -------------------------------
# Uses PyYAML, which is already installed (it's a requirements.txt dependency
# of the app itself) rather than hand-rolling a YAML writer here.
python3 - <<'PYEOF'
import json
import yaml

with open("/data/options.json", encoding="utf-8") as f:
    opts = json.load(f)

cfg = {
    "people": opts.get("people", []),
    "ics_feeds": opts.get("ics_feeds", []),
    "weather": opts.get("weather", {"lat": -27.48, "lon": 152.98}),
    "ha_tiles": opts.get("ha_tiles", []),
    "screen": opts.get("screen", {"sleep": "21:00", "wake": "05:45"}),
    "sync_minutes": opts.get(
        "sync_minutes", {"ics": 5, "imap": 5, "weather": 30, "ha": 0.25}
    ),
    "photos": opts.get(
        "photos",
        {"folder": "/media/family_panel_photos", "shared_album_url": "",
         "interval_s": 45, "idle_minutes": 3},
    ),
}

with open("/data/config.yaml", "w", encoding="utf-8") as f:
    f.write("# Generated on every start from the add-on Configuration tab. Do not edit by\n")
    f.write("# hand — your changes will be overwritten on the next restart.\n")
    yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

print("Wrote /data/config.yaml")
PYEOF

# ---- 3: export secrets / env vars for the Flask app --------------------------
export ADMIN_PIN LLM_PROVIDER ANTHROPIC_API_KEY LLM_MODEL LMSTUDIO_BASE_URL \
       LMSTUDIO_MODEL IMAP_HOST IMAP_USER IMAP_APP_PASSWORD \
       PANEL_DB PANEL_CONFIG PANEL_HOST PANEL_PORT PANEL_JOBS

ADMIN_PIN="$(bashio::config 'admin_pin' '0000')"
LLM_PROVIDER="$(bashio::config 'llm_provider' 'anthropic')"
ANTHROPIC_API_KEY="$(bashio::config 'anthropic_api_key' '')"
LLM_MODEL="$(bashio::config 'llm_model' 'claude-haiku-4-5')"
LMSTUDIO_BASE_URL="$(bashio::config 'lmstudio_base_url' '')"
LMSTUDIO_MODEL="$(bashio::config 'lmstudio_model' 'local-model')"
IMAP_HOST="$(bashio::config 'imap_host' 'imap.gmail.com')"
IMAP_USER="$(bashio::config 'imap_user' '')"
IMAP_APP_PASSWORD="$(bashio::config 'imap_app_password' '')"

# HA_BASE_URL / HA_TOKEN are deliberately NOT set here: config.py's
# running_as_ha_addon() detects SUPERVISOR_TOKEN (which the Supervisor
# injects automatically because homeassistant_api: true is set in
# config.yaml) and talks to http://supervisor/core/api instead. That only
# happens when running as this add-on — HA_BASE_URL/HA_TOKEN are for the
# non-add-on deployment path (Option B), set via .env there instead.

PANEL_DB=/data/panel.db
PANEL_CONFIG=/data/config.yaml
PANEL_HOST=0.0.0.0
PANEL_PORT=8080
PANEL_JOBS=1

bashio::log.info "Configuration written. Starting gunicorn on :8080 (1 worker)..."
cd /app
# --timeout 90: assist.audio_query legitimately blocks up to ~60s (30s HA
# connect + 30s pipeline run); gunicorn's default 30s worker timeout would
# kill the worker mid-request.
exec gunicorn --workers 1 --timeout 90 --bind 0.0.0.0:8080 app:app
