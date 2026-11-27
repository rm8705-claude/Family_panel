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
import ipaddress
import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import requests

import db
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

KINDS = ("artist", "playlist", "album")          # what Discover deals
PLAYABLE = KINDS + ("track",)                     # what search can also return


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
           "ma_api": None, "accounts": None, "music_services": None,
           "ma_players_direct": None, "speaker_to_ma_player": None, "last_plays": list(_attempts)}
    try:
        _ma.update(providers=None, down_until=0.0, base=None)   # probe afresh
        _players_direct.update(list=None)
        out["accounts"] = sources(config)
        out["ma_api"] = f"ok at {_ma['base']}"
        # every streaming service MA has, working or not — "available: false"
        # is the usual reason for "media item could not be found"
        out["music_services"] = [
            {"name": p.get("name"), "domain": p.get("domain"), "id": p.get("instance_id"),
             "available": p.get("available", True)}
            for p in (_ma["providers"] or []) if isinstance(p, dict) and p.get("domain") in STREAMING]
        out["ma_players_direct"] = ma_players(config)
        out["speaker_to_ma_player"] = {
            t["entity"]: ma_queue_for(config, t["entity"])
            for t in config.get("ha_tiles", []) or [] if t.get("type") == "media" and t.get("entity")}
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
# Both ports: newer Music Assistant builds serve the API on 8094 as well as /
# instead of 8095 (Home Assistant's own integration was seen on :8094).
MA_CANDIDATES = ("http://172.30.32.1:8095", "http://172.30.32.1:8094",
                 "http://d5369777-music-assistant:8095", "http://d5369777-music-assistant:8094")
CONNECT_TIMEOUT = 2.5        # a dead address must fail fast, not eat the request
DOWN_RETRY_S = 300
STREAMING = {"spotify": "Spotify", "ytmusic": "YouTube Music", "apple_music": "Apple Music",
             "tidal": "Tidal", "deezer": "Deezer", "qobuz": "Qobuz", "soundcloud": "SoundCloud"}
_ma = {"base": None, "at": 0.0, "providers": None, "down_until": 0.0, "down_why": None}


class MAUnavailable(Exception):
    pass


class MAError(RuntimeError):
    """Home Assistant / Music Assistant turned a call down; the message is
    the reason in plain words."""
    def __init__(self, msg: str, status: int = 0, not_found: bool | None = None):
        super().__init__(msg)
        self.status = status
        low = msg.lower()
        self.not_found = (not_found if not_found is not None else
                          ("not be found" in low or "notfound" in low or "couldn't find" in low))


def _ma_reason(text: str) -> str:
    t = " ".join(str(text or "").split())
    if "MediaNotFound" in t or "could not be found" in t:
        return ("Music Assistant couldn't find that item — usually its Spotify / YouTube Music "
                "connection isn't working (Music Assistant > Settings > Providers; Spotify needs Premium)")
    if "500" in t and "trouble" in t.lower():
        return ("Music Assistant couldn't play it (Home Assistant only passes on \"500\"; its log "
                "usually says media item not found — the panel needs music_assistant_token to "
                "talk to Music Assistant directly)")
    if "ProviderUnavailable" in t or "unavailable" in t.lower():
        return "Music Assistant says that music service is unavailable right now"
    return f"Music Assistant said: {t[:180]}" if t else "Music Assistant turned it down"


