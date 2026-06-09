"""API routes for the dashboard — aggregated data for UI."""
import math
import random
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import src.derms.fleet as fleet

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/summary")
async def get_dashboard_summary():
    """Complete dashboard summary for the main overview."""
    return fleet.get_fleet_summary()


@router.get("/generation-profile")
async def get_generation_profile(hours: int = 24):
    """
    System-level generation profile for the past N hours.
    Merges DER generation with load data.
    """
    now = datetime.now(timezone.utc)
    points = []

    total_nameplate = sum(
        d["nameplate_kw"] for d in fleet.get_all_ders()
        if d.get("der_type") == "Solar PV"
    )
    total_bess = sum(
        d["nameplate_kw"] for d in fleet.get_all_ders()
        if d.get("der_type") == "BESS"
    )

    for i in range(hours * 4):  # 15-min intervals
        ts = now - timedelta(minutes=15 * (hours * 4 - i))
        h = ts.hour + ts.minute / 60.0

        # Solar generation
        solar_f = max(0, math.sin(math.pi * (h - 6) / 12)) if 6 <= h <= 18 else 0
        solar_kw = total_nameplate * solar_f * random.uniform(0.80, 0.95)

        # BESS (charge during day, discharge evening)
        if 10 <= h <= 15:
            bess_kw = -total_bess * 0.5 * solar_f  # Charging
        elif h >= 17:
            bess_kw = total_bess * 0.7  # Discharging
        else:
            bess_kw = 0

        # System load (MW scale for whole pilot)
        base_load = total_nameplate * 3
        morning = 0.3 * math.exp(-0.5 * ((h - 8.5) ** 2))
        evening = 0.4 * math.exp(-0.5 * ((h - 19.0) ** 2) / 2)
        load_kw = base_load * (0.4 + morning + evening) + random.uniform(-10, 10)
        net_load = max(0, load_kw - solar_kw - max(0, bess_kw))

        # Clear-sky forecast
        clear_sky = total_nameplate * solar_f

        points.append({
            "time": ts.strftime("%H:%M"),
            "timestamp": ts.isoformat(),
            "solar_kw": round(max(0, solar_kw), 1),
            "bess_kw": round(bess_kw, 1),
            "load_kw": round(load_kw, 1),
            "net_load_kw": round(net_load, 1),
            "clear_sky_kw": round(clear_sky, 1),
        })

    return {"period_hours": hours, "interval_min": 15, "data": points}


@router.get("/alerts")
async def get_alerts(limit: int = 50, unresolved_only: bool = False):
    """Get system alerts."""
    return {"alerts": fleet.get_alerts(limit, unresolved_only)}


class AlertAction(BaseModel):
    action: str   # "acknowledge" | "resolve"
    user: str = "operator"


@router.patch("/alerts/{alert_id}")
async def update_alert(alert_id: str, body: AlertAction):
    """Transition alert state: OPEN → ACKNOWLEDGED → RESOLVED."""
    if body.action == "acknowledge":
        alert = fleet.acknowledge_alert(alert_id, body.user)
    elif body.action == "resolve":
        alert = fleet.resolve_alert(alert_id, body.user)
    else:
        raise HTTPException(status_code=400, detail="action must be 'acknowledge' or 'resolve'")
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return {"status": "ok", "alert": alert}


@router.post("/alerts/seed-demo")
async def seed_demo_alerts():
    """Seed demo alerts matching PRD Module E.4 alert catalogue exactly."""
    alerts = [
        # Priority P1 — in-app + SMS + email to Grid Operator
        ("critical", "HIGH",   "DT Overload: LK1-DT-02 (Madhav Market) loading at 103% for 2 consecutive blocks — CEA thermal limit exceeded", "Pipeline A — DT meter", "LK1-DT-02", "DT"),
        ("critical", "HIGH",   "Voltage Violation: LK1-DT-05 (Rasmi Nagar) voltage 1.064 pu > 1.06 pu limit for 2 blocks — Grid Operator action required", "Pipeline A — DT meter", "LK1-DT-05", "DT"),
        ("critical", "HIGH",   "MDMS Feed Stale: No DT meter records received for > 60 min — pipeline A ingest failure, 4 of 8 DTs missing", "mdms-integration-svc", None, None),
        # Priority P2 — in-app + email to Grid Operator
        ("warning",  "HIGH",   "DT Pre-Alert: LK1-DT-08 (Sanketmochan Purani Gali) loading at 84% — within 80–100% pre-alert band", "Pipeline A — DT meter", "LK1-DT-08", "DT"),
        ("warning",  "HIGH",   "Voltage Pre-Alert: LK1-DT-06 voltage 1.043 pu — in 1.04–1.06 pu amber band (CERC pre-alert threshold)", "Pipeline A — DT meter", "LK1-DT-06", "DT"),
        ("warning",  "HIGH",   "Voltage Sustained: LK1-DT-05 voltage > 1.06 pu unresolved for 65 min — Nodal Officer email escalation triggered", "Pipeline A — DT meter", "LK1-DT-05", "DT"),
        ("warning",  "MEDIUM", "HC Red: LK1-DT-05 (Rasmi Nagar) hosting capacity utilised at 91% (54 kWp active / 59 kW HC limit) — pause new approvals", "der-registry-svc", "LK1-DT-05", "DT"),
        ("warning",  "MEDIUM", "Reverse Power Flow: LK1 feeder net import negative — DT aggregate export 23.4 kW, generation 68 kW > load 45 kW", "Pipeline A — DT meter", "LK1", "Feeder"),
        # Priority P3 — in-app to Grid Operator
        ("warning",  "MEDIUM", "DER Underperforming: LK1-DER-004 (50 kWp) actual 3.1 kW vs irradiance-expected 34.2 kW — performance ratio 9% (< 20% threshold for 3 daylight blocks)", "Pipeline C vs forecast", "LK1-DER-004", "DER"),
        ("info",     "LOW",    "Forecast: DT overload expected on LK1-DT-02 within next 4 blocks — forecast loading 87% at 14:30 IST, recommend DR event", "forecast-svc", "LK1-DT-02", "DT"),
    ]
    for args in alerts:
        fleet.add_alert(*args)
    return {"status": "seeded", "count": len(alerts)}


