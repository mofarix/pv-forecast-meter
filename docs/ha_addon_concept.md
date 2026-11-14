# Konzept: Home-Assistant-Add-on für pv-forecast-meter

Stand: 14.11.2026 · Status: Entwurf umgesetzt, noch ungetestet gegen eine HA-Instanz

## Was die Pipeline heute tut

```
Open-Meteo (stündlich: Temperatur, Wind, DHI, DNI, Bewölkung)
   └─ weather_openmeteo.py          → DataFrame
        └─ pv_libmdl.py (pvlib)     Sonnenstand → Horizont → POA → Faiman → PVWatts DC/AC
             └─ pv_forecast.py      Config-Resolver (Code < config.json < myconfig.json), predict_pv()
                  ├─ CLI            → CSV (time, <sys>_P_ac_W, <sys>_E_kWh, Totale, Wetter)
                  └─ server/app.py  → REST-API + statisches Dashboard (frontend/)
```

Der ganze Zustand sind drei Dateien in `data/`: `config.json` (Basis), `myconfig.json` (Overrides aus dem
Dashboard), `forecast.csv` (letzter Lauf). Das Add-on muss nichts davon umbauen, nur verpacken, zeitgesteuert
rechnen und die Ergebnisse in HA hineinreichen.

## Anforderungen an ein HA-Add-on (Kurzfassung)

| Thema | Anforderung | Umsetzung hier |
|---|---|---|
| Repository | `repository.yaml` im Git-Root, je Add-on ein Ordner mit `config.yaml`, `Dockerfile`, `DOCS.md` | `repository.yaml`, `addon/` |
| Build | Supervisor baut mit dem Add-on-Ordner als Docker-Kontext (kein Zugriff auf `..`) | Dockerfile klont das Repo (Build-Arg `PV_FORECAST_REF`, Tag oder Branch) |
| Basisimage | `ghcr.io/home-assistant/{arch}-base-*` mit s6-overlay, `init: false` | Debian bookworm + venv, damit numpy/pandas als Wheels kommen (musl/Alpine kompiliert sonst ewig) |
| Architekturen | pandas liefert keine armv7-Wheels | nur `amd64`, `aarch64` |
| Optionen | `options` + `schema` in `config.yaml`, Werte landen in `/data/options.json` | Standort + Anlagenliste + Intervalle + Publish-Modus |
| Persistenz | nur `/data` überlebt Updates | `PV_DATA_DIR=/data/pv` |
| Web-UI | Ingress: HA proxyt auf `ingress_port`, Pfad-Präfix `/api/hassio_ingress/<token>/` | Frontend nutzt relative URLs (`api/...` statt `/api/...`), Redirect relativ |
| HA-Zugriff | `SUPERVISOR_TOKEN`, `http://supervisor/core/api/...`, `http://supervisor/services/mqtt` | Home-Koordinaten als Standort-Fallback, MQTT-Zugangsdaten vom Mosquitto-Add-on |
| Entities | Add-ons können keine Integration registrieren; Sensoren kommen über MQTT-Discovery (sauber, mit Gerät, persistent) oder die Core-REST-API (ohne Registry, weg nach HA-Neustart) | MQTT bevorzugt, REST als Fallback, `publish: none` möglich |

## Entscheidung: bestehendes Dashboard einbinden oder HA-Bordmittel?

**Beides, weil das Dashboard fast nichts kostet.** Es ist eine statische Seite, die der FastAPI-Server ohnehin
ausliefert. Über Ingress erscheint es als Seitenleisten-Panel; einzige Änderung sind relative API-Pfade. Damit
bleibt der Parameter-Editor (Faiman, Verluste, Horizontprofil, Archiv-Jahresansicht) erhalten, den man in HA
nur mit viel Aufwand nachbauen würde.

HA-native geht, was HA besser kann: Sensoren für Automationen (Wallbox/Wärmepumpe nach `energie_rest_heute`),
History, Energie-Karten, Benachrichtigungen. Die Stundenreihe liegt als Attribut auf dem Prognose-Sensor, damit
ApexCharts & Co. sie zeichnen können.

Nicht erreichbar mit einem Add-on: die Solarprognose-Kurve im **Energy-Dashboard**. Die verlangt eine
Integration mit `energy`-Plattform. Das wäre Schritt 2 (kleine HACS-Integration, die die Add-on-API abfragt).

## Parameterfluss

```
HA Add-on-Optionen (/data/options.json)
   └─ pv_ha/options.py → /data/pv/config.json      (Basis-Layer, bei jedem Start neu erzeugt)
        └─ Dashboard-Editor → /data/pv/myconfig.json (nur Abweichungen vom Basis-Layer)
             └─ pv_forecast.resolve_config()        (Code-Defaults < config.json < myconfig.json)
```

- Leere `site`-Optionen → Standort aus der HA-Konfiguration (Koordinaten, Höhe, Zeitzone).
- Code-Defaults (`PV_SYSTEMS`), die nicht in den Optionen stehen, werden deaktiviert, sonst rechnet
  Kaiserstuhl heimlich mit.
- Das Dashboard speichert nur noch Abweichungen (Diff gegen den Basis-Layer). Dadurch wirken spätere
  Optionsänderungen in HA weiter, solange derselbe Wert nicht im Dashboard bewusst geändert wurde.
  "Verwerfen"/Overrides löschen setzt zurück auf die Optionen.

## Laufzeit im Container

```
python -m pv_ha
 ├─ Optionen lesen, Basis-Layer schreiben, Publisher wählen (MQTT > REST > keiner)
 ├─ Thread: alle update_interval_min Prognose rechnen (server.app.run_forecast, gleiche Funktion wie der Button im Dashboard)
 │         alle publish_interval_min Kennzahlen aus forecast.csv für "jetzt" ableiten und publizieren
 └─ uvicorn server.app auf 0.0.0.0:8099 (Ingress; optional als Host-Port freigebbar für REST-Sensoren)
```

Die Kennzahlen (`pv_ha/metrics.py`) sind reine Funktionen über dem CSV: Leistung jetzt (zwischen
Intervallmitten interpoliert), Energie heute / bisher / Rest, nächste Stunde, nächste 24 h, morgen,
Prognosezeitraum, Spitze heute mit Uhrzeit, plus Stunden- und Tagesreihe als Attribute. Je Anlage und `Gesamt`.

## Beim ersten Test prüfen

1. `schema`-Syntax in `config.yaml` (`float(0,)`, `match(...)`, leeres `site: {}`) wird vom Supervisor akzeptiert.
2. Ingress: Panel lädt, `api/...` löst korrekt auf, Speichern & Neuberechnen funktioniert durch den Proxy.
3. MQTT-Discovery: Gerät "PV-Prognose" mit Entities `sensor.pv_prognose_<anlage>_<kennzahl>` erscheint;
   `peak_time_today` wird als Timestamp akzeptiert.
4. Build-Zeit auf aarch64 (Wheels für numpy/pandas/uvloop müssen gezogen werden, nicht kompiliert).
5. Erststart ohne Internet: Lauf scheitert sauber, Panel zeigt den Fehler, Scheduler versucht es weiter.
