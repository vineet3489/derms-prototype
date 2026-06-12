"""
Counterparty & Integration API
================================
Manage third-party DER aggregators, pull their assets/telemetry/forecast/baseline
via standard REST API calls.

Endpoints:
  GET    /api/counterparties              — list all configured counterparties
  POST   /api/counterparties              — add a counterparty (API config)
  GET    /api/counterparties/{id}         — get one
  PUT    /api/counterparties/{id}         — update config
  DELETE /api/counterparties/{id}         — remove
  POST   /api/counterparties/{id}/test    — live API connection test
  POST   /api/counterparties/{id}/sync    — pull assets → register in fleet
  POST   /api/counterparties/{id}/refresh — pull telemetry → update fleet kW
  GET    /api/counterparties/{id}/assets  — cached/live DER asset list
  GET    /api/counterparties/{id}/telemetry
  GET    /api/counterparties/{id}/forecast
  GET    /api/counterparties/{id}/baseline
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

import src.integrations.counterparty.engine as engine

router = APIRouter(prefix="/api/counterparties", tags=["Counterparties"])
logger = logging.getLogger(__name__)


@router.get("")
async def list_counterparties():
    return {"counterparties": engine.list_counterparties(), "count": len(engine.list_counterparties())}


@router.post("", status_code=201)
async def create_counterparty(data: dict):
    if not data.get("name"):
        raise HTTPException(400, "name is required")
    if not data.get("api_base_url"):
        raise HTTPException(400, "api_base_url is required")
    return engine.create_counterparty(data)


@router.get("/{cp_id}")
async def get_counterparty(cp_id: str):
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    return cp


@router.put("/{cp_id}")
async def update_counterparty(cp_id: str, data: dict):
    cp = engine.update_counterparty(cp_id, data)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    return cp


@router.delete("/{cp_id}")
async def delete_counterparty(cp_id: str):
    if not engine.delete_counterparty(cp_id):
        raise HTTPException(404, "Counterparty not found")
    return {"status": "deleted", "cp_id": cp_id}


@router.post("/{cp_id}/test")
async def test_connection(cp_id: str):
    """
    Makes a live GET {api_base_url}/health call and checks authentication.
    Updates health status on the counterparty record.
    """
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    result = await engine.test_connection(cp)
    engine.update_counterparty(cp_id, {"health": "ok" if result["ok"] else "error"})
    return result


@router.post("/{cp_id}/sync")
async def sync_assets(cp_id: str):
    """
    Pull DER asset list from counterparty API and register them in the DERMS fleet.
    DERs created with data_source='counterparty_api' for audit trail.
    """
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    try:
        registered = await engine.sync_assets(cp)
        engine.update_counterparty(cp_id, {
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "der_count": len(registered),
            "status": "active",
            "health": "ok",
        })
        return {"status": "synced", "registered_ders": registered, "count": len(registered)}
    except ValueError as e:
        engine.update_counterparty(cp_id, {"health": "error"})
        raise HTTPException(502, f"Counterparty API error: {e}")


@router.post("/{cp_id}/refresh")
async def refresh_telemetry(cp_id: str):
    """Pull current generation telemetry and update fleet kW values."""
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    updated = await engine.refresh_telemetry(cp)
    return {"status": "refreshed", "ders_updated": updated}


@router.get("/{cp_id}/assets")
async def get_assets(cp_id: str):
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    cached = engine.get_cp_ders(cp_id)
    if cached:
        return {"source": "cache", "cp_id": cp_id, "assets": cached, "count": len(cached)}
    try:
        await engine.sync_assets(cp)
        assets = engine.get_cp_ders(cp_id)
        return {"source": "live", "cp_id": cp_id, "assets": assets, "count": len(assets)}
    except ValueError as e:
        raise HTTPException(502, str(e))


@router.get("/{cp_id}/telemetry")
async def get_telemetry(cp_id: str):
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    items = await engine.fetch_telemetry(cp)
    return {"cp_id": cp_id, "timestamp": datetime.now(timezone.utc).isoformat(), "telemetry": items}


@router.get("/{cp_id}/forecast")
async def get_forecast(cp_id: str):
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    rows = await engine.fetch_forecast(cp)
    return {"cp_id": cp_id, "forecast": rows}


@router.get("/{cp_id}/baseline")
async def get_baseline(cp_id: str):
    cp = engine.get_counterparty(cp_id)
    if not cp:
        raise HTTPException(404, "Counterparty not found")
    rows = await engine.fetch_baseline(cp)
    return {"cp_id": cp_id, "baseline": rows}
