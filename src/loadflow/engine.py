"""
pandapower Load Flow Engine
============================
Builds a radial distribution network from assumed parameters (MVP) or
CIM model (Phase 2) and runs backward-forward sweep (BFS) power flow.

Three run modes:
  - quasi_realtime : every 15 min, uses current MDMS DT aggregations
  - forecast       : uses generation/load forecast for next 24h (96 intervals)
  - whatif         : on-demand, adds a hypothetical DER to test impact

Outputs per run:
  - Voltage (pu) at each DT bus
  - DT loading (%)
  - Line current (kA) on each segment
  - DER Operating Capacity (DOC) per DER — max kW before any constraint
  - Binding constraint label (e.g. "Voltage at LK1-DT-08" or "Thermal LK1-DT-07→LK1-DT-08")

Results are flagged "indicative" until a CIM model is loaded.
"""
import math
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ─── In-memory result cache (latest run per feeder) ──────────────────────────
_results: dict = {}


def get_latest_results(feeder_id: str = "LK1") -> Optional[dict]:
    return _results.get(feeder_id)


def get_all_results() -> dict:
    return dict(_results)


# ─── Network builder ─────────────────────────────────────────────────────────

def _build_network(feeder_id: str, dts: list, ders: list, cfg) -> tuple:
    """
    Build a pandapower radial network from DT list + config assumptions.

    dts  : list of dicts with keys: id, name, rated_kva, order, net_load_kw
    ders : list of dicts with keys: der_id, dt_id, nameplate_kw, current_kw
    cfg  : FeederNetworkConfig

    Returns (net, bus_map) where bus_map maps dt_id → pandapower bus index.
    """
    import pandapower as pp

    net = pp.create_empty_network(f_hz=50, sn_mva=10)
    conductor = cfg.conductor

    # Sort DTs by electrical order (ascending from source)
    ordered_dts = sorted(dts, key=lambda d: d.get("order", 99))

    # ── Bus 0: Feeder head (11 kV slack bus) ──────────────────────────────
    slack_bus = pp.create_bus(
        net, vn_kv=11.0, name=f"{feeder_id}_HEAD", type="b"
    )
    pp.create_ext_grid(
        net, bus=slack_bus,
        vm_pu=cfg.feeder_head_voltage_pu,
        name="Feeder Head (11kV slack)"
    )

    bus_map = {}   # dt_id → LV bus index (0.4 kV secondary)
    prev_hv_bus = slack_bus

    for dt in ordered_dts:
        dt_id = dt["id"]
        # Override kVA if configured
        rated_kva = cfg.dt_kva_overrides.get(dt_id, dt["rated_kva"])

        # ── HV bus (11 kV primary side of DT) ────────────────────────────
        hv_bus = pp.create_bus(
            net, vn_kv=11.0, name=f"{dt_id}_HV", type="b"
        )

        # ── LV bus (0.4 kV secondary / consumer side) ────────────────────
        lv_bus = pp.create_bus(
            net, vn_kv=0.4, name=f"{dt_id}_LV", type="b"
        )
        bus_map[dt_id] = lv_bus

        # ── Distribution transformer ──────────────────────────────────────
        pp.create_transformer_from_parameters(
            net,
            hv_bus=hv_bus, lv_bus=lv_bus,
            sn_mva=rated_kva / 1000,
            vn_hv_kv=11.0, vn_lv_kv=0.4,
            vkr_percent=1.0,
            vk_percent=cfg.dt_transformer_z_pct,
            pfe_kw=0.0, i0_percent=0.0,
            name=f"T_{dt_id}",
        )

        # ── 11 kV line: previous HV bus → this DT HV bus ─────────────────
        # Distance approximated from GPS coordinates
        dist_km = _approx_distance_km(ordered_dts, dt, prev_hv_bus == slack_bus)
        if dist_km < 0.05:
            dist_km = 0.05   # minimum segment length

        pp.create_line_from_parameters(
            net,
            from_bus=prev_hv_bus, to_bus=hv_bus,
            length_km=dist_km,
            r_ohm_per_km=conductor["r_ohm_per_km"],
            x_ohm_per_km=conductor["x_ohm_per_km"],
            c_nf_per_km=0.0,
            max_i_ka=conductor["max_current_a"] / 1000,
            name=f"LINE_{feeder_id}_{dt['order']}",
        )
        prev_hv_bus = hv_bus

        # ── Load injection: net load at this DT ───────────────────────────
        net_load_kw = max(0, dt.get("net_load_kw", dt.get("total_load_kw", 10.0)))
        net_load_kvar = net_load_kw * math.tan(math.acos(0.90))  # assume PF 0.90 load side
        pp.create_load(
            net, bus=lv_bus,
            p_mw=net_load_kw / 1000,
            q_mvar=net_load_kvar / 1000,
            name=f"LOAD_{dt_id}",
        )

    # ── DER generators (one sgen per DER on its LV bus) ───────────────────
    for der in ders:
        lv_bus = bus_map.get(der["dt_id"])
        if lv_bus is None:
            continue
        gen_kw = max(0, der.get("current_kw", 0))
        if gen_kw > 0:
            pp.create_sgen(
                net, bus=lv_bus,
                p_mw=gen_kw / 1000,
                q_mvar=0.0,          # unity PF (typical inverter default)
                name=f"DER_{der['der_id']}",
            )

    return net, bus_map, ordered_dts


