# PV-Prognose (Home Assistant Add-on)

Stündliche PV-Leistungsprognose für mehrere Anlagen an einem Standort, gerechnet mit
[pvlib](https://pvlib-python.readthedocs.io/) aus [Open-Meteo](https://open-meteo.com/)-Wetterdaten.

- Sensoren je Anlage und gesamt (MQTT-Discovery oder REST-API): Leistung jetzt, Energie heute / bisher / Rest,
  nächste Stunde, nächste 24 h, morgen, Spitze heute, Stundenreihe als Attribut.
- Dashboard als Seitenleisten-Panel (Ingress) mit Parameter-Editor und Jahresansicht.
- REST-API unter `api/*` (optional auch als Host-Port).

Details in [DOCS.md](DOCS.md). Quellcode: https://github.com/mofarix/pv-forecast-meter
