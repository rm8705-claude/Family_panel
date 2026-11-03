import os
import sys
import tempfile

# Point the app at a throwaway DB BEFORE anything imports db/app.
os.environ["PANEL_DB"] = os.path.join(tempfile.mkdtemp(prefix="panel-test-"), "test.db")
os.environ["PANEL_JOBS"] = "0"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "family_panel", "app"))

import pytest  # noqa: E402


@pytest.fixture()
def client():
    import app as app_module
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture(autouse=True)
def clean_tables():
    import db
    conn = db.connect()
    yield
    with conn:
        for t in ("ics_cache", "events_local", "magic_imports", "chores",
                  "chore_done", "star_redemptions", "list_items", "recipes",
                  "meal_plan"):
            conn.execute(f"DELETE FROM {t}")
