"""
Load Flow API — pandapower-based network analysis for pilot feeders.

Endpoints:
  GET  /api/loadflow/config              — Get network parameters
  PUT  /api/loadflow/config              — Update parameters + re-run
  GET  /api/loadflow/conductors          — Conductor library
  GET  /api/loadflow/results/{feeder_id} — Latest load flow results
  POST /api/loadflow/run/{feeder_id}     — Trigger manual run
  GET  /api/loadflow/doc/{feeder_id}     — DER Operating Capacity table
  POST /api/loadflow/whatif              — What-if DER connection simulation
  GET  /api/loadflow/hosting-capacity    — Per-DT HC with PRD formula
"""
import math
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

import src.derms.fleet as fleet
from src.loadflow.network_config import (
    load_config, save_config, get_feeder_config,
    CONDUCTOR_LIBRARY, FeederNetworkConfig
)
from src.loadflow.engine import run_load_flow, run_whatif, get_latest_results, get_all_results
from src.data.real_pilot_data import LANKA_DTS, LANKA_DERS

router = APIRouter(prefix="/api/loadflow", tags=["Load Flow"])
logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _enrich_dts_with_realtime(feeder_id: str) -> list:
    """
    Enrich DT list with current net load from fleet cache.
    Net load = DT total load (approximated from consumer sanctioned loads)
               minus current DER generation on that DT.
    """
    ders = fleet.get_all_ders()
    fleet_dts = fleet.get_all_dts()

    # Generation per DT from fleet cache
    gen_per_dt = {}
    for der in ders:
        if der.get("feeder_id") == feeder_id:
            dtid = der.get("dt_id", "")
            gen_per_dt[dtid] = gen_per_dt.get(dtid, 0) + der.get("current_kw", 0)

    # Load per DT from fleet cache or real_pilot_data defaults
    load_per_dt = {}
    for dt in fleet_dts:
        if dt.get("feeder_id") == feeder_id:
            load_per_dt[dt["dt_id"]] = dt.get("total_load_kw", dt.get("rated_kva", 100) * 0.4)

    # Use LANKA_DTS as base for real pilot feeder
    if feeder_id == "LK1":
        base_dts = [dict(d) for d in LANKA_DTS]
    else:
        base_dts = [
            {
                "id": dt["dt_id"], "name": dt.get("name", dt["dt_id"]),
                "feeder_id": feeder_id, "rated_kva": dt.get("rated_kva", 100),
                "order": i + 1, "lat": dt.get("lat", 25.27), "lng": dt.get("lng", 82.99),
                "total_load_kw": dt.get("rated_kva", 100) * 0.4,
            }
            for i, dt in enumerate(fleet_dts) if dt.get("feeder_id") == feeder_id
        ]

    for dt in base_dts:
        dtid = dt["id"]
        total_load = load_per_dt.get(dtid, dt.get("total_load_kw", 20.0))
        gen = gen_per_dt.get(dtid, 0)
        dt["net_load_kw"] = max(0, total_load - gen)
        dt["total_load_kw"] = total_load
        dt["generation_kw"] = gen

    return base_dts


def _enrich_ders_with_realtime(feeder_id: str) -> list:
    """Get DERs for feeder enriched with current generation from fleet cache."""
    ders = fleet.get_all_ders()
    feeder_ders = [d for d in ders if d.get("feeder_id") == feeder_id]

    if feeder_id == "LK1" and not feeder_ders:
        # Use real pilot data if fleet cache not yet populated
        return [
            {
                "der_id": d["der_id"],
                "dt_id": d["dt_id"],
                "nameplate_kw": d["nameplate_kw"],
                "current_kw": 0.0,
            }
            for d in LANKA_DERS
        ]

    return [
        {
            "der_id": d["der_id"],
            "dt_id": d["dt_id"],
            "nameplate_kw": d.get("nameplate_kw", 0),
            "current_kw": d.get("current_kw", 0),
        }
        for d in feeder_ders
    ]


def _hc_traffic_light(pct: float, cfg) -> str:
    if pct >= cfg.hc_amber_pct:
        return "red"
    elif pct >= cfg.hc_green_pct:
        return "amber"
    return "green"


# ─── Config endpoints ─────────────────────────────────────────────────────────

@router.get("/conductors")
async def get_conductor_library():
    return {"conductors": CONDUCTOR_LIBRARY}


