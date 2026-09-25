"""FastAPI backend for the PV forecast dashboard.

Start:  uvicorn server.app:app --reload   (from the repo root)
Serves the API under /api/* and the frontend under /.

Parameters come from pv_forecast.resolve_config(), highest priority first:
data/myconfig.json (local, written here, git-ignored) > data/config.json (versioned
defaults) > constants in pv_forecast.py > defaults of pv_libmdl. The CLI uses the
same resolver, so both compute from the same source.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pvlib
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import pv_forecast
from pv_forecast import (
    ConfigOverrides, SiteOverrides, SystemPatch,
    config_hash, merge_config, predict_pv, read_layer, resolve_config,
)
from weather_openmeteo import get_weather_api_archive, get_weather_api_forecast

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PV_DATA_DIR", ROOT / "data"))
FRONTEND = ROOT / "frontend"

FORECAST_MAX_AGE_S = 3 * 3600  # GET /api/forecast recomputes older files (Open-Meteo updates hourly)

# Keys of a system entry that define it rather than tune the model; they survive a reset.
IDENTITY_KEYS = ("name", "label", "active", "kWp", "surface_azimuth", "p_inv_ac_kW")

app = FastAPI(title="PV-Prognose API")
_compute_lock = threading.Lock()
_state_lock = threading.Lock()
_run_state: dict = {"status": "idle", "started": None, "finished": None, "rows": None,
                    "error": None, "config_hash": None}


def base_config_path() -> Path:
    """Versioned defaults; never written by the API."""
    return DATA_DIR / "config.json"


def config_path() -> Path:
    """Local overrides from the dashboard."""
    return DATA_DIR / "myconfig.json"


def forecast_path() -> Path:
    return DATA_DIR / "forecast.csv"


def forecast_hash_path() -> Path:
    """config_hash of the config forecast.csv was computed with."""
    return DATA_DIR / "forecast.hash"


def _atomic_write(path: Path, text: str, backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


# ---------- Config storage (overrides only; the effective config is always resolved) ----------
def read_overrides() -> dict:
    path = config_path()
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"site": dict(raw.get("site") or {}), "systems": list(raw.get("systems") or [])}


def save_overrides(overrides: dict) -> dict:
    """Validate, write atomically and return the effective config. Writes nothing on error."""
    try:
        parsed = ConfigOverrides.model_validate(overrides)
        effective = merge_config(read_layer(base_config_path()), parsed.model_dump(mode="json"))
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    except ValueError as exc:
        raise HTTPException(422, [{"loc": str(exc).split(":")[0].split("."), "msg": str(exc)}]) from exc
    text = json.dumps(parsed.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False)
    _atomic_write(config_path(), text + "\n", backup=True)
    return effective


def _apply_patch(target: dict, patch: dict) -> None:
    """Sent values override; an explicit null removes the override (falls back to the default)."""
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value


def effective_config(user_layer: bool = True) -> dict:
    paths = (base_config_path(), config_path()) if user_layer else (base_config_path(),)
    try:
        return resolve_config(*paths)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(500, f"Konfiguration ungültig ({', '.join(p.name for p in paths)}): {exc}") from exc


def default_names() -> set[str]:
    """Systems defined without the local overrides (code + data/config.json)."""
    return {s["name"] for s in effective_config(user_layer=False)["systems"]}


# ---------- Computing ----------
def forecast_stale() -> bool:
    """Missing, older than FORECAST_MAX_AGE_S or computed with a different config."""
    path, hash_path = forecast_path(), forecast_hash_path()
    if not path.exists() or time.time() - path.stat().st_mtime > FORECAST_MAX_AGE_S:
        return True
    try:
        current = config_hash(resolve_config(base_config_path(), config_path()))
    except (ValidationError, ValueError):
        return False  # keep serving the old file; the run would fail on the same config
    return not hash_path.exists() or hash_path.read_text().strip() != current


def run_forecast(days: int = 7, only_if_stale: bool = False) -> None:
    with _compute_lock:
        # Re-checked under the lock: concurrent stale requests compute once, the rest reuse the result.
        if only_if_stale and not forecast_stale():
            return
        with _state_lock:
            _run_state.update(status="running", started=datetime.now().isoformat(timespec="seconds"),
                              finished=None, error=None)
        try:
            cfg = resolve_config(base_config_path(), config_path())
            site = cfg["site"]
            weather = get_weather_api_forecast(site["latitude"], site["longitude"],
                                               forecast_days=days, timezone=site["timezone"])
            df = predict_pv(weather, cfg)
            current_hash = config_hash(cfg)
            _atomic_write(forecast_path(), df.to_csv(index=False))
            _atomic_write(forecast_hash_path(), current_hash)
            update = dict(status="ok", rows=len(df), config_hash=current_hash)
        except Exception as exc:  # noqa: BLE001 - reported to the UI via GET /api/run
            update = dict(status="error", error=f"{type(exc).__name__}: {exc}")
        with _state_lock:
            _run_state.update(finished=datetime.now().isoformat(timespec="seconds"), **update)


# ---------- Endpoints ----------
@app.get("/api/forecast", response_class=PlainTextResponse)
def get_forecast():
    """Hourly forecast in the CSV format of pv_forecast.py --output (recomputed when stale)."""
    if forecast_stale():
        run_forecast(only_if_stale=True)
    if not forecast_path().exists():
        raise HTTPException(503, _run_state.get("error") or "Keine Prognose vorhanden")
    return PlainTextResponse(forecast_path().read_text(encoding="utf-8"), media_type="text/csv")


@app.get("/api/systems")
def get_systems():
    """Effective config: code defaults < data/config.json < data/myconfig.json."""
    return effective_config()


@app.put("/api/systems")
def put_systems(cfg: ConfigOverrides):
    return save_overrides(cfg.model_dump(mode="json"))


@app.patch("/api/systems/{name}")
def patch_system(name: str, patch: SystemPatch):
    overrides = read_overrides()
    entry = next((s for s in overrides["systems"] if s.get("name") == name), None)
    if entry is None:
        if name not in default_names():
            raise HTTPException(404, f"Unbekannte Anlage: {name}")
        entry = {"name": name}
        overrides["systems"].append(entry)
    _apply_patch(entry, patch.model_dump(mode="json", exclude_unset=True))
    return save_overrides(overrides)


@app.patch("/api/site")
def patch_site(patch: SiteOverrides):
    overrides = read_overrides()
    _apply_patch(overrides["site"], patch.model_dump(mode="json", exclude_unset=True))
    return save_overrides(overrides)


@app.delete("/api/systems/{name}/overrides")
def delete_overrides(name: str):
    """Back to the defaults. Systems that exist only in myconfig.json keep their identity."""
    overrides = read_overrides()
    in_code = name in default_names()
    entries = [s for s in overrides["systems"] if s.get("name") == name]
    if not in_code and not entries:
        raise HTTPException(404, f"Unbekannte Anlage: {name}")
    overrides["systems"] = [s for s in overrides["systems"] if s.get("name") != name]
    if not in_code:
        overrides["systems"].append({k: v for k, v in entries[0].items() if k in IDENTITY_KEYS})
    return save_overrides(overrides)


@app.get("/api/defaults")
def get_defaults():
    """Defaults without the local overrides (code + data/config.json), for "reset" in the UI."""
    return effective_config(user_layer=False)


@app.post("/api/run")
def post_run(background: BackgroundTasks, days: int = 7):
    if not 1 <= days <= 16:
        raise HTTPException(400, "days muss zwischen 1 und 16 liegen")
    with _state_lock:
        if _run_state["status"] == "running":
            raise HTTPException(409, "Lauf läuft bereits")
        _run_state.update(status="running", started=datetime.now().isoformat(timespec="seconds"),
                          finished=None, error=None)
    background.add_task(run_forecast, days)
    return {"status": "started"}


@app.get("/api/run")
def get_run():
    path = forecast_path()
    updated = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds") if path.exists() else None
    return {**_run_state, "forecast_updated": updated}


@app.get("/api/archive")
def get_archive(year: int):
    """Monthly totals (kWh) per system from Open-Meteo archive weather."""
    today = date.today()
    if year > today.year:
        raise HTTPException(400, f"{year} liegt in der Zukunft")
    if year < 1940:
        raise HTTPException(400, "Open-Meteo-Archiv beginnt 1940")
    cfg = effective_config()
    site = cfg["site"]
    end = min(date(year, 12, 31), today - timedelta(days=2))  # archive lags ~2 days
    if end < date(year, 1, 1):
        return {"year": year, "until": None, "months": []}

    cache, cache_hash = DATA_DIR / f"archive_{year}.csv", DATA_DIR / f"archive_{year}.hash"
    current_hash = config_hash(cfg)
    cached = (year < today.year and cache.exists() and cache_hash.exists()
              and cache_hash.read_text().strip() == current_hash)
    if cached:
        df = pd.read_csv(cache)
    else:
        weather = get_weather_api_archive(f"{year}-01-01", end.isoformat(),
                                          site["latitude"], site["longitude"], site["timezone"])
        df = predict_pv(weather, cfg)
        if year < today.year:
            _atomic_write(cache, df.to_csv(index=False))
            _atomic_write(cache_hash, current_hash)
    df["month"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(site["timezone"]).dt.month
    e_cols = [c for c in df if c.endswith("_E_kWh")] + ["E_total_kWh"]
    monthly = df.groupby("month")[e_cols].sum().round(2)
    return {"year": year, "until": end.isoformat(),
            "months": [{"month": int(m), **row.to_dict()} for m, row in monthly.iterrows()]}


@app.get("/api/sun")
def get_sun(days: int = 8, start: date | None = None):
    """Sunrise/sunset per day as decimal hours in local time (NaN-free: polar days give null)."""
    if not 1 <= days <= 366:
        raise HTTPException(400, "days muss zwischen 1 und 366 liegen")
    site = effective_config()["site"]
    first = start or pd.Timestamp.now(tz=site["timezone"]).date()
    times = pd.date_range(pd.Timestamp(first), periods=days, freq="D", tz=site["timezone"])
    sun = pvlib.solarposition.sun_rise_set_transit_spa(times, site["latitude"], site["longitude"])

    def hours(ts) -> float | None:
        if pd.isna(ts):
            return None
        return round(ts.hour + ts.minute / 60 + ts.second / 3600, 4)

    return {"days": [{"date": t.date().isoformat(), "sunrise_h": hours(row.sunrise), "sunset_h": hours(row.sunset)}
                     for t, row in zip(times, sun.itertuples())]}


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/PV%20Prognose.dc.html")


# Frontend under /, mounted after the API routes so /api/* keeps priority.
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
