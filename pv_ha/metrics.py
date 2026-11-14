"""Sensor values derived from forecast.csv for a given point in time (pure functions)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HOUR = 3600.0


def load_forecast(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def groups(df: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """{group: (power column, energy column)} - 'total' first, then one entry per system."""
    names = [c[: -len("_P_ac_W")] for c in df.columns if c.endswith("_P_ac_W") and c != "P_total_ac_W"]
    return {"total": ("P_total_ac_W", "E_total_kWh"), **{n: (f"{n}_P_ac_W", f"{n}_E_kWh") for n in names}}


def _overlap_h(start: np.ndarray, end: np.ndarray, a: float, b: float) -> np.ndarray:
    """Hours each interval [start, end) shares with [a, b)."""
    return np.clip(np.minimum(end, b) - np.maximum(start, a), 0, None) / HOUR


def compute(df: pd.DataFrame, now: pd.Timestamp, tz: str) -> dict[str, dict]:
    """Per group {'state': {...}, 'attributes': {...}} at time `now` (tz-aware)."""
    # A CSV row holds the mean over the hour *ending* at `time`. Epoch seconds independent of the
    # datetime resolution (pandas 3 parses to microseconds, astype('int64') would be off by 1000).
    end = (df["time"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()
    start = end - HOUR
    mid = end - HOUR / 2
    t = now.timestamp()
    day = (df["time"] - pd.Timedelta(minutes=30)).dt.tz_convert(tz).dt.date.to_numpy()
    today = now.tz_convert(tz).date()
    is_today, is_tomorrow, future = day == today, day == today + timedelta(days=1), day >= today
    done = np.clip((t - start) / HOUR, 0, 1)  # elapsed fraction of each interval
    in_range = start.min() <= t <= end.max()
    local_start = pd.to_datetime(start, unit="s", utc=True).tz_convert(tz)

    def r(value, digits: int = 2):  # plain Python numbers for JSON
        return round(float(value), digits) if digits else int(round(float(value)))

    def now_value(col: str, digits: int) -> float | None:
        return r(np.interp(t, mid, df[col].to_numpy(float)), digits) if in_range else None

    out = {}
    for group, (pcol, ecol) in groups(df).items():
        P, E = df[pcol].to_numpy(float), df[ecol].to_numpy(float)
        e_today, produced = E[is_today].sum(), (E * done)[is_today].sum()
        peak = int(np.argmax(np.where(is_today, P, -1.0))) if is_today.any() else None
        state = {
            "power_now": now_value(pcol, 0) or 0,
            "energy_today": r(e_today),
            "energy_produced": r(produced),
            "energy_remaining": r(e_today - produced),
            "energy_next_hour": r((E * _overlap_h(start, end, t, t + HOUR)).sum()),
            "energy_next_24h": r((E * _overlap_h(start, end, t, t + 24 * HOUR)).sum()),
            "energy_tomorrow": r(E[is_tomorrow].sum()),
            "energy_forecast": r(E[future].sum()),
            "peak_power_today": r(P[peak], 0) if peak is not None else 0,
            "peak_time_today": local_start[peak].isoformat() if peak is not None else None,
        }
        attributes = {
            "hours": [{"start": s.isoformat(), "power_w": r(p, 0), "energy_kwh": r(e, 3)}
                      for s, p, e in zip(local_start[future], P[future], E[future])],
            "days": {str(d): r(v) for d, v in pd.Series(E[future]).groupby(day[future]).sum().items()},
        }
        out[group] = {"state": state, "attributes": attributes}

    if "temp_air_C" in df:
        out["total"]["state"]["temp_air"] = now_value("temp_air_C", 1)
    if "cloud_cover_pct" in df:
        out["total"]["state"]["cloud_cover"] = now_value("cloud_cover_pct", 0)
    return out
