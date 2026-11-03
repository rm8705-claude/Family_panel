"""HA tile shaping, action mapping, and graceful-offline behaviour."""
import pytest

import db
import ha


def test_tile_shape_per_type():
    climate = ha.tile_shape(
        {"entity": "climate.living", "label": "Aircon", "type": "climate",
         "allow_action": True},
        {"state": "cool", "attributes": {"current_temperature": 26.5,
                                         "temperature": 23.0}})
    assert climate["attrs"] == {"current_temp": 26.5, "setpoint": 23.0,
                                "hvac_mode": "cool"}
    assert climate["allow_action"] is True

    media = ha.tile_shape(
        {"entity": "media_player.sonos", "label": "Sonos", "type": "media"},
        {"state": "playing", "attributes": {"media_title": "Hot Potato",
                                            "media_artist": "The Wiggles",
                                            "volume_level": 0.4}})
    assert media["attrs"]["playing"] is True
    assert media["attrs"]["title"] == "Hot Potato"

    cam = ha.tile_shape({"entity": "camera.front", "type": "camera_snapshot"},
                        {"state": "idle", "attributes": {}})
    assert cam["attrs"]["snapshot_url"] == "/api/ha/camera/camera.front"

    # no cached state -> state None, empty attrs (renders grey)
    empty = ha.tile_shape({"entity": "sensor.x", "type": "sensor"}, None)
    assert empty["state"] is None and empty["attrs"] == {}


def test_action_table_rejects_wrong_domain():
    with pytest.raises(ValueError):
        ha.call_action("cover.blinds", "turn_on")   # not a cover action
    with pytest.raises(ValueError):
        ha.call_action("sensor.temp", "open")       # sensors have no actions


def test_tiles_endpoint_offline_grey(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "cover.curtains", "label": "Curtains", "type": "cover",
         "allow_action": True}])
    db.mark_sync("ha", False, "connection refused")
    r = client.get("/api/ha/tiles").get_json()
    assert r["available"] is False
    assert r["tiles"][0]["state"] is None

    # action on an unknown tile 404s; action without allow_action 403s
    assert client.post("/api/ha/action",
                       json={"entity": "cover.nope", "action": "open"}).status_code == 404
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "media_player.sonos", "type": "media"}])
    assert client.post("/api/ha/action",
                       json={"entity": "media_player.sonos",
                             "action": "play"}).status_code == 403


def test_fan_and_media_tile_shapes():
    fan = ha.tile_shape(
        {"entity": "fan.bedroom_dyson", "label": "Dyson", "type": "fan",
         "allow_action": True},
        {"state": "on", "attributes": {"percentage": 40, "oscillating": True,
                                       "preset_mode": "Auto"}})
    assert fan["attrs"] == {"percentage": 40, "oscillating": True,
                            "preset_mode": "Auto", "on": True}

    media = ha.tile_shape(
        {"entity": "media_player.sonos", "type": "media"},
        {"state": "paused", "attributes": {
            "media_title": "T", "volume_level": 0.2,
            "source_list": ["Triple J", "Wiggles Radio"], "source": "Triple J"}})
    assert media["attrs"]["source_list"] == ["Triple J", "Wiggles Radio"]
    assert media["attrs"]["playing"] is False


def test_fan_and_media_actions_mapped():
    assert ha.ACTIONS["fan"]["set_percentage"][2](150) == {"percentage": 100}
    assert ha.ACTIONS["fan"]["oscillate_off"][2](None) == {"oscillating": False}
    assert ha.ACTIONS["media_player"]["select_source"][2]("Triple J") == {"source": "Triple J"}
    with pytest.raises(ValueError):
        ha.call_action("fan.dyson", "open")


SOLAR_TILE = {
    "type": "solar", "label": "Solar",
    "entities": {"pv_power": "sensor.total_dc_power",
                 "load_power": "sensor.load_power",
                 "grid_power": "sensor.export_power",
                 "battery_power": "sensor.battery_power",
                 "battery_level": "sensor.battery_level"}}