def _approx_distance_km(ordered_dts: list, current_dt: dict, from_source: bool) -> float:
    """
    Approximate line segment length from GPS coordinates.
    Uses Haversine formula between consecutive DTs.
    Falls back to 0.5 km if no GPS available.
    """
    order = current_dt.get("order", 1)
    if from_source or order <= 1:
        # First segment: substation → first DT (approximate 0.3–0.8 km)
        return 0.5

    prev = next((d for d in ordered_dts if d.get("order") == order - 1), None)
    if not prev:
        return 0.5

    lat1, lon1 = prev.get("lat", 25.27), prev.get("lng", 82.99)
    lat2, lon2 = current_dt.get("lat", 25.27), current_dt.get("lng", 82.99)

    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    dist = R * 2 * math.asin(math.sqrt(a))
    return max(0.05, dist)


# ─── Load Flow Runner ─────────────────────────────────────────────────────────

def run_load_flow(feeder_id: str, dts: list, ders: list, label: str = "quasi_realtime") -> dict:
    """
    Run pandapower BFS load flow and compute DOC for each DER.

    dts  : enriched with net_load_kw (import_kw - export_kw per DT)
    ders : enriched with current_kw (current generation)

    Returns result dict stored in _results[feeder_id].
    """
    from src.loadflow.network_config import get_feeder_config
    import pandapower as pp

    t_start = time.time()
    cfg = get_feeder_config(feeder_id)

    try:
        net, bus_map, ordered_dts = _build_network(feeder_id, dts, ders, cfg)
        pp.runpp(net, algorithm="bfsw", numba=False)
    except Exception as e:
        logger.error(f"Load flow failed for {feeder_id}: {e}")
        return {"status": "failed", "error": str(e)}

    elapsed = time.time() - t_start

    # ── Extract bus voltages ───────────────────────────────────────────────
    bus_voltages = {}
    for dt in ordered_dts:
        lv_bus = bus_map.get(dt["id"])
        if lv_bus is not None and lv_bus < len(net.res_bus):
            vm_pu = float(net.res_bus.at[lv_bus, "vm_pu"])
            bus_voltages[dt["id"]] = {
                "dt_id": dt["id"],
                "dt_name": dt["name"],
                "order": dt["order"],
                "vm_pu": round(vm_pu, 4),
                "vm_v": round(vm_pu * 400, 1),
                "violation": vm_pu > cfg.voltage_upper_pu or vm_pu < cfg.voltage_lower_pu,
                "pre_alert": vm_pu > cfg.voltage_pre_alert_upper_pu or vm_pu < cfg.voltage_pre_alert_lower_pu,
                "status": (
                    "violation" if vm_pu > cfg.voltage_upper_pu or vm_pu < cfg.voltage_lower_pu
                    else "pre_alert" if vm_pu > cfg.voltage_pre_alert_upper_pu or vm_pu < cfg.voltage_pre_alert_lower_pu
                    else "normal"
                ),
            }

    # ── Extract line loading ───────────────────────────────────────────────
    line_results = []
    for idx, row in net.res_line.iterrows():
        if idx < len(net.line):
            line_name = net.line.at[idx, "name"]
            max_i = float(net.line.at[idx, "max_i_ka"])
            i_ka = float(row["i_ka"])
            loading_pct = (i_ka / max_i * 100) if max_i > 0 else 0
            line_results.append({
                "line": line_name,
                "i_ka": round(i_ka, 4),
                "loading_pct": round(loading_pct, 1),
                "thermal_violation": loading_pct > 100,
            })

    # ── DT transformer loading ─────────────────────────────────────────────
    dt_loading = {}
    for idx, row in net.res_trafo.iterrows():
        if idx < len(net.trafo):
            trafo_name = net.trafo.at[idx, "name"]
            dt_id = trafo_name.replace("T_", "")
            dt_loading[dt_id] = round(float(row["loading_percent"]), 1)

    # ── DOC calculation per DER ────────────────────────────────────────────
    doc_results = _compute_doc(feeder_id, dts, ders, cfg, bus_map, ordered_dts)

    result = {
        "feeder_id": feeder_id,
        "run_label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 2),
        "model_source": "CIM" if cfg.use_cim_model else "assumed",
        "indicative": not cfg.use_cim_model,
        "conductor_type": cfg.conductor_type,
        "feeder_head_voltage_pu": cfg.feeder_head_voltage_pu,
        "bus_voltages": list(bus_voltages.values()),
        "line_loading": line_results,
        "dt_loading_pct": dt_loading,
        "doc_per_der": doc_results,
        "violations": {
            "voltage": [v for v in bus_voltages.values() if v["violation"]],
            "thermal": [l for l in line_results if l["thermal_violation"]],
        },
    }

    _results[feeder_id] = result
    logger.info(
        f"Load flow {feeder_id} completed in {elapsed:.2f}s | "
        f"violations: V={len(result['violations']['voltage'])} "
        f"T={len(result['violations']['thermal'])}"
    )
    return result


