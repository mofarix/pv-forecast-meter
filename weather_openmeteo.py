import numpy as np
import pandas as pd
import openmeteo_requests
import requests_cache
from retry_requests import retry


def _get_weather(url: str, params: dict, timezone: str, cache_expire_s: int) -> pd.DataFrame:
    """Fetch hourly Open-Meteo data and return the model input columns."""
    cache_session = requests_cache.CachedSession(".cache", expire_after=cache_expire_s)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    response = openmeteo_requests.Client(session=retry_session).weather_api(url, params=params)[0]
    hourly = response.Hourly()
    dates = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    )
    return pd.DataFrame({
        "date": dates.tz_convert(timezone),
        "temperature_2m": hourly.Variables(0).ValuesAsNumpy(),
        "wind_speed_10m": hourly.Variables(1).ValuesAsNumpy(),
        "diffuse_radiation": hourly.Variables(2).ValuesAsNumpy(),
        "direct_normal_irradiance": hourly.Variables(3).ValuesAsNumpy(),
    })


def get_weather_api_forecast(
    latitude: float,
    longitude: float,
    forecast_days: int = 7,
    past_days: int = 0,
    timezone: str = "Europe/Berlin",
    cache_expire_s: int = 3600,
) -> pd.DataFrame:
    """Load hourly forecast weather data from Open-Meteo."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": [
            "temperature_2m",
            "wind_speed_10m",
            "diffuse_radiation",
            "direct_normal_irradiance",
        ],
        "wind_speed_unit": "ms",
        "timezone": timezone,
        "forecast_days": forecast_days,
        "past_days": past_days,
    }
    return _get_weather("https://api.open-meteo.com/v1/forecast", params, timezone, cache_expire_s)


def get_weather_api_archive(
    start_date: str,
    end_date: str,
    latitude: float,
    longitude: float,
    timezone: str = "Europe/Berlin",
    cache_expire_s: int = 3600,
) -> pd.DataFrame:
    """
    Lädt stündliche Wetterdaten vom Open-Meteo Archive API.

    Rückgabe: DataFrame mit Spalten
        date                        (tz-aware, timezone)
        temperature_2m              [°C]
        wind_speed_10m              [km/h] ist es laut BEschreibung !!!
        diffuse_radiation           [W/m²]
        direct_normal_irradiance    [W/m²]
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": [
            "temperature_2m",
            "wind_speed_10m",
            "diffuse_radiation",
            "direct_normal_irradiance",
        ],
        "wind_speed_unit": "ms",
        "timezone": timezone,
        "start_date": start_date,
        "end_date": end_date,
    }

    return _get_weather(
        "https://archive-api.open-meteo.com/v1/archive",
        params,
        timezone,
        cache_expire_s,
    )