@router.get("/hosting-capacity")
async def get_hosting_capacity_summary():
    """Hosting capacity summary per feeder."""
    feeders = fleet.get_all_feeders()
    result = []
    for f in feeders:
        used = f.get("used_capacity_kw", 0)
        total = f.get("hosting_capacity_kw", 1)
        util_pct = (used / total * 100) if total > 0 else 0
        result.append({
            "feeder_id": f["feeder_id"],
            "name": f["name"],
            "hosting_capacity_kw": total,
            "used_capacity_kw": used,
            "available_kw": total - used,
            "utilization_pct": round(util_pct, 1),
            "traffic_light": (
                "red" if util_pct > 85
                else "amber" if util_pct > 60
                else "green"
            ),
        })
    return {"feeders": result}


@router.get("/voltage-monitoring")
async def get_voltage_monitoring():
    """Real-time voltage status for all DTs."""
    dts = fleet.get_all_dts()
    result = []
    for dt in dts:
        v1 = dt.get("voltage_l1", 230)
        v2 = dt.get("voltage_l2", 230)
        v3 = dt.get("voltage_l3", 230)
        avg_v = (v1 + v2 + v3) / 3
        violations = sum([
            v > 244 or v < 216 for v in [v1, v2, v3]
        ])
        result.append({
            "dt_id": dt["dt_id"],
            "name": dt.get("name", dt["dt_id"]),
            "feeder_id": dt["feeder_id"],
            "voltage_l1": v1, "voltage_l2": v2, "voltage_l3": v3,
            "avg_voltage": round(avg_v, 1),
            "nominal_v": 230.0,
            "deviation_pct": round((avg_v - 230) / 230 * 100, 2),
            "phase_violations": violations,
            "status": (
                "Critical" if violations >= 2
                else "Warning" if violations == 1 or abs(avg_v - 230) > 10
                else "Normal"
            ),
            "loading_pct": dt.get("current_loading_pct", 0),
        })
    return {"dts": result}


@router.get("/aggregators")
async def get_aggregators():
    """Get IEEE 2030.5 aggregator connection status."""
    from src.integrations.ieee2030_5.server import get_end_devices, get_der_resources
    end_devices = get_end_devices()
    der_resources = get_der_resources()

    result = []
    for edev_id, edev in end_devices.items():
        der_count = len(der_resources.get(edev_id, {}))
        # Find matching aggregator cache entry
        agg_info = next(
            (a for a in fleet.get_all_aggregators() if a.get("lfdi") == edev.lFDI), {}
        )
        result.append({
            "edev_id": edev_id,
            "agg_id": agg_info.get("agg_id", edev_id),
            "name": agg_info.get("name", f"Aggregator {edev.sFDI}"),
            "sfdi": edev.sFDI,
            "lfdi": edev.lFDI[:16] + "...",
            "status": "Online" if edev.enabled else "Offline",
            "der_count": der_count,
            "protocol": "IEEE 2030.5 / SEP 2.0",
            "last_seen": agg_info.get("last_seen", "Unknown"),
        })
    return {"aggregators": result}


