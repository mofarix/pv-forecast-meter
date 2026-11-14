#!/usr/bin/with-contenv bashio
# Nur /data ist persistent; API und Dashboard lauschen auf dem Ingress-Port.
export PV_DATA_DIR=/data/pv
export PV_OPTIONS=/data/options.json
export PV_PORT=8099
export PV_FORECAST_DAYS="$(bashio::config 'forecast_days')"

bashio::log.info "Starte PV-Prognose (Daten in ${PV_DATA_DIR})"
cd /app
exec python3 -m pv_ha
