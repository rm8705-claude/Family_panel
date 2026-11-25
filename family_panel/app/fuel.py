"""Cheapest fuel near home, from Queensland's Fuel Price Reporting scheme.

Every Queensland servo has to report its prices within 30 minutes of a
change, and the State publishes them through a free API for registered
"data consumers" (fuelpricesqld.com.au — register, and the subscriber token
it issues goes in the add-on option qld_fuel_token).

refresh() runs on the scheduler every 30 minutes: the site list (names,
addresses, coordinates — refetched daily), brands, fuel types and current
prices; it keeps the servos within fuel_radius_km of home (8 by default),
for the fuel in fuel_type ("Unleaded" by default — or "E10", "Premium
Unleaded 95", "Premium Unleaded 98", "Diesel", …), cheapest first, and
caches the answer for /api/fuel.

Prices come back in tenths of a cent (1859 = 185.9 c/L); 9999 means the
servo doesn't currently sell that fuel.
"""
import logging
import math
import time

import requests

import db

log = logging.getLogger("family-panel.fuel")

API = "https://fppdirectapi-prod.fuelpricesqld.com.au"
QLD = {"countryId": 21, "geoRegionLevel": 3, "geoRegionId": 1}
TIMEOUT = 30
CACHE_KEY = "fuel:latest"
SITES_KEY = "fuel:sites"
SITES_MAX_AGE_S = 24 * 3600


class NotConfigured(Exception):
    pass


def _token(config: dict) -> str:
    return str(config.get("qld_fuel_token") or "").strip()


def _get(config: dict, path: str, params: dict) -> dict:
    tok = _token(config)
    if not tok:
        raise NotConfigured("No qld_fuel_token in the add-on options")
    r = requests.get(f"{API}{path}", params=params, timeout=TIMEOUT, headers={
        "Authorization": f"FPDAPI SubscriberToken={tok}", "Content-Type": "application/json"})
    if r.status_code in (401, 403):
        raise RuntimeError("The fuel price API refused the token — check qld_fuel_token")
    r.raise_for_status()
    return r.json() or {}


def km_between(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _sites(config: dict) -> dict:
    """{site_id: {name, address, postcode, brand_id, lat, lon}} — refetched daily."""
    cached = db.get_json_setting(SITES_KEY) or {}
    if cached.get("sites") and time.time() - float(cached.get("at") or 0) < SITES_MAX_AGE_S:
        return cached["sites"]
    raw = _get(config, "/Subscriber/GetFullSiteDetails", QLD).get("S") or []
    sites = {}
    for s in raw:
        try:
            sites[str(s["S"])] = {"name": s.get("N") or "", "address": s.get("A") or "",
                                  "postcode": s.get("P") or "", "brand_id": s.get("B"),
                                  "lat": float(s["Lat"]), "lon": float(s["Lng"])}
        except (KeyError, TypeError, ValueError):
            continue
    db.set_json_setting(SITES_KEY, {"at": time.time(), "sites": sites})
    return sites


def _fuel_id(config: dict, wanted: str) -> tuple[int, str]:
    fuels = _get(config, "/Subscriber/GetCountryFuelTypes", {"countryId": 21}).get("Fuels") or []
    w = (wanted or "Unleaded").strip().lower()
    exact = [f for f in fuels if str(f.get("Name", "")).strip().lower() == w]
    loose = [f for f in fuels if w in str(f.get("Name", "")).lower()]
    hit = (exact or loose or [None])[0]
    if not hit:
        names = ", ".join(str(f.get("Name")) for f in fuels)
        raise ValueError(f"No fuel called “{wanted}” — the API has: {names}")
    return int(hit["FuelId"]), str(hit["Name"])


def refresh(config: dict, home: dict) -> dict:
    radius = float(config.get("fuel_radius_km") or 8)
    fuel_id, fuel_name = _fuel_id(config, str(config.get("fuel_type") or "Unleaded"))
    sites = _sites(config)
    brands = {b.get("BrandId"): b.get("Name") for b in
              (_get(config, "/Subscriber/GetCountryBrands", {"countryId": 21}).get("Brands") or [])}
    prices = _get(config, "/Price/GetSitesPrices", QLD).get("SitePrices") or []
    near = []
    for p in prices:
        if p.get("FuelId") != fuel_id:
            continue
        try:
            price = float(p.get("Price"))
        except (TypeError, ValueError):
            continue
        if price <= 0 or price >= 9000:          # 9999 = not selling it
            continue
        site = sites.get(str(p.get("SiteId")))
        if not site:
            continue
        km = km_between(home["lat"], home["lon"], site["lat"], site["lon"])
        if km > radius:
            continue
        near.append({"name": site["name"], "brand": brands.get(site["brand_id"]) or "",
                     "address": site["address"], "postcode": site["postcode"],
                     "lat": site["lat"], "lon": site["lon"],
                     "price": round(price / 10, 1), "km": round(km, 1),
                     "updated": p.get("TransactionDateUtc")})
    near.sort(key=lambda s: (s["price"], s["km"]))
    avg = round(sum(s["price"] for s in near) / len(near), 1) if near else None
    out = {"at": db.utc_now_iso(), "fuel": fuel_name, "radius_km": radius,
           "count": len(near), "average": avg, "cheapest": near[:8]}
    db.set_json_setting(CACHE_KEY, out)
    return out


def latest() -> dict | None:
    return db.get_json_setting(CACHE_KEY)
