"""
Operating Envelope Engine
==========================
Computes per-DER Operating Envelope (OE) using pandapower load flow results.

Phase 1 (load-flow-based, surpassing PRD's simpler rule-based approach):
  OE_voltage   = DOC from pandapower sweep (max kW before voltage violation)
  OE_thermal   = proportional share of DT transformer thermal capacity
  OE_RPF       = proportional share of DT consumer load (no RPF when sum <= load)
  OE           = min(nameplate, OE_voltage, OE_thermal, OE_RPF)

Runs every 30 min via background loop; also computes 48-block daily schedule.
"""
import math
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── In-memory OE cache ──────────────────────────────────────────────────────
_oe_current: dict = {}     # dt_id → {ders: [...], rpf: bool, ...}
_oe_schedule: dict = {}    # dt_id → list of 48 blocks
_rpf_status: dict = {}     # dt_id → {rpf, export_kw, consumer_load_kw, ...}
_oe_violations: list = []  # recent exceedance log
_last_computed: Optional[str] = None


def get_oe_current() -> dict:
    return _oe_current


def get_oe_by_dt(dt_id: str) -> Optional[dict]:
    return _oe_current.get(dt_id)


def get_oe_schedule(dt_id: str) -> list:
    return _oe_schedule.get(dt_id, [])


def get_oe_all_schedules() -> dict:
    return _oe_schedule


def get_rpf_status() -> dict:
    return _rpf_status


def get_oe_violations(limit: int = 100) -> list:
    return _oe_violations[:limit]


def get_last_computed() -> Optional[str]:
    return _last_computed


# ── Load estimate helpers ────────────────────────────────────────────────────

def _dt_load_kw(dt: dict, hour_ist: float) -> float:
    """Estimate consumer load behind DT at a given IST hour."""
    rated_kva = dt.get("rated_kva", 100)
    base_load = dt.get("total_load_kw", rated_kva * 0.40)
    morning = 0.28 * math.exp(-0.5 * ((hour_ist - 9) ** 2) / 3)
    evening = 0.38 * math.exp(-0.5 * ((hour_ist - 19.5) ** 2) / 3)
    factor = 0.40 + morning + evening
    return round(base_load * factor, 2)


# ── Core OE computation ──────────────────────────────────────────────────────

