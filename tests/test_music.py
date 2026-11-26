"""Music Assistant: player matching, Discover picks, play/surprise routing."""
import pytest

import music


@pytest.fixture(autouse=True)
def fresh_ma():
    """Each test starts with no remembered Music Assistant address, account
    list or "unreachable" note — they'd otherwise carry from test to test."""
    music._ma.update(base=None, at=0.0, providers=None, down_until=0.0, down_why=None)
    yield
    music._ma.update(base=None, at=0.0, providers=None, down_until=0.0, down_why=None)


class Resp:
    status_code = 200

    def __init__(self, data=None, text=None):
        self._data = data
        self.text = text if text is not None else ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


CONFIG = {
    "ha_tiles": [
        {"entity": "media_player.lounge", "label": "Lounge", "type": "media", "allow_action": True},
        {"entity": "media_player.roam", "label": "Roam", "type": "media", "allow_action": True},
    ],
    "music_assistant_players": "media_player.roam=media_player.roam_ma",
}

LIB = {
    "artist": [{"media_type": "artist", "uri": "spotify://artist/a", "name": "Khruangbin"}],
    "playlist": [{"media_type": "playlist", "uri": "ytmusic://playlist/p", "name": "Arvo"}],
    "album": [{"media_type": "album", "uri": "library://album/1", "name": "Currents",
               "artists": [{"name": "Tame Impala"}],
               "image": "http://ma:8095/imageproxy?path=x&provider=ytmusic--a1b2"},
              {"media_type": "album", "uri": "library://album/2", "name": "Rumours",
               "image": "https://i.scdn.co/image/ab67"}],
}


def fake_ha(monkeypatch, calls):
    monkeypatch.setattr(music.ha, "_api_base", lambda: ("http://ha/api", "t"))
    monkeypatch.setattr(music.ha, "cached_states", lambda: {"states": {
        "media_player.lounge": {"attributes": {"friendly_name": "Lounge"}}}})
    music._cache.update(at=0.0, data=None)

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        if url.endswith("/template"):
            return Resp(text='[{"entity": "media_player.lounge_2", "name": "Lounge", "entry": "E1"},'
                             ' {"entity": "media_player.roam_ma", "name": "Bathroom", "entry": "E1"}]')
        if "get_library" in url:
            return Resp({"service_response": {"items": LIB[json["media_type"]]}})
        return Resp({})
    monkeypatch.setattr(music.requests, "post", post)


def test_players_match_by_name_and_override(monkeypatch):
    fake_ha(monkeypatch, [])
    players, entry = music.player_map(CONFIG)
    assert entry == "E1"
    assert players == {"media_player.lounge": "media_player.lounge_2",
                       "media_player.roam": "media_player.roam_ma"}


def test_items_are_labelled_with_their_service():
    by = {i["name"]: i for i in (music._shape(x) for k in LIB.values() for x in k)}
    assert by["Khruangbin"]["provider"] == "Spotify"
    assert by["Arvo"]["provider"] == "YouTube Music"
    assert by["Currents"]["provider"] == "YouTube Music"    # from the image proxy link
    assert by["Currents"]["sub"] == "Tame Impala"


def test_play_artist_shuffles_its_own_tracks_no_radio(monkeypatch):
    calls = []
    fake_ha(monkeypatch, calls)
    monkeypatch.setattr(music, "_started", lambda target, before, wait=0: True)
    monkeypatch.setattr(music, "_ha_state", lambda e: ("idle", {}))
    music.play(CONFIG, "media_player.lounge", "spotify://artist/a", "artist")
    url, body = calls[-1]
    assert url.endswith("/services/music_assistant/play_media")
    assert body["entity_id"] == "media_player.lounge_2"
    assert body["radio_mode"] is False
    assert any("shuffle_set" in u for u, _ in calls)


def test_song_radio_falls_back_to_plain_then_reports(monkeypatch):
    calls = []
    fake_ha(monkeypatch, calls)
    monkeypatch.setattr(music, "_ha_state", lambda e: ("idle", {}))
    started = iter([False, True])
    monkeypatch.setattr(music, "_started", lambda target, before, wait=0: next(started))
    music.play(CONFIG, "media_player.lounge", "ytmusic://track/1", "track")
    radios = [b["radio_mode"] for u, b in calls if u.endswith("play_media")]
    assert radios == [True, False]
    monkeypatch.setattr(music, "_started", lambda target, before, wait=0: False)
    with pytest.raises(RuntimeError, match="never started"):
        music.play(CONFIG, "media_player.lounge", "spotify://album/1", "album")


