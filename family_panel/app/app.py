"""Family Panel — self-hosted family wall calendar.

Flask backend + JSON API. Frontend is a single-page vanilla-JS app in static/.
Not an icubed work tool: domestic design system, no icubed branding.
"""
import json
import os
from datetime import date, datetime, time, timedelta

from flask import Flask, jsonify, request, send_from_directory

import db
from config import BASE_DIR, load_config, env

APP_VERSION = "0.15.15"

CONFIG = load_config()
db.init_db(CONFIG)

import recipes as _recipes_mod
_recipes_mod.seed_starters()
_recipes_mod.upgrade_starter_methods()

app = Flask(__name__, static_folder="static", static_url_path="/static")


def admin_pin_ok() -> bool:
    pin = env("ADMIN_PIN", "0000")
    return request.headers.get("X-Admin-Pin", "") == pin


def require_pin():
    if not admin_pin_ok():
        return jsonify({"error": "Admin PIN required"}), 403
    return None


# ------------------------------------------------------------------ shell

@app.get("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "static"), "index.html")


@app.get("/healthz")
def healthz():
    return f"ok v{APP_VERSION}"


# ------------------------------------------------------------------ status

@app.get("/api/status")
def api_status():
    jobs = {}
    for job in ("ics", "imap", "weather", "ha", "photos"):
        jobs[job] = db.get_json_setting(f"sync:{job}")
    feeds = {}
    for feed in CONFIG.get("ics_feeds", []):
        feeds[feed["name"]] = db.get_json_setting(f"sync:feed:{feed['name']}")
    return jsonify({
        "version": APP_VERSION,
        "now_utc": db.utc_now_iso(),
        "today_local": db.local_today().isoformat(),
        "jobs": jobs,
        "feeds": feeds,
        "config": {
            "people": CONFIG.get("people", []),
            "screen": CONFIG.get("screen", {}),
            # feed metadata only — the secret URLs never leave the backend
            "feeds": [
                {"name": f.get("name"), "person": f.get("person"),
                 "kind": f.get("kind", "calendar")}
                for f in CONFIG.get("ics_feeds", []) or []
            ],
            "ha_tiles": [
                {k: v for k, v in t.items() if k != "token"}
                for t in CONFIG.get("ha_tiles", [])
            ],
            "assist": _assist_available(),
        },
    })


def _assist_available():
    import assist
    return assist.available(CONFIG)


# ------------------------------------------------------------------ agenda

def _parse_date(s, fallback: date | None = None) -> date:
    if not s:
        if fallback is None:
            raise ValueError("date required")
        return fallback
    return date.fromisoformat(s)


def _event_window_rows(from_d: date, to_d: date):
    """All ICS + local events overlapping the inclusive local date range."""
    win_start = db.local_date_to_utc_iso(from_d)
    win_end = db.local_date_to_utc_iso(to_d + timedelta(days=1))
    conn = db.connect()
    events = []
    for row in conn.execute(
            "SELECT * FROM ics_cache WHERE start_utc < ? AND end_utc > ?",
            (win_end, win_start)):
        events.append(db.event_local_shape(row, "ics", feed=row["feed"]))
    for row in conn.execute(
            "SELECT * FROM events_local WHERE start_utc < ? AND end_utc > ?",
            (win_end, win_start)):
        events.append(db.event_local_shape(row, "local"))
    # within a date: all-day first, then by start time, then title
    events.sort(key=lambda e: (e["date"], 0 if e["all_day"] else 1,
                               e["start"] or "", e["title"].lower()))
    return events


@app.get("/api/agenda")
def api_agenda():
    try:
        from_d = _parse_date(request.args.get("from"), db.local_today())
        to_d = _parse_date(request.args.get("to"), from_d + timedelta(days=1))
    except ValueError:
        return jsonify({"error": "Bad date — use YYYY-MM-DD"}), 400
    return jsonify({
        "from": from_d.isoformat(), "to": to_d.isoformat(),
        "events": _event_window_rows(from_d, to_d),
        "last_sync": db.get_json_setting("sync:ics"),
    })


# ------------------------------------------------------------------ local events

def _event_times_from_payload(body: dict, existing: dict | None = None):
    """Turn {date, all_day, start, end, end_date} into stored UTC strings."""
    cur = existing or {}
    d = _parse_date(body.get("date") or cur.get("date"))
    all_day = bool(body.get("all_day", cur.get("all_day", False)))
    if all_day:
        end_raw = body.get("end_date") or cur.get("end_date")
        end_d = _parse_date(end_raw, d)
        if end_d < d:
            end_d = d
        return (db.local_date_to_utc_iso(d),
                db.local_date_to_utc_iso(end_d + timedelta(days=1)), 1)
    start_s = body.get("start", cur.get("start")) or "09:00"
    end_s = body.get("end", cur.get("end"))
    start_t = time.fromisoformat(start_s)
    start_dt = datetime.combine(d, start_t, tzinfo=db.LOCAL_TZ)
    if end_s:
        end_dt = datetime.combine(d, time.fromisoformat(end_s), tzinfo=db.LOCAL_TZ)
        if end_dt <= start_dt:                    # crosses midnight
            end_dt += timedelta(days=1)
    else:
        end_dt = start_dt + timedelta(hours=1)
    return db.to_utc_iso(start_dt), db.to_utc_iso(end_dt), 0


def _local_event_json(event_id: int):
    row = db.connect().execute(
        "SELECT * FROM events_local WHERE id=?", (event_id,)).fetchone()
    return db.event_local_shape(row, "local") if row else None


@app.post("/api/events")
def api_event_create():
    body = request.get_json(force=True, silent=True) or {}
    if not (body.get("title") or "").strip():
        return jsonify({"error": "Title is required"}), 400
    try:
        start_utc, end_utc, all_day = _event_times_from_payload(body)
    except (ValueError, TypeError):
        return jsonify({"error": "Bad date or time — use YYYY-MM-DD and HH:MM"}), 400
    conn = db.connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO events_local(title, person_id, start_utc, end_utc, all_day,"
            " location, notes, countdown, source) VALUES(?,?,?,?,?,?,?,?, 'manual')",
            (body["title"].strip(), body.get("person_id"), start_utc, end_utc, all_day,
             body.get("location"), body.get("notes"),
             1 if body.get("countdown") else 0))
    return jsonify(_local_event_json(cur.lastrowid)), 201


@app.patch("/api/events/<int:event_id>")
def api_event_update(event_id):
    existing = _local_event_json(event_id)
    if existing is None:
        return jsonify({"error": "Event not found (Google events are read-only)"}), 404
    body = request.get_json(force=True, silent=True) or {}
    try:
        start_utc, end_utc, all_day = _event_times_from_payload(body, existing)
    except (ValueError, TypeError):
        return jsonify({"error": "Bad date or time — use YYYY-MM-DD and HH:MM"}), 400
    fields = {
        "title": (body.get("title") or existing["title"]).strip(),
        "person_id": body.get("person_id", existing["person_id"]),
        "location": body.get("location", existing["location"]),
        "notes": body.get("notes", existing["notes"]),
        "countdown": 1 if body.get("countdown", existing["countdown"]) else 0,
        "start_utc": start_utc, "end_utc": end_utc, "all_day": all_day,
    }
    conn = db.connect()
    with conn:
        conn.execute(
            "UPDATE events_local SET title=?, person_id=?, location=?, notes=?,"
            " countdown=?, start_utc=?, end_utc=?, all_day=? WHERE id=?",
            (*fields.values(), event_id))
    return jsonify(_local_event_json(event_id))


@app.delete("/api/events/<int:event_id>")
def api_event_delete(event_id):
    conn = db.connect()
    with conn:
        cur = conn.execute("DELETE FROM events_local WHERE id=?", (event_id,))
    if cur.rowcount == 0:
        return jsonify({"error": "Event not found (Google events are read-only)"}), 404
    return jsonify({"ok": True})


# ------------------------------------------------------------------ chores + stars

WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _stars_earned(conn, person_id: str, from_d: date | None = None,
                  to_d: date | None = None) -> int:
    q = ("SELECT COALESCE(SUM(c.stars),0) AS s FROM chore_done cd"
         " JOIN chores c ON c.id = cd.chore_id WHERE c.person_id=?")
    args: list = [person_id]
    if from_d:
        q += " AND cd.date >= ?"
        args.append(from_d.isoformat())
    if to_d:
        q += " AND cd.date <= ?"
        args.append(to_d.isoformat())
    return conn.execute(q, args).fetchone()["s"]


@app.get("/api/chores/today")
def api_chores_today():
    try:
        d = _parse_date(request.args.get("date"), db.local_today())
    except ValueError:
        return jsonify({"error": "Bad date — use YYYY-MM-DD"}), 400
    weekday = WEEKDAY_CODES[d.weekday()]
    week_start = _monday(d)
    conn = db.connect()
    done_ids = {r["chore_id"] for r in conn.execute(
        "SELECT chore_id FROM chore_done WHERE date=?", (d.isoformat(),))}
    slot_order = {"morning": 0, "afternoon": 1, "evening": 2}
    people = []
    for p in CONFIG.get("people", []):
        chores = [
            {"id": r["id"], "title": r["title"], "slot": r["slot"],
             "stars": r["stars"], "done": r["id"] in done_ids}
            for r in conn.execute(
                "SELECT * FROM chores WHERE person_id=? AND days LIKE ?",
                (p["id"], f"%{weekday}%"))
        ]
        chores.sort(key=lambda c: (slot_order.get(c["slot"], 3), c["title"].lower()))
        people.append({
            "id": p["id"], "name": p["name"], "colour": p["colour"],
            "stars_week": _stars_earned(conn, p["id"], week_start,
                                        week_start + timedelta(days=6)),
            "chores": chores,
        })
    return jsonify({"date": d.isoformat(), "weekday": weekday, "people": people})


@app.post("/api/chores/<int:chore_id>/toggle")
def api_chore_toggle(chore_id):
    try:
        d = _parse_date(request.args.get("date"), db.local_today())
    except ValueError:
        return jsonify({"error": "Bad date — use YYYY-MM-DD"}), 400
    conn = db.connect()
    if not conn.execute("SELECT 1 FROM chores WHERE id=?", (chore_id,)).fetchone():
        return jsonify({"error": "Chore not found"}), 404
    with conn:
        cur = conn.execute("DELETE FROM chore_done WHERE chore_id=? AND date=?",
                           (chore_id, d.isoformat()))
        done = False
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO chore_done(chore_id, date, done_at) VALUES(?,?,?)",
                (chore_id, d.isoformat(), db.utc_now_iso()))
            done = True
    return jsonify({"id": chore_id, "date": d.isoformat(), "done": done})


