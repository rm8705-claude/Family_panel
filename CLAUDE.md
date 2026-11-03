# Family Panel — the canonical source

This repo IS the Family Panel. Home Assistant installs the add-on straight
from it (Settings → Add-on Store → Repositories), so a push to `main` is a
release: the wall picks it up on the next add-on update.

The old copy in the `rm8705-claude/claude` monorepo (`family-panel/`) is
retired — it went stale at v0.17.3 while this repo moved on, and work was
done against the wrong copy. Do not develop there; its folder is now a
pointer README plus the private install notes.

## Layout
- `family_panel/app/` — the Flask app (app.py, ha.py, db.py, …) and
  `static/` (one vanilla-JS SPA: app.js, styles.css, index.html).
- `family_panel/config.yaml` — add-on manifest. `family_panel/app/config.yaml`
  — the app's default config.
- `tests/` — pytest suite (not shipped in the add-on image).

## Every release
1. Bump `APP_VERSION` in `family_panel/app/app.py` AND `version:` in
   `family_panel/config.yaml` — same number (patch = fix, minor = feature).
2. `pip install -r requirements-dev.txt && pytest -q tests`
3. Check the UI in demo mode: serve `family_panel/app/static/` with `{{V}}`
   filled in and open `/?mock=1` (no backend needed). The wall is 1280x800
   landscape; also check 1024x600 and a phone width.
4. Commit, push `main`, update the add-on in Home Assistant, and confirm
   `http://<panel>:8080/healthz` returns `ok v<APP_VERSION>`.

## Conventions
- Timezone: storage UTC ISO-8601, Australia/Brisbane at the API layer only.
- The API contract is documented in the monorepo's `family-panel/API.md`
  history up to v0.17.3; newer endpoints are documented in code comments.
  Change frontend + backend together.
- This repo is PUBLIC: never commit tokens, calendar ICS URLs, IPs or
  household data. Real config lives in the add-on options on the Pi.
