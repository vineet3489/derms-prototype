"""
Operating Envelope API
=======================
Exposes OE computation results per DT and per DER.

GET  /api/oe/summary          — fleet-wide OE status
GET  /api/oe/current          — OE for all DTs (current moment)
GET  /api/oe/{dt_id}          — OE for one DT with per-DER breakdown
GET  /api/oe/{dt_id}/schedule — 48-block daily OE schedule
GET  /api/oe/violations       — recent OE exceedance log
POST /api/oe/compute          — manually trigger OE computation
GET  /api/oe/rpf              — RPF status per DT
"""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from src.loadflow.oe_engine import (
    get_oe_current, get_oe_by_dt, get_oe_schedule, get_oe_all_schedules,
    get_rpf_status, get_oe_violations, get_last_computed,
    compute_oe, compute_oe_schedule,
)

router = APIRouter(prefix="/api/oe", tags=["Operating Envelope"])


@router.get("/summary")
async def oe_summary():
    """Fleet-wide OE summary: counts, RPF status, exceedances."""
    oe = get_oe_current()
    rpf = get_rpf_status()
    violations = get_oe_violations(limit=10)

    rpf_dts = [v for v in rpf.values() if v["rpf"]]
    all_ders = [d for dt in oe.values() for d in dt.get("ders", [])]
    exceeding = [d for d in all_ders if d["exceeding"]]

    return {
        "last_computed": get_last_computed(),
        "total_dts": len(oe),
        "rpf_dts": len(rpf_dts),
        "oe_normal_dts": len([v for v in oe.values() if v.get("oe_status") == "NORMAL"]),
        "oe_warning_dts": len([v for v in oe.values() if v.get("oe_status") == "WARNING"]),
        "oe_exceeding_dts": len([v for v in oe.values() if v.get("oe_status") == "EXCEEDING"]),
        "total_ders": len(all_ders),
        "ders_exceeding_oe": len(exceeding),
        "recent_violations": violations[:5],
        "rpf_details": rpf_dts,
    }


@router.get("/current")
async def oe_current():
    """OE for all DTs at current moment."""
    oe = get_oe_current()
    if not oe:
        return {"status": "not_computed", "message": "Run POST /api/oe/compute first or wait for background loop"}
    return {
        "computed_at": get_last_computed(),
        "dts": list(oe.values()),
        "count": len(oe),
    }


@router.get("/violations")
async def oe_violations(limit: int = 50):
    """Recent OE exceedance log."""
    return {
        "violations": get_oe_violations(limit),
        "count": len(get_oe_violations(500)),
    }


@router.get("/rpf")
async def rpf_status():
    """RPF (Reverse Power Flow) status per DT."""
    rpf = get_rpf_status()
    rpf_active = [v for v in rpf.values() if v["rpf"]]
    return {
        "dts": list(rpf.values()),
        "rpf_count": len(rpf_active),
        "any_rpf": len(rpf_active) > 0,
    }


@router.post("/compute")
async def trigger_compute(feeder_id: str = "LK1"):
    """Manually trigger OE computation for a feeder."""
    result = compute_oe(feeder_id)
    schedule_result = compute_oe_schedule(feeder_id)
    return {
        "oe": result,
        "schedule": schedule_result,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{dt_id}/schedule")
async def dt_oe_schedule(dt_id: str):
    """48-block daily OE schedule for a specific DT."""
    schedule = get_oe_schedule(dt_id)
    if not schedule:
        # Try computing
        compute_oe_schedule()
        schedule = get_oe_schedule(dt_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"No OE schedule for DT {dt_id}")
    return {
        "dt_id": dt_id,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "blocks": schedule,
        "block_count": len(schedule),
    }


@router.get("/{dt_id}")
async def dt_oe(dt_id: str):
    """OE for a specific DT with per-DER breakdown."""
    oe = get_oe_by_dt(dt_id)
    if not oe:
        return {"status": "not_computed", "dt_id": dt_id,
                "message": "Trigger POST /api/oe/compute or wait for background loop (runs every 30 min)"}
    schedule = get_oe_schedule(dt_id)
    return {**oe, "schedule_blocks": len(schedule), "schedule_available": len(schedule) > 0}
