"""Push metrics into Home Assistant: MQTT discovery (preferred) or the Core REST API as fallback."""

from __future__ import annotations

import json
import logging
import os

import requests

from pv_ha import PROJECT_URL, VERSION
from pv_ha.options import SUPERVISOR, supervisor_get

log = logging.getLogger(__name__)
DEVICE_NAME = "PV-Prognose"

# One HA sensor per entry and group. 'total_only' entries exist once, 'attributes' carries the hourly series.
SENSORS = [
    dict(key="power_now", name="Leistung jetzt", unit="W", device_class="power", state_class="measurement"),
    dict(key="energy_today", name="Energie heute", unit="kWh", device_class="energy"),
    dict(key="energy_produced", name="Energie bisher heute", unit="kWh", device_class="energy"),
    dict(key="energy_remaining", name="Energie Rest heute", unit="kWh", device_class="energy"),
    dict(key="energy_next_hour", name="Energie nächste Stunde", unit="kWh", device_class="energy"),
    dict(key="energy_next_24h", name="Energie nächste 24 h", unit="kWh", device_class="energy"),
    dict(key="energy_tomorrow", name="Energie morgen", unit="kWh", device_class="energy"),
    dict(key="energy_forecast", name="Energie Prognosezeitraum", unit="kWh", device_class="energy", attributes=True),
    dict(key="peak_power_today", name="Spitzenleistung heute", unit="W", device_class="power"),
    dict(key="peak_time_today", name="Spitzenzeit heute", device_class="timestamp"),
    dict(key="temp_air", name="Lufttemperatur", unit="°C", device_class="temperature", state_class="measurement",
         total_only=True),
    dict(key="cloud_cover", name="Bewölkung", unit="%", icon="mdi:weather-cloudy", total_only=True),
    dict(key="last_run", name="Letzter Lauf", device_class="timestamp", entity_category="diagnostic", total_only=True),
]


def _sensors(group: str):
    return [s for s in SENSORS if group == "total" or not s.get("total_only")]


def mqtt_broker(opts: dict) -> dict | None:
    """Broker from the options, else from the Mosquitto add-on via the Supervisor services API."""
    if opts.get("mqtt_host"):
        return {"host": opts["mqtt_host"], "port": opts.get("mqtt_port") or 1883,
                "username": opts.get("mqtt_user"), "password": opts.get("mqtt_password")}
    info = supervisor_get("/services/mqtt")
    return info["data"] if info and info.get("result") == "ok" else None


class MqttPublisher:
    """Retained discovery config per entity, one JSON state topic per group."""

    def __init__(self, broker: dict, prefix: str = "pv_forecast"):
        import paho.mqtt.client as mqtt

        self.prefix = prefix
        self.announced: set[str] = set()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{prefix}_addon")
        if broker.get("username"):
            self.client.username_pw_set(broker["username"], broker.get("password"))
        self.client.will_set(f"{prefix}/status", "offline", retain=True)
        self.client.connect(broker["host"], int(broker.get("port") or 1883), keepalive=60)
        self.client.loop_start()
        self._pub(f"{prefix}/status", "online")
        log.info("MQTT-Publisher verbunden mit %s:%s", broker["host"], broker.get("port"))

    def _pub(self, topic: str, payload) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, text, retain=True)

    def _discovery(self, group: str, label: str) -> None:
        device = {"identifiers": [f"{self.prefix}_meter"], "name": DEVICE_NAME, "manufacturer": "mofarix",
                  "model": "pv-forecast-meter", "sw_version": VERSION}
        for s in _sensors(group):
            uid = f"{self.prefix}_{group}_{s['key']}".lower()
            template = f"{{{{ value_json.{s['key']} }}}}"
            if s.get("device_class") == "timestamp":  # null must not render as the string "None"
                template = f"{{{{ value_json.{s['key']} or '' }}}}"
            cfg = {
                "unique_id": uid, "name": f"{label} {s['name']}",
                "state_topic": f"{self.prefix}/{group}/state", "value_template": template,
                "availability_topic": f"{self.prefix}/status", "device": device,
                "origin": {"name": "pv-forecast-meter", "sw_version": VERSION, "support_url": PROJECT_URL},
            }
            for key, ha_key in (("unit", "unit_of_measurement"), ("device_class", "device_class"),
                                ("state_class", "state_class"), ("icon", "icon"), ("entity_category", "entity_category")):
                if s.get(key):
                    cfg[ha_key] = s[key]
            if s.get("attributes"):
                cfg["json_attributes_topic"] = f"{self.prefix}/{group}/attributes"
            self._pub(f"homeassistant/sensor/{uid}/config", cfg)

    def publish(self, group: str, label: str, state: dict, attributes: dict | None = None) -> None:
        if group not in self.announced:
            self._discovery(group, label)
            self.announced.add(group)
        self._pub(f"{self.prefix}/{group}/state", state)
        if attributes is not None:  # only after a new run, keeps the recorder small
            self._pub(f"{self.prefix}/{group}/attributes", attributes)


class RestPublisher:
    """Core REST API fallback: states without registry entry (no device, gone after an HA restart)."""

    def __init__(self):
        self.headers = {"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}"}
        self.attributes: dict[str, dict] = {}
        log.info("REST-Publisher aktiv (kein MQTT-Broker gefunden)")

    def publish(self, group: str, label: str, state: dict, attributes: dict | None = None) -> None:
        if attributes is not None:
            self.attributes[group] = attributes
        for s in _sensors(group):
            value = state.get(s["key"])
            if value is None:
                continue
            attrs = {"friendly_name": f"{DEVICE_NAME} {label} {s['name']}"}
            for key, ha_key in (("unit", "unit_of_measurement"), ("device_class", "device_class"),
                                ("state_class", "state_class"), ("icon", "icon")):
                if s.get(key):
                    attrs[ha_key] = s[key]
            if s.get("attributes"):
                attrs.update(self.attributes.get(group, {}))
            entity = f"sensor.pv_prognose_{group}_{s['key']}".lower()
            try:
                requests.post(f"{SUPERVISOR}/core/api/states/{entity}", headers=self.headers,
                              json={"state": value, "attributes": attrs}, timeout=10).raise_for_status()
            except requests.RequestException as exc:
                log.warning("REST %s: %s", entity, exc)


def make_publisher(opts: dict):
    """publish: auto -> MQTT if a broker is known, else REST; mqtt/rest force one; none disables."""
    mode = opts.get("publish", "auto")
    if mode == "none":
        return None
    if mode in ("auto", "mqtt"):
        broker = mqtt_broker(opts)
        if broker:
            try:
                return MqttPublisher(broker)
            except Exception as exc:  # noqa: BLE001 - fall through to REST
                log.error("MQTT-Verbindung fehlgeschlagen: %s", exc)
        if mode == "mqtt":
            log.error("publish: mqtt gesetzt, aber kein Broker erreichbar - keine Sensoren")
            return None
    if os.environ.get("SUPERVISOR_TOKEN"):
        return RestPublisher()
    log.warning("Kein SUPERVISOR_TOKEN - es werden keine Sensoren veröffentlicht")
    return None
