"""Music Assistant: fresh music for the Sonos sheet, beyond the favourites.

The Sonos favourites (source_list) are the same 10-20 things forever. Music
Assistant, installed as a Home Assistant add-on with Spotify / YouTube Music
as providers, holds the household's whole streaming library and can start
"radio mode" — an endless run of similar tracks from one artist or playlist.

Everything goes through Home Assistant's REST API, same token as ha.py:
  - which Music Assistant players exist, and the config entry their services
    need, come from one /api/template render (integration_entities +
    config_entry_id), so nothing has to be configured by hand;
  - library picks come from music_assistant.get_library (?return_response);
  - playback is music_assistant.play_media aimed at the MA player that wraps
    the Sonos speaker the panel was asked to play on.

MA creates its own media_player for each Sonos, named after the room. The
panel matches them to its Sonos tiles by name; the add-on option
`music_assistant_players` ("media_player.lounge=media_player.lounge_2, ...")
overrides that when names don't line up.

When Music Assistant isn't installed, info() says unavailable and the panel
just doesn't draw the Discover row.
"""
import json
import logging
import random
import re
import time

import requests

import ha

log = logging.getLogger("family-panel.music")

TIMEOUT = 10
_CACHE_S = 300
_cache = {"at": 0.0, "data": None}

# One render: every MA media_player, its name, and its config entry.
_TEMPLATE = (
    "[{% for e in integration_entities('music_assistant') "
    "if e.startswith('media_player.') %}"
    "{{ {'entity': e, 'name': state_attr(e, 'friendly_name') or e, "
    "'entry': config_entry_id(e)} | tojson }}{{ ',' if not loop.last }}"
    "{% endfor %}]"
)

KINDS = ("artist", "playlist", "album")


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _overrides(config: dict) -> dict:
    """music_assistant_players: "sonos_entity=ma_entity, ..." (flat string,
    because the Supervisor schema can't take an optional map)."""
    out = {}
    for pair in str(config.get("music_assistant_players") or "").split(","):
        if "=" in pair:
            a, b = (x.strip() for x in pair.split("=", 1))
            if a and b:
                out[a] = b
    return out


def _players() -> list[dict]:
    now = time.time()
    if _cache["data"] is not None and now - _cache["at"] < _CACHE_S:
        return _cache["data"]
    base, token = ha._api_base()
    r = requests.post(f"{base}/template", headers=ha._headers(token),
                      json={"template": _TEMPLATE}, timeout=TIMEOUT)
    r.raise_for_status()
    try:
        data = json.loads(r.text or "[]")
    except ValueError:
        data = []
    data = [p for p in data if isinstance(p, dict) and p.get("entity")]
    _cache.update(at=now, data=data)
    return data


def player_map(config: dict) -> tuple[dict, str | None]:
    """({sonos_entity: ma_entity}, config_entry_id) for the panel's speakers."""
    players = _players()
    entry = next((p.get("entry") for p in players if p.get("entry")), None)
    by_name = {_norm(p.get("name")): p["entity"] for p in players}
    ma_ids = {p["entity"] for p in players}
    states = (ha.cached_states() or {}).get("states", {})
    over = _overrides(config)
    out = {}
    for t in config.get("ha_tiles", []) or []:
        if t.get("type") != "media" or not t.get("entity"):
            continue
        e = t["entity"]
        if e in over:
            out[e] = over[e]
            continue
        if e in ma_ids:                     # the tile already IS an MA player
            out[e] = e
            continue
        name = ((states.get(e) or {}).get("attributes") or {}).get("friendly_name")
        for cand in (name, t.get("label")):
            hit = by_name.get(_norm(cand)) if cand else None
            if hit:
                out[e] = hit
                break
    return out, entry


def info(config: dict) -> dict:
    try:
        players, entry = player_map(config)
    except Exception as e:                  # noqa: BLE001 - MA simply absent
        log.info("music assistant unavailable: %s", e)
        return {"available": False, "players": {}}
    return {"available": bool(players and entry), "players": players}


def _service(service: str, body: dict, want_response: bool = False):
    base, token = ha._api_base()
    url = f"{base}/services/music_assistant/{service}"
    if want_response:
        url += "?return_response"
    r = requests.post(url, headers=ha._headers(token), json=body, timeout=TIMEOUT)
    r.raise_for_status()
    if not want_response:
        return None
    data = r.json() or {}
    return data.get("service_response", data)