def solar_states(**over):
    base = {"sensor.total_dc_power": {"state": "5200"},
            "sensor.load_power": {"state": "1400"},
            "sensor.export_power": {"state": "2600"},
            "sensor.battery_power": {"state": "1200"},
            "sensor.battery_level": {"state": "84"}}
    base.update(over)
    return base


def test_solar_shape_and_sign_conventions():
    t = ha.solar_shape(SOLAR_TILE, solar_states())
    assert t["type"] == "solar" and t["allow_action"] is False
    assert t["attrs"] == {"pv_w": 5200.0, "load_w": 1400.0, "grid_w": 2600.0,
                          "battery_w": 1200.0, "battery_pct": 84.0}

    inv = ha.solar_shape({**SOLAR_TILE, "invert_grid": True,
                          "invert_battery": True}, solar_states())
    assert inv["attrs"]["grid_w"] == -2600.0
    assert inv["attrs"]["battery_w"] == -1200.0

    kw = ha.solar_shape({**SOLAR_TILE, "power_unit": "kW"},
                        solar_states(**{"sensor.total_dc_power": {"state": "5.2"}}))
    assert kw["attrs"]["pv_w"] == 5200.0
    assert kw["attrs"]["battery_pct"] == 84.0   # percent never scaled


def test_solar_shape_derives_load_and_degrades():
    # missing load sensor -> derived from pv - grid - battery
    tile = {**SOLAR_TILE, "entities": {**SOLAR_TILE["entities"]}}
    del tile["entities"]["load_power"]
    t = ha.solar_shape(tile, solar_states())
    assert t["attrs"]["load_w"] == 5200.0 - 2600.0 - 1200.0

    # unavailable sensors -> nulls, state None (renders grey), no crash
    dead = ha.solar_shape(SOLAR_TILE, {})
    assert dead["state"] is None
    assert all(v is None for v in dead["attrs"].values())


def test_wanted_entities_includes_solar_map():
    tiles = [SOLAR_TILE, {"entity": "cover.curtains", "type": "cover"}]
    wanted = ha.wanted_entities(tiles)
    assert "sensor.battery_level" in wanted and "cover.curtains" in wanted


# The add-on's Configuration tab can't nest `entities` under a tile — the
# Supervisor makes any nested schema object mandatory, which would force every
# curtain and light switch to carry an empty `entities: {}`. So the add-on
# writes the solar sensors flat on the tile, and both shapes must work.
FLAT_SOLAR_TILE = {"type": "solar", "label": "Solar",
                   **SOLAR_TILE["entities"]}


def test_solar_accepts_flat_keys_identically_to_nested():
    flat = ha.solar_shape(FLAT_SOLAR_TILE, solar_states())
    nested = ha.solar_shape(SOLAR_TILE, solar_states())
    assert flat["attrs"] == nested["attrs"]
    assert ha.wanted_entities([FLAT_SOLAR_TILE]) == ha.wanted_entities([SOLAR_TILE])


def test_non_solar_tiles_need_no_solar_keys():
    """The regression this shape exists to prevent: an ordinary tile carries
    none of the solar keys, and must not acquire phantom entities."""
    plain = {"entity": "cover.curtains", "label": "Curtains", "type": "cover"}
    assert ha.solar_entities(plain) == {}
    assert ha.wanted_entities([plain]) == {"cover.curtains"}


def test_empty_nested_entities_falls_through_to_flat():
    tile = {**FLAT_SOLAR_TILE, "entities": {}}
    assert ha.solar_shape(tile, solar_states())["attrs"]["pv_w"] == 5200.0


def test_tiles_endpoint_with_solar(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [SOLAR_TILE])
    db.set_json_setting("ha:states", {"at": db.utc_now_iso(),
                                      "states": solar_states()})
    db.mark_sync("ha", True)
    r = client.get("/api/ha/tiles").get_json()
    assert r["tiles"][0]["attrs"]["battery_pct"] == 84.0
    # solar is read-only even if someone posts an action at it
    assert client.post("/api/ha/action",
                       json={"entity": "solar", "action": "turn_off"}).status_code in (403, 404)


