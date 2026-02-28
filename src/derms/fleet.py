"""
DERMS Fleet Manager
====================
Central registry and state manager for all DER assets.
Receives updates from IEEE 2030.5 server and ADMS,
maintains authoritative fleet state for dispatch and monitoring.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import AsyncSessionLocal
from src.models import (
    DERAsset, Feeder, DistributionTransformer, Aggregator, Alert,
    DERStatus, DERType, AlertType, AlertPriority
)
from src.config import settings

logger = logging.getLogger(__name__)

# ─── In-Memory State Cache ────────────────────────────────────────────────────
# Keeps a fast-access copy of current fleet state; DB is source of truth

_der_cache: Dict[str, dict] = {}
_feeder_cache: Dict[str, dict] = {}
_dt_cache: Dict[str, dict] = {}
_aggregator_cache: Dict[str, dict] = {}
_alerts: List[dict] = []
_alert_counter: int = 0


async def initialize_fleet():
    """Initialize fleet from the ADMS topology and aggregator definitions."""
    await _seed_topology()
    logger.info("Fleet manager initialized")


async def _seed_topology():
    """Seed feeders and DTs from ADMS simulator topology into DB."""
    from src.integrations.adms.simulator import FEEDERS, DTS

    async with AsyncSessionLocal() as db:
        # Seed feeders
        for f in FEEDERS:
            existing = await db.execute(select(Feeder).where(Feeder.feeder_id == f["id"]))
            if not existing.scalar_one_or_none():
                feeder = Feeder(
                    feeder_id=f["id"],
                    name=f["name"],
                    substation_id="S-VAR-001",
                    voltage_kv=f["voltage_kv"],
                    rated_mva=f["rated_mva"],
                    hosting_capacity_kw=f["rated_mva"] * 1000 * 0.20,  # 20% of MVA as kW HC
                )
                db.add(feeder)

        # Seed DTs
        for dt in DTS:
            existing = await db.execute(
                select(DistributionTransformer).where(DistributionTransformer.dt_id == dt["id"])
            )
            if not existing.scalar_one_or_none():
                dist_tr = DistributionTransformer(
                    dt_id=dt["id"],
                    feeder_id=dt["feeder_id"],
                    name=dt["name"],
                    rated_kva=dt["rated_kva"],
                    hosting_capacity_kw=dt["rated_kva"] * 0.80,
                    lat=dt["lat"],
                    lng=dt["lng"],
                )
                db.add(dist_tr)

        await db.commit()

    # Rebuild caches
    await _rebuild_caches()


async def _rebuild_caches():
    """Rebuild in-memory caches from DB."""
    global _der_cache, _feeder_cache, _dt_cache

    async with AsyncSessionLocal() as db:
        feeders = (await db.execute(select(Feeder))).scalars().all()
        for f in feeders:
            _feeder_cache[f.feeder_id] = {
                "feeder_id": f.feeder_id, "name": f.name,
                "voltage_kv": f.voltage_kv, "rated_mva": f.rated_mva,
                "current_loading_pct": f.current_loading_pct,
                "hosting_capacity_kw": f.hosting_capacity_kw,
                "used_capacity_kw": f.used_capacity_kw,
            }

        dts = (await db.execute(select(DistributionTransformer))).scalars().all()
        for dt in dts:
            _dt_cache[dt.dt_id] = {
                "dt_id": dt.dt_id, "feeder_id": dt.feeder_id,
                "name": dt.name, "rated_kva": dt.rated_kva,
                "current_loading_pct": dt.current_loading_pct,
                "voltage_l1": dt.voltage_l1, "voltage_l2": dt.voltage_l2,
                "voltage_l3": dt.voltage_l3,
                "hosting_capacity_kw": dt.hosting_capacity_kw,
                "used_capacity_kw": dt.used_capacity_kw,
                "lat": dt.lat, "lng": dt.lng,
            }

        ders = (await db.execute(select(DERAsset))).scalars().all()
        for der in ders:
            _der_cache[der.der_id] = _der_to_dict(der)


def _der_to_dict(der: DERAsset) -> dict:
    return {
        "der_id": der.der_id,
        "aggregator_id": der.aggregator_id,
        "dt_id": der.dt_id,
        "feeder_id": der.feeder_id,
        "consumer_id": der.consumer_id,
        "meter_id": der.meter_id,
        "der_type": der.der_type,
        "nameplate_kw": der.nameplate_kw,
        "inverter_oem": der.inverter_oem,
        "model": der.model,
        "location_name": der.location_name,
        "lat": der.lat,
        "lng": der.lng,
        "status": der.status,
        "current_kw": der.current_kw,
        "current_kvar": der.current_kvar,
        "voltage_v": der.voltage_v,
        "soc_pct": der.soc_pct,
        "cuf_pct": der.cuf_pct,
        "pr_pct": der.pr_pct,
        "available_kw": der.available_kw,
        "curtailment_pct": der.curtailment_pct,
        "last_update": der.last_update.isoformat() if der.last_update else None,
    }


# ─── Aggregator Connection Callbacks ──────────────────────────────────────────

async def on_aggregator_connected(edev_id: str, lfdi: str, sfdi: int):
    """Called when an aggregator registers via IEEE 2030.5."""
    from src.integrations.ieee2030_5.simulator import AGGREGATOR_DEFINITIONS as AGG_DEFS

    # Find matching aggregator definition
    agg_def = next((a for a in AGG_DEFS if a["sfdi"] == sfdi), None)
    agg_id = agg_def["agg_id"] if agg_def else f"AGG-{sfdi}"
    name = agg_def["name"] if agg_def else f"Aggregator {sfdi}"

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(Aggregator).where(Aggregator.agg_id == agg_id))
        if not existing.scalar_one_or_none():
            agg = Aggregator(
                agg_id=agg_id, name=name,
                lfdi=lfdi, sfdi=str(sfdi),
                status="Online", last_seen=datetime.now(timezone.utc),
            )
            db.add(agg)
            await db.commit()

    _aggregator_cache[agg_id] = {
        "agg_id": agg_id, "name": name,
        "lfdi": lfdi, "status": "Online",
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"Fleet: Aggregator connected {agg_id} (sFDI={sfdi})")


# ─── DER Registration ─────────────────────────────────────────────────────────

async def create_der_from_aggregator(data: dict) -> dict:
    """Register a new DER into the fleet (called by aggregator simulator)."""
    der_id = data["der_id"]

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(DERAsset).where(DERAsset.der_id == der_id))
        if existing.scalar_one_or_none():
            return {"status": "exists", "der_id": der_id}

        # Ensure aggregator exists
        agg_id = data["aggregator_id"]
        agg_existing = await db.execute(select(Aggregator).where(Aggregator.agg_id == agg_id))
        if not agg_existing.scalar_one_or_none():
            agg_name = data.get("aggregator_name", f"Aggregator {agg_id}")
            agg = Aggregator(
                agg_id=agg_id, name=agg_name,
                status="Online", last_seen=datetime.now(timezone.utc),
            )
            db.add(agg)
            # Populate aggregator cache
            if agg_id not in _aggregator_cache:
                _aggregator_cache[agg_id] = {
                    "agg_id": agg_id, "name": agg_name,
                    "status": "Online",
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                }

        der = DERAsset(
            der_id=der_id,
            aggregator_id=agg_id,
            dt_id=data["dt_id"],
            feeder_id=data["feeder_id"],
            consumer_id=data["consumer_id"],
            meter_id=data["meter_id"],
            der_type=data["der_type"],
            nameplate_kw=data["nameplate_kw"],
            inverter_oem=data.get("inverter_oem", "Unknown"),
            location_name=data.get("location_name", "Varanasi"),
            lat=data.get("lat", 25.317645),
            lng=data.get("lng", 82.973915),
            status=DERStatus.ONLINE,
            current_kw=0.0,
            last_update=datetime.now(timezone.utc),
        )
        db.add(der)

        # Update DT used capacity
        dt = (await db.execute(
            select(DistributionTransformer).where(DistributionTransformer.dt_id == data["dt_id"])
        )).scalar_one_or_none()
        if dt:
            dt.used_capacity_kw += data["nameplate_kw"]

        await db.commit()

    der_dict = {
        "der_id": der_id, "aggregator_id": agg_id,
        "dt_id": data["dt_id"], "feeder_id": data["feeder_id"],
        "consumer_id": data["consumer_id"], "meter_id": data["meter_id"],
        "der_type": data["der_type"], "nameplate_kw": data["nameplate_kw"],
        "inverter_oem": data.get("inverter_oem", "Unknown"),
        "location_name": data.get("location_name", "Varanasi"),
        "lat": data.get("lat", 25.317645), "lng": data.get("lng", 82.973915),
        "status": "Online", "current_kw": 0.0, "available_kw": data["nameplate_kw"],
        "curtailment_pct": 100.0, "cuf_pct": 0.0, "pr_pct": 0.0,
        "voltage_v": 230.0, "soc_pct": None, "current_kvar": 0.0,
        "last_update": datetime.now(timezone.utc).isoformat(),
    }
    _der_cache[der_id] = der_dict
    logger.info(f"Fleet: DER registered {der_id} ({data['der_type']} {data['nameplate_kw']}kW)")

    # Update feeder used capacity
    feeder_id = data["feeder_id"]
    if feeder_id in _feeder_cache:
        _feeder_cache[feeder_id]["used_capacity_kw"] = sum(
            d["nameplate_kw"] for d in _der_cache.values()
            if d["feeder_id"] == feeder_id
        )

    return {"status": "created", "der_id": der_id}


# ─── Real-Time State Updates ──────────────────────────────────────────────────

async def on_der_status_update(
    der_id: str, current_kw: Optional[float], current_kvar: float,
    online: bool, soc_pct: Optional[float]
):
    """Update DER real-time state from IEEE 2030.5 DERStatus."""
    if der_id not in _der_cache:
        return

    der = _der_cache[der_id]
    nameplate = der["nameplate_kw"]

    if current_kw is not None:
        der["current_kw"] = current_kw
    der["current_kvar"] = current_kvar
    der["status"] = "Online" if online else "Offline"
    if soc_pct is not None:
        der["soc_pct"] = soc_pct

    # Calculate CUF and PR (only for Solar PV during daytime)
    if nameplate > 0 and der["der_type"] == "Solar PV":
        import math
        hour = datetime.now().hour + datetime.now().minute / 60.0
        clear_sky_factor = max(0, math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0
        clear_sky_kw = nameplate * clear_sky_factor
        cuf = (der["current_kw"] / nameplate) * 100 if nameplate > 0 else 0
        pr = (der["current_kw"] / clear_sky_kw * 100) if clear_sky_kw > 0 else 100.0
        der["cuf_pct"] = round(min(100, max(0, cuf)), 1)
        der["pr_pct"] = round(min(100, max(0, pr)), 1)

    der["last_update"] = datetime.now(timezone.utc).isoformat()

    # Persist to DB
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(DERAsset).where(DERAsset.der_id == der_id).values(
                current_kw=der["current_kw"],
                current_kvar=current_kvar,
                status=der["status"],
                soc_pct=soc_pct,
                cuf_pct=der["cuf_pct"],
                pr_pct=der["pr_pct"],
                last_update=datetime.now(timezone.utc),
            )
        )
        await db.commit()


async def on_der_availability_update(der_id: str, avail_kw: float):
    """Update available flex from IEEE 2030.5 DERAvailability."""
    if der_id in _der_cache:
        _der_cache[der_id]["available_kw"] = avail_kw


async def on_meter_reading(end_device_id: str, reading: Any):
    """Process metering data from IEEE 2030.5 Mirror Usage Points."""
    logger.debug(f"Fleet: Meter reading received from edev={end_device_id}")


async def update_grid_state(adms_state: dict):
    """
    Update feeder and DT state from ADMS real-time SCADA data.
    Called by the ADMS polling background task.
    """
    for f_state in adms_state.get("feeders", []):
        fid = f_state["feeder_id"]
        if fid in _feeder_cache:
            _feeder_cache[fid]["current_loading_pct"] = f_state["loading_pct"]

    for dt_state in adms_state.get("distribution_transformers", []):
        dtid = dt_state["dt_id"]
        if dtid in _dt_cache:
            _dt_cache[dtid]["current_loading_pct"] = dt_state["loading_pct"]
            _dt_cache[dtid]["voltage_l1"] = dt_state["voltage_l1"]
            _dt_cache[dtid]["voltage_l2"] = dt_state["voltage_l2"]
            _dt_cache[dtid]["voltage_l3"] = dt_state["voltage_l3"]

    async with AsyncSessionLocal() as db:
        for f_state in adms_state.get("feeders", []):
            await db.execute(
                update(Feeder).where(Feeder.feeder_id == f_state["feeder_id"]).values(
                    current_loading_pct=f_state["loading_pct"]
                )
            )
        for dt_state in adms_state.get("distribution_transformers", []):
            await db.execute(
                update(DistributionTransformer).where(
                    DistributionTransformer.dt_id == dt_state["dt_id"]
                ).values(
                    current_loading_pct=dt_state["loading_pct"],
                    voltage_l1=dt_state["voltage_l1"],
                    voltage_l2=dt_state["voltage_l2"],
                    voltage_l3=dt_state["voltage_l3"],
                )
            )
        await db.commit()


# ─── Alert Management ─────────────────────────────────────────────────────────

def add_alert(alert_type: str, priority: str, message: str, module: str,
              resource_id: str = None, resource_type: str = None):
    global _alert_counter
    _alert_counter += 1
    alert = {
        "id": str(uuid.uuid4()),
        "alert_type": alert_type,
        "priority": priority,
        "message": message,
        "module": module,
        "resource_id": resource_id,
        "resource_type": resource_type,
        "resolved": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _alerts.insert(0, alert)
    if len(_alerts) > 200:
        _alerts.pop()
    logger.info(f"Alert [{priority}]: {message}")
    return alert


# ─── Query Functions ──────────────────────────────────────────────────────────

def get_all_ders() -> List[dict]:
    return list(_der_cache.values())


def get_der(der_id: str) -> Optional[dict]:
    return _der_cache.get(der_id)


def get_all_feeders() -> List[dict]:
    return list(_feeder_cache.values())


def get_all_dts() -> List[dict]:
    return list(_dt_cache.values())


def get_all_aggregators() -> List[dict]:
    return list(_aggregator_cache.values())


def get_alerts(limit: int = 50, unresolved_only: bool = False) -> List[dict]:
    alerts = _alerts if not unresolved_only else [a for a in _alerts if not a["resolved"]]
    return alerts[:limit]


def get_fleet_summary() -> dict:
    ders = list(_der_cache.values())
    solar_ders = [d for d in ders if "Solar" in d.get("der_type", "")]
    online_ders = [d for d in ders if d.get("status") == "Online"]
    total_gen = sum(d.get("current_kw", 0) for d in ders if d.get("current_kw", 0) > 0)
    total_cap = sum(d.get("nameplate_kw", 0) for d in ders)
    curtailed = [d for d in ders if d.get("curtailment_pct", 100) < 100]

    cuf_vals = [d["cuf_pct"] for d in solar_ders if d.get("cuf_pct", 0) > 0]
    avg_cuf = sum(cuf_vals) / len(cuf_vals) if cuf_vals else 0.0

    # Compute used capacity per feeder and DT from DER cache (authoritative)
    feeder_used = {}
    dt_used = {}
    for d in ders:
        fid = d.get("feeder_id", "")
        feeder_used[fid] = feeder_used.get(fid, 0) + d.get("nameplate_kw", 0)
        dtid = d.get("dt_id", "")
        if dtid:
            dt_used[dtid] = dt_used.get(dtid, 0) + d.get("nameplate_kw", 0)

    feeders = [dict(f) for f in _feeder_cache.values()]  # Make copies to avoid mutation
    for f in feeders:
        fid = f["feeder_id"]
        f["used_capacity_kw"] = round(feeder_used.get(fid, 0), 1)
        f["available_capacity_kw"] = round(f["hosting_capacity_kw"] - f["used_capacity_kw"], 1)
        f_loading = f.get("current_loading_pct", 0)
        if f_loading > settings.feeder_loading_warn:
            f["status"] = "Warning"
        elif f_loading > settings.feeder_loading_max:
            f["status"] = "Critical"
        else:
            f["status"] = "Normal"

    dts = [dict(dt) for dt in _dt_cache.values()]  # Make copies
    for dt in dts:
        dtid = dt["dt_id"]
        dt["used_capacity_kw"] = round(dt_used.get(dtid, 0), 1)
        dt["available_capacity_kw"] = round(dt["hosting_capacity_kw"] - dt["used_capacity_kw"], 1)
    for dt in dts:
        avg_v = (dt.get("voltage_l1", 230) + dt.get("voltage_l2", 230) + dt.get("voltage_l3", 230)) / 3
        if avg_v > settings.voltage_high_warn or avg_v < settings.voltage_low_warn:
            dt["voltage_status"] = "Warning"
        else:
            dt["voltage_status"] = "Normal"

    active_alerts = [a for a in _alerts if not a.get("resolved")]

    return {
        "total_ders": len(ders),
        "online_ders": len(online_ders),
        "offline_ders": len([d for d in ders if d.get("status") == "Offline"]),
        "degraded_ders": len([d for d in ders if d.get("status") == "Degraded"]),
        "curtailed_ders": len(curtailed),
        "total_generation_kw": round(total_gen, 2),
        "total_capacity_kw": round(total_cap, 2),
        "system_cuf_pct": round(avg_cuf, 1),
        "active_alerts": len(active_alerts),
        "feeders": feeders,
        "distribution_transformers": dts,
        "recent_alerts": _alerts[:10],
        "adms_sync_status": "Connected",
        "adms_last_sync": datetime.now(timezone.utc).isoformat(),
        "ieee_aggregators_online": len(_aggregator_cache),
        "active_der_controls": 0,
    }


def update_der_dispatch(der_id: str, curtailment_pct: float):
    """Apply dispatch result to DER cache."""
    if der_id in _der_cache:
        _der_cache[der_id]["curtailment_pct"] = curtailment_pct
        if curtailment_pct < 100:
            _der_cache[der_id]["status"] = "Curtailed"
