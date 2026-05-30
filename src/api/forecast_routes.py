"""
Generation Forecast API
========================
D+1 solar generation forecast using NASA POWER irradiance data
with Ineichen-Perez clear-sky model and temperature derating.

GET /api/forecast/generation      — 48-slot D+1 forecast (all DTs)
GET /api/forecast/generation/{dt_id} — per-DT forecast
GET /api/forecast/irradiance      — raw GHI forecast (hourly)
POST /api/forecast/refresh        — force refresh from NASA POWER API
"""
import math
import logging
import random
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/forecast", tags=["Generation Forecast"])

# ── Cache ────────────────────────────────────────────────────────────────────
_forecast_cache: dict = {}        # dt_id → [48 blocks]
_irradiance_cache: list = []      # 24 hourly GHI values (W/m²)
_forecast_date: Optional[str] = None
_last_fetched: Optional[str] = None

# Lanka Feeder location (Varanasi)
LAT = 25.27
LON = 82.99


def _ineichen_perez_ghi(hour_ist: float, month: int = None) -> float:
    """
    Simplified Ineichen-Perez clear-sky GHI model.
    Returns GHI in W/m² for a given IST hour.
    """
    if hour_ist < 6 or hour_ist > 18:
        return 0.0
    month = month or datetime.now().month
    # Declination angle (simplified)
    day_of_year = (month - 1) * 30 + 15
    decl = math.radians(23.45 * math.sin(math.radians(360 / 365 * (day_of_year - 81))))
    lat_rad = math.radians(LAT)
    hour_angle = math.radians((hour_ist - 12) * 15)
    cos_zenith = (
        math.sin(lat_rad) * math.sin(decl)
        + math.cos(lat_rad) * math.cos(decl) * math.cos(hour_angle)
    )
    cos_zenith = max(0.0, cos_zenith)
    # Clear-sky GHI (simplified — Ineichen-Perez uses Linke turbidity)
    am = 1 / (cos_zenith + 0.50572 * (96.07995 - math.degrees(math.acos(cos_zenith))) ** -1.6364) if cos_zenith > 0.01 else 99
    am = min(am, 20)
    linke = 3.5  # Varanasi average Linke turbidity
    ghi = 1353 * cos_zenith * math.exp(-0.09 * linke * am)
    return round(max(0, ghi), 1)


def _temperature_derating(temp_c: float, stc_temp: float = 25.0, gamma: float = -0.0037) -> float:
    """Panel temperature derating factor. gamma = -0.37%/°C typical."""
    return 1 + gamma * (temp_c - stc_temp)


def _simulate_nasa_power() -> list:
    """
    Simulate NASA POWER API response (hourly GHI for Varanasi).
    In production: calls https://power.larc.nasa.gov/api/temporal/hourly/point
    Returns list of 24 hourly GHI values (W/m²).
    """
    now = datetime.now(timezone.utc)
    month = now.month
    # Add realistic daily variability
    cloud_factor = random.uniform(0.75, 0.95)
    hourly_ghi = []
    for h in range(24):
        ghi = _ineichen_perez_ghi(h, month) * cloud_factor
        # Add small noise
        ghi *= random.uniform(0.90, 1.05)
        hourly_ghi.append(round(max(0, ghi), 1))
    return hourly_ghi


