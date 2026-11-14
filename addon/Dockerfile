ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

# Der Supervisor baut mit diesem Ordner als Kontext, daher wird der Code aus dem Repo geklont
# (Tag oder Branch über PV_FORECAST_REF, siehe build.yaml).
ARG PV_FORECAST_REPO=https://github.com/mofarix/pv-forecast-meter.git
ARG PV_FORECAST_REF=main

COPY constraints.txt /tmp/constraints.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates \
    && git clone --depth 1 --branch "${PV_FORECAST_REF}" "${PV_FORECAST_REPO}" /app \
    && rm -rf /app/.git /app/docs /app/tests \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -c /tmp/constraints.txt -r /app/requirements.txt \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
