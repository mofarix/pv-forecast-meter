# python-pv-forecast

Standalone PV power forecast for the Kaiserstuhl region (Baden-Württemberg), built on [pvlib](https://pvlib-python.readthedocs.io/) and the [Open-Meteo](https://open-meteo.com/) weather API.

It fetches hourly weather forecasts, simulates each configured PV system and reports AC power and hourly energy per system and in total.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
python pv_forecast.py                          # 7-day forecast, printed to the console
python pv_forecast.py --output forecast.csv    # write results to CSV
python pv_forecast.py --forecast-days 3 --past-days 1
```

| Option            | Default         | Description                  |
|-------------------|-----------------|------------------------------|
| `--latitude`      | 48.1000         | Site latitude (Kaiserstuhl)  |
| `--longitude`     | 7.6700          | Site longitude (Kaiserstuhl) |
| `--forecast-days` | 7               | Days to forecast             |
| `--past-days`     | 0               | Past days to include         |
| `--timezone`      | Europe/Berlin   | Output timezone              |
| `--output`        | –               | CSV file for the results     |

## Output

One row per hour with these columns:

- `time` – timestamp in the selected timezone
- `<system>_P_ac_W` / `<system>_E_kWh` – AC power and hourly energy for each active system
- `P_total_ac_W` / `E_total_kWh` – sum over all active systems

After the table, the script prints the peak total power and the total forecast energy.

## Configuration

PV systems are defined in `PV_SYSTEMS` in [pv_forecast.py](pv_forecast.py). Each entry has a name, peak power (`kWp`), azimuth (180° = south), inverter AC limit and an `active` flag; inactive systems are skipped.

Current setup:

| System        | kWp | Azimuth           | Inverter AC | Active |
|---------------|-----|-------------------|-------------|--------|
| `pv_system_1` | 15  | 135° (south-east) | 12.5 kW     | yes    |
| `pv_system_2` | 15  | 225° (south-west) | 12.5 kW     | yes    |
| `pv_system_3` | –   | –                 | –           | no     |
| `pv_system_4` | –   | –                 | –           | no     |

Shared model parameters are constants at the top of the same file:

| Parameter            | Value  |
|----------------------|--------|
| Module tilt          | 30°    |
| DC cable loss        | 4 %    |
| System loss          | 14 %   |
| Inverter efficiency  | 96 %   |
| Faiman U0 / U1       | 20 / 5 |
| Wind height factor   | 0.8    |

## Files

- [pv_forecast.py](pv_forecast.py) – CLI entry point and system configuration
- [pv_libmdl.py](pv_libmdl.py) – pvlib model chain producing AC power and yield
- [weather_openmeteo.py](weather_openmeteo.py) – Open-Meteo forecast/archive client with request caching
