"""Recipe import + starter seeding.

Import: fetch a recipe page (RecipeTin Eats or any site publishing
schema.org/Recipe JSON-LD) and parse name, ingredients, servings, times,
photo and the method steps. Stored for this household's own panel only
(the owner's explicit call — like printing a recipe for the kitchen);
every imported recipe keeps its source_url and credits the author, and
the panel never republishes anything.

Starter pack: a dozen original family-dinner entries (written for this
project, not copied from anywhere) seeded once, so the planner is useful
before anything has been imported.
"""
import json
import logging
import os
import re

import requests

import db

log = logging.getLogger("family-panel.recipes")

FETCH_TIMEOUT = 20
MAX_INGREDIENTS = 60
MAX_PAGE_BYTES = 5 * 1024 * 1024

STARTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "starter_recipes.json")


class ImportError_(Exception):
    """Import failed for a reason worth showing the user."""


# ------------------------------------------------------------------ JSON-LD

def _iter_jsonld_blocks(html: str):
    for m in re.finditer(
            r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, flags=re.DOTALL | re.IGNORECASE):
        raw = m.group(1).strip()
        try:
            yield json.loads(raw)
        except ValueError:
            # some sites leave trailing commas or comments; try a light cleanup
            try:
                yield json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except ValueError:
                continue


def _find_recipe_obj(data):
    """Walk a JSON-LD document (object, list or @graph) for a Recipe node."""
    if isinstance(data, list):
        for item in data:
            found = _find_recipe_obj(item)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("@type", "")
    types = t if isinstance(t, list) else [t]
    if any(str(x).lower() == "recipe" for x in types):
        return data
    for key in ("@graph", "mainEntity"):
        if key in data:
            found = _find_recipe_obj(data[key])
            if found:
                return found
    return None


def _iso_duration_minutes(value) -> int | None:
    """PT1H30M -> 90. Tolerates missing parts; None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    m = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m or not any(m.groups()):
        return None
    days, hours, mins, secs = (int(x) if x else 0 for x in m.groups())
    return days * 1440 + hours * 60 + mins + (1 if secs >= 30 else 0)


def _first_str(value) -> str | None:
    """JSON-LD fields are polymorphic: str | [str] | {"@type": ...}."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for v in value:
            s = _first_str(v)
            if s:
                return s
        return None
    if isinstance(value, dict):
        return _first_str(value.get("url") or value.get("name"))
    return None


def _clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&amp;", "&").replace("&#39;", "'").replace("&#039;", "'")
          .replace("&quot;", '"').replace("&nbsp;", " ")
          .replace("&#8217;", "'").replace("&#8211;", "-"))
    return re.sub(r"\s+", " ", s).strip()


def parse_recipe_html(html: str, url: str) -> dict:
    """Extract one recipe from a page's JSON-LD. Raises ImportError_ if none."""
    recipe = None
    for block in _iter_jsonld_blocks(html):
        recipe = _find_recipe_obj(block)
        if recipe:
            break
    if recipe is None:
        raise ImportError_(
            "No recipe found on that page — check it's a recipe link, not a "
            "category or search page")

    name = _clean_text(_first_str(recipe.get("name")) or "")
    if not name:
        raise ImportError_("The recipe on that page has no name")

    raw_ings = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
    if isinstance(raw_ings, str):
        raw_ings = [raw_ings]
    ingredients = [_clean_text(i) for i in raw_ings if isinstance(i, str)]
    ingredients = [i for i in ingredients if i][:MAX_INGREDIENTS]
    if not ingredients:
        raise ImportError_("That page's recipe has no ingredient list")

    desc = _clean_text(_first_str(recipe.get("description")) or "")
    if len(desc) > 300:
        desc = desc[:297].rsplit(" ", 1)[0] + "…"

    steps: list[str] = []

    def walk_steps(node):
        if isinstance(node, str):
            s = _clean_text(node)
            if s:
                steps.append(s)
        elif isinstance(node, list):
            for x in node:
                walk_steps(x)
        elif isinstance(node, dict):
            # HowToSection nests HowToSteps under itemListElement
            if node.get("itemListElement"):
                walk_steps(node["itemListElement"])
            else:
                s = _clean_text(str(node.get("text") or node.get("name") or ""))
                if s:
                    steps.append(s)

    walk_steps(recipe.get("recipeInstructions") or [])
    steps = steps[:40]

    servings = _first_str(recipe.get("recipeYield"))
    meta = {
        "servings": _clean_text(servings) if servings else None,
        "prep_minutes": _iso_duration_minutes(recipe.get("prepTime")),
        "cook_minutes": _iso_duration_minutes(recipe.get("cookTime")),
        "total_minutes": _iso_duration_minutes(recipe.get("totalTime")),
        "image_url": _first_str(recipe.get("image")),
        "author": _clean_text(_first_str(recipe.get("author")) or "") or None,
        "steps": steps,
    }
    return {"name": name, "notes": desc or None, "ingredients": ingredients,
            "source_url": url, "meta": meta}


