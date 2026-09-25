import numpy as np
import pandas as pd
import pvlib


# ---------------------------------------------------------------
# PV-Ertrag via pvlib (Sonnenstand -> POA -> Zelltemperatur -> PVWatts DC/AC)
# ---------------------------------------------------------------
def pv_yield_from_pvlib_model(
    df: pd.DataFrame,
    latitude: float,
    longitude: float,
    kWp: float,
    surface_tilt: float = 30.0,
    surface_azimuth: float = 180.0,
    altitude: float = 35.0,
    tz: str = "Europe/Berlin",
    gamma_pdc: float = -0.004,
    eta_inv_nom: float = 0.96,
    p_inv_ac_kW: float | None = None,
    dc_ac_ratio: float = 1.0,
    dc_cable_loss: float = 0.01,
    wind_height_factor: float = 0.5,
    faiman_u0: float = 25.0,
    faiman_u1: float = 6.84,
    system_loss: float = 0.0,
    horizon_profile: dict | None = None,
    interval_h: float = 1.0,
    debug: bool = False,
) -> tuple[pd.DataFrame, float]:
    """
    Erwartet im df: date, temperature_2m, wind_speed_10m, diffuse_radiation, direct_normal_irradiance.
    Kette: Sonnenstand -> Horizontverschattung -> GHI aus DNI+DHI -> POA-Bestrahlungsstärke
           -> Zelltemperatur (Faiman) -> DC-Leistung (PVWatts) -> AC-Leistung (PVWatts-Wechselrichter).

    horizon_profile: Dict {azimuth_deg: obstruction_angle_deg} — Horizontprofil als Stützpunkte.
        Beispiel Bäume im Westen: {0: 0, 200: 0, 220: 10, 270: 15, 300: 8, 360: 0}
        DNI wird auf 0 gesetzt wenn Sonnenhöhe < interpolierter Abschattungswinkel.
    """
    location = pvlib.location.Location(
        latitude=latitude,
        longitude=longitude,
        tz=tz,
        altitude=altitude,
    )

    solar_pos = location.get_solarposition(df["date"])
    solar_pos.index = df.index  # pvlib reindexiert auf Timestamp, positional zurück angleichen

    # Horizontverschattung (z.B. Bäume, Gebäude)
    dni = df["direct_normal_irradiance"].copy()
    if horizon_profile is not None:
        azimuths = np.array(sorted(horizon_profile.keys()), dtype=float)
        angles   = np.array([horizon_profile[a] for a in sorted(horizon_profile)], dtype=float)
        obstruction = np.interp(solar_pos["azimuth"], azimuths, angles, left=angles[0], right=angles[-1])
        shaded = solar_pos["apparent_elevation"] < obstruction
        dni = dni.where(~shaded, 0.0)
        if debug:
            print(f"Verschattete Stunden: {shaded.sum()} von {len(shaded)}")

    cos_zen = np.cos(np.radians(solar_pos["apparent_zenith"])).clip(lower=0)
    ghi = (
        dni * cos_zen + df["diffuse_radiation"]
    ).clip(lower=0)

    irrad = pvlib.irradiance.get_total_irradiance(
        surface_tilt=surface_tilt,
        surface_azimuth=surface_azimuth,
        solar_zenith=solar_pos["apparent_zenith"],
        solar_azimuth=solar_pos["azimuth"],
        dni=dni,
        ghi=ghi,
        dhi=df["diffuse_radiation"],
    )
    poa_global = irrad["poa_global"].clip(lower=0)

    cell_temp = pvlib.temperature.faiman(
        poa_global=poa_global,
        temp_air=df["temperature_2m"],
        wind_speed=df["wind_speed_10m"] * wind_height_factor,
        u0=faiman_u0,
        u1=faiman_u1,
    )

    pdc0 = kWp * 1000.0  # W
    pdc0_inv = (p_inv_ac_kW * 1000.0) if p_inv_ac_kW is not None else (pdc0 / dc_ac_ratio)

    pdc = pvlib.pvsystem.pvwatts_dc(
        effective_irradiance=poa_global,
        temp_cell=cell_temp,
        pdc0=pdc0,
        gamma_pdc=gamma_pdc,
        temp_ref=25.0,
    ).clip(lower=0)
    pdc = pdc * (1.0 - dc_cable_loss)

    pac = pvlib.inverter.pvwatts(
        pdc=pdc,
        pdc0=pdc0_inv,
        eta_inv_nom=eta_inv_nom,
    ).clip(lower=0)
    pac = pac * (1.0 - system_loss)

    df = df.copy()
    df["solar_elevation"] = solar_pos["apparent_elevation"].values
    df["ghi"] = ghi
    df["poa_global"] = poa_global
    df["cell_temperature"] = cell_temp
    df["P_dc_W"] = pdc
    df["P_ac_W"] = pac
    df["pv_yield_kWh"] = pac * interval_h / 1000.0

    total_yield = df["pv_yield_kWh"].sum()

    if debug:
        print(f"Anlage {kWp:.2f} kWp | Wechselrichter {pdc0_inv/1000:.2f} kW AC | DC-Kabelverlust {dc_cable_loss*100:.1f}%")
        print(f"Faiman u0={faiman_u0} u1={faiman_u1} | wind_height_factor={wind_height_factor}")
        print(f"System-Verluste gesamt: DC-Kabel {dc_cable_loss*100:.1f}% + Sonstige {system_loss*100:.1f}%")
        print(f"Max POA: {poa_global.max():.1f} W/m²")
        print(f"Max Zelltemperatur: {cell_temp.max():.1f} °C")
        print(f"Gesamtertrag (pvlib-Modell): {total_yield:.2f} kWh")

    return df, total_yield
