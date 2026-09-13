"""Standings for the two things this house follows: ATP men's singles and the
F1 drivers' championship.

Both are fetched on a slow schedule — ATP publishes on Mondays, F1 only moves
after a race — and cached in the settings table, so the panel keeps drawing the
last good table while the link is down, exactly as weather does.

WHY THE MOVEMENT IS COMPUTED HERE
Neither source can be relied on for "up 2 / down 1". Ergast (and so Jolpica)
has no movement field at all, and a source that does have one can rename or
drop it. So the panel keeps the last published table and diffs against it. The
comparison point only advances when the table ACTUALLY changes, which is what
makes an arrow mean "since the last time this moved" rather than "since three
hours ago" — otherwise every arrow would fall back to zero within one sync of a
ranking being published, which is precisely when you want to see it.
First sync after a cold start shows no arrows: there is nothing to compare
against yet, and zeroes would be a claim that everybody held station.

ON THE ATP SOURCE
The ATP publish no API, and scraping atptour.com would break the first time
they touch their markup. ESPN publish the same rankings as JSON, keyless — the
numbers are the ATP's either way. That endpoint is undocumented, so the parser
below goes looking for the table rather than trusting one fixed key path, and
stashes the raw payload when it can't find it (see /api/sports/raw) so a shape
change is a five-minute fix instead of a guessing game.
"""
import json
import re

import requests

import db

TIMEOUT = 25

# Tried in order until one yields a rankings table. Both are ESPN — there is
# no other keyless ATP source worth the name — but on different hosts with
# evidently different edge protection, confirmed against a real production
# fetch (not from where this is written; every tennis host is unreachable
# here):
#   sports.core.api.espn.com  -> answered 200. First in line.
#   site.api.espn.com         -> Akamai "Access Denied", reference number and
#     all. Kept as a fallback in case that eases, but not likely to: Akamai's
#     bot filtering fingerprints the TLS handshake itself, and Python's
#     requests/urllib3 has a distinctive one no amount of User-Agent spoofing
#     touches — the header changes tried before this never had a real chance.
# (A third host, site.web.api.espn.com, 404'd outright — not "unverified",
# actually wrong — and has been dropped rather than kept as dead weight.)
#
# `atp_rankings_url` in the add-on config still takes precedence over both,
# for a source that turns out to work better than either.
ATP_URLS = [
    "https://sports.core.api.espn.com/v2/sports/tennis/leagues/atp/rankings",
    "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/rankings",
]
F1_URL = "https://api.jolpi.ca/ergast/f1/current/driverstandings/?format=json"
F1_CONS_URL = "https://api.jolpi.ca/ergast/f1/current/constructorstandings/?format=json"

TOP_N = 10


# ---------------------------------------------------------------- flags -----
# Emoji flags are two regional-indicator letters, so everything below only has
# to reach an ISO 3166-1 alpha-2 code. Android (the kiosk, and the phones)
# renders these; desktop Chrome on Windows notoriously does not and shows the
# letters instead, which is legible enough to not be worth shipping an image
# set for.
#
# Tennis quotes nationality as an IOC-style three-letter code, F1 as a demonym.
# Neither maps to ISO2 by rule — GER/DE and NED/NL and SUI/CH all disagree with
# their own first two letters — so both are tables. Anything unmapped falls
# back to showing the code itself rather than a wrong flag.

