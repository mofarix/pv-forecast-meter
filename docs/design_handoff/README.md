# Handoff: PV-Prognose-Dashboard für `mofarix/pv-forecast-meter`

## Overview
A browser dashboard for the pvlib/Open-Meteo pipeline in `pv-forecast-meter`. The target user is an installer or operator running 2–10 systems at one site. The UI is in German and targets desktop screens (≥ 1280 px). It has four screens:

1. **Heute**: the day's forecast, how much energy has been produced so far compared with the day's forecast, hourly weather.
2. **7 Tage**: one card per day plus an hourly detail chart.
3. **Jahr**: monthly bars (archive, forecast, expectation).
4. **Parameter**: edits `PV_SYSTEMS` and the model constants from `pv_forecast.py`. Coordinates and PV specs are visible immediately; Faiman, losses, η and the horizon profile sit in a collapsible section.

There is **no measured data**. "Bereits erzeugt" (already produced) is the forecast integrated up to the current time.

## About the Design Files
`design/` contains **design references built in HTML**: a clickable prototype showing layout, look and behaviour. It is not production code. The task is to rebuild the design in a suitable environment. Recommendation: a small Vite + React (or Svelte) frontend whose build output goes into `frontend/` and is served by the FastAPI app in `server/app.py`. Alternatively, a single static HTML/JS page is enough, because the logic is small.

To view the prototype: serve `design/` with a static server (`python -m http.server` inside `design/`) and open `PV Prognose.dc.html`. It loads `uploads/forecast.csv`. Tweaks such as the simulated time are only needed for the demo.

## Fidelity
**High-fidelity.** Colours, type, spacing and chart styles are final. Rebuild the design as closely as possible. The styling comes from the "Industry" design system (`design/_ds/…/styles.css`); adopt that file unchanged as the token and class base.

## Architecture / Integration
```
pv-forecast-meter/
  pv_forecast.py, pv_libmdl.py, weather_openmeteo.py   (existing)
  server/app.py            ← from this package
  data/config.json         ← replaces PV_SYSTEMS + constants (schema below)
  data/forecast.csv        ← written by POST /api/run
  data/archive_<year>.csv  ← cache for the annual view
  frontend/                ← build output of the dashboard
```
`uvicorn server.app:app` (requires `fastapi`, `uvicorn`, `pydantic>=2` in `requirements.txt`).

### API
| Method | Path | Response |
|---|---|---|
| GET | `/api/forecast` | CSV in exactly the format of `pv_forecast.py --output` (+ optional `temp_air_C`, `cloud_cover_pct`) |
| GET | `/api/archive?year=2026` | `{year, until, months:[{month, pv_system_1_E_kWh, …, E_total_kWh}]}` |
| GET | `/api/systems` | `Config` (schema below) |
| PUT | `/api/systems` | save the full `Config` |
| PUT | `/api/systems/{name}` | save one system (+ optional `site`) |
| POST | `/api/run?days=7` | start a new calculation in the background → `{status:"started"}` |
| GET | `/api/run` | `{status: idle|running|ok|error, started, finished, rows, error}` |

### Config schema (identical to the frontend's JSON import/export)
See `server/config.example.json`. `site` is global (matching `LATITUDE`, `LONGITUDE`, `TIMEZONE`, `SURFACE_TILT` in `pv_forecast.py`; `altitude` = the `pv_libmdl` parameter). Each `systems[]` entry = one `PV_SYSTEMS` dict plus per-system overrides for all `pv_yield_from_pvlib_model` parameters. **Losses and η are fractions (0.04) in the JSON and percentages (4) in the UI.**

### Required changes to the pipeline
1. **Config instead of constants**: `pv_forecast.py` should read `data/config.json` when present; otherwise fall back to today's constants. `predict_pv()` should take per-system overrides (`system.get("faiman_u0", FAIMAN_U0)` etc.). `server/app.py` already contains an equivalent `_predict()`.
2. **Weather columns for the dashboard**: add `cloud_cover` to the Open-Meteo request in `weather_openmeteo.py` and write `temp_air_C` and `cloud_cover_pct` into the CSV. The mockup currently only *derives* cloud cover (PV energy relative to the best day at the same hour) and uses placeholder temperatures. Replace both with the real columns.
3. **Sunrise and sunset**: calculate them in the backend with `pvlib.solarposition.sun_rise_set_transit_spa` and either deliver them in the API (`/api/forecast` as JSON with `days:[{date, sunrise, sunset}]`) or as a separate endpoint. The mockup approximates them.
4. **Archive**: the annual view currently uses placeholder monthly values. `/api/archive` computes them with `get_weather_api_archive` + `_predict`. Note: Open-Meteo archive data lag about 2 days behind.

