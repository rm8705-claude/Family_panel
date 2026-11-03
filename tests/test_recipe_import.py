"""Recipe import: JSON-LD parsing, the import endpoint, starter seeding."""
import json
import os

import pytest

import db
import recipes

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture_html() -> str:
    with open(os.path.join(FIXTURES, "recipe_page.html"), encoding="utf-8") as f:
        return f.read()


def test_parse_jsonld_graph_recipe():
    r = recipes.parse_recipe_html(fixture_html(), "https://example-kitchen.test/one-pan-chicken/")
    assert r["name"] == "One-Pan Chicken Dinner"
    assert "8 chicken thighs, bone in" in r["ingredients"]
    # HTML tags and entities cleaned out of ingredient lines
    assert "1 tsp smoked paprika" in r["ingredients"]
    assert r["meta"]["prep_minutes"] == 15
    assert r["meta"]["cook_minutes"] == 60
    assert r["meta"]["total_minutes"] == 75
    assert r["meta"]["servings"] == "4"
    assert r["meta"]["author"] == "Test Author"
    assert r["meta"]["image_url"].startswith("https://example-kitchen.test/img/")
    assert r["notes"].startswith("A one-pan chicken dinner with crispy potatoes &")
    # the method IS captured now (owner's call: household use, source credited)
    assert r["meta"]["steps"] == ["Preheat the oven.", "Roast everything."]


def test_parse_rejects_pages_without_recipes():
    with pytest.raises(recipes.ImportError_):
        recipes.parse_recipe_html("<html><body>Just a blog post</body></html>", "https://x.test/")
    with pytest.raises(recipes.ImportError_):
        recipes.parse_recipe_html(
            '<script type="application/ld+json">{"@type":"Article","name":"News"}</script>',
            "https://x.test/")


def test_iso_duration_parsing():
    f = recipes._iso_duration_minutes
    assert f("PT1H30M") == 90
    assert f("PT45M") == 45
    assert f("PT2H") == 120
    assert f("P1DT2H") == 1560
    assert f("nonsense") is None
    assert f(None) is None
    assert f("PT") is None


def test_import_endpoint(client, monkeypatch):
    monkeypatch.setattr(recipes, "fetch_and_parse",
                        lambda url: recipes.parse_recipe_html(fixture_html(), url))
    r = client.post("/api/recipes/import",
                    json={"url": "https://example-kitchen.test/one-pan-chicken/"})
    assert r.status_code == 201, r.get_json()
    rec = r.get_json()
    assert rec["source_url"] == "https://example-kitchen.test/one-pan-chicken/"
    assert rec["meta"]["cook_minutes"] == 60
    # appears in the recipe list with metadata intact
    listed = client.get("/api/recipes").get_json()["recipes"]
    match = next(x for x in listed if x["id"] == rec["id"])
    assert match["meta"]["servings"] == "4"


def test_import_endpoint_bad_url(client):
    r = client.post("/api/recipes/import", json={"url": "not a url"})
    assert r.status_code == 422
    assert "web address" in r.get_json()["error"]


def test_fetch_and_parse_rejects_oversized_page(monkeypatch):
    class HugeResponse:
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            # yields well past the 5MB cap without ever building it in memory
            chunk = b"x" * chunk_size
            for _ in range(recipes.MAX_PAGE_BYTES // chunk_size + 2):
                yield chunk

    monkeypatch.setattr(recipes.requests, "get", lambda *a, **k: HugeResponse())
    with pytest.raises(recipes.ImportError_, match="too large"):
        recipes.fetch_and_parse("https://example-kitchen.test/huge/")


def test_starter_seeding_once_only():
    conn = db.connect()
    # app import already seeded; flag set, table populated at some point
    assert db.get_setting("seeded_starter_recipes") == "1"
    before = conn.execute("SELECT COUNT(*) c FROM recipes").fetchone()["c"]
    assert recipes.seed_starters() == 0          # never re-seeds
    after = conn.execute("SELECT COUNT(*) c FROM recipes").fetchone()["c"]
    assert before == after


def test_starter_pack_is_valid():
    with open(recipes.STARTER_PATH, encoding="utf-8") as f:
        starters = json.load(f)
    assert len(starters) >= 12
    for s in starters:
        assert s["name"] and len(s["ingredients"]) >= 4


def test_recipe_image_endpoint_404_when_uncached(client):
    r = client.post("/api/recipes", json={"name": "Photo-less",
                                          "ingredients": ["a", "b", "c", "d"]})
    rid = r.get_json()["id"]
    assert client.get(f"/api/recipes/{rid}/image").status_code == 404


def test_cached_image_roundtrip(client, tmp_path, monkeypatch):
    monkeypatch.setattr(recipes, "IMAGE_DIR", str(tmp_path))
    r = client.post("/api/recipes", json={"name": "With photo",
                                          "ingredients": ["a", "b", "c", "d"]})
    rid = r.get_json()["id"]
    (tmp_path / f"{rid}.jpg").write_bytes(b"\xff\xd8\xff\xdbfakejpeg")
    resp = client.get(f"/api/recipes/{rid}/image")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert resp.data.startswith(b"\xff\xd8")


def test_starters_seeded_with_methods_and_backfill():
    conn = db.connect()
    # fresh seed path stores steps in meta
    with open(recipes.STARTER_PATH, encoding="utf-8") as f:
        starters = json.load(f)
    assert all(len(s.get("method", [])) >= 3 for s in starters)

    # backfill path: simulate a pre-methods database
    db.set_setting("seeded_starter_recipes", "1")
    conn.execute("DELETE FROM settings WHERE key='starter_methods_v2'")
    conn.commit()
    with conn:
        conn.execute(
            "INSERT INTO recipes(name, notes, ingredients, source_url, meta)"
            " VALUES('Tacos', 'x', '[]', NULL, '{}')")
    assert recipes.upgrade_starter_methods() == 1
    row = conn.execute(
        "SELECT meta FROM recipes WHERE name='Tacos'").fetchone()
    assert "Everyone builds their own" in row["meta"]
    # flag set -> second run is a no-op
    assert recipes.upgrade_starter_methods() == 0
