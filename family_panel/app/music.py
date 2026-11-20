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
           "unmatched_speakers": None, "library_counts": {}, "error": None,
           "ma_api": None, "accounts": None}
    try:
        _ma.update(providers=None)
        out["accounts"] = sources(config)
        out["ma_api"] = f"ok at {_ma['base']}"
    except Exception as e:                  # noqa: BLE001
        out["ma_api"] = f"{type(e).__name__}: {e}"
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


# ------------------------------------------------- Music Assistant, directly
# Home Assistant's music_assistant services can't say WHICH account an item
# belongs to, and a household with two Spotify logins needs exactly that. So
# the panel also asks Music Assistant itself (its JSON API on port 8095): the
# provider list gives one entry per connected account, and library items
# carry provider_mappings naming the account(s) they came from.
# MA runs on the host network, so from another add-on it's the Supervisor
# gateway; music_assistant_url overrides, music_assistant_token is only for
# an MA that has login turned on.
MA_CANDIDATES = ("http://172.30.32.1:8095", "http://d5369777-music-assistant:8095",
                 "http://homeassistant.local:8095")
STREAMING = {"spotify": "Spotify", "ytmusic": "YouTube Music", "apple_music": "Apple Music",
             "tidal": "Tidal", "deezer": "Deezer", "qobuz": "Qobuz", "soundcloud": "SoundCloud"}
_ma = {"base": None, "at": 0.0, "providers": None}


class MAUnavailable(Exception):
    pass


def _ma_post(base: str, config: dict, command: str, args: dict | None):
    headers = {"Content-Type": "application/json"}
    tok = str(config.get("music_assistant_token") or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    r = requests.post(f"{base}/api", headers=headers, timeout=TIMEOUT,
                      json={"message_id": "panel", "command": command, "args": args or {}})
    if r.status_code in (401, 403):
        raise MAUnavailable(f"Music Assistant at {base} wants a login token "
                            "(add-on option music_assistant_token)")
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "message_id" in data and ("result" in data or "error_code" in data):
        if "error_code" in data:
            raise ValueError(data.get("details") or data.get("error_code"))
        return data.get("result")
    return data


def ma_api(config: dict, command: str, args: dict | None = None):
    """One Music Assistant API command, on whichever address answers."""
    own = str(config.get("music_assistant_url") or "").strip().rstrip("/")
    bases = [own] if own else ([_ma["base"]] if _ma["base"] else []) + \
        [b for b in MA_CANDIDATES if b != _ma["base"]]
    last = None
    for base in bases:
        try:
            out = _ma_post(base, config, command, args)
            _ma["base"] = base
            return out
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            continue
    raise MAUnavailable(f"Music Assistant not reachable ({last})")


def _names(config: dict) -> dict:
    """music_source_names: "Spotify=Rohan, Spotify 2=Mel" — relabel buttons."""
    out = {}
    for pair in str(config.get("music_source_names") or "").split(","):
        if "=" in pair:
            a, b = (x.strip() for x in pair.split("=", 1))
            if a and b:
                out[a.lower()] = b
    return out


def sources(config: dict) -> list[dict]:
    """[{id, label, domain}] — one per streaming account connected in Music
    Assistant. Two Spotify logins are two entries. Cached a few minutes."""
    now = time.time()
    if _ma["providers"] is None or now - _ma["at"] > _CACHE_S:
        provs = ma_api(config, "providers") or []
        _ma.update(providers=provs, at=now)
    provs = [p for p in _ma["providers"] if isinstance(p, dict)
             and p.get("domain") in STREAMING and p.get("available", True) is not False]
    seen, out, names = {}, [], _names(config)
    for p in provs:
        base = str(p.get("name") or STREAMING[p["domain"]])
        seen[base] = seen.get(base, 0) + 1
        label = base if seen[base] == 1 else f"{base} {seen[base]}"
        out.append({"id": p.get("instance_id") or p["domain"],
                    "label": names.get(label.lower(), label), "domain": p["domain"]})
    return out


def _from_account(item: dict, instance: str) -> bool:
    return any(isinstance(m, dict) and instance in (m.get("provider_instance"), m.get("provider_domain"))
               for m in item.get("provider_mappings") or [])


def _ma_library(config: dict, kind: str, n: int, instance: str) -> list[dict]:
    """n random library items of one kind from one account."""
    cmd = f"music/{kind}s/library_items"
    try:
        items = ma_api(config, cmd, {"limit": n * 3, "order_by": "random", "provider": instance}) or []
    except (ValueError, requests.HTTPError):
        items = ma_api(config, cmd, {"limit": 300, "order_by": "random"}) or []
    if isinstance(items, dict):
        items = items.get("items") or []
    items = [i for i in items if isinstance(i, dict) and _from_account(i, instance)]
    random.shuffle(items)
    return items[:n]


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
    if not key:
        doms = {m.get("provider_domain") for m in item.get("provider_mappings") or []
                if isinstance(m, dict)}
        key = next((d for d in ("spotify", "ytmusic") if d in doms), None)
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


def _account_picks(config: dict, kind: str, n: int, account: str) -> list[dict]:
    return [s for s in map(_shape, _ma_library(config, kind, n, account)) if s]


def _list_sources(config: dict) -> list[dict]:
    """Accounts from Music Assistant itself; if its API can't be reached,
    the per-service split read off Home Assistant (no per-person split)."""
    try:
        return sources(config)
    except Exception as e:                  # noqa: BLE001
        log.info("music assistant api: %s", e)
        return [{"id": k, "label": v, "domain": k} for k, v in PROVIDERS.items()]


def discover(config: dict, n: int = 6, source: str | None = None) -> dict:
    """The play-from buttons, and a fresh random handful from one of them.
    `source` is an account id from sources() (or "spotify"/"ytmusic" when the
    MA API is out of reach); "favs" or nothing = just the buttons."""
    players, entry = player_map(config)
    if not (players and entry):
        return {"available": False, "items": [], "sources": [], "source": source}
    srcs = _list_sources(config)
    ids = {s["id"] for s in srcs}
    if not source or source == "favs" or source not in ids:
        return {"available": True, "items": [], "sources": srcs,
                "source": "favs" if source not in ids else source}
    direct = source not in PROVIDERS
    items = []
    for kind in KINDS:
        try:
            items += (_account_picks(config, kind, n, source) if direct
                      else _picks(entry, kind, n, source))
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
    return {"available": True, "items": picked, "sources": srcs, "source": source}


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
    direct = bool(source) and source not in PROVIDERS and source != "favs"
    source = source if direct else _source(source)

    def grab(kind):
        return _account_picks(config, kind, 10, source) if direct else _picks(entry, kind, 10, source)
    kind = random.choice(("artist", "artist", "playlist"))
    items = grab(kind)
    if not items:
        kind = "playlist" if kind == "artist" else "artist"
        items = grab(kind)
    if not items:
        raise ValueError("Nothing from that account in the library yet"
                         if source else "Nothing in the Music Assistant library yet")
    pick = random.choice(items)
    _service("play_media", {"entity_id": players[entity], "media_id": pick["uri"],
                            "media_type": pick["media_type"], "enqueue": "replace",
                            "radio_mode": True})
    return pick
