"""Music Assistant: player matching, Discover picks, play/surprise routing."""
import music


class Resp:
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


def test_discover_mixes_kinds_and_labels_providers(monkeypatch):
    fake_ha(monkeypatch, [])
    d = music.discover(CONFIG)
    assert d["available"] is True
    by = {i["name"]: i for i in d["items"]}
    assert by["Khruangbin"]["provider"] == "Spotify"
    assert by["Arvo"]["provider"] == "YouTube Music"
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
    names = {i["name"] for i in music.discover(CONFIG, source="spotify")["items"]}
    assert names == {"Khruangbin", "Rumours"}
    names = {i["name"] for i in music.discover(CONFIG, source="ytmusic")["items"]}
    assert names == {"Arvo", "Currents"}
    assert len(music.discover(CONFIG, source="bogus")["items"]) == 4   # ignored -> both
