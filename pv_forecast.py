"""Standalone PV power forecast using pvlib and Open-Meteo."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

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


def predict_pv(
    weather: pd.DataFrame,
    systems: tuple[dict, ...] = PV_SYSTEMS,
    latitude: float = LATITUDE,
    longitude: float = LONGITUDE,
    timezone: str = TIMEZONE,
) -> pd.DataFrame:
    """Calculate AC power and hourly energy for each active PV system."""
    systems = tuple(system for system in systems if system.get("active", True))
    result = pd.DataFrame({"time": weather["date"]})
    for system in systems:
        simulated, _ = pv_yield_from_pvlib_model(
            df=weather.copy(),
            latitude=latitude,
            longitude=longitude,
            tz=timezone,
            kWp=system["kWp"],
            surface_tilt=SURFACE_TILT,
            surface_azimuth=system["surface_azimuth"],
            eta_inv_nom=ETA_INVERTER,
            p_inv_ac_kW=system["p_inv_ac_kW"],
            dc_cable_loss=DC_CABLE_LOSS,
            wind_height_factor=WIND_HEIGHT_FACTOR,
            faiman_u0=FAIMAN_U0,
            faiman_u1=FAIMAN_U1,
            system_loss=SYSTEM_LOSS,
        )
        name = system["name"]
        result[f"{name}_P_ac_W"] = simulated["P_ac_W"].to_numpy()
        result[f"{name}_E_kWh"] = simulated["pv_yield_kWh"].to_numpy()

    power_columns = [f"{system['name']}_P_ac_W" for system in systems]
    energy_columns = [f"{system['name']}_E_kWh" for system in systems]
    result["P_total_ac_W"] = result[power_columns].sum(axis=1)
    result["E_total_kWh"] = result[energy_columns].sum(axis=1)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, default=LATITUDE)
    parser.add_argument("--longitude", type=float, default=LONGITUDE)
    parser.add_argument("--forecast-days", type=int, default=7)
    parser.add_argument("--past-days", type=int, default=0)
    parser.add_argument("--timezone", default=TIMEZONE)
    parser.add_argument("--output", type=Path, help="Write predictions to this CSV file")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    weather = get_weather_api_forecast(
        latitude=args.latitude,
        longitude=args.longitude,
        forecast_days=args.forecast_days,
        past_days=args.past_days,
        timezone=args.timezone,
    )
    result = predict_pv(
        weather,
        latitude=args.latitude,
        longitude=args.longitude,
        timezone=args.timezone,
    )
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