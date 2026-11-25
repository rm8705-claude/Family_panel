"""Cheapest fuel nearby, and the rain radar for the screensaver."""
import io

import pytest
from PIL import Image

import db
import fuel
import radar

HOME = {"lat": -27.39, "lon": 153.03}


class Resp:
    status_code = 200

    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_fuel_ranks_nearby_servos_by_price(monkeypatch):
    db.set_json_setting(fuel.SITES_KEY, {})
    data = {
        "/Subscriber/GetCountryFuelTypes": {"Fuels": [{"FuelId": 2, "Name": "Unleaded"},
                                                     {"FuelId": 5, "Name": "Premium Unleaded 95"}]},
        "/Subscriber/GetFullSiteDetails": {"S": [
            {"S": 1, "N": "Near Cheap", "A": "1 Rd", "P": "4031", "B": 7, "Lat": -27.40, "Lng": 153.03},
            {"S": 2, "N": "Near Dear", "A": "2 Rd", "P": "4031", "B": 7, "Lat": -27.38, "Lng": 153.04},
            {"S": 3, "N": "Far Cheapest", "A": "3 Rd", "P": "4300", "B": 7, "Lat": -27.70, "Lng": 152.90},
            {"S": 4, "N": "Not selling", "A": "4 Rd", "P": "4031", "B": 7, "Lat": -27.39, "Lng": 153.02}]},
        "/Subscriber/GetCountryBrands": {"Brands": [{"BrandId": 7, "Name": "Indie"}]},
        "/Price/GetSitesPrices": {"SitePrices": [
            {"SiteId": 1, "FuelId": 2, "Price": 1749}, {"SiteId": 2, "FuelId": 2, "Price": 1899},
            {"SiteId": 3, "FuelId": 2, "Price": 1599}, {"SiteId": 4, "FuelId": 2, "Price": 9999},
            {"SiteId": 1, "FuelId": 5, "Price": 1999}]},
    }
    monkeypatch.setattr(fuel.requests, "get",
                        lambda url, params=None, timeout=None, headers=None:
                        Resp(data[url.replace(fuel.API, "")]))
    out = fuel.refresh({"qld_fuel_token": "t"}, HOME)
    assert [c["name"] for c in out["cheapest"]] == ["Near Cheap", "Near Dear"]
    assert out["cheapest"][0]["price"] == 174.9 and out["cheapest"][0]["brand"] == "Indie"
    assert out["average"] == 182.4 and out["fuel"] == "Unleaded"
    with pytest.raises(fuel.NotConfigured):
        fuel.refresh({}, HOME)


def test_radar_picks_the_nearest_and_places_home():
    prod, name, lat, lon = radar.pick({}, HOME)
    assert prod == "IDR663"
    x, y = radar.home_px({"lat": lat, "lon": lon}, lat, lon)
    assert (x, y) == (0.5, 0.5)
    x, y = radar.home_px(HOME, lat, lon)
    assert 0.3 < x < 0.5 and 0.3 < y < 0.5            # north-west of Mt Stapylton
    assert radar.pick({"bom_radar": "idr503"}, HOME)[0] == "IDR503"


def png(dots):
    im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    for x, y in dots:
        im.putpixel((x, y), (0, 120, 255, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def test_rain_near_home_only_counts_close_rain():
    assert radar.rain_near(png([(256 + 30, 256)]), 0.5, 0.5)          # 15 km away
    assert not radar.rain_near(png([(256 + 120, 256)]), 0.5, 0.5)     # 60 km away


def test_radar_image_names_are_checked():
    assert radar.image_path("../../etc/passwd") is None
    assert radar.image_path("IDR663.T.202611250630.png") is None       # well-formed, not on disk


def test_radar_refresh_with_a_fake_ftp(monkeypatch, tmp_path):
    monkeypatch.setattr(radar, "store_dir", lambda: str(tmp_path))
    frames = [f"IDR663.T.20261125{h:02d}00.png" for h in range(0, 9)]
    rainy = png([(200, 200)])

    class FTP:
        def __init__(self, *a, **k):
            pass

        def login(self):
            pass

        def nlst(self, d):
            return [f"{d}/{n}" for n in frames + ["IDR503.T.202611250800.png"]]

        def retrbinary(self, cmd, cb):
            cb(rainy if ".T." in cmd else png([]))

        def quit(self):
            pass
    monkeypatch.setattr(radar.ftplib, "FTP", FTP)
    out = radar.refresh({}, HOME, weather_now_code=61)
    assert out["product"] == "IDR663" and len(out["frames"]) == radar.FRAMES
    assert out["show"] is True and out["wet_now"] is True
    assert len(out["layers"]) == 4
    assert radar.latest()["frames"][-1]["name"] == frames[-1]


def test_fuel_and_radar_endpoints(client):
    db.set_json_setting(fuel.CACHE_KEY, None)
    assert client.get("/api/fuel").get_json() == {"available": False}
    db.set_json_setting(radar.CACHE_KEY, {"show": True, "frames": [{"name": "IDR663.T.202611250800.png", "at": "x"}],
                                          "layers": ["IDR663.background.png"], "home": {"x": .4, "y": .4}})
    r = client.get("/api/radar").get_json()
    assert r["frames"][0]["url"] == "/api/radar/img/IDR663.T.202611250800.png"
    assert r["layers"] == ["/api/radar/img/IDR663.background.png"]
