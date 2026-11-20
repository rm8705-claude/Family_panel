"""Music Assistant: player matching, Discover picks, play/surprise routing."""
import music


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


def test_play_artist_is_radio_on_the_ma_twin(monkeypatch):
    calls = []
    fake_ha(monkeypatch, calls)
    music.play(CONFIG, "media_player.lounge", "spotify://artist/a", "artist")
    url, body = calls[-1]
    assert url.endswith("/services/music_assistant/play_media")
    assert body["entity_id"] == "media_player.lounge_2"
    assert body["radio_mode"] is True


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