## Screens / Views
Global: max-width 1440 px, centred, page padding 28 px, vertical gap 24 px. Background `--color-bg` #f2f2f3, text `--color-text` #1d1f20. All cards are `.blueprint` frames (1 px hairline `--color-divider`, square corners, "+" registration marks at the four corners, **no fill**).

### Header
- Row, padding 14 px 28 px, bottom border 1 px divider.
- Logo: 30×30 blueprint square with a sun icon (Lucide, stroke 1.5, accent) + "PV-PROGNOSE" (Barlow Condensed 600, 21 px, uppercase, letter-spacing .04em).
- Tabs: Heute · 7 Tage · Jahr · Parameter (Barlow Condensed 600, 16 px, uppercase). Active tab: accent colour + 2 px accent underline.
- Right-aligned: run timestamp (12 px, neutral-700) · `btn-secondary` "Import / Export" · system select (`.input`, min. 200 px): "Alle Anlagen" + one entry per system. The selection filters every screen.

### Heute
- Title row: kicker (system name · kWp) + H1 "Heute, Donnerstag, 25. September 2026" (42 px); right-aligned the as-of note.
- 4-column grid (gap 20):
  - **Already produced** (span 2): large value 64 px Condensed, "von X kWh Tagesprognose" 24 px, percentage 40 px in accent-700. Progress bar 22 px: accent fill = produced, diagonal hatching (accent, 45°, 6 px, 45 % opacity) = remaining forecast. Below it three mini KPIs: Noch erwartet / Aktuelle Leistung / Produktive Stunden übrig.
  - **Spitzenleistung** (peak power): 48 px value, time window, and below a divider kWh per system.
  - **Wetter jetzt** (current weather): 44 px icon, temperature 36 px, cloud cover; sunrise and sunset with icons; day length.
- **Hourly chart** (full width): bars for the hours ending 06:00–20:00 (15 slots). Past hours solid accent; future hours hatched with a 1 px accent outline; the current hour is hatched with a partial solid fill. Dashed "Jetzt HH:MM" line (accent-900). Hover highlights the slot (accent-100) and updates the readout at the top right: "12–13 Uhr · 14,2 kW Ø · 14,17 kWh · 34 % Wolken · 18 °C". Y axis has 5 ticks with "nice" rounding. Below it a weather strip aligned to the same 15 slots: icon 20 px, temperature, cloud %.

### 7 Tage
- Title "7-Tage-Prognose · 25.9. – 1.10." + right-aligned the total kWh.
- 7-column grid of day cards: weekday (24 px Condensed) + date, "Heute" tag; weather icon 36 px, max/min temperature, cloud cover; sparkline (area accent-200 + line accent 1.5 px, same scale across all days); kWh 34 px; "Spitze X kW · SA–SU". Clicking selects the day (2 px accent outline).
- Below it the hourly chart for the selected day (same as Heute, without the now line, everything hatched).

### Jahr
- Year switch `.seg` 2025 | 2026.
- 4 KPI cards. For 2026: Ertrag bis heute (yield to date) · Prognose 7 Tage · Hochrechnung Jahresende (year-end projection) · Spezifischer Ertrag (kWh/kWp). For past years: Jahresertrag · Spezifischer Ertrag · Stärkster Monat · Tagesmittel.
- Monthly chart, 12 slots: archive = solid accent; forecast = hatched, stacked on top; expectation for current/future months = dashed neutral-600 outline at full height. Value label above each bar ("≈ 1.275" for expectation only). Month abbreviations below; the current month gets a "laufend" tag (`tag-outline`).