def test_started_watches_for_a_new_track(monkeypatch):
    states = iter([("buffering", {}), ("playing", {"media_title": "Old"}), ("playing", {"media_title": "New"})])
    monkeypatch.setattr(music, "_ha_state", lambda e: next(states))
    monkeypatch.setattr(music, "POLL_S", 0)
    monkeypatch.setattr(music.time, "sleep", lambda s: None)
    assert music._started("media_player.x", ("playing", {"media_title": "Old"}), wait=5) is True


def test_unavailable_when_music_assistant_missing(monkeypatch):
    monkeypatch.setattr(music.ha, "_api_base", lambda: ("http://ha/api", "t"))
    music._cache.update(at=0.0, data=None)

    def boom(*a, **k):
        raise ConnectionError("no MA")
    monkeypatch.setattr(music.requests, "post", boom)
    assert music.info(CONFIG)["available"] is False


def test_music_routes_need_a_driveable_speaker(client, monkeypatch):
    import app as app_module
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "media_player.lounge", "type": "media"}])          # read-only
    r = client.post("/api/ha/music/play", json={"entity": "media_player.lounge",
                                                "uri": "x", "media_type": "artist"})
    assert r.status_code == 404
    monkeypatch.setitem(app_module.CONFIG, "ha_tiles", [
        {"entity": "media_player.lounge", "type": "media", "allow_action": True}])
    got = []
    monkeypatch.setattr(music, "surprise", lambda cfg, e, src=None: got.append((e, src)) or {"name": "X"})
    r = client.post("/api/ha/music/surprise", json={"entity": "media_player.lounge", "source": "spotify"})
    assert r.status_code == 200 and got == [("media_player.lounge", "spotify")]


def test_discover_from_one_service_only(monkeypatch):
    fake_ha(monkeypatch, [])
    music._ma.update(base=None, providers=None, at=0.0)
    monkeypatch.setattr(music, "MA_CANDIDATES", ())   # MA API out of reach
    names = {i["name"] for i in music.discover(CONFIG, source="spotify")["items"]}
    assert names == {"Khruangbin", "Rumours"}
    names = {i["name"] for i in music.discover(CONFIG, source="ytmusic")["items"]}
    assert names == {"Arvo", "Currents"}
    assert music.discover(CONFIG, source="bogus")["source"] == "favs"


def test_two_spotify_logins_are_two_named_buttons(monkeypatch):
    calls = []
    fake_ha(monkeypatch, calls)
    music._ma.update(base=None, providers=None, at=0.0)
    ha_post = music.requests.post

    def post(url, headers=None, json=None, timeout=None):
        if not url.endswith("/api"):
            return ha_post(url, headers=headers, json=json, timeout=timeout)
        cmd = json["command"]
        if cmd == "providers":
            return Resp([{"domain": "spotify", "instance_id": "spotify--r", "name": "Spotify"},
                         {"domain": "spotify", "instance_id": "spotify--m", "name": "Spotify"},
                         {"domain": "ytmusic", "instance_id": "ytmusic--y", "name": "YouTube Music"},
                         {"domain": "filesystem_local", "instance_id": "fs", "name": "Files"}])
        if cmd == "music/playlists/library_items":
            return Resp([{"media_type": "playlist", "uri": "library://playlist/1", "name": "Mel's Mix",
                          "provider_mappings": [{"provider_instance": "spotify--m",
                                                 "provider_domain": "spotify"}]},
                         {"media_type": "playlist", "uri": "library://playlist/2", "name": "Rohan Rock",
                          "provider_mappings": [{"provider_instance": "spotify--r",
                                                 "provider_domain": "spotify"}]}])
        return Resp([])
    monkeypatch.setattr(music.requests, "post", post)
    cfg = {**CONFIG, "music_source_names": "Spotify=Rohan, Spotify 2=Mel"}
    d = music.discover(cfg, source="spotify--m")
    assert [s["label"] for s in d["sources"]] == ["Rohan", "Mel", "YouTube Music"]
    assert [i["name"] for i in d["items"]] == ["Mel's Mix"]
    assert music.discover(cfg)["items"] == []          # favourites: buttons only


def test_artwork_only_from_known_hosts():
    ok = music.art_allowed
    assert ok("https://i.scdn.co/image/ab67616d")
    assert ok("https://lh3.googleusercontent.com/abc=w544")
    assert ok("http://192.168.1.21:8095/imageproxy?path=x&provider=spotify")
    assert not ok("http://169.254.169.254/latest/meta-data")
    assert not ok("https://evil.example.com/imageproxy?path=x")
    assert not ok("http://192.168.1.21:8123/api/states")
    assert not ok("file:///etc/passwd")


