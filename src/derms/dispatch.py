"""
DERMS Dispatch Engine
======================
Evaluates grid conditions (from ADMS) and DER availability (from IEEE 2030.5)
to generate DERControls for curtailment or dispatch.

Logic:
1. Check feeder loading — if >80%, curtail DERs on that feeder proportionally
2. Check voltage violations — if overvoltage, curtail solar DERs at affected DT
3. Check dispatch requests — if DR event active, dispatch available DERs
4. Issue DERControls via IEEE 2030.5 DERControl resource
"""
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

from src.config import settings
import src.derms.fleet as fleet
from src.integrations.ieee2030_5.server import add_der_control
from src.integrations.ieee2030_5.resources import DERControlBase, ActivePower

logger = logging.getLogger(__name__)

# Active DR events
_dr_events: List[dict] = []


async def run_dispatch_cycle():
    """
    Main dispatch evaluation cycle — called periodically.
    Evaluates grid conditions and issues DERControls as needed.
    """
    issues_found = []

    # 1. Feeder overloading check
    for feeder in fleet.get_all_feeders():
        loading = feeder.get("current_loading_pct", 0)
        feeder_id = feeder["feeder_id"]

        if loading > settings.feeder_loading_warn:
            severity = "Critical" if loading > settings.feeder_loading_max else "Warning"
            issues_found.append(f"Feeder {feeder_id} loading {loading:.1f}% ({severity})")

            if loading > settings.feeder_loading_warn:
                await _curtail_feeder_ders(feeder_id, loading)

    # 2. Voltage violation check
    for dt in fleet.get_all_dts():
        dt_id = dt["dt_id"]
        for phase, v in [("L1", dt.get("voltage_l1", 230)), ("L2", dt.get("voltage_l2", 230)), ("L3", dt.get("voltage_l3", 230))]:
            if v > settings.voltage_high_warn:
                issues_found.append(f"DT {dt_id} {phase} overvoltage {v:.1f}V")
                fleet.add_alert(
                    "warning", "HIGH",
                    f"Overvoltage at {dt['name']}: {phase}={v:.1f}V (>{settings.voltage_high_warn}V)",
                    "Monitoring", dt_id, "DistributionTransformer"
                )
                await _curtail_dt_solar(dt_id, v)
            elif v < settings.voltage_low_warn:
                issues_found.append(f"DT {dt_id} {phase} undervoltage {v:.1f}V")
                fleet.add_alert(
                    "warning", "HIGH",
                    f"Undervoltage at {dt['name']}: {phase}={v:.1f}V (<{settings.voltage_low_warn}V)",
                    "Monitoring", dt_id, "DistributionTransformer"
                )

    # 3. Hosting capacity check
    for feeder in fleet.get_all_feeders():
        hc_used_pct = (feeder.get("used_capacity_kw", 0) / feeder.get("hosting_capacity_kw", 1)) * 100
        if hc_used_pct > settings.hosting_capacity_warn:
            fleet.add_alert(
                "warning", "MEDIUM",
                f"Hosting capacity at {hc_used_pct:.1f}% for {feeder['feeder_id']} "
                f"({feeder.get('used_capacity_kw',0):.0f}/{feeder.get('hosting_capacity_kw',0):.0f} kW)",
                "DERMS", feeder["feeder_id"], "Feeder"
            )

    # 4. Release curtailment if conditions improve
    for feeder in fleet.get_all_feeders():
        if feeder.get("current_loading_pct", 0) < settings.feeder_loading_warn - 10:
            await _release_feeder_curtailment(feeder["feeder_id"])

    if issues_found:
        logger.info(f"Dispatch cycle: {len(issues_found)} issues — {'; '.join(issues_found[:3])}")
    else:
        logger.debug("Dispatch cycle: grid conditions normal")


async def _curtail_feeder_ders(feeder_id: str, loading_pct: float):
    """
    Issue curtailment DERControls for solar DERs on an overloaded feeder.
    Curtailment % = proportional to how much over the limit we are.
    """
    over_pct = loading_pct - settings.feeder_loading_warn
    curtail_target = max(10, 100 - int(over_pct * 2))  # e.g. 10% over → curtail to 80%

    solar_ders = [
        d for d in fleet.get_all_ders()
        if d["feeder_id"] == feeder_id
        and d.get("der_type") == "Solar PV"
        and d.get("status") == "Online"
        and d.get("current_kw", 0) > 0
    ]

    if not solar_ders:
        return

    for der in solar_ders:
        control_id = f"CTRL-{uuid.uuid4().hex[:8].upper()}"
        ctrl = DERControlBase(
            href=f"/api/2030.5/derp/{settings.der_program_id}/derc/{control_id}",
            mRID=control_id,
            description=f"Curtailment: Feeder {feeder_id} loading {loading_pct:.1f}%",
            creationTime=int(time.time()),
            interval={"start": int(time.time()), "duration": 900},
            opModMaxLimW=curtail_target,
            rampTms=100,  # 1 second ramp
        )
        add_der_control(ctrl)
        fleet.update_der_dispatch(der["der_id"], float(curtail_target))

    logger.info(
        f"Dispatch: Curtailed {len(solar_ders)} DERs on {feeder_id} to {curtail_target}% "
        f"(loading={loading_pct:.1f}%)"
    )
    fleet.add_alert(
        "warning", "HIGH",
        f"Curtailment issued: {len(solar_ders)} DERs on {feeder_id} limited to {curtail_target}% "
        f"(loading={loading_pct:.1f}%)",
        "DERMS", feeder_id, "Feeder"
    )


