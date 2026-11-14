"""Add-on entrypoint: options -> config layer, scheduled forecast + sensor publishing, API/dashboard server.

    PV_OPTIONS=/data/options.json PV_DATA_DIR=/data/pv PV_PORT=8099 python -m pv_ha
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import pandas as pd
import uvicorn

from pv_ha import metrics, options, publish
from server import app as srv

log = logging.getLogger("pv_ha")


def scheduler(opts: dict, pub) -> None:
    """Recompute every update_interval_min, publish 'now' values every publish_interval_min."""
    update_s, publish_s = opts.get("update_interval_min", 60) * 60, opts.get("publish_interval_min", 5) * 60
    days = opts.get("forecast_days", 7)
    next_run, seen_mtime, df = 0.0, None, None
    while True:
        try:
            due = time.time() >= next_run
            # Same function the dashboard button uses; only_if_stale also catches config changes from the UI.
            srv.run_forecast(days, only_if_stale=not due)
            if due:
                next_run = time.time() + update_s
            if srv._run_state["status"] == "error":
                log.error("Prognoselauf fehlgeschlagen: %s", srv._run_state["error"])

            path = srv.forecast_path()
            if pub and path.exists():
                mtime = path.stat().st_mtime
                new_run = mtime != seen_mtime
                if new_run:
                    df = metrics.load_forecast(path)
                cfg = srv.effective_config()
                tz = cfg["site"]["timezone"]
                labels = {"total": "Gesamt", **{s["name"]: s.get("label") or s["name"] for s in cfg["systems"]}}
                for group, data in metrics.compute(df, pd.Timestamp.now(tz=tz), tz).items():
                    if group == "total":
                        data["state"]["last_run"] = pd.Timestamp(mtime, unit="s", tz=tz).isoformat()
                    pub.publish(group, labels.get(group, group), data["state"], data["attributes"] if new_run else None)
                seen_mtime = mtime
                log.debug("Sensoren aktualisiert (%d Gruppen)", len(labels))
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("Scheduler-Fehler")
        time.sleep(publish_s)


def main() -> None:
    opts = json.loads(Path(os.environ.get("PV_OPTIONS", "/data/options.json")).read_text(encoding="utf-8"))
    logging.basicConfig(level=opts.get("log_level", "info").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Options become the base layer; the dashboard keeps its overrides in myconfig.json on top.
    layer = options.config_layer(opts)
    srv.base_config_path().parent.mkdir(parents=True, exist_ok=True)
    srv.base_config_path().write_text(json.dumps(layer, indent=2, ensure_ascii=False), encoding="utf-8")
    cfg = srv.effective_config()
    active = [f"{s['name']} {s['kWp']} kWp" for s in cfg["systems"] if s.get("active", True) and s["kWp"] > 0]
    log.info("Standort %.4f/%.4f %s, Anlagen: %s", cfg["site"]["latitude"], cfg["site"]["longitude"],
             cfg["site"]["timezone"], ", ".join(active) or "keine")

    pub = publish.make_publisher(opts)
    threading.Thread(target=scheduler, args=(opts, pub), daemon=True, name="scheduler").start()
    uvicorn.run(srv.app, host="0.0.0.0", port=int(os.environ.get("PV_PORT", 8099)),
                log_level=opts.get("log_level", "info"))


if __name__ == "__main__":
    main()