def _compute_doc(feeder_id: str, dts: list, ders: list, cfg, bus_map: dict, ordered_dts: list) -> list:
    """
    Compute DER Operating Capacity via incremental injection sweep.
    For each DER: increase generation from 0 → nameplate in cfg.doc_sweep_step_kw steps.
    DOC = max generation before first voltage or thermal violation anywhere on feeder.
    """
    import pandapower as pp

    doc_results = []

    for der in ders:
        dt_id = der["dt_id"]
        lv_bus = bus_map.get(dt_id)
        nameplate_kw = der.get("nameplate_kw", 0)
        if lv_bus is None or nameplate_kw == 0:
            doc_results.append({
                "der_id": der["der_id"],
                "dt_id": dt_id,
                "nameplate_kw": nameplate_kw,
                "doc_kw": nameplate_kw,
                "doc_pct": 100.0,
                "binding_constraint": "Unconstrained",
                "constrained": False,
            })
            continue

        doc_kw = 0.0
        binding = "Unconstrained"
        step = cfg.doc_sweep_step_kw

        # Build base network (all other DERs at current output, this DER at 0)
        base_dts = [dict(d) for d in dts]
        base_ders = [dict(d) for d in ders if d["der_id"] != der["der_id"]]

        test_kw = 0.0
        while test_kw <= nameplate_kw:
            sweep_ders = base_ders + [{**der, "current_kw": test_kw}]
            try:
                test_net, test_bus_map, _ = _build_network(feeder_id, base_dts, sweep_ders, cfg)
                pp.runpp(test_net, algorithm="bfsw", numba=False)
            except Exception:
                break

            # Check voltage at all buses
            violated = False
            for bid in test_bus_map.values():
                if bid >= len(test_net.res_bus):
                    continue
                vm = float(test_net.res_bus.at[bid, "vm_pu"])
                if vm > cfg.voltage_upper_pu:
                    # Find which DT
                    violated_dt = next(
                        (d["name"] for d in ordered_dts if test_bus_map.get(d["id"]) == bid),
                        f"Bus {bid}"
                    )
                    binding = f"Voltage at {violated_dt} ({vm:.3f} pu > {cfg.voltage_upper_pu} pu)"
                    violated = True
                    break
                if vm < cfg.voltage_lower_pu:
                    violated_dt = next(
                        (d["name"] for d in ordered_dts if test_bus_map.get(d["id"]) == bid),
                        f"Bus {bid}"
                    )
                    binding = f"Low voltage at {violated_dt} ({vm:.3f} pu < {cfg.voltage_lower_pu} pu)"
                    violated = True
                    break

            # Check thermal on all lines
            if not violated:
                for idx, row in test_net.res_line.iterrows():
                    if idx >= len(test_net.line):
                        continue
                    max_i = float(test_net.line.at[idx, "max_i_ka"])
                    i_ka = float(row["i_ka"])
                    if max_i > 0 and i_ka > max_i:
                        line_name = test_net.line.at[idx, "name"]
                        binding = f"Thermal on {line_name} ({i_ka*1000:.0f} A > {max_i*1000:.0f} A)"
                        violated = True
                        break

            if violated:
                break

            doc_kw = test_kw
            test_kw = round(test_kw + step, 3)

        doc_pct = (doc_kw / nameplate_kw * 100) if nameplate_kw > 0 else 100.0
        doc_results.append({
            "der_id": der["der_id"],
            "dt_id": dt_id,
            "nameplate_kw": nameplate_kw,
            "doc_kw": round(doc_kw, 2),
            "doc_pct": round(doc_pct, 1),
            "binding_constraint": binding,
            "constrained": doc_kw < nameplate_kw,
        })

    return doc_results