@router.get("/config")
async def get_network_config():
    cfg = load_config()
    return {
        "global": {
            "default_conductor_type": cfg.default_conductor_type,
            "default_feeder_head_voltage_pu": cfg.default_feeder_head_voltage_pu,
            "default_dt_transformer_z_pct": cfg.default_dt_transformer_z_pct,
            "diversity_factor": cfg.diversity_factor,
            "power_factor": cfg.power_factor,
            "hc_green_pct": cfg.hc_green_pct,
            "hc_amber_pct": cfg.hc_amber_pct,
        },
        "feeders": {
            fid: {
                "feeder_id": f.feeder_id,
                "conductor_type": f.conductor_type,
                "conductor_label": f.conductor.get("label"),
                "r_ohm_per_km": f.conductor.get("r_ohm_per_km"),
                "x_ohm_per_km": f.conductor.get("x_ohm_per_km"),
                "feeder_head_voltage_pu": f.feeder_head_voltage_pu,
                "dt_transformer_z_pct": f.dt_transformer_z_pct,
                "voltage_upper_pu": f.voltage_upper_pu,
                "voltage_lower_pu": f.voltage_lower_pu,
                "use_cim_model": f.use_cim_model,
                "cim_xml_path": f.cim_xml_path,
                "dt_kva_overrides": f.dt_kva_overrides,
            }
            for fid, f in cfg.feeders.items()
        },
        "conductor_library": CONDUCTOR_LIBRARY,
    }


class FeederConfigUpdate(BaseModel):
    feeder_id: str
    conductor_type: Optional[str] = None
    custom_r_ohm_per_km: Optional[float] = None
    custom_x_ohm_per_km: Optional[float] = None
    custom_max_current_a: Optional[float] = None
    feeder_head_voltage_pu: Optional[float] = None
    dt_transformer_z_pct: Optional[float] = None
    voltage_upper_pu: Optional[float] = None
    voltage_lower_pu: Optional[float] = None
    voltage_pre_alert_upper_pu: Optional[float] = None
    voltage_pre_alert_lower_pu: Optional[float] = None
    doc_sweep_step_kw: Optional[float] = None
    use_cim_model: Optional[bool] = None
    cim_xml_path: Optional[str] = None
    dt_kva_overrides: Optional[dict] = None


class GlobalConfigUpdate(BaseModel):
    default_conductor_type: Optional[str] = None
    default_feeder_head_voltage_pu: Optional[float] = None
    default_dt_transformer_z_pct: Optional[float] = None
    diversity_factor: Optional[float] = None
    power_factor: Optional[float] = None
    hc_green_pct: Optional[float] = None
    hc_amber_pct: Optional[float] = None
    feeder: Optional[FeederConfigUpdate] = None


@router.put("/config")
async def update_network_config(body: GlobalConfigUpdate):
    """Update network parameters. Changes take effect on next load flow run."""
    cfg = load_config()

    if body.default_conductor_type is not None:
        cfg.default_conductor_type = body.default_conductor_type
    if body.default_feeder_head_voltage_pu is not None:
        cfg.default_feeder_head_voltage_pu = body.default_feeder_head_voltage_pu
    if body.default_dt_transformer_z_pct is not None:
        cfg.default_dt_transformer_z_pct = body.default_dt_transformer_z_pct
    if body.diversity_factor is not None:
        cfg.diversity_factor = body.diversity_factor
    if body.power_factor is not None:
        cfg.power_factor = body.power_factor
    if body.hc_green_pct is not None:
        cfg.hc_green_pct = body.hc_green_pct
    if body.hc_amber_pct is not None:
        cfg.hc_amber_pct = body.hc_amber_pct

    if body.feeder:
        fid = body.feeder.feeder_id
        if fid not in cfg.feeders:
            cfg.feeders[fid] = FeederNetworkConfig(feeder_id=fid)
        f = cfg.feeders[fid]
        for field, val in body.feeder.model_dump(exclude_none=True).items():
            if hasattr(f, field):
                setattr(f, field, val)

    save_config(cfg)
    return {"status": "saved", "message": "Network config updated. Trigger /api/loadflow/run to apply."}


# ─── Run endpoints ────────────────────────────────────────────────────────────

@router.post("/run/{feeder_id}")
async def trigger_load_flow(feeder_id: str):
    """Manually trigger a load flow run for a feeder."""
    dts = _enrich_dts_with_realtime(feeder_id)
    ders = _enrich_ders_with_realtime(feeder_id)

    if not dts:
        raise HTTPException(status_code=404, detail=f"No DTs found for feeder {feeder_id}")

    result = run_load_flow(feeder_id, dts, ders, label="manual")
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("error"))

    return result


@router.get("/results/{feeder_id}")
async def get_results(feeder_id: str):
    result = get_latest_results(feeder_id)
    if result is None:
        # Auto-run if no results yet
        dts = _enrich_dts_with_realtime(feeder_id)
        ders = _enrich_ders_with_realtime(feeder_id)
        if not dts:
            raise HTTPException(status_code=404, detail=f"Feeder {feeder_id} not found")
        result = run_load_flow(feeder_id, dts, ders, label="auto")
    return result


@router.get("/results")
async def get_all_feeder_results():
    return {"results": get_all_results()}


# ─── DOC endpoint ─────────────────────────────────────────────────────────────