@app.get("/api/chores")
def api_chores_list():
    rows = db.connect().execute("SELECT * FROM chores ORDER BY person_id, slot").fetchall()
    return jsonify({"chores": [dict(r) for r in rows]})


def _chore_payload_ok(body):
    days = [x.strip().upper() for x in (body.get("days") or "").split(",") if x.strip()]
    if not (body.get("title") or "").strip():
        return None, "Title is required"
    if body.get("person_id") not in {p["id"] for p in CONFIG.get("people", [])}:
        return None, "Unknown person"
    if not days or any(x not in WEEKDAY_CODES for x in days):
        return None, "Days must be codes like MO,TU,WE"
    if body.get("slot", "morning") not in ("morning", "afternoon", "evening"):
        return None, "Slot must be morning, afternoon or evening"
    return {
        "title": body["title"].strip(), "person_id": body["person_id"],
        "days": ",".join(days), "slot": body.get("slot", "morning"),
        "stars": max(0, int(body.get("stars", 1))),
    }, None


@app.post("/api/chores")
def api_chore_create():
    if (resp := require_pin()) is not None:
        return resp
    payload, err = _chore_payload_ok(request.get_json(force=True, silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    conn = db.connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO chores(title, person_id, days, slot, stars) VALUES(?,?,?,?,?)",
            tuple(payload.values()))
    row = conn.execute("SELECT * FROM chores WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.patch("/api/chores/<int:chore_id>")
def api_chore_update(chore_id):
    if (resp := require_pin()) is not None:
        return resp
    conn = db.connect()
    row = conn.execute("SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Chore not found"}), 404
    body = {**dict(row), **(request.get_json(force=True, silent=True) or {})}
    payload, err = _chore_payload_ok(body)
    if err:
        return jsonify({"error": err}), 400
    with conn:
        conn.execute(
            "UPDATE chores SET title=?, person_id=?, days=?, slot=?, stars=? WHERE id=?",
            (*payload.values(), chore_id))
    return jsonify(dict(conn.execute(
        "SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()))


@app.delete("/api/chores/<int:chore_id>")
def api_chore_delete(chore_id):
    if (resp := require_pin()) is not None:
        return resp
    conn = db.connect()
    with conn:
        cur = conn.execute("DELETE FROM chores WHERE id=?", (chore_id,))
        conn.execute("DELETE FROM chore_done WHERE chore_id=?", (chore_id,))
    if cur.rowcount == 0:
        return jsonify({"error": "Chore not found"}), 404
    return jsonify({"ok": True})


@app.get("/api/stars")
def api_stars():
    conn = db.connect()
    week_start = _monday(db.local_today())
    people = []
    for p in CONFIG.get("people", []):
        earned_total = _stars_earned(conn, p["id"])
        redeemed = conn.execute(
            "SELECT COALESCE(SUM(stars),0) AS s FROM star_redemptions WHERE person_id=?",
            (p["id"],)).fetchone()["s"]
        people.append({
            "id": p["id"],
            "earned_week": _stars_earned(conn, p["id"], week_start,
                                         week_start + timedelta(days=6)),
            "earned_total": earned_total,
            "redeemed_total": redeemed,
            "balance": earned_total - redeemed,
        })
    return jsonify({"people": people})


@app.post("/api/stars/redeem")
def api_stars_redeem():
    if (resp := require_pin()) is not None:
        return resp
    body = request.get_json(force=True, silent=True) or {}
    person_id = body.get("person_id")
    try:
        stars = int(body.get("stars", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Stars must be a number"}), 400
    if person_id not in {p["id"] for p in CONFIG.get("people", [])}:
        return jsonify({"error": "Unknown person"}), 400
    if stars <= 0:
        return jsonify({"error": "Stars must be at least 1"}), 400
    conn = db.connect()
    balance = (_stars_earned(conn, person_id)
               - conn.execute("SELECT COALESCE(SUM(stars),0) AS s FROM star_redemptions"
                              " WHERE person_id=?", (person_id,)).fetchone()["s"])
    if stars > balance:
        return jsonify({"error": f"Only {balance} stars available"}), 400
    with conn:
        conn.execute(
            "INSERT INTO star_redemptions(person_id, stars, note, date) VALUES(?,?,?,?)",
            (person_id, stars, body.get("note"), db.local_today().isoformat()))
    return jsonify({"ok": True, "balance": balance - stars})


# ------------------------------------------------------------------ lists

def _list_json(conn, list_id: int):
    row = conn.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()
    if row is None:
        return None
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM list_items WHERE list_id=? ORDER BY done, id", (list_id,))]
    return {**dict(row), "items": items}


@app.get("/api/lists")
def api_lists():
    conn = db.connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM lists ORDER BY id")]
    return jsonify({"lists": [_list_json(conn, i) for i in ids]})


@app.post("/api/lists")
def api_list_create():
    body = request.get_json(force=True, silent=True) or {}
    if not (body.get("name") or "").strip():
        return jsonify({"error": "Name is required"}), 400
    kind = body.get("kind", "custom")
    if kind not in ("grocery", "todo", "custom"):
        kind = "custom"
    conn = db.connect()
    with conn:
        cur = conn.execute("INSERT INTO lists(name, kind) VALUES(?,?)",
                           (body["name"].strip(), kind))
    return jsonify(_list_json(conn, cur.lastrowid)), 201


@app.delete("/api/lists/<int:list_id>")
def api_list_delete(list_id):
    conn = db.connect()
    with conn:
        cur = conn.execute("DELETE FROM lists WHERE id=?", (list_id,))
        conn.execute("DELETE FROM list_items WHERE list_id=?", (list_id,))
    if cur.rowcount == 0:
        return jsonify({"error": "List not found"}), 404
    return jsonify({"ok": True})


@app.post("/api/lists/<int:list_id>/items")
def api_item_create(list_id):
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Text is required"}), 400
    conn = db.connect()
    if not conn.execute("SELECT 1 FROM lists WHERE id=?", (list_id,)).fetchone():
        return jsonify({"error": "List not found"}), 404
    with conn:
        cur = conn.execute(
            "INSERT INTO list_items(list_id, text, done, created_at) VALUES(?,?,0,?)",
            (list_id, text, db.utc_now_iso()))
    row = conn.execute("SELECT * FROM list_items WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.patch("/api/items/<int:item_id>")
def api_item_update(item_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM list_items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Item not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or row["text"]).strip() or row["text"]
    done = 1 if body.get("done", bool(row["done"])) else 0
    with conn:
        conn.execute("UPDATE list_items SET text=?, done=? WHERE id=?",
                     (text, done, item_id))
    return jsonify(dict(conn.execute(
        "SELECT * FROM list_items WHERE id=?", (item_id,)).fetchone()))


@app.delete("/api/items/<int:item_id>")
def api_item_delete(item_id):
    conn = db.connect()
    with conn:
        cur = conn.execute("DELETE FROM list_items WHERE id=?", (item_id,))
    if cur.rowcount == 0:
        return jsonify({"error": "Item not found"}), 404
    return jsonify({"ok": True})


# ------------------------------------------------------------------ recipes + meal plan

def _recipe_json(row) -> dict:
    try:
        ingredients = json.loads(row["ingredients"] or "[]")
    except ValueError:
        ingredients = []
    try:
        meta = json.loads(row["meta"] or "{}")
    except (ValueError, KeyError, IndexError):
        meta = {}
    return {"id": row["id"], "name": row["name"], "notes": row["notes"],
            "ingredients": ingredients,
            "source_url": row["source_url"], "meta": meta}


@app.get("/api/recipes")
def api_recipes():
    rows = db.connect().execute("SELECT * FROM recipes ORDER BY name").fetchall()
    return jsonify({"recipes": [_recipe_json(r) for r in rows]})


@app.post("/api/recipes")
def api_recipe_create():
    body = request.get_json(force=True, silent=True) or {}
    if not (body.get("name") or "").strip():
        return jsonify({"error": "Name is required"}), 400
    ingredients = [str(x).strip() for x in body.get("ingredients", []) if str(x).strip()]
    conn = db.connect()
    with conn:
        cur = conn.execute("INSERT INTO recipes(name, notes, ingredients) VALUES(?,?,?)",
                           (body["name"].strip(), body.get("notes"),
                            json.dumps(ingredients)))
    return jsonify(_recipe_json(conn.execute(
        "SELECT * FROM recipes WHERE id=?", (cur.lastrowid,)).fetchone())), 201


@app.patch("/api/recipes/<int:recipe_id>")
def api_recipe_update(recipe_id):
    conn = db.connect()
    row = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Recipe not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    current = _recipe_json(row)
    name = (body.get("name") or current["name"]).strip()
    notes = body.get("notes", current["notes"])
    ingredients = body.get("ingredients", current["ingredients"])
    ingredients = [str(x).strip() for x in ingredients if str(x).strip()]
    with conn:
        conn.execute("UPDATE recipes SET name=?, notes=?, ingredients=? WHERE id=?",
                     (name, notes, json.dumps(ingredients), recipe_id))
    return jsonify(_recipe_json(conn.execute(
        "SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()))


@app.delete("/api/recipes/<int:recipe_id>")
def api_recipe_delete(recipe_id):
    conn = db.connect()
    with conn:
        cur = conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
    if cur.rowcount == 0:
        return jsonify({"error": "Recipe not found"}), 404
    return jsonify({"ok": True})


@app.post("/api/recipes/import")
def api_recipe_import():
    import recipes as recipes_mod
    body = request.get_json(force=True, silent=True) or {}
    url = (body.get("url") or "").strip()
    try:
        parsed = recipes_mod.fetch_and_parse(url)
    except recipes_mod.ImportError_ as e:
        return jsonify({"error": str(e)}), 422
    conn = db.connect()
    with conn:
        cur = conn.execute(
            "INSERT INTO recipes(name, notes, ingredients, source_url, meta)"
            " VALUES(?,?,?,?,?)",
            (parsed["name"], parsed["notes"], json.dumps(parsed["ingredients"]),
             parsed["source_url"], json.dumps(parsed["meta"])))
    recipe_id = cur.lastrowid
    if parsed["meta"].get("image_url"):
        if recipes_mod.cache_image(recipe_id, parsed["meta"]["image_url"]):
            parsed["meta"]["image_cached"] = True
            with conn:
                conn.execute("UPDATE recipes SET meta=? WHERE id=?",
                             (json.dumps(parsed["meta"]), recipe_id))
    return jsonify(_recipe_json(conn.execute(
        "SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone())), 201


@app.get("/api/recipes/<int:recipe_id>/image")
def api_recipe_image(recipe_id):
    import recipes as recipes_mod
    path = recipes_mod.cached_image_path(recipe_id)
    if path is None:
        return jsonify({"error": "No photo for this recipe"}), 404
    mime = {"jpg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}[path.rsplit(".", 1)[1]]
    with open(path, "rb") as f:
        return app.response_class(f.read(), mimetype=mime)


MEAL_SLOTS = ("dinner", "lunch", "other")


@app.get("/api/mealplan")
def api_mealplan():
    try:
        week_start = _monday(_parse_date(request.args.get("week"), db.local_today()))
    except ValueError:
        return jsonify({"error": "Bad date — use YYYY-MM-DD"}), 400
    conn = db.connect()
    days = []
    for i in range(7):
        d = (week_start + timedelta(days=i)).isoformat()
        day = {"date": d, "dinner": None, "lunch": None, "other": None}
        for r in conn.execute(
                "SELECT mp.*, rec.name AS recipe_name FROM meal_plan mp"
                " LEFT JOIN recipes rec ON rec.id = mp.recipe_id WHERE mp.date=?", (d,)):
            day[r["slot"]] = {"recipe_id": r["recipe_id"],
                              "recipe_name": r["recipe_name"],
                              "free_text": r["free_text"]}
        days.append(day)
    return jsonify({"week_start": week_start.isoformat(), "days": days})


@app.put("/api/mealplan")
def api_mealplan_put():
    body = request.get_json(force=True, silent=True) or {}
    try:
        d = _parse_date(body.get("date"))
    except (ValueError, TypeError):
        return jsonify({"error": "Bad date — use YYYY-MM-DD"}), 400
    slot = body.get("slot", "dinner")
    if slot not in MEAL_SLOTS:
        return jsonify({"error": "Slot must be dinner, lunch or other"}), 400
    recipe_id = body.get("recipe_id")
    free_text = (body.get("free_text") or "").strip() or None
    conn = db.connect()
    if recipe_id is not None and not conn.execute(
            "SELECT 1 FROM recipes WHERE id=?", (recipe_id,)).fetchone():
        return jsonify({"error": "Recipe not found"}), 404
    with conn:
        if recipe_id is None and free_text is None:
            conn.execute("DELETE FROM meal_plan WHERE date=? AND slot=?",
                         (d.isoformat(), slot))
            return jsonify({"date": d.isoformat(), "slot": slot, "cleared": True})
        conn.execute(
            "INSERT INTO meal_plan(date, slot, recipe_id, free_text) VALUES(?,?,?,?)"
            " ON CONFLICT(date, slot) DO UPDATE SET recipe_id=excluded.recipe_id,"
            " free_text=excluded.free_text",
            (d.isoformat(), slot, recipe_id, free_text))
    row = conn.execute(
        "SELECT mp.*, rec.name AS recipe_name FROM meal_plan mp"
        " LEFT JOIN recipes rec ON rec.id = mp.recipe_id"
        " WHERE mp.date=? AND mp.slot=?", (d.isoformat(), slot)).fetchone()
    return jsonify({"date": row["date"], "slot": row["slot"],
                    "recipe_id": row["recipe_id"], "recipe_name": row["recipe_name"],
                    "free_text": row["free_text"]})


@app.post("/api/mealplan/push_groceries")
def api_push_groceries():
    body = request.get_json(force=True, silent=True) or {}
    try:
        d = _parse_date(body.get("date"))
    except (ValueError, TypeError):
        return jsonify({"error": "Bad date — use YYYY-MM-DD"}), 400
    slot = body.get("slot", "dinner")
    conn = db.connect()
    row = conn.execute(
        "SELECT rec.* FROM meal_plan mp JOIN recipes rec ON rec.id = mp.recipe_id"
        " WHERE mp.date=? AND mp.slot=?", (d.isoformat(), slot)).fetchone()
    if row is None:
        return jsonify({"error": "No recipe planned for that slot"}), 404
    grocery = conn.execute(
        "SELECT id FROM lists WHERE kind='grocery' ORDER BY id LIMIT 1").fetchone()
    if grocery is None:
        return jsonify({"error": "No grocery list exists — add one first"}), 404
    existing = {r["text"].strip().casefold() for r in conn.execute(
        "SELECT text FROM list_items WHERE list_id=? AND done=0", (grocery["id"],))}
    added, skipped = [], []
    with conn:
        for ing in _recipe_json(row)["ingredients"]:
            if ing.casefold() in existing:
                skipped.append(ing)
                continue
            conn.execute(
                "INSERT INTO list_items(list_id, text, done, created_at) VALUES(?,?,0,?)",
                (grocery["id"], ing, db.utc_now_iso()))
            existing.add(ing.casefold())
            added.append(ing)
    return jsonify({"added": added, "skipped": skipped})


# ------------------------------------------------------------------ magic imports

@app.get("/api/imports/pending")
def api_imports_pending():
    conn = db.connect()
    imports = []
    for r in conn.execute(
            "SELECT * FROM magic_imports WHERE status='pending' ORDER BY id"):
        try:
            events = json.loads(r["extracted_json"] or "{}").get("events", [])
        except ValueError:
            events = []
        imports.append({
            "id": r["id"], "received_at": r["received_at"],
            "from_addr": r["from_addr"], "subject": r["subject"],
            "body_excerpt": r["body_excerpt"],
            "events": [{"index": i, **e} for i, e in enumerate(events)],
        })
    return jsonify({"imports": imports})


@app.post("/api/imports/<int:import_id>/approve")
def api_import_approve(import_id):
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM magic_imports WHERE id=? AND status='pending'",
        (import_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Import not found or already handled"}), 404
    try:
        proposed = json.loads(row["extracted_json"] or "{}").get("events", [])
    except ValueError:
        proposed = []
    body = request.get_json(force=True, silent=True) or {}
    created = []
    with conn:
        for sel in body.get("events", []):
            idx = sel.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(proposed):
                continue
            src = proposed[idx]
            merged = {
                "title": sel.get("title", src.get("title")),
                "date": sel.get("date", src.get("date")),
                "start": sel.get("start_time", src.get("start_time")),
                "end": sel.get("end_time", src.get("end_time")),
                "all_day": sel.get("all_day", src.get("all_day", False)),
            }
            try:
                start_utc, end_utc, all_day = _event_times_from_payload(merged)
            except (ValueError, TypeError):
                continue
            person_id = sel.get("person_id") or src.get("person_hint") or "family"
            if person_id not in {p["id"] for p in CONFIG.get("people", [])}:
                person_id = "family"
            cur = conn.execute(
                "INSERT INTO events_local(title, person_id, start_utc, end_utc,"
                " all_day, location, notes, source, import_id)"
                " VALUES(?,?,?,?,?,?,?, 'magic_import', ?)",
                ((merged["title"] or "(untitled)").strip(), person_id,
                 start_utc, end_utc, all_day, src.get("location"),
                 src.get("notes"), import_id))
            created.append(_local_event_json(cur.lastrowid))
        conn.execute("UPDATE magic_imports SET status='approved' WHERE id=?",
                     (import_id,))
    return jsonify({"ok": True, "created": created})


@app.post("/api/imports/<int:import_id>/dismiss")
def api_import_dismiss(import_id):
    conn = db.connect()
    with conn:
        cur = conn.execute(
            "UPDATE magic_imports SET status='dismissed' WHERE id=? AND status='pending'",
            (import_id,))
    if cur.rowcount == 0:
        return jsonify({"error": "Import not found or already handled"}), 404
    return jsonify({"ok": True})


# ------------------------------------------------------------------ weather + HA

@app.get("/api/weather")
def api_weather():
    import weather
    data = weather.latest()
    if data is None:
        return jsonify({"available": False})
    return jsonify({"available": True, **data})


@app.get("/api/ha/tiles")
def api_ha_tiles():
    import ha
    tiles_cfg = CONFIG.get("ha_tiles", []) or []
    cached = ha.cached_states() or {}
    states = cached.get("states", {})
    sync = db.get_json_setting("sync:ha") or {}
    available = bool(sync.get("ok")) and bool(states)
    def shape(t):
        if t.get("type") == "solar":
            return ha.solar_shape(t, states)
        return ha.tile_shape(t, states.get(t.get("entity")))

    return jsonify({
        "available": available,
        "updated_at": cached.get("at"),
        "tiles": [shape(t) for t in tiles_cfg],
    })


@app.post("/api/ha/action")
def api_ha_action():
    import ha
    body = request.get_json(force=True, silent=True) or {}
    entity = body.get("entity")
    tile = next((t for t in CONFIG.get("ha_tiles", []) or []
                 if t.get("entity") == entity), None)
    if tile is None:
        return jsonify({"error": "Unknown tile"}), 404
    if not tile.get("allow_action"):
        return jsonify({"error": "Actions are not enabled for this tile"}), 403
    try:
        ha.call_action(entity, body.get("action", ""), body.get("value"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ha.NotConfigured:
        return jsonify({"error": "Home Assistant is not configured"}), 503
    except Exception as e:
        return jsonify({"error": f"Home Assistant did not respond ({e})"}), 502
    return jsonify({"ok": True})


@app.get("/api/ha/art/<entity>")
def api_ha_art(entity):
    """Album art for a configured media tile, proxied from Home Assistant."""
    import ha
    if not any(t.get("entity") == entity and t.get("type") == "media"
               for t in CONFIG.get("ha_tiles", []) or []):
        return jsonify({"error": "Unknown media player"}), 404
    try:
        content, ctype = ha.media_art(entity)
    except Exception:
        # No art for this track is completely normal (radio, local files) —
        # the panel falls back to its own spinning-record artwork.
        return jsonify({"error": "No album art"}), 404
    resp = app.response_class(content, mimetype=ctype)
    # The URL carries a hash of HA's own picture token, so it changes whenever
    # the track does. Within one track it's safe to cache hard.
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.get("/api/ha/camera/<entity>")
def api_ha_camera(entity):
    import ha
    if not any(t.get("entity") == entity and t.get("type") == "camera_snapshot"
               for t in CONFIG.get("ha_tiles", []) or []):
        return jsonify({"error": "Unknown camera"}), 404
    try:
        content, ctype = ha.camera_snapshot(entity)
    except Exception:
        return jsonify({"error": "Camera unavailable"}), 502
    resp = app.response_class(content, mimetype=ctype)
    # The panel re-asks every 10 s behind a fresh cache-buster; without this
    # the tablet writes every one of those frames to its own disk cache.
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ photos
# The screensaver's manifest + images. Everything is served from the local
# cache photos.py maintains — the browser never touches Google.

@app.get("/api/photos")
def api_photos():
    import photos
    m = photos.manifest()
    sync = db.get_json_setting("sync:photos") or {}
    pcfg = CONFIG.get("photos") or {}
    return jsonify({
        "available": m["count"] > 0,
        "updated_at": sync.get("at"),
        "interval_s": int(pcfg.get("interval_s") or 45),
        "idle_minutes": int(pcfg.get("idle_minutes") or 3),
        "photos": m["photos"],
    })


@app.get("/api/photos/<int:photo_id>/image")
def api_photo_image(photo_id):
    import photos
    path = photos.image_path(photo_id)
    if path is None:
        return jsonify({"error": "No such photo"}), 404
    with open(path, "rb") as f:
        return app.response_class(f.read(), mimetype="image/jpeg")


# ------------------------------------------------------------------ assist
# Home Assistant's Assist, proxied for the wall. The browser never talks to
# HA directly (no token there); audio_query blocks for up to 30 s, which is
# fine under the one-worker gunicorn this app ships with.

@app.post("/api/assist/text")
def api_assist_text():
    import assist
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Nothing to ask."}), 400
    return jsonify(assist.text_query(CONFIG, text))


@app.post("/api/assist/audio")
def api_assist_audio():
    import assist
    # raw 16-bit mono PCM, no container; ~100-160KB for a press-to-talk clip
    pcm = request.get_data(cache=False)
    if not pcm:
        return jsonify({"ok": False, "error": "No audio received."}), 400
    if len(pcm) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "That was too long — keep it under 15 seconds."}), 413
    try:
        rate = int(request.args.get("sample_rate", "16000"))
    except ValueError:
        rate = 16000
    return jsonify(assist.audio_query(CONFIG, pcm, sample_rate=rate))


@app.get("/api/assist/tts")
def api_assist_tts():
    import assist
    try:
        content, ctype = assist.tts_fetch(CONFIG, request.args.get("path", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return app.response_class(content, mimetype=ctype)


# Background jobs start at import time so gunicorn workers get them too
# (run one worker under gunicorn). PANEL_JOBS=0 disables (tests, dev shells).
if env("PANEL_JOBS", "1") == "1":
    import jobs
    jobs.start(CONFIG)


if __name__ == "__main__":
    host = env("PANEL_HOST", "0.0.0.0")
    port = int(env("PANEL_PORT", "8080"))
    app.run(host=host, port=port, debug=False)