# ─── What-If Simulation ───────────────────────────────────────────────────────

def run_whatif(feeder_id: str, dts: list, ders: list,
               target_dt_id: str, hypothetical_kw: float) -> dict:
    """
    Add a hypothetical DER at target_dt_id and report voltage/loading impact.
    Runs in < 30s for pilot-scale networks.
    """
    import pandapower as pp
    from src.loadflow.network_config import get_feeder_config

    cfg = get_feeder_config(feeder_id)

    # Base run (current state)
    base_net, base_bus_map, ordered_dts = _build_network(feeder_id, dts, ders, cfg)
    try:
        pp.runpp(base_net, algorithm="bfsw", numba=False)
    except Exception as e:
        return {"status": "failed", "error": f"Base run failed: {e}"}

    # Hypothetical run (add new DER)
    hyp_ders = list(ders) + [{
        "der_id": "WHATIF_NEW",
        "dt_id": target_dt_id,
        "nameplate_kw": hypothetical_kw,
        "current_kw": hypothetical_kw,   # assume full output (worst case)
    }]
    hyp_net, hyp_bus_map, _ = _build_network(feeder_id, dts, hyp_ders, cfg)
    try:
        pp.runpp(hyp_net, algorithm="bfsw", numba=False)
    except Exception as e:
        return {"status": "failed", "error": f"Hypothetical run failed: {e}"}

    # Compare
    comparison = []
    for dt in ordered_dts:
        lv_bus = base_bus_map.get(dt["id"])
        if lv_bus is None or lv_bus >= len(base_net.res_bus):
            continue
        base_vm = float(base_net.res_bus.at[lv_bus, "vm_pu"])
        hyp_vm = float(hyp_net.res_bus.at[lv_bus, "vm_pu"])
        delta = hyp_vm - base_vm
        comparison.append({
            "dt_id": dt["id"],
            "dt_name": dt["name"],
            "base_vm_pu": round(base_vm, 4),
            "hyp_vm_pu": round(hyp_vm, 4),
            "delta_pu": round(delta, 4),
            "violation_after": hyp_vm > cfg.voltage_upper_pu or hyp_vm < cfg.voltage_lower_pu,
            "pre_alert_after": hyp_vm > cfg.voltage_pre_alert_upper_pu or hyp_vm < cfg.voltage_pre_alert_lower_pu,
        })

    any_violation = any(c["violation_after"] for c in comparison)
    recommendation = (
        "NOT RECOMMENDED — voltage violation after connection"
        if any_violation
        else "SAFE — no voltage or thermal violations"
    )

    return {
        "feeder_id": feeder_id,
        "target_dt_id": target_dt_id,
        "hypothetical_kw": hypothetical_kw,
        "recommendation": recommendation,
        "voltage_violation": any_violation,
        "dt_comparison": comparison,
        "indicative": not cfg.use_cim_model,
        "model_source": "CIM" if cfg.use_cim_model else "assumed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Sandbox Runner (explicit config, no cache) ───────────────────────────────

def run_load_flow_sandbox(feeder_id: str, dts: list, ders: list, cfg) -> dict:
    """
    Run load flow with explicit config — used by the interactive sandbox.
    Identical logic to run_load_flow but does NOT update _results cache.
    """
    import pandapower as pp

    t_start = time.time()

    try:
        net, bus_map, ordered_dts = _build_network(feeder_id, dts, ders, cfg)
        pp.runpp(net, algorithm="bfsw", numba=False)
    except Exception as e:
        logger.error(f"Sandbox load flow failed for {feeder_id}: {e}")
        return {"status": "failed", "error": str(e)}

    elapsed = time.time() - t_start

    bus_voltages = {}
    for dt in ordered_dts:
        lv_bus = bus_map.get(dt["id"])
        if lv_bus is not None and lv_bus < len(net.res_bus):
            vm_pu = float(net.res_bus.at[lv_bus, "vm_pu"])
            bus_voltages[dt["id"]] = {
                "dt_id": dt["id"],
                "dt_name": dt.get("name", dt["id"]),
                "order": dt.get("order", 0),
                "vm_pu": round(vm_pu, 4),
                "vm_v": round(vm_pu * 400, 1),
                "violation": vm_pu > cfg.voltage_upper_pu or vm_pu < cfg.voltage_lower_pu,
                "pre_alert": vm_pu > cfg.voltage_pre_alert_upper_pu or vm_pu < cfg.voltage_pre_alert_lower_pu,
                "status": (
                    "violation" if vm_pu > cfg.voltage_upper_pu or vm_pu < cfg.voltage_lower_pu
                    else "pre_alert" if vm_pu > cfg.voltage_pre_alert_upper_pu or vm_pu < cfg.voltage_pre_alert_lower_pu
                    else "normal"
                ),
            }

    line_results = []
    for idx, row in net.res_line.iterrows():
        if idx < len(net.line):
            line_name = net.line.at[idx, "name"]
            max_i = float(net.line.at[idx, "max_i_ka"])
            i_ka = float(row["i_ka"])
            loading_pct = (i_ka / max_i * 100) if max_i > 0 else 0
            line_results.append({
                "line": line_name,
                "i_ka": round(i_ka, 4),
                "loading_pct": round(loading_pct, 1),
                "thermal_violation": loading_pct > 100,
            })

    dt_loading = {}
    for idx, row in net.res_trafo.iterrows():
        if idx < len(net.trafo):
            trafo_name = net.trafo.at[idx, "name"]
            dt_id = trafo_name.replace("T_", "")
            dt_loading[dt_id] = round(float(row["loading_percent"]), 1)

    doc_results = _compute_doc(feeder_id, dts, ders, cfg, bus_map, ordered_dts)

    return {
        "feeder_id": feeder_id,
        "run_label": "sandbox",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 2),
        "model_source": "CIM" if cfg.use_cim_model else "assumed",
        "indicative": not cfg.use_cim_model,
        "conductor_type": cfg.conductor_type,
        "feeder_head_voltage_pu": cfg.feeder_head_voltage_pu,
        "bus_voltages": list(bus_voltages.values()),
        "line_loading": line_results,
        "dt_loading_pct": dt_loading,
        "doc_per_der": doc_results,
        "violations": {
            "voltage": [v for v in bus_voltages.values() if v["violation"]],
            "thermal": [l for l in line_results if l["thermal_violation"]],
        },
    }