def test_items_carry_an_art_link_from_any_shape():
    music._ma.update(base="http://172.30.32.1:8095")
    a = music._shape({"media_type": "album", "uri": "spotify://album/1", "name": "Currents",
                      "metadata": {"images": [{"type": "thumb", "path": "https://i.scdn.co/image/x"}]}})
    assert a["image"].startswith("/api/ha/music/art?u=https%3A%2F%2Fi.scdn.co")
    t = music._shape({"media_type": "track", "uri": "ytmusic://track/9", "name": "Song",
                      "artists": [{"name": "Band"}],
                      "image": {"path": "/data/thumbs/x.jpg", "provider": "filesystem"}})
    assert t["sub"] == "Band" and "imageproxy" in t["image"]
    assert music._shape({"media_type": "playlist", "uri": "u", "name": "No art"})["image"] is None


def test_search_keeps_to_one_account(monkeypatch):
    fake_ha(monkeypatch, [])
    music._ma.update(base=None, providers=None, at=0.0)
    ha_post = music.requests.post

    def post(url, headers=None, json=None, timeout=None):
        if not url.endswith("/api"):
            return ha_post(url, headers=headers, json=json, timeout=timeout)
        assert json["command"] == "music/search"
        return Resp({"artists": [{"media_type": "artist", "uri": "spotify--m://artist/1", "name": "Mel pick",
                                  "provider": "spotify--m"},
                                 {"media_type": "artist", "uri": "spotify--r://artist/2", "name": "Rohan pick",
                                  "provider": "spotify--r"}],
                     "tracks": [{"media_type": "track", "uri": "spotify--m://track/3", "name": "Song",
                                 "provider_mappings": [{"provider_instance": "spotify--m"}]}]})
    monkeypatch.setattr(music.requests, "post", post)
    r = music.search(CONFIG, "spotify--m", "khruangbin")
    assert [a["name"] for a in r["artists"]] == ["Mel pick"]
    assert [t["name"] for t in r["tracks"]] == ["Song"]
    import pytest
    with pytest.raises(ValueError):
        music.search(CONFIG, "spotify--m", "k")


def test_art_endpoint_refuses_other_hosts(client):
    assert client.get("/api/ha/music/art?u=http://169.254.169.254/").status_code == 404


def test_ma_probe_fails_fast_and_remembers(monkeypatch):
    music._ma.update(base=None, providers=None, at=0.0, down_until=0.0, down_why=None)
    tried = []

    def post(url, headers=None, json=None, timeout=None):
        tried.append(url)
        assert timeout[0] <= 3                       # short connect timeout
        raise music.requests.ConnectionError("refused")
    monkeypatch.setattr(music.requests, "post", post)
    import pytest
    with pytest.raises(music.MAUnavailable):
        music.ma_api({}, "providers")
    assert any(":8094/api" in u for u in tried)
    n = len(tried)
    with pytest.raises(music.MAUnavailable):        # remembered: no re-probe
        music.ma_api({}, "providers")
    assert len(tried) == n
    music._ma.update(down_until=0.0)


def test_ma_probe_skips_a_404_port(monkeypatch):
    music._ma.update(base=None, providers=None, at=0.0, down_until=0.0, down_why=None)

    class R404:
        status_code = 404

        def raise_for_status(self):
            raise music.requests.HTTPError(response=self)

    def post(url, headers=None, json=None, timeout=None):
        return Resp(["ok"]) if ":8094" in url else R404()
    monkeypatch.setattr(music.requests, "post", post)
    assert music.ma_api({}, "providers") == ["ok"]
    assert music._ma["base"].endswith(":8094")
    music._ma.update(base=None)


def test_start_play_returns_at_once_and_reports_failure(monkeypatch):
    import threading
    fake_ha(monkeypatch, [])
    gate = threading.Event()

    def slow_fail(*a, **k):
        gate.wait(2)
        raise RuntimeError("PO token server not reachable")
    monkeypatch.setattr(music, "play", slow_fail)
    music.start_play(CONFIG, "media_player.lounge", "ytmusic://track/1", "track", "Song")
    assert music.play_status("media_player.lounge")["state"] == "starting"   # didn't wait
    gate.set()
    for _ in range(50):
        if music.play_status("media_player.lounge")["state"] != "starting":
            break
        threading.Event().wait(0.02)
    st = music.play_status("media_player.lounge")
    assert st["state"] == "error" and "PO token" in st["error"] and st["name"] == "Song"
    with pytest.raises(ValueError):                      # a bad order still fails on the tap
        music.start_play(CONFIG, "media_player.nowhere", "x", "track")