IOC_TO_ISO2 = {
    "ARG": "AR", "AUS": "AU", "AUT": "AT", "BEL": "BE", "BIH": "BA", "BLR": "BY",
    "BOL": "BO", "BRA": "BR", "BUL": "BG", "CAN": "CA", "CHI": "CL", "CHN": "CN",
    "COL": "CO", "CRO": "HR", "CYP": "CY", "CZE": "CZ", "DEN": "DK", "DOM": "DO",
    "ECU": "EC", "EGY": "EG", "ESA": "SV", "ESP": "ES", "EST": "EE", "FIN": "FI",
    "FRA": "FR", "GBR": "GB", "GEO": "GE", "GER": "DE", "GRE": "GR", "HKG": "HK",
    "HUN": "HU", "IND": "IN", "INA": "ID", "IRL": "IE", "ISR": "IL", "ITA": "IT",
    "JPN": "JP", "KAZ": "KZ", "KOR": "KR", "LAT": "LV", "LTU": "LT", "LUX": "LU",
    "MAR": "MA", "MDA": "MD", "MEX": "MX", "MON": "MC", "NED": "NL", "NOR": "NO",
    "NZL": "NZ", "PAK": "PK", "PER": "PE", "PHI": "PH", "POL": "PL", "POR": "PT",
    "PUR": "PR", "QAT": "QA", "ROU": "RO", "RSA": "ZA", "RUS": "RU", "SRB": "RS",
    "SVK": "SK", "SLO": "SI", "SUI": "CH", "SWE": "SE", "TPE": "TW", "THA": "TH",
    "TUN": "TN", "TUR": "TR", "UKR": "UA", "URU": "UY", "USA": "US", "UZB": "UZ",
    "VEN": "VE", "ZIM": "ZW",
}

DEMONYM_TO_ISO2 = {
    "american": "US", "argentine": "AR", "argentinian": "AR", "australian": "AU",
    "austrian": "AT", "belgian": "BE", "brazilian": "BR", "british": "GB",
    "canadian": "CA", "chinese": "CN", "colombian": "CO", "czech": "CZ",
    "danish": "DK", "dutch": "NL", "english": "GB", "finnish": "FI",
    "french": "FR", "german": "DE", "hungarian": "HU", "indian": "IN",
    "indonesian": "ID", "irish": "IE", "italian": "IT", "japanese": "JP",
    "mexican": "MX", "monegasque": "MC", "new zealander": "NZ", "polish": "PL",
    "portuguese": "PT", "russian": "RU", "spanish": "ES", "swedish": "SE",
    "swiss": "CH", "thai": "TH", "american-french": "US",
}


def flag_emoji(iso2: str | None) -> str:
    """ISO2 -> the two regional-indicator letters that render as that flag."""
    if not iso2 or len(iso2) != 2 or not iso2.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso2.upper())


def _iso2_from_code(code: str | None) -> str | None:
    """A three-letter IOC code, or an ISO2 that arrived already correct."""
    if not code:
        return None
    c = str(code).strip().upper()
    if len(c) == 2 and c.isalpha():
        return c
    return IOC_TO_ISO2.get(c)


def _iso2_from_nationality(text: str | None) -> str | None:
    if not text:
        return None
    return DEMONYM_TO_ISO2.get(str(text).strip().lower())


# ESPN's core API (sports.core.api.espn.com, as opposed to their "site"
# surface) gives an athlete's country as a plain name — "Italy", not
# "Italian" or "ITA" — under `citizenship`. Same top-ATP-100 coverage as
# DEMONYM_TO_ISO2 above, just keyed the other way.
COUNTRY_NAME_TO_ISO2 = {
    "united states": "US", "argentina": "AR", "australia": "AU",
    "austria": "AT", "belgium": "BE", "brazil": "BR", "great britain": "GB",
    "united kingdom": "GB", "canada": "CA", "china": "CN", "colombia": "CO",
    "czech republic": "CZ", "czechia": "CZ", "denmark": "DK",
    "netherlands": "NL", "finland": "FI", "france": "FR", "germany": "DE",
    "hungary": "HU", "india": "IN", "indonesia": "ID", "ireland": "IE",
    "italy": "IT", "japan": "JP", "mexico": "MX", "monaco": "MC",
    "new zealand": "NZ", "poland": "PL", "portugal": "PT", "russia": "RU",
    "spain": "ES", "sweden": "SE", "switzerland": "CH", "thailand": "TH",
    "serbia": "RS", "croatia": "HR", "norway": "NO", "bulgaria": "BG",
    "chile": "CL", "kazakhstan": "KZ", "south korea": "KR", "chinese taipei": "TW",
}


def _iso2_from_country_name(text: str | None) -> str | None:
    if not text:
        return None
    return COUNTRY_NAME_TO_ISO2.get(str(text).strip().lower())