@router.get("/dt/{dt_id}")
async def get_dt_drilldown(dt_id: str):
    """
    Per-DT drill-down: combines voltage, thermal, OE, DER list, and load flow results.
    """
    from src.loadflow.engine import get_latest_results
    from src.loadflow.oe_engine import get_oe_by_dt, get_oe_schedule
    from src.data.real_pilot_data import LANKA_DTS

    dts = fleet.get_all_dts()
    dt = next((d for d in dts if d["dt_id"] == dt_id), None)
    if not dt:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"DT {dt_id} not found")

    # Enrich with LANKA_DTS data
    lk1_meta = next((d for d in LANKA_DTS if d["id"] == dt_id), {})
    dt = {**dt, **{k: v for k, v in lk1_meta.items() if k not in dt or not dt[k]}}

    # DERs on this DT
    all_ders = fleet.get_all_ders()
    ders_on_dt = [d for d in all_ders if d.get("dt_id") == dt_id]

    # Load flow results
    lf = get_latest_results("LK1")
    lf_voltage = None
    lf_loading = None
    doc_for_dt = []
    if lf:
        lf_voltage = next((bv for bv in lf.get("bus_voltages", []) if bv["dt_id"] == dt_id), None)
        lf_loading = lf.get("dt_loading_pct", {}).get(dt_id)
        doc_for_dt = [d for d in lf.get("doc_per_der", []) if d["dt_id"] == dt_id]

    # OE data
    oe = get_oe_by_dt(dt_id)
    oe_schedule = get_oe_schedule(dt_id)

    # 24-slot OE chart data for UI
    chart_blocks = []
    if oe_schedule:
        # Downsample 48 → 24 blocks for chart readability
        for i in range(0, 48, 2):
            b1 = oe_schedule[i] if i < len(oe_schedule) else {}
            b2 = oe_schedule[i+1] if i+1 < len(oe_schedule) else b1
            total_oe = sum(d["oe_kw"] for d in b1.get("ders", []))
            total_gen = b1.get("forecast_gen_kw", 0)
            chart_blocks.append({
                "time": b1.get("time", ""),
                "oe_kw": round(total_oe, 2),
                "forecast_kw": round(total_gen, 2),
                "consumer_load_kw": round(b1.get("consumer_load_kw", 0), 2),
                "rpf_forecast": b1.get("rpf_forecast", False),
            })

    # Hosting capacity
    rated_kva = dt.get("rated_kva", 100)
    total_der_kw = sum(d.get("nameplate_kw", 0) for d in ders_on_dt)
    hc_kw = rated_kva * 0.95 * 0.80
    hc_used_pct = round(total_der_kw / hc_kw * 100, 1) if hc_kw > 0 else 0

    return {
        "dt": dt,
        "rated_kva": rated_kva,
        "consumer_count": lk1_meta.get("consumer_count", "?"),
        "ders": ders_on_dt,
        "der_count": len(ders_on_dt),
        "total_der_nameplate_kw": round(total_der_kw, 2),
        "total_der_generation_kw": round(sum(d.get("current_kw", 0) for d in ders_on_dt), 2),
        "loadflow": {
            "voltage": lf_voltage,
            "loading_pct": lf_loading,
            "doc": doc_for_dt,
            "run_ts": lf.get("timestamp") if lf else None,
        },
        "oe": oe,
        "hosting_capacity": {
            "hc_kw": round(hc_kw, 1),
            "used_kw": round(total_der_kw, 1),
            "used_pct": hc_used_pct,
            "traffic_light": "red" if hc_used_pct > 85 else "amber" if hc_used_pct > 60 else "green",
        },
        "oe_chart_24h": chart_blocks,
    }


@router.get("/p2p-transactions")
async def get_p2p_transactions():
    """Mock P2P transaction data (future module)."""
    # Simulated P2P transactions for UI demonstration
    import random, uuid
    from datetime import datetime, timedelta, timezone

    ders = fleet.get_all_ders()
    solar_ders = [d for d in ders if d.get("der_type") == "Solar PV" and d.get("current_kw", 0) > 0]

    transactions = []
    now = datetime.now(timezone.utc)
    for i in range(min(10, len(solar_ders))):
        der = solar_ders[i]
        tx_kw = round(der.get("current_kw", 0) * random.uniform(0.3, 0.7), 2)
        rate = round(random.uniform(4.5, 7.5), 2)
        transactions.append({
            "tx_id": f"P2P-{uuid.uuid4().hex[:8].upper()}",
            "seller_der": der["der_id"],
            "seller_location": der["location_name"],
            "units_kwh": round(tx_kw * 0.25, 3),  # 15-min interval
            "rate_per_kwh": rate,
            "amount": round(tx_kw * 0.25 * rate, 2),
            "status": random.choice(["Settled", "Settled", "Pending"]),
            "dt": der["dt_id"],
            "timestamp": (now - timedelta(minutes=random.randint(5, 120))).isoformat(),
        })

    total_volume = sum(t["units_kwh"] for t in transactions if t["status"] == "Settled")
    total_value = sum(t["amount"] for t in transactions if t["status"] == "Settled")

    return {
        "transactions": transactions,
        "summary": {
            "today_volume_kwh": round(total_volume, 2),
            "today_value_inr": round(total_value, 2),
            "transaction_count": len(transactions),
            "settled_count": len([t for t in transactions if t["status"] == "Settled"]),
            "avg_rate": round(total_value / total_volume, 2) if total_volume > 0 else 0,
        },
    }