async def _curtail_dt_solar(dt_id: str, voltage_v: float):
    """Curtail solar DERs at an overvoltage DT."""
    over_v = voltage_v - settings.voltage_nominal
    curtail_pct = max(20, 100 - int(over_v * 5))

    solar_ders = [
        d for d in fleet.get_all_ders()
        if d["dt_id"] == dt_id
        and d.get("der_type") == "Solar PV"
        and d.get("current_kw", 0) > 0
    ]

    for der in solar_ders:
        control_id = f"CTRL-{uuid.uuid4().hex[:8].upper()}"
        ctrl = DERControlBase(
            href=f"/api/2030.5/derp/{settings.der_program_id}/derc/{control_id}",
            mRID=control_id,
            description=f"Voltage curtailment: DT {dt_id} V={voltage_v:.1f}V",
            creationTime=int(time.time()),
            interval={"start": int(time.time()), "duration": 600},
            opModMaxLimW=curtail_pct,
            rampTms=50,
        )
        add_der_control(ctrl)
        fleet.update_der_dispatch(der["der_id"], float(curtail_pct))


async def _release_feeder_curtailment(feeder_id: str):
    """Release curtailment for DERs on feeder when grid conditions improve."""
    curtailed = [
        d for d in fleet.get_all_ders()
        if d["feeder_id"] == feeder_id
        and d.get("curtailment_pct", 100) < 100
    ]
    for der in curtailed:
        control_id = f"CTRL-{uuid.uuid4().hex[:8].upper()}"
        ctrl = DERControlBase(
            href=f"/api/2030.5/derp/{settings.der_program_id}/derc/{control_id}",
            mRID=control_id,
            description=f"Release curtailment: Feeder {feeder_id} back to normal",
            creationTime=int(time.time()),
            interval={"start": int(time.time()), "duration": 900},
            opModMaxLimW=100,  # Full power
            rampTms=300,       # Gradual ramp up
        )
        add_der_control(ctrl)
        fleet.update_der_dispatch(der["der_id"], 100.0)


# ─── Demand Response ─────────────────────────────────────────────────────────

async def create_dr_event(
    target_kw: float, duration_min: int = 60,
    feeder_id: Optional[str] = None,
    reason: str = "Peak Shaving"
) -> dict:
    """
    Create a Demand Response event — dispatch available DERs to reduce load.
    Uses battery DERs for discharging and signals EV chargers to pause.
    """
    event_id = f"DR-{datetime.now().strftime('%Y%m%d%H%M')}"

    # Find battery DERs with available capacity
    bess_ders = [
        d for d in fleet.get_all_ders()
        if d.get("der_type") == "BESS"
        and d.get("status") == "Online"
        and (feeder_id is None or d["feeder_id"] == feeder_id)
        and d.get("soc_pct", 0) > 20  # Need at least 20% SoC
    ]

    dispatched_kw = 0
    dispatched_ders = []

    for der in bess_ders:
        if dispatched_kw >= target_kw:
            break
        avail = min(der["nameplate_kw"], der.get("available_kw", der["nameplate_kw"]))
        dispatch_kw = min(avail, target_kw - dispatched_kw)

        control_id = f"DR-{uuid.uuid4().hex[:8].upper()}"
        ctrl = DERControlBase(
            href=f"/api/2030.5/derp/{settings.der_program_id}/derc/{control_id}",
            mRID=control_id,
            description=f"DR Event {event_id}: Dispatch {dispatch_kw:.1f}kW",
            creationTime=int(time.time()),
            interval={"start": int(time.time()), "duration": duration_min * 60},
            opModTargetW=ActivePower.from_kw(dispatch_kw),
            rampTms=300,
        )
        add_der_control(ctrl)
        dispatched_kw += dispatch_kw
        dispatched_ders.append(der["der_id"])

    dr_event = {
        "event_id": event_id,
        "reason": reason,
        "target_kw": target_kw,
        "dispatched_kw": round(dispatched_kw, 2),
        "duration_min": duration_min,
        "feeder_id": feeder_id,
        "dispatched_ders": dispatched_ders,
        "status": "Active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _dr_events.append(dr_event)

    fleet.add_alert(
        "info", "MEDIUM",
        f"DR Event {event_id}: Target={target_kw}kW, Dispatched={dispatched_kw:.1f}kW via {len(dispatched_ders)} BESS",
        "DR", event_id, "DREvent"
    )
    logger.info(f"DR event created: {event_id} target={target_kw}kW dispatched={dispatched_kw:.1f}kW")
    return dr_event


def get_dr_events() -> List[dict]:
    return _dr_events