def compute_oe(feeder_id: str = "LK1") -> dict:
    """
    Compute OE for all DTs and DERs on a feeder using current load flow results.
    Called every 30 min by the monitoring background loop.
    """
    global _oe_current, _rpf_status, _last_computed

    from src.loadflow.engine import get_latest_results
    import src.derms.fleet as fleet
    from src.data.real_pilot_data import LANKA_DTS

    lf = get_latest_results(feeder_id)
    if not lf:
        logger.warning("OE: No load flow results available yet")
        return {"status": "no_loadflow"}

    ders = fleet.get_all_ders()
    dts = fleet.get_all_dts()
    dt_map = {dt["dt_id"]: dt for dt in dts}

    # Augment with LANKA_DTS rated_kva + total_load_kw
    lk1_dt_map = {d["id"]: d for d in LANKA_DTS}
    for dt_id, dt in dt_map.items():
        if dt_id in lk1_dt_map:
            if "rated_kva" not in dt or not dt.get("rated_kva"):
                dt["rated_kva"] = lk1_dt_map[dt_id]["rated_kva"]
            dt.setdefault("total_load_kw", lk1_dt_map[dt_id]["total_load_kw"])

    # Group DERs by DT
    dt_ders: dict = {}
    for der in ders:
        dt_id = der.get("dt_id")
        if dt_id:
            dt_ders.setdefault(dt_id, []).append(der)

    # DOC per DER from load flow (voltage + thermal constraint)
    doc_map = {d["der_id"]: d["doc_kw"] for d in lf.get("doc_per_der", [])}

    # DT transformer loading % from load flow
    dt_loading_pct = lf.get("dt_loading_pct", {})

    # Voltage per DT (pu) from load flow
    voltage_map = {bv["dt_id"]: bv["vm_pu"] for bv in lf.get("bus_voltages", [])}

    now = datetime.now(timezone.utc)
    hour_ist = (now.hour + 5.5) % 24
    oe_results = {}

    for dt_id, dt_ders_list in dt_ders.items():
        dt = dt_map.get(dt_id, {})
        rated_kva = dt.get("rated_kva", 100)
        consumer_load_kw = _dt_load_kw(dt, hour_ist)

        total_der_gen_kw = sum(d.get("current_kw", 0) for d in dt_ders_list)
        net_export_kw = total_der_gen_kw - consumer_load_kw
        rpf_detected = net_export_kw > 1.0  # 1 kW tolerance

        _rpf_status[dt_id] = {
            "dt_id": dt_id,
            "rpf": rpf_detected,
            "export_kw": round(max(0, net_export_kw), 2),
            "consumer_load_kw": round(consumer_load_kw, 2),
            "der_gen_kw": round(total_der_gen_kw, 2),
            "ts": now.isoformat(),
        }

        total_nameplate = sum(d.get("nameplate_kw", 0) for d in dt_ders_list) or 1.0
        dt_loading = dt_loading_pct.get(dt_id, 0)

        oe_per_der = []
        for der in dt_ders_list:
            der_id = der["der_id"]
            nameplate_kw = der.get("nameplate_kw", 0)
            prop = nameplate_kw / total_nameplate

            # Constraint 1: voltage DOC from pandapower sweep
            oe_v = doc_map.get(der_id, nameplate_kw)

            # Constraint 2: thermal — proportional share of DT kVA × 0.95 × 0.80
            oe_th = prop * rated_kva * 0.95 * 0.80

            # Constraint 3: RPF — proportional share of consumer load
            oe_rpf = prop * consumer_load_kw

            oe_kw = round(max(0.0, min(nameplate_kw, oe_v, oe_th, oe_rpf)), 2)

            # Binding constraint label
            vals = {"voltage_DOC": oe_v, "thermal_HC": oe_th, "RPF_limit": oe_rpf, "nameplate": nameplate_kw}
            binding = min(vals, key=lambda k: vals[k])

            current_kw = der.get("current_kw", 0)
            exceeding = current_kw > oe_kw + 0.5

            if exceeding:
                viol = {
                    "der_id": der_id, "dt_id": dt_id,
                    "oe_kw": oe_kw, "actual_kw": round(current_kw, 2),
                    "excess_kw": round(current_kw - oe_kw, 2),
                    "ts": now.isoformat(),
                }
                _oe_violations.insert(0, viol)
                if len(_oe_violations) > 500:
                    _oe_violations.pop()

            oe_per_der.append({
                "der_id": der_id,
                "dt_id": dt_id,
                "location_name": der.get("location_name", dt_id),
                "nameplate_kw": nameplate_kw,
                "oe_kw": oe_kw,
                "current_kw": round(current_kw, 2),
                "oe_util_pct": round(current_kw / oe_kw * 100 if oe_kw > 0 else 0, 1),
                "exceeding": exceeding,
                "excess_kw": round(max(0, current_kw - oe_kw), 2),
                "binding_constraint": binding,
                "oe_voltage_kw": round(oe_v, 2),
                "oe_thermal_kw": round(oe_th, 2),
                "oe_rpf_kw": round(oe_rpf, 2),
            })

        # DT-level OE status
        dt_oe_util = (
            max(d["oe_util_pct"] for d in oe_per_der) if oe_per_der else 0
        )
        oe_results[dt_id] = {
            "dt_id": dt_id,
            "rated_kva": rated_kva,
            "consumer_load_kw": round(consumer_load_kw, 2),
            "total_der_gen_kw": round(total_der_gen_kw, 2),
            "rpf": rpf_detected,
            "export_kw": round(max(0, net_export_kw), 2),
            "dt_loading_pct": round(dt_loading, 1),
            "voltage_pu": voltage_map.get(dt_id),
            "n_ders": len(dt_ders_list),
            "oe_status": (
                "RPF" if rpf_detected
                else "EXCEEDING" if any(d["exceeding"] for d in oe_per_der)
                else "WARNING" if dt_oe_util > 80
                else "NORMAL"
            ),
            "ders": oe_per_der,
            "computed_at": now.isoformat(),
        }

    _oe_current = oe_results
    _last_computed = now.isoformat()
    _check_and_alert(oe_results)

    logger.info(
        f"OE computed: {len(oe_results)} DTs, "
        f"RPF={sum(1 for v in oe_results.values() if v['rpf'])} DTs"
    )
    return {"status": "ok", "computed_at": _last_computed, "dts": len(oe_results)}