# ------------------------------------------------------------- ATP ---------
# The payload is undocumented, so rather than one key path this walks the JSON
# for the rankings table and reads each row by trying the names these feeds
# actually use. Being generous here is the whole point: the alternative is a
# parser that returns nothing the day a key is renamed, on a wall panel nobody
# is watching logs for.

_RANK_KEYS = ("current", "rank", "position", "currentRank")
_PREV_KEYS = ("previous", "previousRank", "prevRank", "lastWeek")
_POINT_KEYS = ("points", "statValue", "value", "score")


def _num(v):
    """First integer in whatever this is — feeds quote numbers as often as not,
    and points arrive as "11,500" more often than 11500."""
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*", v)
        if m:
            try:
                return int(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def _pick(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _looks_like_rank_row(d) -> bool:
    return (isinstance(d, dict)
            and _pick(d, _RANK_KEYS) is not None
            and (_pick(d, _POINT_KEYS) is not None or "athlete" in d))


def _find_rank_rows(node, depth=0):
    """Depth-first hunt for the longest list of rank-shaped dicts."""
    if depth > 8:
        return []
    best = []
    if isinstance(node, list):
        rows = [x for x in node if _looks_like_rank_row(x)]
        if len(rows) > len(best):
            best = rows
        for item in node:
            found = _find_rank_rows(item, depth + 1)
            if len(found) > len(best):
                best = found
    elif isinstance(node, dict):
        for value in node.values():
            found = _find_rank_rows(value, depth + 1)
            if len(found) > len(best):
                best = found
    return best


def _athlete_bits(row: dict):
    """(name, country code) out of whichever nest the athlete is in."""
    ath = row.get("athlete") if isinstance(row.get("athlete"), dict) else row
    name = (_pick(ath, ("displayName", "fullName", "shortName", "name"))
            or _pick(row, ("displayName", "name")))
    code = None
    flag = ath.get("flag")
    if isinstance(flag, dict):
        code = _pick(flag, ("abbreviation", "alt", "countryCode"))
        href = flag.get("href")
        if not code and isinstance(href, str):
            # .../countries/500/aus.png
            m = re.search(r"/([a-zA-Z]{2,3})\.(?:png|svg)", href)
            if m:
                code = m.group(1)
    if not code:
        for key in ("countryCode", "country", "nationality", "abbreviation",
                    "citizenship"):
            v = ath.get(key) or row.get(key)
            if isinstance(v, dict):
                v = _pick(v, ("abbreviation", "name", "alt"))
            if v:
                code = v
                break
    return (str(name).strip() if name else None), (str(code).strip() if code else None)


# ---------------------------------------------------- ESPN's linked data ---
# ESPN's "core" API (sports.core.api.espn.com) is a different animal from
# their "site" API (site.api.espn.com): where "site" embeds everything, "core"
# is HATEOAS-style — a query answers with {"$ref": url} pointers rather than
# the resource itself, all the way down to each individual athlete. Confirmed
# against a real response: a rankings query returned exactly one item, a
# pointer to that week's actual rankings resource, not a list of ranks.
#
# This can't be verified end to end from where it's written — the host is
# unreachable here just like every other tennis source — so it's built to
# fail soft and say exactly what it saw: a ref that won't resolve is left in
# place rather than losing the whole table, and every hop that mattered goes
# into /api/sports/raw.

REF_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}


def _deref(url: str, timeout: int) -> dict:
    r = requests.get(url, timeout=timeout, headers=REF_HEADERS)
    r.raise_for_status()
    return r.json()


def _is_ref(node) -> bool:
    return isinstance(node, dict) and isinstance(node.get("$ref"), str)


def _unwrap_espn_collection(payload, timeout: int):
    """If this is a paginated list of {$ref} pointers rather than the
    resource itself, follow the first one — a current-rankings query has
    exactly one live list to report, never several to choose between — and
    return what THAT points at. Anything else (a payload that already embeds
    real data, whether from ESPN's "site" surface or a completely different
    source someone's pointed atp_rankings_url at) passes through unchanged,
    so this is a no-op everywhere except the one shape it exists for."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(items, list) and items and _is_ref(items[0]):
        return _deref(items[0]["$ref"], timeout)
    return payload


def _resolve_athlete_refs(rows: list[dict], timeout: int, limit: int) -> list[dict]:
    """Follow each row's `athlete` link if it's a bare pointer rather than
    embedded data — core-API rankings link out to the athlete instead of
    including their name, exactly the same shape the collection above needs
    unwrapping for. Bounded to `limit` rows, since the panel only keeps the
    top TOP_N regardless of how many the source hands back, and best-effort:
    a row whose athlete link 404s keeps whatever it already had (nothing)
    rather than losing the other nine over one bad link."""
    for row in rows[:limit]:
        ath = row.get("athlete")
        if _is_ref(ath):
            try:
                row["athlete"] = _deref(ath["$ref"], timeout)
            except Exception:
                pass
    return rows


def _build_atp_rows(raw_rows: list[dict]) -> list[dict]:
    """Our row shape from already-located rank dicts (see _find_rank_rows) —
    split out from fetch_atp so athlete refs can be resolved in between
    finding the rows and reading names out of them."""
    out = []
    for row in raw_rows:
        pos = _num(_pick(row, _RANK_KEYS))
        name, code = _athlete_bits(row)
        if pos is None or not name:
            continue
        # `code` might be a 3-letter IOC code, an ISO2 already, OR a full
        # country name (ESPN's core API's `citizenship` gives "Italy", not
        # "ITA") — try the code table first since it's the common case, the
        # name table second.
        iso2 = _iso2_from_code(code) or _iso2_from_country_name(code)
        out.append({
            "id": str(_pick(row, ("id", "athleteId")) or name),
            "pos": pos,
            "name": name,
            "points": _num(_pick(row, _POINT_KEYS)),
            "code": (code or "").upper()[:3] if code and len(code) <= 3 else None,
            "flag": flag_emoji(iso2),
            "team": None,
            # The source's own previous rank is better than our diff when it is
            # there — it is the real week-on-week number rather than whenever
            # this panel last looked.
            "src_prev": _num(_pick(row, _PREV_KEYS)),
        })
    out.sort(key=lambda r: r["pos"])
    return out[:TOP_N]


def _atp_rows_from(payload) -> list[dict]:
    """Convenience wrapper for tests and any source with no refs to resolve."""
    return _build_atp_rows(_find_rank_rows(payload))


def fetch_atp(cfg: dict | None = None) -> list[dict]:
    """Try each candidate ATP endpoint until one gives up a rankings table.

    Why a list rather than one URL. ESPN answered the panel's first request
    with a flat 403 — from the house's own address, not this sandbox's — and a
    browser-shaped User-Agent didn't shift it. Every live tennis source is
    unreachable from where this code gets written (ESPN, SofaScore, atptour,
    tennisabstract all refuse the connection outright), so no amount of care
    here can confirm a fix before it ships. Guessing one URL at a time and
    waiting a release to find out is the wrong shape for that; trying several
    and recording precisely what each one said is the right one.

    `atp_rankings_url` in the add-on config jumps the queue, so a source that
    does work can be pointed at without waiting for a release at all. The
    parser hunts for the rankings table rather than following a fixed key
    path (see _find_rank_rows), so a swapped-in URL has a fair chance of just
    working, whatever its shape.

    A candidate that answers 200 still isn't necessarily the data: ESPN's core
    API answers a rankings query with a link to the actual resource, and each
    rank row with a link to the actual athlete, rather than embedding either —
    see _unwrap_espn_collection/_resolve_athlete_refs. Confirmed against a
    real response, not guessed.

    Every attempt — the winner and each failure, with status and a snippet —
    is kept for /api/sports/raw. On a headless box that endpoint is the only
    way anyone finds out WHY, and "403 from this host, 404 from that one" is
    the difference between a five-minute fix and another blind round.
    """
    attempts = []
    override = (cfg or {}).get("atp_rankings_url")
    urls = ([override] if override else []) + ATP_URLS

    for url in urls:
        try:
            r = requests.get(url, timeout=TIMEOUT, headers=REF_HEADERS | {
                "Referer": "https://www.espn.com/tennis/rankings/_/type/atp",
            })
            if r.status_code != 200:
                attempts.append({"url": url, "result": f"HTTP {r.status_code}",
                                 "body": r.text[:400]})
                continue

            payload = _unwrap_espn_collection(r.json(), TIMEOUT)
            raw_rows = _find_rank_rows(payload)
            if not raw_rows:
                attempts.append({"url": url, "result": "200, but no rankings "
                                 "table found in the payload",
                                 "body": json.dumps(payload)[:800]})
                continue

            raw_rows = _resolve_athlete_refs(raw_rows, TIMEOUT, TOP_N + 3)
            rows = _build_atp_rows(raw_rows)
            if not rows:
                # The table was found but nothing in it had both a position
                # and a readable name — most likely every athlete ref failed
                # to resolve. Keep one resolved (or unresolved) row on hand so
                # the real field names are visible, not just "it was empty".
                attempts.append({"url": url, "result": "found a rankings table "
                                 "but couldn't read any athlete out of it",
                                 "body": json.dumps(raw_rows[0])[:800] if raw_rows else None})
                continue

            db.set_json_setting("sports:atp_raw",
                                {"at": db.utc_now_iso(), "winner": url,
                                 "tried": attempts,
                                 "sample_row": raw_rows[0] if raw_rows else None})
            return rows
        except Exception as e:                        # noqa: BLE001 - recorded
            attempts.append({"url": url, "result": f"{type(e).__name__}: {e}"[:200],
                             "body": None})

    db.set_json_setting("sports:atp_raw", {"at": db.utc_now_iso(),
                                           "winner": None, "tried": attempts})
    first = attempts[0]["result"] if attempts else "no candidates"
    raise ValueError(f"no ATP source answered ({len(attempts)} tried, first: "
                     f"{first}) — see /api/sports/raw")


# -------------------------------------------------------------- F1 ---------
# Ergast's shape, which Jolpica keeps as a drop-in: MRData.StandingsTable
# .StandingsLists[0].DriverStandings[]. Stable for fifteen years.

def fetch_f1() -> list[dict]:
    r = requests.get(F1_URL, timeout=TIMEOUT,
                     headers={"User-Agent": "family-panel/1.0"})
    r.raise_for_status()
    lists = (r.json().get("MRData", {}).get("StandingsTable", {})
             .get("StandingsLists") or [])
    if not lists:
        # An empty list is normal in the off-season, not a failure — there is
        # simply no championship in progress to report.
        return []
    out = []
    for row in lists[0].get("DriverStandings", []):
        drv = row.get("Driver", {})
        cons = row.get("Constructors") or [{}]
        name = " ".join(x for x in (drv.get("givenName"), drv.get("familyName")) if x)
        pos = _num(row.get("position"))
        if pos is None or not name:
            continue
        iso2 = _iso2_from_nationality(drv.get("nationality"))
        out.append({
            "id": drv.get("driverId") or name,
            "pos": pos,
            "name": name,
            "points": _num(row.get("points")),
            "code": (drv.get("code") or "").upper() or None,
            "flag": flag_emoji(iso2),
            "team": cons[0].get("name"),
            "src_prev": None,
        })
    out.sort(key=lambda r: r["pos"])
    return out[:TOP_N]


# The constructors' championship — same shape as the drivers' so the sheet can
# draw both with one function, just fetched from Ergast/Jolpica's other
# standings list. There are only ever ~10 teams on the grid, so "top 10" here
# means the whole championship, not a cut.

def fetch_f1_constructors() -> list[dict]:
    r = requests.get(F1_CONS_URL, timeout=TIMEOUT,
                     headers={"User-Agent": "family-panel/1.0"})
    r.raise_for_status()
    lists = (r.json().get("MRData", {}).get("StandingsTable", {})
             .get("StandingsLists") or [])
    if not lists:
        return []
    out = []
    for row in lists[0].get("ConstructorStandings", []):
        cons = row.get("Constructor", {})
        name = cons.get("name")
        pos = _num(row.get("position"))
        if pos is None or not name:
            continue
        iso2 = _iso2_from_nationality(cons.get("nationality"))
        out.append({
            "id": cons.get("constructorId") or name,
            "pos": pos,
            "name": name,
            "points": _num(row.get("points")),
            "code": None,
            "flag": flag_emoji(iso2),
            "team": None,
            "src_prev": None,
        })
    out.sort(key=lambda r: r["pos"])
    return out[:TOP_N]


# --------------------------------------------------------- movement --------

def _apply_movement(key: str, rows: list[dict]) -> list[dict]:
    """Fill in `move`: +n rose n places, -n fell, 0 held, None not known yet.

    A source that publishes its own previous rank wins outright — that is the
    real week-on-week number, where a diff can only ever mean "since this panel
    last looked". Only sports with no such field (F1) fall back to diffing, and
    then the stored comparison point advances ONLY when the table actually
    changes, so an arrow goes on meaning "since this last moved" all week
    instead of resetting to zero on the next sync.
    """
    if any(r.get("src_prev") for r in rows):
        for r in rows:
            sp = r.get("src_prev")
            r["move"] = (sp - r["pos"]) if sp else None
        return rows

    setting = f"sports:prev:{key}"
    prev = db.get_json_setting(setting) or {}
    prev_pos = prev.get("pos") or {}
    prev_move = prev.get("move") or {}
    cur_pos = {r["id"]: r["pos"] for r in rows}

    if prev_pos and cur_pos == prev_pos:        # nothing has moved since
        for r in rows:
            r["move"] = prev_move.get(r["id"])
        return rows

    for r in rows:
        was = prev_pos.get(r["id"])             # empty on a cold start -> None
        r["move"] = (was - r["pos"]) if was is not None else None
    db.set_json_setting(setting, {"pos": cur_pos,
                                  "move": {r["id"]: r.get("move") for r in rows}})
    return rows


# ---------------------------------------------------------- refresh --------

def _fetch_one(key: str, fetch, prev_block: dict | None) -> dict:
    """One source's block of the cached payload: fresh rows plus movement, or
    whatever was there before plus why it wasn't replaced. Isolated per source
    so the WDC table going stale never touches the ATP or CWC ones."""
    try:
        rows = _apply_movement(key, fetch())
        return {"rows": rows, "error": None, "updated_at": db.utc_now_iso()}
    except Exception as e:                            # noqa: BLE001 - reported
        kept = (prev_block or {}).get("rows") or []
        return {"rows": kept, "error": f"{key}: {e}"[:200],
                "updated_at": (prev_block or {}).get("updated_at")}


def refresh(cfg: dict | None = None) -> dict:
    """Fetch all three tables. Each fails independently and keeps its own last
    known rows — a dead ATP fetch must not cost F1 its table, and a dead
    constructors fetch must not cost the drivers theirs."""
    prev = latest() or {}
    prev_f1 = prev.get("f1") or {}
    out = {"updated_at": db.utc_now_iso()}

    out["atp"] = _fetch_one("atp", lambda: fetch_atp(cfg), prev.get("atp"))
    out["f1"] = {
        "drivers": _fetch_one("f1", fetch_f1, prev_f1.get("drivers")),
        "constructors": _fetch_one("f1_cons", fetch_f1_constructors,
                                   prev_f1.get("constructors")),
    }

    db.set_json_setting("sports:latest", out)
    errors = [b["error"] for b in
             (out["atp"], out["f1"]["drivers"], out["f1"]["constructors"])
             if b["error"]]
    if errors:
        raise RuntimeError("; ".join(errors))
    return out


def latest() -> dict | None:
    return db.get_json_setting("sports:latest")


def raw_debug() -> dict | None:
    """Whatever the ATP fetch couldn't parse, for working out the shape."""
    return db.get_json_setting("sports:atp_raw")
