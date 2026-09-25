"""Acceptance tests for config overrides, CLI and API (no network: weather is monkeypatched)."""

import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import pv_forecast
from server import app as app_module


def fake_weather(latitude=None, longitude=None, forecast_days=2, past_days=0, timezone="Europe/Berlin", **_):
    """Two days of moderate irradiance, low enough that the 12.5 kW inverter never clips."""
    dates = pd.date_range("2026-06-01", periods=24 * forecast_days, freq="h", tz=timezone)
    daylight = np.clip(np.sin(np.pi * (dates.hour - 5) / 16), 0, None)
    return pd.DataFrame({
        "date": dates,
        "temperature_2m": 15 + 8 * daylight,
        "wind_speed_10m": np.full(len(dates), 2.0),
        "diffuse_radiation": 80 * daylight,
        "direct_normal_irradiance": 250 * daylight,
        "cloud_cover": np.full(len(dates), 40.0),
    })


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_module, "get_weather_api_forecast", fake_weather)
    monkeypatch.setattr(pv_forecast, "get_weather_api_forecast", fake_weather)
    app_module._run_state.update(status="idle", error=None)
    return TestClient(app_module.app)


def system(cfg, name):
    return next(s for s in cfg["systems"] if s["name"] == name)


def energy(client, name):
    df = pd.read_csv(pd.io.common.StringIO(client.get("/api/forecast").text))
    return df[f"{name}_E_kWh"].sum()


def test_no_config_returns_code_constants(tmp_path):
    cfg = pv_forecast.resolve_config(tmp_path / "missing.json")
    assert cfg["site"] == {"latitude": pv_forecast.LATITUDE, "longitude": pv_forecast.LONGITUDE, "altitude": 35.0,
                           "timezone": pv_forecast.TIMEZONE, "surface_tilt": pv_forecast.SURFACE_TILT}
    assert [s["name"] for s in cfg["systems"]] == [s["name"] for s in pv_forecast.PV_SYSTEMS]
    for resolved, code in zip(cfg["systems"], pv_forecast.PV_SYSTEMS):
        for key, value in code.items():
            assert resolved[key] == value
        assert resolved["faiman_u0"] == pv_forecast.FAIMAN_U0
        assert resolved["faiman_u1"] == pv_forecast.FAIMAN_U1
        assert resolved["wind_height_factor"] == pv_forecast.WIND_HEIGHT_FACTOR
        assert resolved["dc_cable_loss"] == pv_forecast.DC_CABLE_LOSS
        assert resolved["system_loss"] == pv_forecast.SYSTEM_LOSS
        assert resolved["eta_inv_nom"] == pv_forecast.ETA_INVERTER
        assert resolved["gamma_pdc"] == -0.004 and resolved["dc_ac_ratio"] == 1.0


def test_partial_override_changes_only_that_field(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"systems": [{"name": "pv_system_1", "faiman_u0": 30}]}))
    defaults, cfg = pv_forecast.default_config(), pv_forecast.resolve_config(path)
    assert system(cfg, "pv_system_1")["faiman_u0"] == 30
    assert {**system(cfg, "pv_system_1"), "faiman_u0": pv_forecast.FAIMAN_U0} == system(defaults, "pv_system_1")
    assert system(cfg, "pv_system_2") == system(defaults, "pv_system_2")
    assert cfg["site"] == defaults["site"]


def test_patch_kwp_scales_energy(client):
    assert client.post("/api/run?days=2").status_code == 200
    before = energy(client, "pv_system_1")
    assert client.patch("/api/systems/pv_system_1", json={"kWp": 20}).status_code == 200
    assert system(client.get("/api/systems").json(), "pv_system_1")["kWp"] == 20
    assert client.post("/api/run?days=2").status_code == 200
    assert client.get("/api/run").json()["status"] == "ok"
    assert energy(client, "pv_system_1") / before == pytest.approx(20 / 15, rel=0.03)