def _check_and_alert(oe_results: dict):
    """Generate A-03 (RPF) and A-06 (OE exceedance) alerts."""
    import src.derms.fleet as fleet

    existing = {a["message"][:70] for a in fleet.get_alerts(200) if not a["resolved"]}

    for dt_id, dt_oe in oe_results.items():
        # A-03: Reverse Power Flow
        if dt_oe["rpf"] and dt_oe["export_kw"] > 5:
            msg = (
                f"A-03 RPF: {dt_id} exporting {dt_oe['export_kw']:.1f} kW — "
                f"DER gen {dt_oe['total_der_gen_kw']:.0f} kW > load {dt_oe['consumer_load_kw']:.0f} kW"
            )
            if msg[:70] not in existing:
                fleet.add_alert("warning", "HIGH", msg, "oe-engine", dt_id, "DT")

        # A-06: OE exceedance (> 2 blocks in real system; 1 in prototype)
        for der in dt_oe["ders"]:
            if der["exceeding"] and der["excess_kw"] > 1.0:
                msg = (
                    f"A-06 OE Exceedance: {der['der_id']} at {der['oe_kw']:.1f} kW OE, "
                    f"generating {der['current_kw']:.1f} kW (+{der['excess_kw']:.1f} kW excess)"
                )
                if msg[:70] not in existing:
                    fleet.add_alert("warning", "MEDIUM", msg, "oe-engine", der["der_id"], "DER")


# ── 48-block schedule ────────────────────────────────────────────────────────

def compute_oe_schedule(feeder_id: str = "LK1") -> dict:
    """
    Compute 48-block (30-min) OE schedule for today using generation forecast.
    Called once per day (or on demand). Uses rule-based approach since
    we'd need 48 separate load flow runs for full accuracy.
    """
    global _oe_schedule

    import src.derms.fleet as fleet
    from src.data.real_pilot_data import LANKA_DTS

    ders = fleet.get_all_ders()
    dts = fleet.get_all_dts()
    dt_map = {dt["dt_id"]: dt for dt in dts}
    lk1_dt_map = {d["id"]: d for d in LANKA_DTS}

    for dt_id, dt in dt_map.items():
        if dt_id in lk1_dt_map and not dt.get("rated_kva"):
            dt["rated_kva"] = lk1_dt_map[dt_id]["rated_kva"]
        dt.setdefault("total_load_kw", lk1_dt_map.get(dt_id, {}).get("total_load_kw", dt.get("rated_kva", 100) * 0.4))

    dt_ders: dict = {}
    for der in ders:
        dt_id = der.get("dt_id")
        if dt_id:
            dt_ders.setdefault(dt_id, []).append(der)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for dt_id, dt_ders_list in dt_ders.items():
        dt = dt_map.get(dt_id, {})
        rated_kva = dt.get("rated_kva", 100)
        total_nameplate = sum(d.get("nameplate_kw", 0) for d in dt_ders_list) or 1.0

        blocks = []
        for b in range(48):
            hour_ist = b * 0.5
            label = f"{int(hour_ist):02d}:{int((hour_ist % 1) * 60):02d}"

            # Solar generation factor
            gen_factor = max(0, math.sin(math.pi * (hour_ist - 6) / 12)) if 6 <= hour_ist <= 18 else 0

            consumer_load = _dt_load_kw(dt, hour_ist)
            total_forecast_gen = sum(
                d.get("nameplate_kw", 0) * gen_factor * 0.88
                for d in dt_ders_list
            )
            rpf_forecast = total_forecast_gen > consumer_load

            per_der = []
            for der in dt_ders_list:
                nameplate_kw = der.get("nameplate_kw", 0)
                prop = nameplate_kw / total_nameplate
                oe_th = prop * rated_kva * 0.95 * 0.80
                oe_rpf = prop * consumer_load
                oe_kw = round(max(0.0, min(nameplate_kw, oe_th, oe_rpf)), 2)
                forecast_kw = round(nameplate_kw * gen_factor * 0.88, 2)
                per_der.append({
                    "der_id": der["der_id"],
                    "oe_kw": oe_kw,
                    "forecast_kw": forecast_kw,
                    "within_envelope": forecast_kw <= oe_kw + 0.1,
                })

            blocks.append({
                "block": b,
                "time": label,
                "consumer_load_kw": round(consumer_load, 2),
                "forecast_gen_kw": round(total_forecast_gen, 2),
                "rpf_forecast": rpf_forecast,
                "ders": per_der,
            })

        _oe_schedule[dt_id] = blocks

    return {"status": "ok", "date": today, "dts": len(dt_ders), "blocks_per_dt": 48}