def _ma_post(base: str, config: dict, command: str, args: dict | None):
    headers = {"Content-Type": "application/json"}
    tok = str(config.get("music_assistant_token") or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    r = requests.post(f"{base}/api", headers=headers, timeout=(CONNECT_TIMEOUT, TIMEOUT),
                      json={"message_id": "panel", "command": command, "args": args or {}})
    if r.status_code in (401, 403):
        raise MAUnavailable(f"Music Assistant at {base} wants a login token "
                            "(add-on option music_assistant_token)")
    if r.status_code >= 400 and r.status_code not in (404, 405):
        # MA's own reason (e.g. MediaNotFoundError), not just "500 Server Error"
        raise ValueError(" ".join(str(r.text or f"HTTP {r.status_code}").split())[:240])
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
    # Known unreachable: say so at once rather than re-probing every address
    # on every request (that probing is what timed the panel out).
    if not _ma["base"] and time.time() < _ma["down_until"]:
        raise MAUnavailable(_ma["down_why"] or "Music Assistant not reachable")
    bases = [own] if own else ([_ma["base"]] if _ma["base"] else []) + \
        [b for b in MA_CANDIDATES if b != _ma["base"]]
    last = None
    for base in bases:
        try:
            out = _ma_post(base, config, command, args)
            _ma.update(base=base, down_until=0.0, down_why=None)
            return out
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        except requests.HTTPError as e:     # something else on that port
            if e.response is None or e.response.status_code not in (404, 405):
                raise
            last = e
        if base == _ma["base"]:
            _ma["base"] = None              # the one that worked has gone; look again
    why = f"Music Assistant not reachable ({last})"
    _ma.update(down_until=time.time() + DOWN_RETRY_S, down_why=why)
    raise MAUnavailable(why)


# ------------------------------------------------- Music Assistant players
# Playback goes straight to Music Assistant (player_queues/play_media) when
# its API is reachable — the same server the library, Discover and search
# came from, so the item ids always line up. Going through Home Assistant's
# music_assistant integration instead hit "media item could not be found"
# whenever that integration and the panel weren't looking at the same thing.
_players_direct = {"at": 0.0, "list": None}
_attempts: list = []                         # the last few plays, for the status page


def _note(entity: str, uri: str, media_type: str, route: str, result: str) -> None:
    _attempts.append({"at": db.utc_now_iso(), "speaker": entity, "uri": uri, "type": media_type,
                      "route": route, "result": result[:240]})
    del _attempts[:-10]


def ma_players(config: dict) -> list[dict]:
    if _players_direct["list"] is None or time.time() - _players_direct["at"] > _CACHE_S:
        raw = ma_api(config, "players/all") or []
        _players_direct.update(at=time.time(), list=[
            {"player_id": p.get("player_id"), "name": p.get("display_name") or p.get("name"),
             "available": p.get("available", True), "provider": p.get("provider")}
            for p in raw if isinstance(p, dict) and p.get("player_id")])
    return _players_direct["list"]


def ma_queue_for(config: dict, entity: str) -> str | None:
    """Music Assistant's own player id for a Sonos tile, matched by name."""
    states = (ha.cached_states() or {}).get("states", {})
    names = {_norm(n) for n in (
        ((states.get(entity) or {}).get("attributes") or {}).get("friendly_name"),
        next((t.get("label") for t in config.get("ha_tiles", []) or [] if t.get("entity") == entity), None),
    ) if n}
    twin = player_map(config)[0].get(entity)
    if twin:
        names.add(_norm(next((p.get("name") for p in _players() if p.get("entity") == twin), "")))
    names.discard("")
    for p in ma_players(config):
        if _norm(p["name"]) in names and p.get("available", True) is not False:
            return p["player_id"]
    return None


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
    if item.get("provider") == instance:
        return True
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


# play_media doesn't answer until Music Assistant has the stream going — for
# Spotify / YouTube Music that's often well past 10 s, and the old 10 s limit
# turned a slow start into a "read timed out" error on the wall.
PLAY_TIMEOUT = 90


def _service(service: str, body: dict, want_response: bool = False, timeout: float = TIMEOUT):
    base, token = ha._api_base()
    url = f"{base}/services/music_assistant/{service}"
    if want_response:
        url += "?return_response"
    r = requests.post(url, headers=ha._headers(token), json=body, timeout=(5, timeout))
    if r.status_code >= 400:
        raw = str(r.text or "")
        raise MAError(_ma_reason(raw), r.status_code,
                      "MediaNotFound" in raw or "could not be found" in raw)
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
    except (requests.HTTPError, MAError):
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


# ---------------------------------------------------------------- artwork
# The cover art lives on Spotify's / Google's image hosts, or behind Music
# Assistant's own image proxy for anything it caches locally. The panel never
# loads a picture from outside the house directly, so each one is handed to
# the browser as /api/ha/music/art?u=<url> and fetched by the backend — and
# that endpoint only fetches from these hosts (see art_allowed).
ART_HOSTS = ("scdn.co", "spotifycdn.com", "googleusercontent.com", "ytimg.com", "ggpht.com")


def _image(item: dict) -> str | None:
    """The best cover URL an item carries, in whichever shape it came."""
    cands = []
    img = item.get("image")
    if isinstance(img, str):
        cands.append((img, None))
    elif isinstance(img, dict):
        cands.append((img.get("path"), img.get("provider")))
    for im in ((item.get("metadata") or {}).get("images") or []):
        if isinstance(im, dict) and im.get("type", "thumb") == "thumb":
            cands.append((im.get("path"), im.get("provider")))
    album = item.get("album")
    if isinstance(album, dict) and album is not item:
        sub = _image({k: v for k, v in album.items() if k != "album"})
        if sub:
            return sub
    for path, prov in cands:
        if not path:
            continue
        if str(path).startswith("http"):
            return str(path)
        if _ma["base"]:                     # a file only MA can read: its proxy
            return (f"{_ma['base']}/imageproxy?path={quote(str(path), safe='')}"
                    f"&provider={quote(str(prov or ''), safe='')}&size=300")
    return None


def art_allowed(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    if any(host == h or host.endswith("." + h) for h in ART_HOSTS):
        return True
    # Music Assistant's image proxy, on the house network only.
    if u.path.rstrip("/") == "/imageproxy":
        if any(host == urlparse(b).hostname for b in MA_CANDIDATES):
            return True
        try:
            return ipaddress.ip_address(host).is_private
        except ValueError:
            return host.endswith(".local")
    return False


def fetch_art(url: str) -> tuple[bytes, str]:
    if not art_allowed(url):
        raise ValueError("Not an artwork address")
    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (family-panel)"})
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "image/jpeg")
    if not ctype.startswith("image/"):
        raise ValueError("Not an image")
    return r.content[:3_000_000], ctype


def _art_link(item: dict) -> str | None:
    url = _image(item)
    return f"/api/ha/music/art?u={quote(url, safe='')}" if url and art_allowed(url) else None


def _shape(item: dict) -> dict | None:
    uri, name = item.get("uri"), item.get("name")
    kind = item.get("media_type")
    if not uri or not name or kind not in PLAYABLE:
        return None
    artists = ", ".join(a.get("name", "") for a in (item.get("artists") or [])
                        if isinstance(a, dict) and a.get("name"))
    sub = {"artist": "Artist radio", "playlist": "Playlist", "album": "Album", "track": "Song"}[kind]
    if kind in ("album", "track") and artists:
        sub = artists
    key = _provider_key(item)
    if not key:
        doms = {m.get("provider_domain") for m in item.get("provider_mappings") or []
                if isinstance(m, dict)}
        key = next((d for d in ("spotify", "ytmusic") if d in doms), None)
    return {"uri": uri, "name": name, "media_type": kind, "sub": sub,
            "source": key, "provider": PROVIDERS.get(key), "image": _art_link(item),
            "spotify": spotify_link(item)}


# ------------------------------------------------- Sonos plays Spotify itself
# The Sonos already has Spotify linked (in the Sonos app), and Home Assistant
# can hand a Sonos speaker a Spotify share link to play directly — no Music
# Assistant in the path at all, which is the part that kept failing. Songs,
# albums and playlists can go that way; artists (no share-link playback),
# YouTube Music, and anything the Sonos turns down go through MA as before.
SONOS_SPOTIFY_KINDS = ("track", "album", "playlist")
_SPOTIFY_URI = re.compile(r"^spotify[^:/]*://(track|album|playlist|artist)/([A-Za-z0-9]{10,40})$")


def spotify_link(item: dict) -> str | None:
    kind = item.get("media_type")
    if kind not in SONOS_SPOTIFY_KINDS:
        return None
    m = _SPOTIFY_URI.match(str(item.get("uri") or ""))
    sid = m.group(2) if m else None
    if not sid:
        for pm in item.get("provider_mappings") or []:
            if isinstance(pm, dict) and str(pm.get("provider_domain") or "").startswith("spotify"):
                raw = str(pm.get("item_id") or "").split(":")[-1]
                if re.fullmatch(r"[A-Za-z0-9]{10,40}", raw):
                    sid = raw
                    break
    return f"https://open.spotify.com/{kind}/{sid}" if sid else None


def _ha_call(domain: str, service: str, body: dict, timeout: float = 30) -> None:
    base, token = ha._api_base()
    r = requests.post(f"{base}/services/{domain}/{service}", headers=ha._headers(token),
                      json=body, timeout=(5, timeout))
    r.raise_for_status()


def _same(a, b) -> bool:
    norm = lambda x: re.sub(r"[^a-z0-9]", "", str(x or "").lower())    # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def spotify_via_search(config: dict, name: str, media_type: str,
                       artist: str | None = None) -> tuple[str | None, str | None]:
    """(link, kind) — find the Spotify copy of a library item by searching
    through Home Assistant (needs no Music Assistant token). Search results
    carry real Spotify ids where library items only have library://… ones.
    An artist becomes Spotify's own "This Is <artist>" playlist, since a
    Sonos can't play an artist link."""
    if not name:
        return None, None
    _, entry = player_map(config)
    if not entry:
        return None, None
    if media_type == "artist":
        kind, query, want = "playlist", f"This Is {name}", f"This Is {name}"
    elif media_type in SONOS_SPOTIFY_KINDS:
        kind, query, want = media_type, name, name
    else:
        return None, None
    body = {"config_entry_id": entry, "name": query, "media_type": [kind], "limit": 15,
            "library_only": False}
    if artist and kind in ("album", "track"):
        body["artist"] = artist
    try:
        res = _service("search", body, True) or {}
    except Exception as e:                  # noqa: BLE001
        log.info("spotify search %r: %s", query, e)
        return None, None
    found = [i for i in (res.get(kind + "s") or []) if isinstance(i, dict)]
    links = [(i, spotify_link({**i, "media_type": kind})) for i in found]
    links = [(i, l) for i, l in links if l]
    exact = [l for i, l in links if _same(i.get("name"), want)]
    if exact:
        return exact[0], kind
    # an album or song by a near-enough name will do; an artist needs the real "This Is"
    return (links[0][1], kind) if links and media_type != "artist" else (None, None)


