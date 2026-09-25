"""FastAPI-Gerüst für das PV-Prognose-Dashboard.

Start:  uvicorn server.app:app --reload   (aus dem Repo-Root von pv-forecast-meter)
Liefert das Frontend unter /  und die API unter /api/*.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import pv_forecast
from pv_forecast import predict_pv
from weather_openmeteo import get_weather_api_archive, get_weather_api_forecast

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = DATA / "config.json"
FORECAST_PATH = DATA / "forecast.csv"
FRONTEND = ROOT / "frontend"
DATA.mkdir(exist_ok=True)

app = FastAPI(title="PV-Prognose API")
_run_lock = threading.Lock()
_run_state: dict = {"status": "idle", "started": None, "finished": None, "rows": None, "error": None}


# ---------- Schema (identisch mit dem JSON-Export des Frontends) ----------
class Site(BaseModel):
    latitude: float = pv_forecast.LATITUDE
    longitude: float = pv_forecast.LONGITUDE
    altitude: float = 35.0
    timezone: str = pv_forecast.TIMEZONE
    surface_tilt: float = pv_forecast.SURFACE_TILT


class System(BaseModel):
    name: str
    label: str | None = None
    active: bool = True
    kWp: float
    surface_azimuth: float = 180.0
    p_inv_ac_kW: float | None = None
    faiman_u0: float = pv_forecast.FAIMAN_U0
    faiman_u1: float = pv_forecast.FAIMAN_U1
    wind_height_factor: float = pv_forecast.WIND_HEIGHT_FACTOR
    gamma_pdc: float = -0.004
    dc_cable_loss: float = Field(pv_forecast.DC_CABLE_LOSS, ge=0, lt=1)
    system_loss: float = Field(pv_forecast.SYSTEM_LOSS, ge=0, lt=1)
    eta_inv_nom: float = Field(pv_forecast.ETA_INVERTER, gt=0, le=1)
    dc_ac_ratio: float = 1.0
    horizon_profile: dict[float, float] | None = None


class Config(BaseModel):
    site: Site = Site()
    systems: list[System]


def default_config() -> Config:
    return Config(systems=[
        System(name=s["name"], label=s["name"], active=s["active"], kWp=s["kWp"],
               surface_azimuth=s["surface_azimuth"], p_inv_ac_kW=s["p_inv_ac_kW"])
        for s in pv_forecast.PV_SYSTEMS
    ])


def load_config() -> Config:
    if CONFIG_PATH.exists():
        return Config.model_validate_json(CONFIG_PATH.read_text())
    return default_config()


def save_config(cfg: Config) -> None:
    CONFIG_PATH.write_text(cfg.model_dump_json(indent=2))


# ---------- Rechnen ----------
# TODO(pv_forecast.py): predict_pv() nutzt heute globale Konstanten für Faiman/Verluste.
# Vorschlag: pro System optionale Overrides aus dem dict lesen, z. B.
#   faiman_u0=system.get("faiman_u0", FAIMAN_U0), ...
# Bis dahin rechnet run_forecast() je System einzeln über pv_libmdl.

def _predict(weather: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    from pv_libmdl import pv_yield_from_pvlib_model

    systems = [s for s in cfg.systems if s.active and s.kWp > 0]
    result = pd.DataFrame({"time": weather["date"]})
    for s in systems:
        sim, _ = pv_yield_from_pvlib_model(
            df=weather.copy(), latitude=cfg.site.latitude, longitude=cfg.site.longitude,
            altitude=cfg.site.altitude, tz=cfg.site.timezone, kWp=s.kWp,
            surface_tilt=cfg.site.surface_tilt, surface_azimuth=s.surface_azimuth,
            gamma_pdc=s.gamma_pdc, eta_inv_nom=s.eta_inv_nom, p_inv_ac_kW=s.p_inv_ac_kW,
            dc_ac_ratio=s.dc_ac_ratio, dc_cable_loss=s.dc_cable_loss,
            wind_height_factor=s.wind_height_factor, faiman_u0=s.faiman_u0, faiman_u1=s.faiman_u1,
            system_loss=s.system_loss, horizon_profile=s.horizon_profile,
        )
        result[f"{s.name}_P_ac_W"] = sim["P_ac_W"].to_numpy()
        result[f"{s.name}_E_kWh"] = sim["pv_yield_kWh"].to_numpy()
    p_cols = [c for c in result if c.endswith("_P_ac_W")]
    e_cols = [c for c in result if c.endswith("_E_kWh")]
    result["P_total_ac_W"] = result[p_cols].sum(axis=1)
    result["E_total_kWh"] = result[e_cols].sum(axis=1)
    # Wetterspalten fürs Dashboard (ersetzt die abgeleiteten Werte im Mockup)
    result["temp_air_C"] = weather["temperature_2m"].to_numpy()
    if "cloud_cover" in weather:
        result["cloud_cover_pct"] = weather["cloud_cover"].to_numpy()
    return result


def run_forecast(days: int = 7) -> None:
    with _run_lock:
        _run_state.update(status="running", started=datetime.now().isoformat(), error=None)
        try:
            cfg = load_config()
            weather = get_weather_api_forecast(cfg.site.latitude, cfg.site.longitude,
                                               forecast_days=days, timezone=cfg.site.timezone)
            df = _predict(weather, cfg)
            df.to_csv(FORECAST_PATH, index=False)
            _run_state.update(status="ok", finished=datetime.now().isoformat(), rows=len(df))
        except Exception as exc:  # noqa: BLE001
            _run_state.update(status="error", finished=datetime.now().isoformat(), error=str(exc))


# ---------- Endpunkte ----------
@app.get("/api/forecast", response_class=PlainTextResponse)
def get_forecast():
    """Stündliche Prognose im CSV-Format von pv_forecast.py --output."""
    if not FORECAST_PATH.exists():
        run_forecast()
    if not FORECAST_PATH.exists():
        raise HTTPException(503, _run_state.get("error") or "Keine Prognose vorhanden")
    return PlainTextResponse(FORECAST_PATH.read_text(), media_type="text/csv")


@app.get("/api/archive")
def get_archive(year: int):
    """Monatssummen (kWh) je System aus Open-Meteo-Archivwetter."""
    cfg = load_config()
    today = datetime.now().date()
    start, end = f"{year}-01-01", min(pd.Timestamp(f"{year}-12-31").date(), today - pd.Timedelta(days=2)).isoformat()
    cache = DATA / f"archive_{year}.csv"
    if cache.exists() and year < today.year:
        df = pd.read_csv(cache, parse_dates=["time"])
    else:
        weather = get_weather_api_archive(start, end, cfg.site.latitude, cfg.site.longitude, cfg.site.timezone)
        df = _predict(weather, cfg)
        df.to_csv(cache, index=False)
    df["month"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(cfg.site.timezone).dt.month
    e_cols = [c for c in df if c.endswith("_E_kWh")]
    monthly = df.groupby("month")[e_cols].sum().round(2)
    return {"year": year, "until": end,
            "months": [{"month": int(m), **row.to_dict()} for m, row in monthly.iterrows()]}


@app.get("/api/systems", response_model=Config)
def get_systems():
    return load_config()


@app.put("/api/systems", response_model=Config)
def put_systems(cfg: Config):
    save_config(cfg)
    return cfg


@app.put("/api/systems/{name}", response_model=Config)
def put_system(name: str, system: System, site: Site | None = None):
    cfg = load_config()
    cfg.systems = [system if s.name == name else s for s in cfg.systems]
    if all(s.name != name for s in cfg.systems):
        cfg.systems.append(system)
    if site:
        cfg.site = site
    save_config(cfg)
    return cfg


@app.post("/api/run")
def post_run(background: BackgroundTasks, days: int = 7):
    if _run_state["status"] == "running":
        raise HTTPException(409, "Lauf läuft bereits")
    background.add_task(run_forecast, days)
    return {"status": "started"}


@app.get("/api/run")
def get_run():
    return _run_state


# Frontend (Build-Output oder die HTML-Referenz) unter /
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
