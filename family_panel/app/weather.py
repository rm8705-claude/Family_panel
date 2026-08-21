"""Weather fetch. Open-Meteo primary (keyless); provider interface kept thin
so a BOM provider can be swapped in later without touching callers.

refresh(cfg) is called by the scheduler; the latest normalised payload is
cached in the settings table so the API keeps serving data offline.
"""
import requests

import db

# WMO weather interpretation codes -> short human description.
WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail",
}


def describe(code) -> str:
    try:
        return WMO.get(int(code), "—")
    except (TypeError, ValueError):
        return "—"


class OpenMeteoProvider:
    URL = "https://api.open-meteo.com/v1/forecast"

    def fetch(self, lat: float, lon: float) -> dict:
        r = requests.get(self.URL, params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,weather_code",
            "daily": ("temperature_2m_min,temperature_2m_max,"
                      "precipitation_probability_max,uv_index_max,weather_code,"
                      "sunrise,sunset"),
            "timezone": "Australia/Brisbane",
            "forecast_days": 7,
        }, timeout=20)
        r.raise_for_status()
        raw = r.json()
        cur = raw.get("current", {})
        daily = raw.get("daily", {})
        def hhmm(v):
            # Open-Meteo returns local ISO like "2026-08-17T05:23"
            return v[-5:] if isinstance(v, str) and len(v) >= 16 else None

        days = []
        for i, d in enumerate(daily.get("time", [])):
            days.append({
                "date": d,
                "min": daily["temperature_2m_min"][i],
                "max": daily["temperature_2m_max"][i],
                "rain_prob": daily["precipitation_probability_max"][i],
                "uv": daily["uv_index_max"][i],
                "code": daily["weather_code"][i],
                "description": describe(daily["weather_code"][i]),
                "sunrise": hhmm(daily.get("sunrise", [None] * 7)[i]),
                "sunset": hhmm(daily.get("sunset", [None] * 7)[i]),
            })
        out = {
            "current": {
                "temp": cur.get("temperature_2m"),
                "code": cur.get("weather_code"),
                "description": describe(cur.get("weather_code")),
            },
            "daily": days,
        }
        if days:
            out["sun"] = {"sunrise": days[0]["sunrise"], "sunset": days[0]["sunset"]}
        return out


PROVIDER = OpenMeteoProvider()


def refresh(cfg: dict) -> dict:
    data = PROVIDER.fetch(cfg.get("lat", -27.48), cfg.get("lon", 152.98))
    data["updated_at"] = db.utc_now_iso()
    db.set_json_setting("weather:latest", data)
    return data


def latest() -> dict | None:
    return db.get_json_setting("weather:latest")