def test_camera_snapshot_is_never_cached(client, monkeypatch):
    """The panel re-asks every 10 s behind a cache-buster — the tablet must not
    write each of those frames into its own disk cache."""
    import app as app_module
    import ha as ha_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles",
                        [{"entity": "camera.front", "type": "camera_snapshot"}])
    monkeypatch.setattr(ha_module, "camera_snapshot",
                        lambda entity: (b"\xff\xd8jpegbytes", "image/jpeg"))
    r = client.get("/api/ha/camera/camera.front?t=7")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "no-store"


# ---------------------------------------------------------------- album art

MEDIA_TILE = {"entity": "media_player.lounge", "label": "Sonos", "type": "media",
              "allow_action": True}


def media_cached(pic="/api/media_player_proxy/media_player.lounge?token=abc&cache=t1"):
    attrs = {"media_title": "Dumb Things", "media_artist": "Paul Kelly",
             "volume_level": 0.35, "source_list": ["ABC Jazz"], "source": "ABC Jazz"}
    if pic:
        attrs["entity_picture"] = pic
    return {"state": "playing", "attributes": attrs}


def test_media_art_url_points_at_our_proxy_not_ha():
    """HA's entity_picture is relative to HA's origin; served to a browser on
    the panel's port it 404s. The tile must hand out our own proxy path."""
    t = ha.tile_shape(MEDIA_TILE, media_cached())
    pic = t["attrs"]["entity_picture"]
    assert pic.startswith("/api/ha/art/media_player.lounge?v=")
    assert "media_player_proxy" not in pic
    assert t["attrs"]["volume"] == 0.35


def test_media_art_cache_key_tracks_the_song():
    a = ha.tile_shape(MEDIA_TILE, media_cached("/x?cache=track1"))["attrs"]["entity_picture"]
    b = ha.tile_shape(MEDIA_TILE, media_cached("/x?cache=track2"))["attrs"]["entity_picture"]
    same = ha.tile_shape(MEDIA_TILE, media_cached("/x?cache=track1"))["attrs"]["entity_picture"]
    assert a != b, "art URL must change with the track or the browser caches stale art"
    assert a == same, "same track must reuse the URL so it caches"


def test_media_without_art_is_none_not_a_dead_url():
    """Radio and local files often have no art. The panel draws its own
    record sleeve then — it must not be handed a URL that 404s."""
    t = ha.tile_shape(MEDIA_TILE, media_cached(pic=None))
    assert t["attrs"]["entity_picture"] is None


# ------------------------------------------------------- live camera stream

def test_camera_stream_endpoint_rejects_unconfigured_entity(client, monkeypatch):
    """Same guard the snapshot endpoint has: the stream proxy must not become
    an open relay to arbitrary HA camera entities."""
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "camera.front", "type": "camera_snapshot"}])
    assert client.get("/api/ha/camera/camera.nope/stream").status_code == 404


def test_camera_stream_relays_chunks_and_closes_upstream(client, monkeypatch):
    """The proxy must stream through rather than buffer, and must close the
    upstream response when the viewer goes away — otherwise every camera view
    leaks a connection to Home Assistant."""
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "camera.front", "type": "camera_snapshot"}])

    closed = {"yes": False}

    class FakeUpstream:
        headers = {"Content-Type": "multipart/x-mixed-replace; boundary=x"}
        def iter_content(self, chunk_size=None):
            yield b"--x\r\n"
            yield b"frame1"
            yield b"--x\r\n"
        def close(self):
            closed["yes"] = True

    import ha
    monkeypatch.setattr(ha, "camera_stream",
                        lambda e: (FakeUpstream(), FakeUpstream.headers["Content-Type"]))

    r = client.get("/api/ha/camera/camera.front/stream")
    assert r.status_code == 200
    assert "multipart/x-mixed-replace" in r.headers["Content-Type"]
    assert r.headers["Cache-Control"] == "no-store"
    assert b"frame1" in r.get_data()
    assert closed["yes"], "upstream response was never closed"


