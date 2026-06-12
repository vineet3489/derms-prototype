"""API routes for DER fleet management."""
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

import src.derms.fleet as fleet
import src.derms.dispatch as dispatch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ders", tags=["DER Fleet"])


@router.post("/create-from-aggregator", status_code=201)
async def create_der_from_aggregator(data: dict):
    """Called by aggregator simulator to register a DER in the fleet."""
    result = await fleet.create_der_from_aggregator(data)
    return result


@router.get("")
async def list_ders(
    feeder_id: Optional[str] = None,
    dt_id: Optional[str] = None,
    status: Optional[str] = None,
    der_type: Optional[str] = None,
):
    """List all registered DERs with optional filters."""
    ders = fleet.get_all_ders()
    if feeder_id:
        ders = [d for d in ders if d.get("feeder_id") == feeder_id]
    if dt_id:
        ders = [d for d in ders if d.get("dt_id") == dt_id]
    if status:
        ders = [d for d in ders if d.get("status") == status]
    if der_type:
        ders = [d for d in ders if d.get("der_type") == der_type]
    return {"ders": ders, "count": len(ders)}


@router.get("/{der_id}")
async def get_der(der_id: str):
    """Get details for a specific DER."""
    der = fleet.get_der(der_id)
    if not der:
        raise HTTPException(status_code=404, detail=f"DER {der_id} not found")
    return der


@router.get("/{der_id}/timeseries")
async def get_der_timeseries(der_id: str, hours: int = Query(24, ge=1, le=168)):
    """
    Get generation time series for a DER.
    In production: fetch from MDMS. Here we generate synthetic history.
    """
    import math
    import random
    from datetime import datetime, timedelta, timezone

    der = fleet.get_der(der_id)
    if not der:
        raise HTTPException(status_code=404, detail="DER not found")

    now = datetime.now(timezone.utc)
    nameplate = der.get("nameplate_kw", 5.0)
    points = []

    for i in range(hours * 4):  # 15-min intervals
        ts = now - timedelta(minutes=15 * (hours * 4 - i))
        h = ts.hour + ts.minute / 60.0
        if 6 <= h <= 18:
            solar_f = max(0, math.sin(math.pi * (h - 6) / 12))
            gen_kw = nameplate * solar_f * random.uniform(0.75, 1.0)
        else:
            gen_kw = 0.0

        points.append({
            "timestamp": ts.isoformat(),
            "generation_kw": round(gen_kw, 2),
            "nameplate_kw": nameplate,
        })

    return {"der_id": der_id, "period_hours": hours, "interval_min": 15, "data": points}


@router.post("/dispatch/dr-event")
async def trigger_dr_event(data: dict):
    """Trigger a Demand Response event."""
    target_kw = data.get("target_kw", 50.0)
    duration_min = data.get("duration_min", 60)
    feeder_id = data.get("feeder_id")
    reason = data.get("reason", "Manual DR")

    event = await dispatch.create_dr_event(target_kw, duration_min, feeder_id, reason)
    return event


@router.post("/register")
async def register_der_manually(data: dict):
    """
    Manually register a DER in the fleet.
    Use when MDMS data is not yet available or the DER has no counterparty API.
    Operator-entered details; performance data can be added later via MDMS API.
    """
    import uuid
    from datetime import datetime, timezone

    required = ["feeder_id", "dt_id", "der_type", "nameplate_kw"]
    for f in required:
        if not data.get(f):
            from fastapi import HTTPException
            raise HTTPException(400, f"'{f}' is required")

    der_id = data.get("der_id") or f"MAN-{str(uuid.uuid4())[:8].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    fleet._der_cache[der_id] = {
        "der_id": der_id,
        "aggregator_id": data.get("aggregator_id", "MANUAL"),
        "feeder_id": data["feeder_id"],
        "dt_id": data["dt_id"],
        "der_type": data["der_type"],
        "nameplate_kw": float(data["nameplate_kw"]),
        "location_name": data.get("location_name", data["dt_id"]),
        "status": data.get("status", "Online"),
        "current_kw": float(data.get("current_kw", 0)),
        "current_kvar": 0.0,
        "voltage_v": 230.0,
        "soc_pct": None,
        "cuf_pct": 0.0,
        "pr_pct": 0.0,
        "available_kw": float(data["nameplate_kw"]),
        "curtailment_pct": 0.0,
        "lat": float(data.get("lat", 25.27)),
        "lng": float(data.get("lng", 82.99)),
        "consumer_id": data.get("consumer_id", ""),
        "meter_id": data.get("meter_id", ""),
        "commission_date": data.get("commission_date"),
        "metering_type": data.get("metering_type", "NET"),
        "monthly_kwh": data.get("monthly_kwh"),
        "data_source": "manual",
        "notes": data.get("notes", ""),
        "last_update": now_iso,
    }
    return {"status": "registered", "der_id": der_id, "der": fleet._der_cache[der_id]}


@router.get("/dispatch/dr-events")
async def get_dr_events():
    """List DR events."""
    return {"events": dispatch.get_dr_events()}


@router.get("/dispatch/der-controls")
async def get_active_controls():
    """Get active IEEE 2030.5 DERControls."""
    from src.integrations.ieee2030_5.server import get_active_controls_for_program
    from src.config import settings
    controls = get_active_controls_for_program(settings.der_program_id)
    return {"controls": [c.model_dump() for c in controls], "count": len(controls)}
