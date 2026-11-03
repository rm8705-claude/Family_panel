"""Chores/stars, lists, meal planner + grocery push, and admin PIN gating."""
from datetime import timedelta

import db

PIN = {"X-Admin-Pin": "0000"}


def make_chore(client, **kw):
    payload = {"title": "Feed the dog", "person_id": "kid",
               "days": "MO,TU,WE,TH,FR,SA,SU", "slot": "morning", "stars": 2}
    payload.update(kw)
    r = client.post("/api/chores", json=payload, headers=PIN)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_chore_crud_requires_pin(client):
    r = client.post("/api/chores", json={"title": "x", "person_id": "kid",
                                         "days": "MO", "slot": "morning"})
    assert r.status_code == 403
    chore = make_chore(client)
    assert client.delete(f"/api/chores/{chore['id']}").status_code == 403
    assert client.delete(f"/api/chores/{chore['id']}", headers=PIN).status_code == 200


def test_chores_today_toggle_and_weekly_stars(client):
    chore = make_chore(client, stars=3)
    today = db.local_today()
    r = client.get("/api/chores/today").get_json()
    kid = next(p for p in r["people"] if p["id"] == "kid")
    assert kid["chores"][0]["done"] is False
    assert kid["stars_week"] == 0

    r = client.post(f"/api/chores/{chore['id']}/toggle?date={today}").get_json()
    assert r["done"] is True
    kid = next(p for p in client.get("/api/chores/today").get_json()["people"]
               if p["id"] == "kid")
    assert kid["chores"][0]["done"] is True
    assert kid["stars_week"] == 3

    # toggle back off
    r = client.post(f"/api/chores/{chore['id']}/toggle?date={today}").get_json()
    assert r["done"] is False


def test_chore_day_filtering(client):
    today = db.local_today()
    today_code = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"][today.weekday()]
    other_code = "MO" if today_code != "MO" else "TU"
    make_chore(client, title="Only other days", days=other_code)
    make_chore(client, title="Today yes", days=today_code)
    kid = next(p for p in client.get("/api/chores/today").get_json()["people"]
               if p["id"] == "kid")
    titles = [c["title"] for c in kid["chores"]]
    assert "Today yes" in titles and "Only other days" not in titles


def test_star_redeem_flow(client):
    chore = make_chore(client, stars=5)
    today = db.local_today()
    client.post(f"/api/chores/{chore['id']}/toggle?date={today}")

    assert client.post("/api/stars/redeem",
                       json={"person_id": "kid", "stars": 3}).status_code == 403
    r = client.post("/api/stars/redeem", headers=PIN,
                    json={"person_id": "kid", "stars": 3, "note": "Ice cream"})
    assert r.status_code == 200 and r.get_json()["balance"] == 2
    # can't over-redeem
    r = client.post("/api/stars/redeem", headers=PIN,
                    json={"person_id": "kid", "stars": 99})
    assert r.status_code == 400
    stars = client.get("/api/stars").get_json()
    kid = next(p for p in stars["people"] if p["id"] == "kid")
    assert kid["balance"] == 2 and kid["earned_total"] == 5


def test_lists_and_items(client):
    lists = client.get("/api/lists").get_json()["lists"]
    grocery = next(l for l in lists if l["kind"] == "grocery")

    item = client.post(f"/api/lists/{grocery['id']}/items",
                       json={"text": "Milk"}).get_json()
    r = client.patch(f"/api/items/{item['id']}", json={"done": True}).get_json()
    assert r["done"] == 1
    assert client.delete(f"/api/items/{item['id']}").status_code == 200
    assert client.delete(f"/api/items/{item['id']}").status_code == 404

    custom = client.post("/api/lists", json={"name": "Bunnings", "kind": "custom"})
    assert custom.status_code == 201
    assert client.delete(f"/api/lists/{custom.get_json()['id']}").status_code == 200


def test_mealplan_and_grocery_push(client):
    r = client.post("/api/recipes", json={
        "name": "Spag bol",
        "notes": "Kid favourite",
        "ingredients": ["Mince", "Passata", "Spaghetti", "Onion"]})
    recipe = r.get_json()
    assert r.status_code == 201

    d = db.local_today()
    r = client.put("/api/mealplan", json={
        "date": d.isoformat(), "slot": "dinner", "recipe_id": recipe["id"]}).get_json()
    assert r["recipe_name"] == "Spag bol"

    week = client.get(f"/api/mealplan?week={d.isoformat()}").get_json()
    assert week["week_start"] == (d - timedelta(days=d.weekday())).isoformat()
    day = next(x for x in week["days"] if x["date"] == d.isoformat())
    assert day["dinner"]["recipe_name"] == "Spag bol"

    # push ingredients; "mince" already unticked on the grocery list -> skipped
    grocery = next(l for l in client.get("/api/lists").get_json()["lists"]
                   if l["kind"] == "grocery")
    client.post(f"/api/lists/{grocery['id']}/items", json={"text": "mince"})
    r = client.post("/api/mealplan/push_groceries",
                    json={"date": d.isoformat(), "slot": "dinner"}).get_json()
    assert set(r["added"]) == {"Passata", "Spaghetti", "Onion"}
    assert r["skipped"] == ["Mince"]

    # clearing the slot
    r = client.put("/api/mealplan", json={"date": d.isoformat(), "slot": "dinner",
                                          "recipe_id": None, "free_text": None})
    assert r.get_json()["cleared"] is True

    # free-text night
    r = client.put("/api/mealplan", json={"date": d.isoformat(), "slot": "dinner",
                                          "free_text": "Takeaway"}).get_json()
    assert r["free_text"] == "Takeaway" and r["recipe_id"] is None


# --------------------------------------------------------- asset versioning
# A kiosk tablet is rarely closed, so its browser cache rarely clears on its
# own either — an add-on update used to leave it silently running the OLD
# app.js while the server had already moved on. Stamping every asset URL
# with ?v=<APP_VERSION> means a release is a genuinely different URL, so
# there's nothing stale left for the browser to serve.

def test_index_stamps_every_asset_with_the_running_version(client):
    import app as app_module
    html = client.get("/").get_data(as_text=True)
    assert "{{V}}" not in html, "unsubstituted template placeholder reached the browser"
    v = app_module.APP_VERSION
    assert f"/static/app.js?v={v}" in html
    assert f"/static/styles.css?v={v}" in html
    assert f"/static/fonts/fonts.css?v={v}" in html


def test_index_itself_is_never_cached(client):
    r = client.get("/")
    assert r.headers["Cache-Control"] == "no-cache"


def test_versioned_static_asset_is_cached_hard(client):
    r = client.get("/static/app.js?v=9.9.9")
    assert r.status_code == 200
    assert "immutable" in r.headers["Cache-Control"]
    assert "max-age=31536000" in r.headers["Cache-Control"]


def test_static_still_serves_nested_paths(client):
    """Disabling Flask's automatic static handler (for the Cache-Control
    override) must not break serving files inside subfolders."""
    r = client.get("/static/fonts/fonts.css")
    assert r.status_code == 200


def test_static_404s_for_a_missing_file(client):
    assert client.get("/static/does-not-exist.js").status_code == 404