def play_on_sonos(entity: str, link: str, media_type: str) -> bool:
    """Hand the Sonos a Spotify link; True if it really started."""
    before = _ha_state(entity)
    if media_type in ("album", "playlist"):
        try:
            _ha_call("media_player", "shuffle_set", {"entity_id": entity, "shuffle": True})
        except Exception as e:              # noqa: BLE001
            log.info("sonos shuffle %s: %s", entity, e)
    try:
        _ha_call("media_player", "play_media", {
            "entity_id": entity, "media_content_id": link,
            "media_content_type": "playlist" if media_type != "track" else "music",
            "enqueue": "replace"}, timeout=45)
    except Exception as e:                  # noqa: BLE001
        log.info("sonos spotify %s on %s: %s", link, entity, e)
        return False
    return _started(entity, before, wait=15)


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

    def one(kind):
        try:
            return (_account_picks(config, kind, n, source) if direct
                    else _picks(entry, kind, n, source))
        except Exception as e:              # noqa: BLE001 - one kind failing is fine
            log.info("discover %s from %s: %s", kind, source, e)
            return []
    # the three kinds at once — one after another was most of the wait
    with ThreadPoolExecutor(max_workers=len(KINDS)) as pool:
        items = [it for chunk in pool.map(one, KINDS) for it in chunk]
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