@router.get("/doc/{feeder_id}")
async def get_doc(feeder_id: str):
    """DER Operating Capacity table for all DERs on a feeder."""
    result = get_latest_results(feeder_id)
    if result is None:
        dts = _enrich_dts_with_realtime(feeder_id)
        ders = _enrich_ders_with_realtime(feeder_id)
        result = run_load_flow(feeder_id, dts, ders, label="auto")

    doc_list = result.get("doc_per_der", [])
    return {
        "feeder_id": feeder_id,
        "timestamp": result.get("timestamp"),
        "indicative": result.get("indicative", True),
        "model_source": result.get("model_source", "assumed"),
        "doc": doc_list,
        "summary": {
            "total_ders": len(doc_list),
            "constrained_ders": sum(1 for d in doc_list if d.get("constrained")),
            "total_nameplate_kw": round(sum(d.get("nameplate_kw", 0) for d in doc_list), 1),
            "total_doc_kw": round(sum(d.get("doc_kw", 0) for d in doc_list), 1),
        },
    }


# ─── What-if endpoint ─────────────────────────────────────────────────────────

class WhatIfRequest(BaseModel):
    feeder_id: str
    target_dt_id: str
    hypothetical_kw: float


@router.post("/whatif")
async def whatif_simulation(body: WhatIfRequest):
    """
    Simulate connecting a new DER at target_dt_id.
    Returns voltage impact at every DT on the feeder.
    """
    dts = _enrich_dts_with_realtime(body.feeder_id)
    ders = _enrich_ders_with_realtime(body.feeder_id)

    if not dts:
        raise HTTPException(status_code=404, detail=f"Feeder {body.feeder_id} not found")

    result = run_whatif(body.feeder_id, dts, ders, body.target_dt_id, body.hypothetical_kw)
    if result.get("status") == "failed":
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


# ─── Hosting Capacity (PRD-formula) ──────────────────────────────────────────

@router.get("/hosting-capacity")
async def get_hosting_capacity(feeder_id: str = "LK1"):
    """
    Static HC per DT using PRD formula:
      HC Limit (kW) = DT kVA × PF × diversity_factor
      HC Utilised % = Σ active DER capacity ÷ HC Limit × 100

    Also shows pipeline capacity (pending DERs) for projected HC.
    """
    cfg = load_config()
    fcfg = get_feeder_config(feeder_id)

    ders = fleet.get_all_ders()
    fleet_dts = fleet.get_all_dts()

    # Base DT list
    if feeder_id == "LK1":
        base_dts = LANKA_DTS
    else:
        base_dts = [
            {"id": dt["dt_id"], "name": dt.get("name"), "rated_kva": dt.get("rated_kva", 100)}
            for dt in fleet_dts if dt.get("feeder_id") == feeder_id
        ]

    # Active DER capacity per DT
    active_kw_per_dt = {}
    for der in ders:
        if der.get("feeder_id") == feeder_id and der.get("status") not in ("Offline", "Suspended"):
            dtid = der.get("dt_id", "")
            active_kw_per_dt[dtid] = active_kw_per_dt.get(dtid, 0) + der.get("nameplate_kw", 0)

    results = []
    for dt in base_dts:
        dt_id = dt["id"]
        rated_kva = fcfg.dt_kva_overrides.get(dt_id, dt.get("rated_kva", 100))

        hc_limit_kw = rated_kva * cfg.power_factor * cfg.diversity_factor
        active_kw = active_kw_per_dt.get(dt_id, 0)
        hc_used_pct = (active_kw / hc_limit_kw * 100) if hc_limit_kw > 0 else 0
        hc_available_kw = max(0, hc_limit_kw - active_kw)

        results.append({
            "dt_id": dt_id,
            "dt_name": dt.get("name", dt_id),
            "rated_kva": rated_kva,
            "hc_limit_kw": round(hc_limit_kw, 1),
            "active_der_kw": round(active_kw, 1),
            "hc_used_pct": round(hc_used_pct, 1),
            "hc_available_kw": round(hc_available_kw, 1),
            "traffic_light": _hc_traffic_light(hc_used_pct, cfg),
        })

    return {
        "feeder_id": feeder_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "power_factor": cfg.power_factor,
            "diversity_factor": cfg.diversity_factor,
            "hc_green_pct": cfg.hc_green_pct,
            "hc_amber_pct": cfg.hc_amber_pct,
        },
        "formula": "HC Limit = DT kVA × PF × diversity_factor",
        "dt_hc": results,
        "summary": {
            "total_hc_limit_kw": round(sum(r["hc_limit_kw"] for r in results), 1),
            "total_active_kw": round(sum(r["active_der_kw"] for r in results), 1),
            "dts_green": sum(1 for r in results if r["traffic_light"] == "green"),
            "dts_amber": sum(1 for r in results if r["traffic_light"] == "amber"),
            "dts_red": sum(1 for r in results if r["traffic_light"] == "red"),
        },
    }
