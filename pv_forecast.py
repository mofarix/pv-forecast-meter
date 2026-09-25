"""Standalone PV power forecast using pvlib and Open-Meteo."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pv_libmdl import pv_yield_from_pvlib_model
from weather_openmeteo import get_weather_api_forecast


# Kaiserstuhl (Baden-Württemberg)
LATITUDE = 48.1000
LONGITUDE = 7.6700
TIMEZONE = "Europe/Berlin"
SURFACE_TILT = 30.0
WIND_HEIGHT_FACTOR = 0.8
FAIMAN_U0 = 20.0
FAIMAN_U1 = 5.0
DC_CABLE_LOSS = 0.04
SYSTEM_LOSS = 0.14
ETA_INVERTER = 0.96

PV_SYSTEMS = (
    {"name": "pv_system_1", "kWp": 15, "surface_azimuth": 180.0-45.0, "p_inv_ac_kW": 12.5, "active": True},
    {"name": "pv_system_2", "kWp": 15, "surface_azimuth": 180.0+45.0, "p_inv_ac_kW": 12.5, "active": True},
    {"name": "pv_system_3", "kWp": 0, "surface_azimuth": 180.0, "p_inv_ac_kW": 0, "active": False},
    {"name": "pv_system_4", "kWp": 0, "surface_azimuth": 180.0, "p_inv_ac_kW": 0, "active": False},
)

# Config layers on top of the constants above (later wins):
#   data/config.json    versioned site defaults (Kaiserstuhl)
#   data/myconfig.json  local overrides written by the dashboard, git-ignored
DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_CONFIG_PATH = DATA_DIR / "config.json"
USER_CONFIG_PATH = DATA_DIR / "myconfig.json"

SITE_PARAMS = ("latitude", "longitude", "altitude", "timezone", "surface_tilt")
# Per-system parameters passed straight to pv_yield_from_pvlib_model.
MODEL_PARAMS = (
    "kWp", "surface_azimuth", "p_inv_ac_kW", "faiman_u0", "faiman_u1", "wind_height_factor",
    "gamma_pdc", "dc_cable_loss", "system_loss", "eta_inv_nom", "dc_ac_ratio", "horizon_profile",
)
SYSTEM_PARAMS = ("label", "active", *MODEL_PARAMS)


# ---------- Config schema (identical to the dashboard's JSON import/export) ----------
# Every field is optional: a missing key or null falls through to the code defaults.
class SiteOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    altitude: float | None = None
    timezone: str | None = None
    surface_tilt: float | None = Field(None, ge=0, le=90)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (KeyError, ValueError) as exc:  # ZoneInfoNotFoundError is a KeyError
                raise ValueError(f"unknown timezone {value!r}") from exc
        return value


class SystemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    active: bool | None = None
    kWp: float | None = Field(None, ge=0)
    surface_azimuth: float | None = Field(None, ge=0, le=360)
    p_inv_ac_kW: float | None = Field(None, ge=0)
    faiman_u0: float | None = Field(None, gt=0)
    faiman_u1: float | None = Field(None, ge=0)
    wind_height_factor: float | None = Field(None, ge=0)
    gamma_pdc: float | None = Field(None, ge=-0.02, le=0)
    dc_cable_loss: float | None = Field(None, ge=0, le=1)
    system_loss: float | None = Field(None, ge=0, le=1)
    eta_inv_nom: float | None = Field(None, gt=0, le=1)
    dc_ac_ratio: float | None = Field(None, gt=0)
    horizon_profile: dict[float, float] | None = None


class SystemOverrides(SystemPatch):
    name: str = Field(pattern=r"^[A-Za-z0-9_]+$")  # column prefix in the CSV


class ConfigOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: SiteOverrides = SiteOverrides()
    systems: list[SystemOverrides] = []


# ---------- Resolving: config.json > PV_SYSTEMS / constants > pv_libmdl defaults ----------
def _model_defaults() -> dict:
    return {
        name: param.default
        for name, param in inspect.signature(pv_yield_from_pvlib_model).parameters.items()
        if param.default is not inspect.Parameter.empty
    }


def _module_defaults() -> dict:
    """Model parameters shared by all systems unless a system sets its own."""
    lib = _model_defaults()
    return {
        "surface_azimuth": lib["surface_azimuth"],
        "p_inv_ac_kW": lib["p_inv_ac_kW"],
        "faiman_u0": FAIMAN_U0,
        "faiman_u1": FAIMAN_U1,
        "wind_height_factor": WIND_HEIGHT_FACTOR,
        "gamma_pdc": lib["gamma_pdc"],
        "dc_cable_loss": DC_CABLE_LOSS,
        "system_loss": SYSTEM_LOSS,
        "eta_inv_nom": ETA_INVERTER,
        "dc_ac_ratio": lib["dc_ac_ratio"],
        "horizon_profile": lib["horizon_profile"],
    }


def default_config() -> dict:
    """Effective config from the code alone (no config.json)."""
    site = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "altitude": _model_defaults()["altitude"],
        "timezone": TIMEZONE,
        "surface_tilt": SURFACE_TILT,
    }
    systems = [
        {"name": s["name"], "label": s["name"], "active": True, **_module_defaults(), **s}
        for s in PV_SYSTEMS
    ]
    return {"site": site, "systems": systems}


def merge_config(*layers: dict) -> dict:
    """Apply (partial) override layers in order to the code defaults; return the effective config."""
    config = default_config()
    systems = {s["name"]: s for s in config["systems"]}
    for layer in layers:
        parsed = ConfigOverrides.model_validate(layer).model_dump(exclude_none=True)
        config["site"].update(parsed["site"])
        for override in parsed["systems"]:
            name = override["name"]
            if name not in systems:
                if "kWp" not in override:
                    raise ValueError(f"systems.{name}.kWp: required for a system not defined in PV_SYSTEMS")
                systems[name] = {"name": name, "label": name, "active": True, **_module_defaults()}
            systems[name].update(override)
    config["systems"] = list(systems.values())
    return config


def read_layer(path: Path | None) -> dict:
    """One config file as overrides; a missing file is an empty layer."""
    if path is None or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_config(*paths: Path | None) -> dict:
    """Effective config: code defaults, then each config file in order (later wins).

    Without arguments: data/config.json, then data/myconfig.json.
    """
    if not paths:
        paths = (DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
    return merge_config(*(read_layer(p) for p in paths))


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


# ---------- Prediction ----------
def predict_pv(weather: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """Calculate AC power and hourly energy for each active PV system."""
    config = config if config is not None else resolve_config()
    site = config["site"]
    systems = [s for s in config["systems"] if s.get("active", True) and s["kWp"] > 0]
    result = pd.DataFrame({"time": weather["date"]})
    for system in systems:
        params = {key: system[key] for key in MODEL_PARAMS}
        # 0 kW / empty profile mean "not set" (PV_SYSTEMS uses 0 for unused slots).
        params["p_inv_ac_kW"] = params["p_inv_ac_kW"] or None
        params["horizon_profile"] = params["horizon_profile"] or None
        simulated, _ = pv_yield_from_pvlib_model(
            df=weather.copy(),
            latitude=site["latitude"],
            longitude=site["longitude"],
            altitude=site["altitude"],
            tz=site["timezone"],
            surface_tilt=site["surface_tilt"],
            **params,
        )
        name = system["name"]
        result[f"{name}_P_ac_W"] = simulated["P_ac_W"].to_numpy()
        result[f"{name}_E_kWh"] = simulated["pv_yield_kWh"].to_numpy()

    power_columns = [f"{system['name']}_P_ac_W" for system in systems]
    energy_columns = [f"{system['name']}_E_kWh" for system in systems]
    result["P_total_ac_W"] = result[power_columns].sum(axis=1)
    result["E_total_kWh"] = result[energy_columns].sum(axis=1)
    result["temp_air_C"] = weather["temperature_2m"].to_numpy()
    if "cloud_cover" in weather:
        result["cloud_cover_pct"] = weather["cloud_cover"].to_numpy()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="Versioned defaults (JSON)")
    parser.add_argument("--config", type=Path, default=USER_CONFIG_PATH,
                        help="Local overrides on top of --base-config (JSON, as saved by the dashboard)")
    parser.add_argument("--latitude", type=float, help="Overrides the config")
    parser.add_argument("--longitude", type=float, help="Overrides the config")
    parser.add_argument("--forecast-days", type=int, default=7)
    parser.add_argument("--past-days", type=int, default=0)
    parser.add_argument("--timezone", help="Overrides the config")
    parser.add_argument("--output", type=Path, help="Write predictions to this CSV file")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = resolve_config(args.base_config, args.config)
    for key in ("latitude", "longitude", "timezone"):
        if getattr(args, key) is not None:
            config["site"][key] = getattr(args, key)
    site = config["site"]
    weather = get_weather_api_forecast(
        latitude=site["latitude"],
        longitude=site["longitude"],
        forecast_days=args.forecast_days,
        past_days=args.past_days,
        timezone=site["timezone"],
    )
    result = predict_pv(weather, config)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output, index=False)
        print(f"Wrote {len(result)} rows to {args.output}")
    else:
        print(result.to_string(index=False))
    print(f"Maximum total power: {result['P_total_ac_W'].max() / 1000:.2f} kW")
    print(f"Forecast energy: {result['E_total_kWh'].sum():.2f} kWh")


if __name__ == "__main__":
    main()