def _ha_state(entity: str) -> tuple[str | None, dict]:
    try:
        base, token = ha._api_base()
        r = requests.get(f"{base}/states/{entity}", headers=ha._headers(token), timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json() or {}
        return d.get("state"), d.get("attributes") or {}
    except Exception:                       # noqa: BLE001
        return None, {}


START_WAIT_S = 25
POLL_S = 2.5


def _started(target: str, before: tuple, wait: float = START_WAIT_S) -> bool:
    """Did the speaker actually start? Home Assistant answers play_media as
    soon as Music Assistant takes the order, playing or not — so watch the
    player: playing, and a different track (or it wasn't playing before)."""
    was_state, was_attrs = before
    was_title = was_attrs.get("media_title")
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(POLL_S)
        st, attrs = _ha_state(target)
        if st == "playing" and (was_state != "playing" or attrs.get("media_title") != was_title):
            return True
    return False


def _play_direct(config: dict, entity: str, uri: str, media_type: str, name: str | None) -> bool:
    """Straight to Music Assistant. True = playing; False = its API isn't
    reachable (the caller falls back to Home Assistant); raises with MA's
    own reason if MA turned it down or nothing started."""
    try:
        qid = ma_queue_for(config, entity)
    except Exception as e:                  # noqa: BLE001 - API not reachable
        log.info("MA direct unavailable: %s", e)
        return False
    if not qid:
        _note(entity, uri, media_type, "Music Assistant direct", "no MA player matched this speaker")
        return False
    before = _ha_state(entity)
    if media_type in ("playlist", "album", "artist"):
        try:
            ma_api(config, "player_queues/shuffle", {"queue_id": qid, "shuffle_enabled": True})
        except Exception as e:              # noqa: BLE001
            log.info("MA shuffle %s: %s", qid, e)
    tries = [uri] + ([name] if name and name != uri else [])
    last = None
    for media in tries:
        try:
            ma_api(config, "player_queues/play_media",
                   {"queue_id": qid, "media": media, "option": "replace", "radio_mode": False})
        except MAUnavailable as e:
            # locked (needs a token) or gone: not a refusal — let the caller
            # try through Home Assistant instead
            _note(entity, media, media_type, "Music Assistant direct", f"unreachable: {e}")
            return False
        except (ValueError, requests.HTTPError) as e:
            last = str(e)
            _note(entity, media, media_type, "Music Assistant direct", f"refused: {last}")
            continue
        if _started(entity, before):
            _note(entity, media, media_type, "Music Assistant direct", "playing")
            return True
        last = "accepted, but the speaker never started"
        _note(entity, media, media_type, "Music Assistant direct", last)
    raise MAError(_ma_reason(last or "") if last and "not" in last.lower() else
                  f"Music Assistant: {last or 'it did not play'}",
                  not_found="found" in (last or "").lower())


def _play_media(target: str, uri: str, media_type: str, radio: bool) -> None:
    _service("play_media", {"entity_id": target, "media_id": uri, "media_type": media_type,
                            "enqueue": "replace", "radio_mode": radio}, timeout=PLAY_TIMEOUT)


def play(config: dict, entity: str, uri: str, media_type: str, spotify: str | None = None,
         name: str | None = None, source: str | None = None, artist: str | None = None) -> None:
    """Play a library item on the speaker (via its MA twin), and make sure
    it really started.

    Artists, playlists and albums play their own tracks, shuffled. (Artists
    used to start "radio mode", but that leans on Spotify's recommendations,
    which Spotify has shut off for most apps — the queue came up empty and
    nothing played, silently.) A song still tries radio, so it carries on
    after; if nothing has started 20-odd seconds later, it's played plain."""
    # A Spotify item without a Spotify link (it came through Home Assistant,
    # as library://…): look the link up by name first.
    sp_kind = media_type
    if not spotify and source and source.startswith("spotify") and name:
        spotify, found_kind = spotify_via_search(config, name, media_type, artist)
        if spotify:
            sp_kind = found_kind
            _note(entity, spotify, media_type, "Spotify link found by search", "ok")
        else:
            _note(entity, uri, media_type, "Spotify link found by search", "no match")
    if spotify and sp_kind in SONOS_SPOTIFY_KINDS:
        media_type_was = media_type
        media_type = sp_kind
        if play_on_sonos(entity, spotify, media_type):
            _note(entity, spotify, media_type, "Sonos plays Spotify itself", "playing")
            return                          # the Sonos is playing it from Spotify itself
        _note(entity, spotify, media_type, "Sonos plays Spotify itself", "didn't start — trying Music Assistant")
        media_type = media_type_was
    if _play_direct(config, entity, uri, media_type, name):
        return
    players, _ = player_map(config)
    target = players.get(entity)
    if not target:
        raise ValueError("Music Assistant has no player for that speaker")
    if media_type not in PLAYABLE:
        raise ValueError("Unknown media type")
    if media_type in ("playlist", "album", "artist"):
        try:
            ha_shuffle(target)
        except Exception as e:              # noqa: BLE001
            log.info("shuffle on %s: %s", target, e)
    before = _ha_state(target)
    radio = media_type == "track"
    _note(entity, uri, media_type, "via Home Assistant", "sent")
    try:
        _play_media(target, uri, media_type, radio)
    except MAError as e:
        _note(entity, uri, media_type, "via Home Assistant", str(e))
        # "Not found" by its id: ask again by name, so Music Assistant
        # searches every service it can actually reach for it.
        # Home Assistant hands MA's MediaNotFoundError back as a bare
        # "500 Internal Server Error", so a 500 counts as "not found" too.
        if not ((e.not_found or e.status == 500) and name):
            raise
        log.info("%s not found by uri on %s; trying by name %r", uri, target, name)
        _play_media(target, name, media_type, False)
        radio = False
    if _started(target, before):
        return
    if radio:
        log.info("radio didn't start on %s; playing %s plain", target, uri)
        _play_media(target, uri, media_type, False)
        if _started(target, before):
            return
    st, _ = _ha_state(target)
    raise RuntimeError("Music Assistant took the request but the speaker never started"
                       + (f" (it's {st})" if st else "")
                       + " — Music Assistant's log at this moment will say why")


# ---------------------------------------------------------------- search
SEARCH_KINDS = ("artist", "playlist", "album", "track")


def search(config: dict, source: str, query: str, n: int = 12) -> dict:
    """Search one account (or one service) for artists, playlists, albums and
    songs. The search runs across the services Music Assistant has; results
    are kept to the account asked for."""
    q = " ".join(str(query or "").split())[:100]
    if len(q) < 2:
        raise ValueError("Type at least two letters")
    players, entry = player_map(config)
    if not (players and entry):
        raise ValueError("Music Assistant isn't set up")
    direct = bool(source) and source not in PROVIDERS
    found: dict = {}
    if direct:
        res = ma_api(config, "music/search", {"search_query": q, "media_types": list(SEARCH_KINDS),
                                               "limit": 25, "library_only": False}) or {}
        for kind in SEARCH_KINDS:
            items = res.get(kind + "s") or [] if isinstance(res, dict) else []
            found[kind] = [i for i in items if isinstance(i, dict) and _from_account(i, source)]
    else:
        res = _service("search", {"config_entry_id": entry, "name": q, "limit": 25}, True) or {}
        for kind in SEARCH_KINDS:
            found[kind] = [i for i in (res.get(kind + "s") or []) if isinstance(i, dict)]
    out = {}
    for kind in SEARCH_KINDS:
        shaped = [s for s in map(_shape, found.get(kind, [])) if s]
        if not direct and source in PROVIDERS:
            shaped = [s for s in shaped if s["source"] == source]
        out[kind + "s"] = shaped[:n]
    return {"query": q, "source": source, **out}


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
    _start(entity, pick["name"], lambda: play(config, entity, pick["uri"], pick["media_type"],
                                              pick.get("spotify"), pick["name"],
                                              pick.get("source") or source,
                                              pick.get("sub") if pick["media_type"] == "album" else None))
    return pick


# ------------------------------------------------- start, don't wait
# Music Assistant can take 10-20 s to get a YouTube Music track going (a
# fresh PO token, the stream URL, the speaker buffering), and Home
# Assistant's play_media call waits for all of it. So the panel's request
# returns as soon as the order is checked and handed over; the call runs on
# here, and its outcome is kept per speaker for /api/ha/music/play_status —
# which the panel asks if the music hasn't started after a while.
_jobs: dict = {}
_jobs_lock = threading.Lock()


def _start(entity: str, name: str, call) -> None:
    with _jobs_lock:
        seq = _jobs.get(entity, {}).get("seq", 0) + 1
        _jobs[entity] = {"seq": seq, "state": "starting", "name": name, "error": None,
                         "at": time.time()}

    def run():
        try:
            call()
            outcome = {"state": "sent", "error": None}
        except Exception as e:              # noqa: BLE001 - reported via play_status
            log.warning("music play on %s failed: %s", entity, e)
            outcome = {"state": "error", "error": str(e)[:240]}
        with _jobs_lock:
            if _jobs.get(entity, {}).get("seq") == seq:      # a newer tap wins
                _jobs[entity].update(outcome)

    threading.Thread(target=run, daemon=True, name=f"music-play-{entity}").start()


def play_status(entity: str) -> dict:
    with _jobs_lock:
        j = dict(_jobs.get(entity) or {})
    j.pop("seq", None)
    return j or {"state": None}


def start_play(config: dict, entity: str, uri: str, media_type: str, name: str = "",
               spotify: str | None = None, source: str | None = None,
               artist: str | None = None) -> None:
    """Check the order now (so a bad one still fails on the tap), play it
    in the background."""
    players, _ = player_map(config)
    if not players.get(entity):
        raise ValueError("Music Assistant has no player for that speaker")
    if media_type not in PLAYABLE:
        raise ValueError("Unknown media type")
    if not uri:
        raise ValueError("Nothing to play")
    if spotify and not re.fullmatch(r"https://open\.spotify\.com/(track|album|playlist)/[A-Za-z0-9]{10,40}", spotify):
        spotify = None
    _start(entity, name or uri, lambda: play(config, entity, uri, media_type, spotify, name or None,
                                             source or None, artist or None))