def fetch_and_parse(url: str) -> dict:
    if not re.match(r"^https?://", url or ""):
        raise ImportError_("That doesn't look like a web address")
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT, stream=True, headers={
            "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) family-panel/1.0 "
                           "recipe-import (personal home use)"),
            "Accept": "text/html",
        })
        r.raise_for_status()
        # Stream + cap rather than r.text: an unbounded recipe-page fetch is
        # an easy way for a malicious/broken link to run the panel out of
        # memory. Same 422 error style as a bad URL.
        body = bytearray()
        for chunk in r.iter_content(chunk_size=65536):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                raise ImportError_(
                    "That page is too large to import (over "
                    f"{MAX_PAGE_BYTES // (1024 * 1024)}MB)")
    except requests.RequestException as e:
        raise ImportError_(f"Couldn't fetch that page ({e.__class__.__name__}) "
                           "— check the link and the internet connection")
    text = body.decode(r.encoding or "utf-8", errors="replace")
    return parse_recipe_html(text, url)


# ------------------------------------------------------------------ images

IMAGE_DIR = os.path.join(os.path.dirname(db.DB_PATH), "recipe_images")
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def cache_image(recipe_id: int, image_url: str) -> str | None:
    """Download an imported recipe's photo once and keep it locally, so the
    panel never hotlinks the site from the wall. Best-effort: any failure
    just means the card renders its illustrated placeholder instead."""
    if not image_url or not re.match(r"^https?://", image_url):
        return None
    try:
        r = requests.get(image_url, timeout=FETCH_TIMEOUT, stream=True, headers={
            "User-Agent": "Mozilla/5.0 family-panel/1.0 recipe-import"})
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        ext = {"image/jpeg": ".jpg", "image/png": ".png",
               "image/webp": ".webp"}.get(ctype.split(";")[0].strip())
        if ext is None:
            return None
        data = r.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
        if len(data) > MAX_IMAGE_BYTES:
            return None
        os.makedirs(IMAGE_DIR, exist_ok=True)
        path = os.path.join(IMAGE_DIR, f"{recipe_id}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        log.info("recipe image not cached (%s): %s", image_url, e)
        return None


def cached_image_path(recipe_id: int) -> str | None:
    for ext in (".jpg", ".png", ".webp"):
        path = os.path.join(IMAGE_DIR, f"{recipe_id}{ext}")
        if os.path.exists(path):
            return path
    return None


# ------------------------------------------------------------------ starter pack

def seed_starters() -> int:
    """Load the starter recipes once ever (flag in settings, not table-empty,
    so a user who deletes them all doesn't get them back on reboot)."""
    if db.get_setting("seeded_starter_recipes"):
        return 0
    try:
        with open(STARTER_PATH, encoding="utf-8") as f:
            starters = json.load(f)
    except (OSError, ValueError) as e:
        log.warning("starter recipes unavailable: %s", e)
        return 0
    conn = db.connect()
    with conn:
        for s in starters:
            meta = dict(s.get("meta", {}))
            if s.get("method"):
                meta["steps"] = s["method"]
            conn.execute(
                "INSERT INTO recipes(name, notes, ingredients, source_url, meta)"
                " VALUES(?,?,?,?,?)",
                (s["name"], s.get("notes"), json.dumps(s["ingredients"]),
                 None, json.dumps(meta)))
    db.set_setting("seeded_starter_recipes", "1")
    db.set_setting("starter_methods_v2", "1")
    return len(starters)


def upgrade_starter_methods() -> int:
    """One-time backfill: databases seeded before methods existed get the
    method steps added to any still-unmodified starter rows (matched by name,
    only where no steps are present). User-edited or imported rows untouched."""
    if db.get_setting("starter_methods_v2") or not db.get_setting("seeded_starter_recipes"):
        return 0
    try:
        with open(STARTER_PATH, encoding="utf-8") as f:
            starters = {s["name"]: s for s in json.load(f)}
    except (OSError, ValueError):
        return 0
    conn = db.connect()
    updated = 0
    with conn:
        for row in conn.execute(
                "SELECT id, name, meta FROM recipes WHERE source_url IS NULL"):
            starter = starters.get(row["name"])
            if not starter or not starter.get("method"):
                continue
            try:
                meta = json.loads(row["meta"] or "{}")
            except ValueError:
                meta = {}
            if meta.get("steps"):
                continue
            meta["steps"] = starter["method"]
            conn.execute("UPDATE recipes SET meta=? WHERE id=?",
                         (json.dumps(meta), row["id"]))
            updated += 1
    db.set_setting("starter_methods_v2", "1")
    return updated