def _library(entry: str, kind: str, n: int) -> list[dict]:
    """n random library items of one kind. order_by=random where this MA
    version takes it; otherwise a bigger page, sampled here."""
    try:
        resp = _service("get_library", {"config_entry_id": entry, "media_type": kind,
                                        "limit": n, "order_by": "random"}, True)
        items = (resp or {}).get("items") or []
    except requests.HTTPError:
        resp = _service("get_library", {"config_entry_id": entry, "media_type": kind,
                                        "limit": 250}, True)
        items = (resp or {}).get("items") or []
        items = random.sample(items, min(n, len(items)))
    return items


def _provider(uri: str) -> str | None:
    head = str(uri or "").split("://", 1)[0].lower()
    if head.startswith("spotify"):
        return "Spotify"
    if head.startswith("ytmusic") or head.startswith("youtube"):
        return "YouTube Music"
    return None


def _shape(item: dict) -> dict | None:
    uri, name = item.get("uri"), item.get("name")
    kind = item.get("media_type")
    if not uri or not name or kind not in KINDS:
        return None
    artists = ", ".join(a.get("name", "") for a in (item.get("artists") or [])
                        if isinstance(a, dict) and a.get("name"))
    sub = {"artist": "Artist radio", "playlist": "Playlist", "album": "Album"}[kind]
    if kind == "album" and artists:
        sub = artists
    return {"uri": uri, "name": name, "media_type": kind, "sub": sub,
            "provider": _provider(uri)}


def discover(config: dict, n: int = 6) -> dict:
    """A fresh random handful from the whole library, mixed across kinds."""
    players, entry = player_map(config)
    if not (players and entry):
        return {"available": False, "items": []}
    items = []
    for kind in KINDS:
        try:
            items += [s for s in map(_shape, _library(entry, kind, n)) if s]
        except Exception as e:              # noqa: BLE001 - one kind failing is fine
            log.info("get_library %s: %s", kind, e)
    random.shuffle(items)
    # keep the kinds mixed rather than six playlists in a row
    picked, seen = [], {}
    for it in items:
        if seen.get(it["media_type"], 0) < (n + 2) // 3 + 1:
            picked.append(it)
            seen[it["media_type"]] = seen.get(it["media_type"], 0) + 1
        if len(picked) >= n:
            break
    return {"available": True, "items": picked}


def play(config: dict, entity: str, uri: str, media_type: str) -> None:
    """Play a library item on the speaker (via its MA twin). Artists start
    radio mode — similar tracks, endlessly; playlists and albums play
    shuffled and then carry on into radio once they run out."""
    players, _ = player_map(config)
    target = players.get(entity)
    if not target:
        raise ValueError("Music Assistant has no player for that speaker")
    if media_type not in KINDS:
        raise ValueError("Unknown media type")
    if media_type != "artist":
        try:
            ha_shuffle(target)
        except Exception as e:              # noqa: BLE001
            log.info("shuffle on %s: %s", target, e)
    _service("play_media", {"entity_id": target, "media_id": uri,
                            "media_type": media_type, "enqueue": "replace",
                            "radio_mode": media_type == "artist"})


def ha_shuffle(target: str) -> None:
    base, token = ha._api_base()
    r = requests.post(f"{base}/services/media_player/shuffle_set",
                      headers=ha._headers(token),
                      json={"entity_id": target, "shuffle": True}, timeout=TIMEOUT)
    r.raise_for_status()


def surprise(config: dict, entity: str) -> dict:
    """One random artist or playlist from the library, as radio: it starts
    from something you like and wanders off into things you haven't heard."""
    players, entry = player_map(config)
    if not (players.get(entity) and entry):
        raise ValueError("Music Assistant has no player for that speaker")
    kind = random.choice(("artist", "artist", "playlist"))
    items = [s for s in map(_shape, _library(entry, kind, 10)) if s]
    if not items and kind != "playlist":
        kind = "playlist"
        items = [s for s in map(_shape, _library(entry, kind, 10)) if s]
    if not items:
        raise ValueError("Nothing in the Music Assistant library yet")
    pick = random.choice(items)
    _service("play_media", {"entity_id": players[entity], "media_id": pick["uri"],
                            "media_type": pick["media_type"], "enqueue": "replace",
                            "radio_mode": True})
    return pick