def test_invalid_patch_is_rejected_and_nothing_written(client):
    client.patch("/api/systems/pv_system_1", json={"kWp": 18})
    before = app_module.config_path().read_text()
    resp = client.patch("/api/systems/pv_system_1", json={"system_loss": 1.5})
    assert resp.status_code == 422
    assert "system_loss" in json.dumps(resp.json())
    assert app_module.config_path().read_text() == before
    assert system(client.get("/api/systems").json(), "pv_system_1")["system_loss"] == pv_forecast.SYSTEM_LOSS


def test_delete_overrides_restores_defaults(client):
    client.patch("/api/systems/pv_system_1", json={"kWp": 20, "faiman_u0": 30})
    assert client.delete("/api/systems/pv_system_1/overrides").status_code == 200
    assert system(client.get("/api/systems").json(), "pv_system_1") == \
        system(client.get("/api/defaults").json(), "pv_system_1")


def test_cli_matches_api(client, tmp_path):
    client.patch("/api/systems/pv_system_2", json={"kWp": 12, "system_loss": 0.1})
    client.patch("/api/site", json={"surface_tilt": 25})
    client.post("/api/run?days=2")
    api = pd.read_csv(pd.io.common.StringIO(client.get("/api/forecast").text))
    out = tmp_path / "out.csv"
    pv_forecast.main(["--base-config", str(app_module.base_config_path()), "--config", str(app_module.config_path()),
                      "--output", str(out), "--forecast-days", "2"])
    pd.testing.assert_frame_equal(pd.read_csv(out), api)


def test_archive_future_year_is_rejected(client):
    assert client.get("/api/archive?year=2099").status_code == 400


def test_gui_writes_only_local_layer_on_top_of_defaults(client):
    base = app_module.base_config_path()
    base.write_text(json.dumps({"site": {"altitude": 200}, "systems": [{"name": "pv_system_1", "faiman_u0": 25}]}))
    base_before = base.read_text()
    assert client.patch("/api/systems/pv_system_1", json={"kWp": 20}).status_code == 200
    assert base.read_text() == base_before
    assert json.loads(app_module.config_path().read_text()) == {"site": {}, "systems": [{"name": "pv_system_1", "kWp": 20.0}]}
    s1 = system(client.get("/api/systems").json(), "pv_system_1")
    assert (s1["kWp"], s1["faiman_u0"]) == (20, 25)
    assert client.get("/api/systems").json()["site"]["altitude"] == 200
    client.delete("/api/systems/pv_system_1/overrides")
    s1 = system(client.get("/api/systems").json(), "pv_system_1")
    assert (s1["kWp"], s1["faiman_u0"]) == (15, 25)
    assert system(client.get("/api/defaults").json(), "pv_system_1")["faiman_u0"] == 25


def test_inverter_clips_at_ac_nameplate():
    """Strong irradiance on 15 kWp / 12.5 kW: AC output is capped at the inverter's AC rating, not below it."""
    from pv_libmdl import pv_yield_from_pvlib_model
    dates = pd.date_range("2026-06-21", periods=24, freq="h", tz="Europe/Berlin")
    weather = pd.DataFrame({"date": dates, "temperature_2m": 5.0, "wind_speed_10m": 5.0,
                            "diffuse_radiation": 200.0, "direct_normal_irradiance": 1000.0})
    out, _ = pv_yield_from_pvlib_model(weather, 48.1, 7.67, kWp=15, p_inv_ac_kW=12.5,
                                       system_loss=0.14, eta_inv_nom=0.96)
    assert out["P_ac_W"].max() == pytest.approx(12_500)


def test_config_change_makes_forecast_stale(client):
    client.post("/api/run?days=2")
    before = energy(client, "pv_system_1")
    client.patch("/api/systems/pv_system_1", json={"kWp": 30})  # no explicit run
    assert energy(client, "pv_system_1") > 1.5 * before
