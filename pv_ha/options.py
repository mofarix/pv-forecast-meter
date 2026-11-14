"""Add-on options (/data/options.json) -> base config layer for pv_forecast.resolve_config()."""

from __future__ import annotations

import logging
import os

import requests

import pv_forecast

log = logging.getLogger(__name__)
SUPERVISOR = "http://supervisor"


def supervisor_get(path: str) -> dict | None:
    """GET against the Supervisor API; None when not running as add-on or on any error."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        resp = requests.get(SUPERVISOR + path, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.debug("Supervisor %s: %s", path, exc)
        return None


def ha_home() -> dict:
    """Home location of the HA instance as site fields (missing keys when unavailable)."""
    cfg = supervisor_get("/core/api/config") or {}
    mapping = {"latitude": "latitude", "longitude": "longitude", "altitude": "elevation", "timezone": "time_zone"}
    return {site_key: cfg[ha_key] for site_key, ha_key in mapping.items() if cfg.get(ha_key) is not None}


def parse_horizon(text: str | None) -> dict[float, float] | None:
    """'0:0, 200:0, 220:10' -> {0: 0, 200: 0, 220: 10}; empty -> None (same format as the dashboard)."""
    profile = {}
    for part in str(text or "").split(","):
        if ":" in part:
            az, angle = part.split(":", 1)
            profile[float(az)] = float(angle)
    return profile or None


def config_layer(opts: dict) -> dict:
    """Base layer from the options; HA home fills missing site fields, unlisted code defaults are disabled."""
    site = {k: v for k, v in (opts.get("site") or {}).items() if v not in (None, "")}
    for key, value in ha_home().items():
        site.setdefault(key, value)

    systems = []
    for entry in opts.get("systems") or []:
        system = {k: v for k, v in entry.items() if v not in (None, "")}
        if "horizon_profile" in system:
            system["horizon_profile"] = parse_horizon(system["horizon_profile"])
        systems.append(system)

    # PV_SYSTEMS from the code would otherwise still be computed next to the user's systems.
    configured = {s["name"] for s in systems}
    systems += [{"name": s["name"], "active": False} for s in pv_forecast.PV_SYSTEMS if s["name"] not in configured]
    return {"site": site, "systems": systems}