### Parameter
- Two columns: 300 px system list | editor.
- List: blueprint cards (name 18 px Condensed, "15,0 kWp · Azimut 135° · WR 12,5 kW", ID in monospace). Selected = 2 px accent outline.
- Editor: H2 name + ID; "Ungespeicherte Änderungen" tag when anything has changed.
  - Groups (h6 uppercase, neutral-700) in a 4-column field grid; each field `.field` + `.input` plus the **Python name** below it (11 px monospace, neutral-600):
    - Bezeichnung: `name` (span 2)
    - Standort · gilt für alle Anlagen: `LATITUDE`, `LONGITUDE`, `altitude`, `TIMEZONE`
    - PV-Generator: `kWp`, `surface_azimuth`, `p_inv_ac_kW`, `SURFACE_TILT` (global)
  - **Collapsible** "Erweiterte Modellparameter" (chevron rotates 90°; on the right "Verluste gesamt inkl. WR X %" = 1 − (1−DC)(1−System)·η):
    - Faiman: `FAIMAN_U0`, `FAIMAN_U1`, `WIND_HEIGHT_FACTOR`, `gamma_pdc`
    - Verluste & Wirkungsgrad: `DC_CABLE_LOSS` %, `SYSTEM_LOSS` %, `ETA_INVERTER` %, `dc_ac_ratio`
    - Horizontverschattung: `horizon_profile` as the text "0:0, 200:0, 220:10, 270:15" (span 4)
  - Footer: last run · "Als JSON anzeigen" (ghost) · "Verwerfen" (secondary) · "Speichern & neu berechnen" (primary, solid accent, with corner marks).
- Saving copies the site fields to all systems → `PUT /api/systems` → `POST /api/run` → poll `GET /api/run` until `ok` → reload the forecast. Show an error state if `status=error`.

### Import / Export dialog
`.dialog-backdrop` + dialog (760 px, **background `--color-bg`**, because the DS dialog is transparent). Two columns:
- Import: Prognose-CSV (replaces the data shown; columns are detected by `*_P_ac_W` / `*_E_kWh`) · Parameter-JSON (Config schema).
- Export: Stundenwerte CSV (current selection) · Tageswerte CSV (`date, <sys>_E_kWh…, E_total_kWh, P_peak_kW`) · Monatswerte CSV (`month, archive_kWh, forecast_kWh, expected_kWh`) · Parameter JSON (all systems).
- Status tag after the action ("… heruntergeladen", "Fehler: …").

## Interactions & Behavior
- Timestamp convention: a CSV value at `HH:00` = energy for the interval ending at HH, i.e. (HH−1)–HH. Labels show the end time; the readout shows "HH−1–HH Uhr".
- Already produced = Σ E(h) for h ≤ now + E(current hour) × elapsed fraction.
- Hover on the chart only, no animation. The chart scale is recalculated per selection.
- Loading state: blueprint box "Daten werden geladen …". Error state: same box with the error text.
- Number formatting `de-DE` (decimal comma, thousands point). kWh < 100 with 1 decimal place, otherwise 0.

## State Management
`screen`, `selectedSystem ('all'|id)`, `hoverHour`, `selectedDay`, `year`, `forecast (parsed)`, `config`, `draft (edited System)`, `advancedOpen`, `runStatus`, `ioDialogOpen`, `ioMessage`.
Fetches: `/api/forecast` on start and after a run; `/api/archive?year` when the Jahr tab opens or the year changes; `/api/systems` on start.

## Design Tokens (from styles.css)
- Colours: bg #f2f2f3 · surface #e9e9ea · text #1d1f20 · accent #5980a6 · accent ramp 100 #eef6ff, 200 #d6ebff, 600 #597ea3, 700 #416180, 900 #1d2d3d · neutral 600 #7a7a7d, 700 #5d5d60 · divider = text at 16 %.
- Type: headings Barlow Condensed 600 (H1 42, H2 32, H3 25, H6 13 uppercase .08em); body Barlow 400/500, 15 px / 1.55; small 11–13 px.
- Spacing: 3.4 / 6.8 / 10.2 / 13.6 / 20.4 / 27.2 px; layout gaps 16–28 px.
- Radius: 0 everywhere (blueprint). Shadow only on the dialog (`--shadow-lg`).
- Hatching: `repeating-linear-gradient(45deg, accent 0 1.5px, transparent 1.5px 5px)` or an SVG pattern of 6 px rotated 45°.

## Assets
- Icons: Lucide (sun, cloud-sun, cloud, cloud-rain, moon, sunrise, sunset, chevron-right, download, upload, arrow-up-down), stroke-width 1.5. See `design/WxIcon.dc.html`.
- Fonts: Google Fonts Barlow + Barlow Condensed (imported in styles.css).
- No images.

## Files
- `design/PV Prognose.dc.html`: the complete prototype (template + logic class; the logic contains CSV parsing, chart calculations and JSON mapping and can be reused as reference code).
- `design/WxIcon.dc.html`: weather icons.
- `design/_ds/…/styles.css`: design-system tokens and classes.
- `design/uploads/forecast.csv`: sample output from `pv_forecast.py`.
- `server/app.py`: FastAPI scaffold (endpoints, Pydantic schema, background run, archive cache).
- `server/config.example.json`: config matching the current `PV_SYSTEMS` values.
