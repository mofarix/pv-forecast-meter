# PV-Prognose

## Installation

1. **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**:
   `https://github.com/mofarix/pv-forecast-meter` hinzufügen.
2. "PV-Prognose" installieren, Optionen setzen (mindestens die Anlagen), starten.
3. Optional das Mosquitto-Add-on und die MQTT-Integration einrichten; dann erscheinen die Sensoren
   automatisch als Gerät **PV-Prognose**.

Der erste Lauf braucht Internet (Open-Meteo). Danach wird alle `update_interval_min` neu gerechnet.

## Optionen

```yaml
site:                       # leer lassen = Standort aus Home Assistant
  latitude: 48.1
  longitude: 7.67
  altitude: 200
  timezone: Europe/Berlin
  surface_tilt: 30          # Modulneigung, gilt für alle Anlagen
systems:
  - name: dach_so           # Spaltenpräfix in der CSV, nur A–Z a–z 0–9 _
    label: Dach Südost      # Anzeigename
    kWp: 15
    surface_azimuth: 135    # 180 = Süd, 90 = Ost, 270 = West
    p_inv_ac_kW: 12.5       # Wechselrichter AC; fehlt er, gilt kWp / dc_ac_ratio
    # optional, sonst Modell-Defaults:
    # faiman_u0: 20, faiman_u1: 5, wind_height_factor: 0.8, gamma_pdc: -0.004
    # dc_cable_loss: 0.04, system_loss: 0.14, eta_inv_nom: 0.96, dc_ac_ratio: 1.0
    # horizon_profile: "0:0, 200:0, 220:10, 270:15, 300:8, 360:0"   # Azimut:Abschattungswinkel
forecast_days: 7
update_interval_min: 60     # Prognose neu rechnen
publish_interval_min: 5     # Sensoren aktualisieren (Leistung jetzt, Energie bisher …)
publish: auto               # auto | mqtt | rest | none
log_level: info
```

Verluste und Wirkungsgrad sind Anteile (0.04 = 4 %). Die Optionen bilden den Basis-Layer; was im Dashboard
unter **Parameter** geändert wird, liegt als Abweichung darüber und bleibt auch nach Optionsänderungen
bestehen, bis es dort zurückgesetzt wird.

## Sensoren

Je Anlage (`<label>`) und für `Gesamt`, bei MQTT unter dem Gerät "PV-Prognose":

| Kennzahl | Einheit | Bedeutung |
|---|---|---|
| Leistung jetzt | W | zwischen den Stundenmitteln interpoliert |
| Energie heute | kWh | Tagesprognose |
| Energie bisher heute | kWh | Prognose bis jetzt aufintegriert (keine Messung) |
| Energie Rest heute | kWh | heute − bisher |
| Energie nächste Stunde / nächste 24 h | kWh | ab jetzt |
| Energie morgen | kWh | |
| Energie Prognosezeitraum | kWh | ab heute bis Horizont; Attribute `hours` (Stundenreihe), `days` |
| Spitzenleistung heute / Spitzenzeit heute | W / Zeitstempel | Beginn der stärksten Stunde |
| Lufttemperatur, Bewölkung (nur Gesamt) | °C / % | Open-Meteo, jetzt |
| Letzter Lauf (nur Gesamt, Diagnose) | Zeitstempel | |

Entity-IDs werden von HA aus dem Namen gebildet, z. B. `sensor.pv_prognose_gesamt_energie_rest_heute`.

**MQTT** (empfohlen): persistente Entities mit Gerät, konfigurierbar in der UI.
**REST** (`publish: rest` oder kein Broker): Zustände werden über die Core-API gesetzt. Sie haben keinen
Registry-Eintrag und sind nach einem HA-Neustart bis zum nächsten Intervall `unavailable`.

### Beispiel: Stundenreihe als Karte (ApexCharts)

```yaml
type: custom:apexcharts-card
header: { show: true, title: PV-Prognose }
graph_span: 48h
span: { start: day }
series:
  - entity: sensor.pv_prognose_gesamt_energie_prognosezeitraum
    name: Prognose
    type: column
    data_generator: |
      return entity.attributes.hours.map(h => [new Date(h.start).getTime(), h.power_w / 1000]);
```

### Beispiel: Automation

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.pv_prognose_gesamt_energie_rest_heute
    above: 20
```

## Dashboard und API

Das Panel **PV-Prognose** in der Seitenleiste zeigt Heute, 7 Tage, Jahr (Open-Meteo-Archiv) und den
Parameter-Editor. "Speichern & neu berechnen" wirkt sofort auf die Sensoren.

Wird Port 8099 in den Add-on-Netzwerkeinstellungen freigegeben, ist die API auch direkt erreichbar,
z. B. für einen REST-Sensor oder andere Systeme:

| Pfad | Inhalt |
|---|---|
| `GET /api/forecast` | Stundenwerte als CSV |
| `GET /api/systems` | wirksame Konfiguration |
| `POST /api/run` | Neuberechnung starten |
| `GET /api/archive?year=2025` | Monatsenergie aus dem Open-Meteo-Archiv |
| `GET /docs` | interaktive API-Doku |

## Grenzen

- Keine Messwerte: "Energie bisher heute" ist die aufintegrierte Prognose.
- Die Solarprognose-Kurve im **Energy-Dashboard** braucht eine Integration, kein Add-on. Geplant als
  nächster Schritt.
- Architekturen: `amd64`, `aarch64` (pandas liefert keine 32-bit-ARM-Wheels).

## Daten im Container

`/data/pv/`: `config.json` (aus den Optionen erzeugt), `myconfig.json` (Dashboard-Abweichungen),
`forecast.csv`, `archive_<jahr>.csv`.
