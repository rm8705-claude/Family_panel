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
import re

import requests

import db

TIMEOUT = 25

ATP_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/rankings"
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
        for key in ("countryCode", "country", "nationality", "abbreviation"):
            v = ath.get(key) or row.get(key)
            if isinstance(v, dict):
                v = _pick(v, ("abbreviation", "name", "alt"))
            if v:
                code = v
                break
    return (str(name).strip() if name else None), (str(code).strip() if code else None)


def fetch_atp() -> list[dict]:
    r = requests.get(ATP_URL, timeout=TIMEOUT,
                     headers={"User-Agent": "family-panel/1.0"})
    r.raise_for_status()
    raw = r.json()
    rows = _find_rank_rows(raw)
    if not rows:
        # Keep enough of it to see the shape, then say so loudly.
        db.set_json_setting("sports:atp_raw", {"at": db.utc_now_iso(),
                                               "body": r.text[:4000]})
        raise ValueError("no rankings table found in the ATP payload "
                         "— see /api/sports/raw")
    db.set_json_setting("sports:atp_raw", None)

    out = []
    for row in rows:
        pos = _num(_pick(row, _RANK_KEYS))
        name, code = _athlete_bits(row)
        if pos is None or not name:
            continue
        iso2 = _iso2_from_code(code)
        out.append({
            "id": str(_pick(row, ("id", "athleteId")) or name),
            "pos": pos,
            "name": name,
            "points": _num(_pick(row, _POINT_KEYS)),
            "code": (code or "").upper()[:3] or None,
            "flag": flag_emoji(iso2),
            "team": None,
            # The source's own previous rank is better than our diff when it is
            # there — it is the real week-on-week number rather than whenever
            # this panel last looked.
            "src_prev": _num(_pick(row, _PREV_KEYS)),
        })
    out.sort(key=lambda r: r["pos"])
    return out[:TOP_N]


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

    out["atp"] = _fetch_one("atp", fetch_atp, prev.get("atp"))
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
