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
