"""
Simulated DER Aggregator API — GreenAlt Energy Pvt Ltd
=======================================================
Acts as a stand-in counterparty with their own DER fleet in Varanasi district.
The DERMS integration engine makes real HTTP calls to these endpoints.

Production: swap the counterparty's api_base_url to their real API server.
This simulator remains available for testing and onboarding walkthroughs.
"""
import math
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Request

router = APIRouter(prefix="/sim/cp", tags=["Counterparty Simulator"])

# DERs managed by GreenAlt Energy — not the Lanka Feeder real DERs
CP_DERS = [
    {
        "id": "GA-DER-001",
        "name": "Rajghat Commercial Complex",
        "type": "Solar PV",
        "capacity_kw": 25.0,
        "feeder_id": "FDR-01",
        "dt_id": "DT-RG-01",
        "lat": 25.2850,
        "lng": 82.9860,
        "commission_date": "2025-09-15",
        "metering_type": "NET",
        "consumer_id": "RG2025001",
    },
    {
        "id": "GA-DER-002",
        "name": "Sarnath Solar Farm",
        "type": "Solar PV",
        "capacity_kw": 100.0,
        "feeder_id": "FDR-02",
        "dt_id": "DT-SR-01",
        "lat": 25.3800,
        "lng": 83.0300,
        "commission_date": "2025-11-01",
        "metering_type": "NET",
        "consumer_id": "SR2025001",
    },
    {
        "id": "GA-DER-003",
        "name": "Sigra Colony Rooftop",
        "type": "Solar PV",
        "capacity_kw": 10.0,
        "feeder_id": "FDR-01",
        "dt_id": "DT-SG-01",
        "lat": 25.3200,
        "lng": 82.9750,
        "commission_date": "2025-07-20",
        "metering_type": "NET",
        "consumer_id": "SG2025001",
    },
    {
        "id": "GA-DER-004",
        "name": "BHU Campus Rooftop",
        "type": "Solar PV",
        "capacity_kw": 50.0,
        "feeder_id": "FDR-02",
        "dt_id": "DT-BH-01",
        "lat": 25.2680,
        "lng": 82.9978,
        "commission_date": "2025-12-10",
        "metering_type": "GROSS",
        "consumer_id": "BH2025001",
    },
    {
        "id": "GA-DER-005",
        "name": "Lanka Market Solar",
        "type": "Solar PV",
        "capacity_kw": 15.0,
        "feeder_id": "LK1",
        "dt_id": "LK1-DT-03",
        "lat": 25.2665,
        "lng": 82.9905,
        "commission_date": "2026-01-05",
        "metering_type": "NET",
        "consumer_id": "LK2026005",
    },
]

_TOTAL_KW = sum(d["capacity_kw"] for d in CP_DERS)


def _solar_factor() -> float:
    now = datetime.now(timezone.utc)
    hour_ist = (now.hour + 5.5) % 24
    if 6 <= hour_ist <= 18:
        f = max(0.0, math.sin(math.pi * (hour_ist - 6) / 12))
        return f * random.uniform(0.83, 0.97)
    return 0.0


@router.get("/health")
async def cp_health():
    return {
        "status": "ok",
        "provider": "GreenAlt Energy Pvt Ltd",
        "api_version": "1.2",
        "der_count": len(CP_DERS),
        "total_capacity_kw": _TOTAL_KW,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/assets")
async def cp_assets(request: Request):
    """Standard DER asset list — authenticated by X-API-Key header."""
    return {
        "provider": "GreenAlt Energy Pvt Ltd",
        "schema_version": "1.0",
        "assets": [
            {
                "id": d["id"],
                "name": d["name"],
                "type": d["type"],
                "capacity_kw": d["capacity_kw"],
                "feeder_id": d["feeder_id"],
                "dt_id": d["dt_id"],
                "lat": d["lat"],
                "lng": d["lng"],
                "status": "active",
                "commission_date": d["commission_date"],
                "metering_type": d["metering_type"],
                "consumer_id": d["consumer_id"],
            }
            for d in CP_DERS
        ],
    }


@router.get("/telemetry")
async def cp_telemetry():
    """Real-time generation telemetry for all DERs."""
    sf = _solar_factor()
    now = datetime.now(timezone.utc)
    return {
        "timestamp": now.isoformat(),
        "telemetry": [
            {
                "id": d["id"],
                "current_kw": round(d["capacity_kw"] * sf, 2),
                "voltage_v": round(random.uniform(227.5, 232.5), 1),
                "status": "Online",
                "cuf_pct": round(sf * 100, 1),
                "energy_today_kwh": round(d["capacity_kw"] * sf * 5.2, 1),
            }
            for d in CP_DERS
        ],
    }


@router.get("/forecast")
async def cp_forecast(horizon: int = 24):
    """D+1 hourly generation forecast with P10/P90 confidence bands."""
    rows = []
    for h in range(horizon):
        ist_h = (h + 5) % 24
        if 6 <= ist_h <= 18:
            f = max(0.0, math.sin(math.pi * (ist_h - 6) / 12))
            p50 = _TOTAL_KW * f * 0.85
        else:
            p50 = 0.0
        rows.append({
            "hour_ist": ist_h,
            "forecast_kw": round(p50, 1),
            "p10_kw": round(p50 * 0.78, 1),
            "p90_kw": round(min(p50 * 1.18, _TOTAL_KW), 1),
        })
    return {
        "provider": "GreenAlt Energy",
        "horizon_h": horizon,
        "unit": "kW",
        "forecast": rows,
    }


@router.get("/baseline")
async def cp_baseline():
    """Contractual baseline generation profile per DER (24-hour expected)."""
    result = []
    for d in CP_DERS:
        hourly = []
        for h in range(24):
            ist_h = (h + 5) % 24
            if 6 <= ist_h <= 18:
                f = max(0.0, math.sin(math.pi * (ist_h - 6) / 12))
                hourly.append(round(d["capacity_kw"] * f * 0.82, 2))
            else:
                hourly.append(0.0)
        result.append({
            "id": d["id"],
            "name": d["name"],
            "nameplate_kw": d["capacity_kw"],
            "hourly_expected_kw": hourly,
            "daily_expected_kwh": round(sum(hourly), 1),
            "annual_cuf_pct": 18.5,
        })
    return {"provider": "GreenAlt Energy", "baseline": result}
