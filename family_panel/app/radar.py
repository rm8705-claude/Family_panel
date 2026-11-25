"""BOM rain radar for the screensaver, only while there's rain about.

The Bureau publishes every radar's images on its public FTP server:
  /anon/gen/radar/IDRxx3.T.<YYYYMMDDHHMM>.png      a rain frame every 5-10 min (UTC)
  /anon/gen/radar_transparencies/IDRxx3.<layer>.png  background, topography,
                                                     locations, range
The "…3" products are the 128 km views: 512 x 512 px, the radar at the
centre, so one pixel is half a kilometre. A frame is transparent wherever
it isn't raining.

refresh() picks the radar nearest home (bom_radar overrides it, e.g.
"IDR663"), keeps the last hour's frames on disk next to the database, and
decides whether rain is "about": any rain in the latest frame within
RAIN_NEAR_KM of the house, or Open-Meteo saying it's raining now. Only then
does the panel show the loop — a radar of empty sky on the wall all summer
would just be a picture of Brisbane.
"""
import ftplib
import io
import logging
import math
import os
import re
from datetime import datetime

import db

log = logging.getLogger("family-panel.radar")

FTP_HOST = "ftp.bom.gov.au"
FRAMES_DIR = "/anon/gen/radar"
LAYERS_DIR = "/anon/gen/radar_transparencies"
LAYERS = ("background", "topography", "locations", "range")
FRAMES = 7                       # about an hour at the usual 10-minute beat
SPAN_KM = 256.0                  # a "…3" product is 128 km each way
RAIN_NEAR_KM = 25.0
CACHE_KEY = "radar:latest"
NAME_RE = re.compile(r"^IDR\d{2,3}\.(T\.\d{12}|background|topography|locations|range)\.png$")

# Queensland's radars (128 km products) and where each one stands — enough to
# pick the nearest; bom_radar names any other.
RADARS = {
    "IDR663": ("Brisbane (Mt Stapylton)", -27.718, 153.240),
    "IDR503": ("Brisbane (Marburg)", -27.608, 152.539),
    "IDR083": ("Gympie (Mt Kanigan)", -25.957, 152.577),
    "IDR233": ("Gladstone", -23.855, 151.262),
    "IDR723": ("Emerald", -23.549, 148.239),
    "IDR223": ("Mackay", -21.117, 149.217),
    "IDR243": ("Bowen", -19.886, 148.075),
    "IDR733": ("Townsville (Hervey Range)", -19.420, 146.551),
    "IDR193": ("Cairns", -16.817, 145.683),
}

# Open-Meteo / WMO codes that mean it's actually wet right now
WET_CODES = set(range(51, 68)) | set(range(80, 83)) | {95, 96, 99}


def store_dir() -> str:
    d = os.path.join(os.path.dirname(db.DB_PATH) or ".", "radar")
    os.makedirs(d, exist_ok=True)
    return d


def km_between(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def pick(config: dict, home: dict) -> tuple[str, str, float, float]:
    own = str(config.get("bom_radar") or "").strip().upper()
    if own in RADARS:
        n, la, lo = RADARS[own]
        return own, n, la, lo
    if own:                              # a radar outside the table: centre unknown
        return own, own, home["lat"], home["lon"]
    best = min(RADARS.items(), key=lambda kv: km_between(home["lat"], home["lon"], kv[1][1], kv[1][2]))
    return best[0], best[1][0], best[1][1], best[1][2]


def home_px(home: dict, rlat: float, rlon: float) -> tuple[float, float]:
    """Where the house sits on the 512 px image, as fractions 0-1 (x right, y down)."""
    dx = (home["lon"] - rlon) * 111.32 * math.cos(rlat * math.pi / 180)
    dy = (home["lat"] - rlat) * 110.57
    return 0.5 + dx / SPAN_KM, 0.5 - dy / SPAN_KM


def rain_near(png: bytes, fx: float, fy: float, km: float = RAIN_NEAR_KM) -> bool:
    """Any rain-coloured pixel within `km` of the house on this frame?"""
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = im.size
    cx, cy = fx * w, fy * h
    r = km / SPAN_KM * w
    px = im.load()
    for y in range(max(0, int(cy - r)), min(h, int(cy + r) + 1)):
        for x in range(max(0, int(cx - r)), min(w, int(cx + r) + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r and px[x, y][3] > 0:
                return True
    return False


def _fetch(ftp, path: str, dest: str) -> bytes:
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {path}", buf.write)
    data = buf.getvalue()
    with open(dest, "wb") as f:
        f.write(data)
    return data


def refresh(config: dict, home: dict, weather_now_code=None) -> dict:
    product, name, rlat, rlon = pick(config, home)
    d = store_dir()
    ftp = ftplib.FTP(FTP_HOST, timeout=30)
    try:
        ftp.login()
        names = sorted(n.rsplit("/", 1)[-1] for n in ftp.nlst(FRAMES_DIR)
                       if n.rsplit("/", 1)[-1].startswith(product + ".T."))
        keep = names[-FRAMES:]
        latest_png = None
        for n in keep:
            dest = os.path.join(d, n)
            if os.path.exists(dest):
                if n == keep[-1]:
                    with open(dest, "rb") as f:
                        latest_png = f.read()
                continue
            data = _fetch(ftp, f"{FRAMES_DIR}/{n}", dest)
            if n == keep[-1]:
                latest_png = data
        for layer in LAYERS:
            ln = f"{product}.{layer}.png"
            dest = os.path.join(d, ln)
            if not os.path.exists(dest) or (datetime.now().timestamp() - os.path.getmtime(dest)) > 7 * 86400:
                try:
                    _fetch(ftp, f"{LAYERS_DIR}/{ln}", dest)
                except ftplib.all_errors as e:
                    log.info("radar layer %s: %s", ln, e)
    finally:
        try:
            ftp.quit()
        except Exception:                # noqa: BLE001
            pass
    # tidy old frames
    for n in os.listdir(d):
        if ".T." in n and n not in keep:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
    fx, fy = home_px(home, rlat, rlon)
    near = False
    if latest_png:
        try:
            near = rain_near(latest_png, fx, fy)
        except Exception as e:           # noqa: BLE001
            log.info("radar pixel check: %s", e)
    wet_now = weather_now_code is not None and int(weather_now_code) in WET_CODES
    frames = []
    for n in keep:
        stamp = n.split(".T.")[1][:12]
        try:
            t = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=db.UTC)
            frames.append({"name": n, "at": db.to_utc_iso(t)})
        except ValueError:
            continue
    out = {"at": db.utc_now_iso(), "product": product, "radar": name,
           "show": bool(frames) and (near or wet_now), "rain_near": near, "wet_now": wet_now,
           "home": {"x": round(fx, 4), "y": round(fy, 4)},
           "frames": frames,
           "layers": [f"{product}.{layer}.png" for layer in LAYERS
                      if os.path.exists(os.path.join(d, f"{product}.{layer}.png"))]}
    db.set_json_setting(CACHE_KEY, out)
    return out


def latest() -> dict | None:
    return db.get_json_setting(CACHE_KEY)


def image_path(name: str) -> str | None:
    if not NAME_RE.match(name or ""):
        return None
    p = os.path.join(store_dir(), name)
    return p if os.path.exists(p) else None
