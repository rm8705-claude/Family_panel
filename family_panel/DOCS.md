# Family Panel

A self-hosted family wall calendar: shared Google-synced calendar, chores with
star rewards, lists, a weekly meal planner, "Magic Import" (forward an email —
it turns into a proposed calendar event), and Home Assistant tiles for
aircon, curtains, Sonos and cameras.

No cloud dependency of its own and no subscription — the only outside
services it talks to are the Google Calendar ICS feeds you give it, a
weather API, and (if you use Magic Import) an LLM provider.

If you haven't set this up yet, read `deploy/SETUP.md` in the family-panel
repository first — it's the full beginner's walkthrough from unboxing a
Raspberry Pi to a working panel. This page only covers the add-on's own
configuration options.

## Before you start

Family Panel needs at least one thing to be useful straight away:

- **A Google Calendar ICS feed** under `ics_feeds` — get the "Secret address
  in iCal format" for each calendar from Google Calendar's settings
  (Settings → *your calendar* → "Integrate calendar"). These are read-only —
  Family Panel never writes back to Google.

Everything else (`ha_tiles`, Magic Import, weather) can be added later.

## Configuration options

| Option | What it does |
|---|---|
| `people` | The people whose calendars/chores/stars this panel tracks. Each needs a short `id` (used internally, e.g. `rohan`), a display `name`, and a `colour` (hex, used for their events/chores). Include one entry with `id: family` for shared/whole-family events. |
| `ics_feeds` | One entry per Google Calendar to show. `name` is just a label, `url` is the calendar's secret ICS address, `person` must match one of the `id`s in `people`. |
| `weather.lat` / `weather.lon` | Coordinates for the weather strip (Open-Meteo, no API key needed). Defaults to Brisbane. |
| `ha_tiles` | Home Assistant entities to show as tiles. `entity` is the entity ID (find it under **Developer tools → States**), `label` is what's shown on the tile, `type` is one of `sensor`, `cover`, `switch`, `climate`, `media`, `camera_snapshot`, `presence`. Set `allow_action: true` to let the tile control the entity (open/close, play/pause, setpoint) — every action still needs a confirm tap. The `solar` type is the exception: it has no single `entity`, and instead takes `pv_power`, `load_power`, `grid_power`, `battery_power` and `battery_level` flat on the tile (see `deploy/SETUP.md`). |
| `screen.sleep` / `screen.wake` | 24-hour `HH:MM`. Between these times the panel shows a dim night clock instead of the full dashboard. This is the *in-app* sleep overlay — it doesn't turn a tablet's backlight off; see SETUP.md for that. |
| `admin_pin` | 4-digit PIN required for settings changes and redeeming stars. Change it from the default. |
| `llm_provider` | `anthropic` (cloud, needs `anthropic_api_key`) or `lmstudio` (a local LLM server on your LAN, needs `lmstudio_base_url`) — used only for Magic Import. |
| `anthropic_api_key` | Your Anthropic API key. Only needed if `llm_provider` is `anthropic`. |
| `llm_model` | Defaults to `claude-haiku-4-5`. Leave as-is unless you know why you'd change it. |
| `lmstudio_base_url` | e.g. `http://192.168.1.50:1234/v1` — your LM Studio machine's address on the LAN. Only needed if `llm_provider` is `lmstudio`. |
| `lmstudio_model` | The model name as loaded in LM Studio. |
| `imap_host` / `imap_user` / `imap_app_password` | The dedicated inbox Magic Import polls for forwarded emails. Use a Gmail **App Password** (Google Account → Security → 2-Step Verification → App passwords) — not your normal Gmail password. |
| `sync_minutes` | How often each background job runs, in minutes (`ha` accepts fractions, e.g. `0.25` = 15 seconds). Defaults are sensible; only change these if you know why. |
| `photos.folder` | Local-folder source for the photo screensaver. Defaults to `/media/family_panel_photos` — this add-on maps Home Assistant's own `media` folder in read-only (see **Ports and data** below), so files dropped there via the Samba share's `media` folder are picked up automatically. |
| `photos.shared_album_url` | A Google Photos **shared album** link (`https://photos.app.goo.gl/...`) — the recommended photo source; both parents can add photos to the album from their phones. Treat the link like a password: anyone who has it can view the album. Either `folder`, `shared_album_url`, or both can be set. See `deploy/SETUP.md`'s "Photos on the panel" section for the full walkthrough. |
| `photos.interval_s` / `photos.idle_minutes` | Screensaver timing: how long each photo stays up, and how many touch-free minutes before it starts. |

## Ports and data

- The panel's web UI is on port **8080** — browse to
  `http://homeassistant.local:8080` (or the add-on's **Open Web UI** button).
- Its database and generated `config.yaml` live in `/data`, which the
  Supervisor persists across add-on restarts and updates.
- It uses Home Assistant's own API internally (`homeassistant_api: true`
  above) — no separate long-lived access token needed, unlike the non-add-on
  deployment path.
- The add-on also maps Home Assistant's `/media` folder into the container,
  read-only (`map: media:ro` in `config.yaml`) — this is what lets the photo
  screensaver's `photos.folder` option read files placed under
  `media/family_panel_photos` on the Samba share. The add-on can only read
  from `/media`, never write to it.

## After starting

1. Open the **Log** tab and check for `Configuration written. Starting
   gunicorn on :8080` with no errors underneath.
2. Open the **Web UI** (or browse to `http://homeassistant.local:8080`) —
   you should see the Today view.
3. Check `http://homeassistant.local:8080/api/status` for per-feed and
   per-job last-sync times if something looks stale.

## Troubleshooting

See the troubleshooting table at the end of `deploy/SETUP.md` in the
family-panel repository — it covers the add-on not starting, blank camera
tiles, stale feeds, and `homeassistant.local` not resolving.