async def _fetch_nasa_power() -> list:
    """
    Attempt real NASA POWER API call; fall back to simulation on error.
    NASA endpoint: https://power.larc.nasa.gov/api/temporal/hourly/point
    """
    import httpx
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1))
    date_str = tomorrow.strftime("%Y%m%d")
    url = (
        f"https://power.larc.nasa.gov/api/temporal/hourly/point"
        f"?parameters=ALLSKY_SFC_SW_DWN,T2M"
        f"&community=RE&longitude={LON}&latitude={LAT}"
        f"&start={date_str}&end={date_str}&format=JSON"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                ghi_data = data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
                hourly = []
                for h in range(24):
                    key = f"{date_str}{h:02d}30"
                    val = ghi_data.get(key, 0)
                    # NASA POWER returns kWh/m²/h; convert to W/m² (× 1000)
                    hourly.append(round(max(0, val * 1000), 1))
                logger.info("NASA POWER API: forecast fetched successfully")
                return hourly
    except Exception as e:
        logger.warning(f"NASA POWER API call failed ({e}); using simulation")
    return _simulate_nasa_power()


def _build_dt_forecast(dt: dict, hourly_ghi: list, ders: list) -> list:
    """Build 48 × 30-min generation forecast blocks for one DT."""
    month = datetime.now().month
    avg_temp_c = 28 + 8 * math.sin(math.pi * (month - 3) / 6)  # seasonal estimate
    derating = _temperature_derating(avg_temp_c)
    total_nameplate = sum(d.get("nameplate_kw", 0) for d in ders)
    performance_ratio = 0.78  # inverter + wiring + soiling losses

    blocks = []
    for b in range(48):
        hour_ist = b * 0.5
        hour_idx = int(hour_ist)
        frac = hour_ist - hour_idx
        # Interpolate GHI between hourly values
        ghi_h = hourly_ghi[hour_idx % 24]
        ghi_next = hourly_ghi[(hour_idx + 1) % 24]
        ghi = ghi_h + frac * (ghi_next - ghi_h)
        ghi = max(0, ghi)

        # Convert GHI to generation: P = GHI/1000 × nameplate × PR × derating
        forecast_kw = round(total_nameplate * (ghi / 1000) * performance_ratio * derating, 2)
        clear_sky_ghi = _ineichen_perez_ghi(hour_ist, month)
        clear_sky_kw = round(total_nameplate * (clear_sky_ghi / 1000) * performance_ratio, 2)

        per_der = []
        for der in ders:
            nameplate = der.get("nameplate_kw", 0)
            prop = nameplate / total_nameplate if total_nameplate > 0 else 0
            per_der.append({
                "der_id": der["der_id"],
                "forecast_kw": round(forecast_kw * prop, 2),
                "clear_sky_kw": round(clear_sky_kw * prop, 2),
            })

        blocks.append({
            "block": b,
            "time": f"{int(hour_ist):02d}:{int((hour_ist % 1) * 60):02d}",
            "ghi_wm2": round(ghi, 1),
            "clear_sky_ghi": round(clear_sky_ghi, 1),
            "forecast_kw": forecast_kw,
            "clear_sky_kw": clear_sky_kw,
            "performance_ratio": performance_ratio,
            "temp_derating": round(derating, 4),
            "ders": per_der,
        })
    return blocks


async def _refresh_forecast():
    global _forecast_cache, _irradiance_cache, _forecast_date, _last_fetched
    import src.derms.fleet as fleet
    from src.data.real_pilot_data import LANKA_DTS

    hourly_ghi = await _fetch_nasa_power()
    _irradiance_cache = hourly_ghi

    ders = fleet.get_all_ders()
    dt_ders: dict = {}
    for der in ders:
        dt_id = der.get("dt_id")
        if dt_id:
            dt_ders.setdefault(dt_id, []).append(der)

    lk1_dt_map = {d["id"]: d for d in LANKA_DTS}
    dts = fleet.get_all_dts()

    for dt in dts:
        dt_id = dt["dt_id"]
        if dt_id not in dt_ders:
            continue
        dt_data = {**dt, **lk1_dt_map.get(dt_id, {})}
        _forecast_cache[dt_id] = _build_dt_forecast(dt_data, hourly_ghi, dt_ders[dt_id])

    _forecast_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    _last_fetched = datetime.now(timezone.utc).isoformat()
    logger.info(f"Generation forecast refreshed: {len(_forecast_cache)} DTs, date={_forecast_date}")


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/generation")
async def get_generation_forecast():
    """D+1 generation forecast for all DTs (48 × 30-min blocks)."""
    if not _forecast_cache:
        await _refresh_forecast()
    total_kw_per_block = {}
    for blocks in _forecast_cache.values():
        for blk in blocks:
            b = blk["block"]
            total_kw_per_block[b] = total_kw_per_block.get(b, 0) + blk["forecast_kw"]

    aggregate = []
    for b in range(48):
        hour_ist = b * 0.5
        aggregate.append({
            "block": b,
            "time": f"{int(hour_ist):02d}:{int((hour_ist % 1) * 60):02d}",
            "total_forecast_kw": round(total_kw_per_block.get(b, 0), 2),
        })

    return {
        "forecast_date": _forecast_date,
        "last_fetched": _last_fetched,
        "location": {"lat": LAT, "lon": LON, "name": "Lanka Feeder LK1, Varanasi"},
        "aggregate_48_blocks": aggregate,
        "by_dt": _forecast_cache,
        "peak_forecast_kw": round(max((v["total_forecast_kw"] for v in aggregate), default=0), 2),
        "total_forecast_kwh": round(sum(v["total_forecast_kw"] * 0.5 for v in aggregate), 2),
    }


@router.get("/generation/{dt_id}")
async def get_dt_generation_forecast(dt_id: str):
    """D+1 generation forecast for a specific DT."""
    if not _forecast_cache:
        await _refresh_forecast()
    blocks = _forecast_cache.get(dt_id)
    if not blocks:
        return {"status": "not_available", "dt_id": dt_id}
    peak = max((b["forecast_kw"] for b in blocks), default=0)
    total_kwh = sum(b["forecast_kw"] * 0.5 for b in blocks)
    return {
        "dt_id": dt_id,
        "forecast_date": _forecast_date,
        "last_fetched": _last_fetched,
        "blocks": blocks,
        "peak_kw": round(peak, 2),
        "total_kwh": round(total_kwh, 2),
    }


@router.get("/irradiance")
async def get_irradiance_forecast():
    """Raw hourly GHI (W/m²) forecast used for generation calculation."""
    if not _irradiance_cache:
        await _refresh_forecast()
    hourly = [
        {"hour": h, "time": f"{h:02d}:00", "ghi_wm2": _irradiance_cache[h]}
        for h in range(24)
    ]
    return {
        "location": {"lat": LAT, "lon": LON},
        "forecast_date": _forecast_date,
        "source": "NASA POWER API (or simulation fallback)",
        "hourly": hourly,
        "peak_ghi": max(_irradiance_cache) if _irradiance_cache else 0,
    }


@router.post("/refresh")
async def refresh_forecast():
    """Force refresh D+1 generation forecast from NASA POWER API."""
    await _refresh_forecast()
    return {
        "status": "refreshed",
        "forecast_date": _forecast_date,
        "last_fetched": _last_fetched,
        "dts_computed": len(_forecast_cache),
    }