def test_camera_stream_survives_a_dropped_feed(client, monkeypatch):
    """A DVR yanking the channel mid-stream must not 500 the panel."""
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "camera.front", "type": "camera_snapshot"}])

    closed = {"yes": False}

    class Dropping:
        headers = {"Content-Type": "multipart/x-mixed-replace; boundary=x"}
        def iter_content(self, chunk_size=None):
            yield b"frame1"
            raise ConnectionError("DVR went away")
        def close(self):
            closed["yes"] = True

    import ha
    monkeypatch.setattr(ha, "camera_stream",
                        lambda e: (Dropping(), Dropping.headers["Content-Type"]))

    r = client.get("/api/ha/camera/camera.front/stream")
    assert b"frame1" in r.get_data()      # what arrived before the drop is kept
    assert closed["yes"]


def test_camera_stream_502s_when_ha_is_unreachable(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "camera.front", "type": "camera_snapshot"}])
    import ha
    def boom(e):
        raise ConnectionError("no route to HA")
    monkeypatch.setattr(ha, "camera_stream", boom)
    assert client.get("/api/ha/camera/camera.front/stream").status_code == 502


# ---- tile order (Settings -> Arrange home tiles) --------------------------

ORDER_TILES = [
    {"entity": "person.rohan", "label": "Rohan", "type": "presence"},
    {"entity": "climate.living", "label": "Living", "type": "climate"},
    {"entity": "cover.curtains", "label": "Curtains", "type": "cover"},
    {"entity": "climate.study", "label": "Study", "type": "climate"},
    {"type": "solar", "label": "Solar", "pv_power": "sensor.pv"},
    {"entity": "switch.porch", "label": "Porch", "type": "switch"},
]


def _keys(client):
    return [t["key"] for t in client.get("/api/ha/tiles").get_json()["tiles"]]


def test_tile_order_saved_on_server(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", list(ORDER_TILES))
    monkeypatch.setenv("ADMIN_PIN", "4321")
    db.set_json_setting(app_module.HA_ORDER_KEY, [])

    # config order, with the climates sharing one group key
    assert _keys(client) == ["person.rohan", "group:climate", "cover.curtains",
                             "group:climate", "solar:Solar", "switch.porch"]

    # saving needs the PIN
    new = ["switch.porch", "solar:Solar", "group:climate"]
    assert client.put("/api/ha/order", json={"order": new}).status_code == 403
    r = client.put("/api/ha/order", json={"order": new + ["switch.porch"]},
                   headers={"X-Admin-Pin": "4321"})
    assert r.status_code == 200 and r.get_json()["order"] == new   # repeat dropped
    assert client.get("/api/ha/order").get_json()["order"] == new

    # saved keys first in saved order (both climates together, config order
    # kept between them), unknown tiles after in config order
    r = client.get("/api/ha/tiles").get_json()
    assert [t["label"] for t in r["tiles"]] == ["Porch", "Solar", "Living", "Study",
                                                "Rohan", "Curtains"]
    # the order rides along too, so the frontend can place its own sports tile
    assert r["order"] == new

    # a key for a tile since removed from config.yaml is ignored
    client.put("/api/ha/order", json={"order": ["switch.gone", "cover.curtains"]},
               headers={"X-Admin-Pin": "4321"})
    assert _keys(client)[0] == "cover.curtains"

    # bad bodies are refused, reset goes back to config order
    assert client.put("/api/ha/order", json={"order": "nope"},
                      headers={"X-Admin-Pin": "4321"}).status_code == 400
    assert client.put("/api/ha/order", json={"order": [1, 2]},
                      headers={"X-Admin-Pin": "4321"}).status_code == 400
    assert client.delete("/api/ha/order").status_code == 403
    assert client.delete("/api/ha/order",
                         headers={"X-Admin-Pin": "4321"}).status_code == 200
    assert _keys(client)[0] == "person.rohan"
