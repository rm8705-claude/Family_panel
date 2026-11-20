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


def status(config: dict) -> dict:
    """Step-by-step diagnosis for /api/ha/music/status: which part of the
    chain (find MA players -> match speakers -> read the library) fails."""
    out = {"ma_players": None, "config_entry": None, "speaker_map": None,
           "unmatched_speakers": None, "library_counts": {}, "error": None}
    try:
        _cache.update(at=0.0, data=None)
        out["ma_players"] = _players()
        if not out["ma_players"]:
            out["error"] = ("Home Assistant reports no Music Assistant players. Is the "
                            "Music Assistant integration added (Settings > Devices & "
                            "services), not just the add-on?")
            return out
        players, entry = player_map(config)
        out["config_entry"], out["speaker_map"] = entry, players
        out["unmatched_speakers"] = [t["entity"] for t in config.get("ha_tiles", []) or []
                                     if t.get("type") == "media" and t.get("entity")
                                     and t["entity"] not in players]
        if not players:
            out["error"] = ("No Sonos tile matched a Music Assistant player by name; "
                            "set music_assistant_players in the add-on options.")
            return out
        out["source_counts"] = {}
        for kind in KINDS:
            try:
                got = [s for s in map(_shape, _library(entry, kind, 30)) if s]
                out["library_counts"][kind] = len(got)
                for g in got:
                    k = g["source"] or "unknown"
                    out["source_counts"][k] = out["source_counts"].get(k, 0) + 1
            except Exception as e:          # noqa: BLE001
                out["library_counts"][kind] = f"error: {e}"
    except Exception as e:                  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


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


PROVIDERS = {"spotify": "Spotify", "ytmusic": "YouTube Music"}

# Where an item came from. A library item's uri is just library://playlist/12,
# so the provider is read from whatever else carries it: a provider uri, the
# Music Assistant image proxy link (…&provider=spotify--abc…), or the image
# host itself (Spotify's scdn.co, Google's ytimg/googleusercontent).
_HINTS = (
    ("spotify", ("spotify", "scdn.co")),
    ("ytmusic", ("ytmusic", "youtube", "ytimg.com", "googleusercontent.com")),
)


def _provider_key(item: dict) -> str | None:
    blobs = [str(item.get("uri") or "")]
    img = item.get("image")
    blobs.append(json.dumps(img) if isinstance(img, (dict, list)) else str(img or ""))
    for k in ("provider", "provider_mappings", "provider_domain"):
        if item.get(k):
            blobs.append(json.dumps(item.get(k)))
    text = " ".join(blobs).lower()
    for key, needles in _HINTS:
        if any(n in text for n in needles):
            return key
    return None


def _provider(uri: str) -> str | None:
    return PROVIDERS.get(_provider_key({"uri": uri}))


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
    key = _provider_key(item)
    return {"uri": uri, "name": name, "media_type": kind, "sub": sub,
            "source": key, "provider": PROVIDERS.get(key)}


def _picks(entry: str, kind: str, n: int, source: str | None) -> list[dict]:
    """n shaped items of one kind, only from `source` when one is asked for.
    A filtered ask reads a bigger random page, since only part of it will
    be from that service."""
    items = [s for s in map(_shape, _library(entry, kind, n if not source else 60)) if s]
    if source:
        items = [i for i in items if i["source"] == source]
    random.shuffle(items)
    return items[:n]


def _source(source) -> str | None:
    return source if source in PROVIDERS else None


def discover(config: dict, n: int = 6, source: str | None = None) -> dict:
    """A fresh random handful from the library, mixed across kinds — from
    one service only when `source` is "spotify" or "ytmusic"."""
    source = _source(source)
    players, entry = player_map(config)
    if not (players and entry):
        return {"available": False, "items": [], "source": source}
    items = []
    for kind in KINDS:
        try:
            items += _picks(entry, kind, n, source)
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
    return {"available": True, "items": picked, "source": source}


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


def surprise(config: dict, entity: str, source: str | None = None) -> dict:
    """One random artist or playlist from the library, as radio: it starts
    from something you like and wanders off into things you haven't heard."""
    players, entry = player_map(config)
    if not (players.get(entity) and entry):
        raise ValueError("Music Assistant has no player for that speaker")
    source = _source(source)
    kind = random.choice(("artist", "artist", "playlist"))
    items = _picks(entry, kind, 10, source)
    if not items:
        kind = "playlist" if kind == "artist" else "artist"
        items = _picks(entry, kind, 10, source)
    if not items:
        raise ValueError("Nothing from " + PROVIDERS[source] + " in the library yet"
                         if source else "Nothing in the Music Assistant library yet")
    pick = random.choice(items)
    _service("play_media", {"entity_id": players[entity], "media_id": pick["uri"],
                            "media_type": pick["media_type"], "enqueue": "replace",
                            "radio_mode": True})
    return pick
