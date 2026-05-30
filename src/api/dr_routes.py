"""
API routes for Demand Response management:
  - Power demand analysis vs SLDC schedule
  - Consumer enrollment registry
  - Cost-benefit analysis
  - CBL (Continuous Baseline Load) engine — 10-of-10 method
  - MV&S (Measurement, Verification & Settlement)
  - Event invite / respond lifecycle
"""
import math
import random
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

import src.derms.fleet as fleet
import src.derms.dispatch as dispatch

router = APIRouter(prefix="/api/dr", tags=["Demand Response"])

# ---------------------------------------------------------------------------
# Mock enrolled consumer data
# In production: fetched from PuVVNL billing system
# ---------------------------------------------------------------------------
_CONSUMERS = [
    {
        "consumer_no": "VAR-C-001", "name": "Ramesh Kumar Verma",
        "address": "12, Lanka, Varanasi", "contact": "+91-9415XXXXXX",
        "tariff_category": "LT Commercial", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 25, "max_demand_kw": 18.5, "avg_demand_kw": 12.3,
        "enrolled": True, "enrollment_date": "2025-11-15",
        "feeder_id": "FDR-01", "dt_id": "DT-VAR-0234",
        "events_participated": 7, "total_savings_inr": 3420,
        "monthly_consumption_kwh": [1240,1380,1290,1150,1420,1580,1820,1750,1610,1490,1320,1180],
    },
    {
        "consumer_no": "VAR-C-002", "name": "Suresh Dairy Products Pvt Ltd",
        "address": "B-45, Sigra, Varanasi", "contact": "+91-9936XXXXXX",
        "tariff_category": "LT Industrial", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 75, "max_demand_kw": 62.0, "avg_demand_kw": 44.5,
        "enrolled": True, "enrollment_date": "2025-11-20",
        "feeder_id": "FDR-01", "dt_id": "DT-VAR-0234",
        "events_participated": 5, "total_savings_inr": 8750,
        "monthly_consumption_kwh": [3800,4100,3900,3600,4200,4800,5200,5100,4700,4400,4000,3700],
    },
    {
        "consumer_no": "VAR-C-003", "name": "Hotel Ganges View",
        "address": "Dashashwamedh Ghat Rd, Varanasi", "contact": "+91-9721XXXXXX",
        "tariff_category": "LT Commercial", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 50, "max_demand_kw": 38.0, "avg_demand_kw": 28.6,
        "enrolled": True, "enrollment_date": "2025-12-01",
        "feeder_id": "FDR-02", "dt_id": "DT-VAR-0445",
        "events_participated": 4, "total_savings_inr": 5200,
        "monthly_consumption_kwh": [2400,2600,2500,2200,2700,3100,3400,3300,3000,2800,2500,2300],
    },
    {
        "consumer_no": "VAR-C-004", "name": "BHU Medical College Canteen",
        "address": "BHU Campus, Lanka, Varanasi", "contact": "+91-9415XXXXXX",
        "tariff_category": "LT Commercial", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 30, "max_demand_kw": 22.0, "avg_demand_kw": 16.0,
        "enrolled": True, "enrollment_date": "2025-12-10",
        "feeder_id": "FDR-01", "dt_id": "DT-VAR-0237",
        "events_participated": 6, "total_savings_inr": 4100,
        "monthly_consumption_kwh": [1600,1700,1550,1400,1650,1900,2100,2050,1900,1750,1600,1500],
    },
    {
        "consumer_no": "VAR-C-005", "name": "Sarnath Cold Storage",
        "address": "Sarnath Rd, Varanasi", "contact": "+91-9839XXXXXX",
        "tariff_category": "LT Industrial", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 100, "max_demand_kw": 85.0, "avg_demand_kw": 65.0,
        "enrolled": False, "enrollment_date": None,
        "feeder_id": "FDR-03", "dt_id": "DT-VAR-0612",
        "events_participated": 0, "total_savings_inr": 0,
        "monthly_consumption_kwh": [5200,5600,5300,4900,5500,6200,6800,6700,6100,5700,5200,4900],
    },
    {
        "consumer_no": "VAR-C-006", "name": "Shree Ram Textile Mills",
        "address": "Bhelupur Industrial Area", "contact": "+91-9415XXXXXX",
        "tariff_category": "LT Industrial", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 150, "max_demand_kw": 120.0, "avg_demand_kw": 88.0,
        "enrolled": True, "enrollment_date": "2025-11-25",
        "feeder_id": "FDR-02", "dt_id": "DT-VAR-0448",
        "events_participated": 8, "total_savings_inr": 12400,
        "monthly_consumption_kwh": [7100,7600,7200,6600,7500,8400,9200,9100,8300,7700,7000,6500],
    },
    {
        "consumer_no": "VAR-C-007", "name": "Assi Nagar Housing Society",
        "address": "Assi Nagar, Varanasi", "contact": "+91-7355XXXXXX",
        "tariff_category": "LT Residential (Group)", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 40, "max_demand_kw": 30.0, "avg_demand_kw": 20.5,
        "enrolled": False, "enrollment_date": None,
        "feeder_id": "FDR-03", "dt_id": "DT-VAR-0614",
        "events_participated": 0, "total_savings_inr": 0,
        "monthly_consumption_kwh": [2000,2150,2050,1900,2100,2400,2700,2650,2400,2200,2000,1900],
    },
    {
        "consumer_no": "VAR-C-008", "name": "Kashi Vishwanath Mandir Trust",
        "address": "Vishwanath Gali, Varanasi", "contact": "+91-9415XXXXXX",
        "tariff_category": "LT Religious", "voltage_level": "LT (415V)",
        "contractual_demand_kva": 20, "max_demand_kw": 15.0, "avg_demand_kw": 10.2,
        "enrolled": True, "enrollment_date": "2026-01-05",
        "feeder_id": "FDR-01", "dt_id": "DT-VAR-0236",
        "events_participated": 2, "total_savings_inr": 950,
        "monthly_consumption_kwh": [820,880,850,790,900,1050,1150,1120,1020,940,870,820],
    },
]

# CBA parameters (INR per kWh)
_PEAK_TARIFF_RATE = 8.50    # cost avoided per kWh shed
_INCENTIVE_RATE   = 3.00    # incentive paid to consumer per kWh reduced

# Historical DR events for demo (supplement live events from dispatch module)
_HISTORICAL_EVENTS = [
    {
        "event_id": "DR-202603011430", "reason": "Peak Shaving",
        "target_kw": 75, "dispatched_kw": 68.5, "duration_min": 60,
        "feeder_id": "FDR-02", "status": "Completed", "consumers_participated": 3,
        "created_at": "2026-03-01T14:30:00Z",
    },
    {
        "event_id": "DR-202602281100", "reason": "Voltage Regulation",
        "target_kw": 40, "dispatched_kw": 40.0, "duration_min": 30,
        "feeder_id": "FDR-01", "status": "Completed", "consumers_participated": 2,
        "created_at": "2026-02-28T11:00:00Z",
    },
    {
        "event_id": "DR-202602271700", "reason": "Grid Congestion",
        "target_kw": 100, "dispatched_kw": 87.2, "duration_min": 90,
        "feeder_id": None, "status": "Completed", "consumers_participated": 5,
        "created_at": "2026-02-27T17:00:00Z",
    },
]


@router.get("/demand-analysis")
async def get_demand_analysis():
    """
    Power demand analysis: 24h forecast vs SLDC availability, shortfall detection,
    and DR trigger recommendation.
    In production: fetches from SLDC API and ADMS load-flow forecast model.
    """
    now = datetime.now(timezone.utc)
    ist_now = now + timedelta(hours=5, minutes=30)
    hour_ist = ist_now.hour + ist_now.minute / 60.0

    # Scale to pilot (roughly 3x installed DER capacity as total area load)
    total_cap_kw = sum(d["nameplate_kw"] for d in fleet.get_all_ders())
    pilot_scale_mw = max(2.0, total_cap_kw * 3 / 1000)

    points = []
    for h in range(24):
        base     = pilot_scale_mw * 0.45
        morning  = pilot_scale_mw * 0.28 * math.exp(-0.5 * ((h - 9) ** 2) / 2)
        evening  = pilot_scale_mw * 0.40 * math.exp(-0.5 * ((h - 19) ** 2) / 2)
        demand_mw = base + morning + evening + random.uniform(-0.05, 0.05) * pilot_scale_mw

        # SLDC scheduled availability — constrained during evening peak
        if 17 <= h <= 22:
            avail_mw = demand_mw * random.uniform(0.82, 0.93)
        elif 8 <= h <= 12:
            avail_mw = demand_mw * random.uniform(0.90, 1.05)
        else:
            avail_mw = demand_mw * random.uniform(0.95, 1.10)

        shortfall_kw    = max(0, (demand_mw - avail_mw) * 1000)
        dr_threshold_kw = demand_mw * 1000 * 0.05   # 5% threshold

        points.append({
            "hour":           f"{h:02d}:00",
            "demand_mw":      round(demand_mw, 3),
            "sldc_avail_mw":  round(avail_mw, 3),
            "shortfall_mw":   round(shortfall_kw / 1000, 3),
            "shortfall_kw":   round(shortfall_kw, 1),
            "trigger_dr":     shortfall_kw >= dr_threshold_kw,
            "is_past":        h <= int(hour_ist),
        })

    current = points[int(hour_ist) % 24]
    enrolled = [c for c in _CONSUMERS if c["enrolled"]]
    enrolled_demand_kw = sum(c["avg_demand_kw"] for c in enrolled)

    if current["trigger_dr"]:
        dr_recommendation = {
            "recommended": True,
            "shortfall_kw": current["shortfall_kw"],
            "suggested_target_kw": round(current["shortfall_kw"] * 0.8, 1),
            "enrolled_consumers": len(enrolled),
            "available_flex_kw": round(enrolled_demand_kw * 0.3, 1),
            "message": (
                f"Demand shortfall of {current['shortfall_kw']:.1f} kW detected at "
                f"{current['hour']} IST. Recommend DR event targeting "
                f"{round(current['shortfall_kw']*0.8,1)} kW reduction using "
                f"{len(enrolled)} enrolled consumers."
            ),
        }
    else:
        dr_recommendation = {
            "recommended": False,
            "shortfall_kw": 0,
            "available_flex_kw": round(enrolled_demand_kw * 0.3, 1),
            "enrolled_consumers": len(enrolled),
            "message": "No significant demand shortfall. Grid conditions normal.",
        }

    return {
        "as_of": now.isoformat(),
        "hour_ist": int(hour_ist),
        "system_peak_mw": round(max(p["demand_mw"] for p in points), 3),
        "max_shortfall_kw": round(max(p["shortfall_kw"] for p in points), 1),
        "peak_shortfall_hours": [p["hour"] for p in points if p["shortfall_kw"] > 0],
        "dr_threshold_pct": 5.0,
        "dr_recommendation": dr_recommendation,
        "hourly": points,
    }


@router.get("/consumers")
async def get_consumers(enrolled_only: bool = False):
    """
    Consumer enrollment registry.
    In production: fetches from PuVVNL billing system
    (consumer number, tariff category, demand history, etc.).
    """
    consumers = _CONSUMERS if not enrolled_only else [c for c in _CONSUMERS if c["enrolled"]]
    enrolled  = [c for c in _CONSUMERS if c["enrolled"]]

    return {
        "total_consumers":           len(_CONSUMERS),
        "enrolled_count":            len(enrolled),
        "total_enrolled_demand_kw":  round(sum(c["avg_demand_kw"] for c in enrolled), 1),
        "total_savings_inr":         sum(c["total_savings_inr"] for c in enrolled),
        "consumers":                 consumers,
    }


@router.get("/consumers/lookup")
async def lookup_consumer(consumer_no: str):
    """
    Simulate a billing system lookup by consumer number.
    In production: queries PuVVNL MDMS / billing system API.
    Returns master data for review before enrollment.
    """
    # Check if already in our registry
    existing = next((c for c in _CONSUMERS if c["consumer_no"] == consumer_no), None)
    if existing:
        return {"found": True, "source": "DERMS Registry", "consumer": existing}

    # Simulate a billing system hit for unknown consumer numbers
    # In production this would call the actual billing API
    if not consumer_no.startswith("VAR-"):
        return {"found": False, "message": f"Consumer {consumer_no} not found in PuVVNL billing system."}

    # Generate plausible billing data for demo
    import hashlib
    seed = int(hashlib.md5(consumer_no.encode()).hexdigest()[:8], 16)
    rng  = random.Random(seed)
    avg_demand = round(rng.uniform(8, 90), 1)
    max_demand = round(avg_demand * rng.uniform(1.2, 1.5), 1)
    tariffs    = ["LT Commercial", "LT Industrial", "LT Residential (Group)", "LT Religious"]
    feeders    = ["FDR-01", "FDR-02", "FDR-03"]
    mock = {
        "consumer_no":               consumer_no,
        "name":                      f"Consumer {consumer_no}",
        "address":                   "Varanasi, Uttar Pradesh",
        "contact":                   "+91-9XXXXXXXXX",
        "tariff_category":           rng.choice(tariffs),
        "voltage_level":             "LT (415V)",
        "contractual_demand_kva":    round(max_demand * 1.1, 0),
        "max_demand_kw":             max_demand,
        "avg_demand_kw":             avg_demand,
        "enrolled":                  False,
        "enrollment_date":           None,
        "feeder_id":                 rng.choice(feeders),
        "dt_id":                     f"DT-VAR-0{rng.randint(200,700)}",
        "events_participated":       0,
        "total_savings_inr":         0,
        "monthly_consumption_kwh":   [round(avg_demand * 720 * rng.uniform(0.8, 1.2)) for _ in range(12)],
    }
    return {"found": True, "source": "PuVVNL Billing System", "consumer": mock}


@router.post("/consumers/{consumer_no}/enroll")
async def enroll_consumer(consumer_no: str, body: dict = {}):
    """
    Enroll a consumer in the DR program.
    If the consumer is not yet in the registry, add them from billing data.
    Body (optional): consumer data fetched from billing lookup.
    """
    existing = next((c for c in _CONSUMERS if c["consumer_no"] == consumer_no), None)

    if existing:
        if existing["enrolled"]:
            return {"status": "already_enrolled", "consumer_no": consumer_no,
                    "message": f"{consumer_no} is already enrolled in the DR program."}
        existing["enrolled"]        = True
        existing["enrollment_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {"status": "enrolled", "consumer_no": consumer_no,
                "message": f"{existing['name']} enrolled successfully in PUVVNL DR Program.",
                "consumer": existing}

    # New consumer — add from billing lookup data passed in body
    if not body:
        return {"status": "error", "message": "Consumer not found in registry. Provide billing data in request body."}

    new_consumer = {**body, "enrolled": True,
                    "enrollment_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "events_participated": 0, "total_savings_inr": 0}
    _CONSUMERS.append(new_consumer)
    return {"status": "registered_and_enrolled", "consumer_no": consumer_no,
            "message": f"{new_consumer.get('name', consumer_no)} registered and enrolled in PUVVNL DR Program.",
            "consumer": new_consumer}


@router.post("/consumers/{consumer_no}/unenroll")
async def unenroll_consumer(consumer_no: str):
    """Remove a consumer from the DR program (soft unenroll — keeps record)."""
    consumer = next((c for c in _CONSUMERS if c["consumer_no"] == consumer_no), None)
    if not consumer:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Consumer {consumer_no} not found.")
    if not consumer["enrolled"]:
        return {"status": "not_enrolled", "message": f"{consumer_no} is not currently enrolled."}
    consumer["enrolled"]        = False
    consumer["enrollment_date"] = None
    return {"status": "unenrolled", "consumer_no": consumer_no,
            "message": f"{consumer['name']} unenrolled from the DR program.",
            "consumer": consumer}


@router.get("/cost-benefit")
async def get_cost_benefit():
    """
    Cost-benefit analysis across all DR events.
    Calculates: energy saved, cost avoided (peak tariff), incentives paid,
    net benefit, and per-consumer savings breakdown.
    """
    live_events = [
        {**e, "consumers_participated": random.randint(1, 4)}
        for e in dispatch.get_dr_events()
    ]
    all_events = live_events + _HISTORICAL_EVENTS

    cba_events = []
    total_energy_kwh     = 0.0
    total_cost_avoided   = 0.0
    total_incentives     = 0.0

    for ev in all_events:
        dispatched_kw   = ev.get("dispatched_kw", 0)
        duration_h      = ev.get("duration_min", 60) / 60
        energy_kwh      = dispatched_kw * duration_h
        cost_avoided    = energy_kwh * _PEAK_TARIFF_RATE
        incentives      = energy_kwh * _INCENTIVE_RATE
        net_benefit     = cost_avoided - incentives
        consumers_n     = ev.get("consumers_participated", 1) or 1

        total_energy_kwh   += energy_kwh
        total_cost_avoided += cost_avoided
        total_incentives   += incentives

        cba_events.append({
            "event_id":                  ev.get("event_id"),
            "reason":                    ev.get("reason", "DR"),
            "feeder_id":                 ev.get("feeder_id") or "All",
            "status":                    ev.get("status", "Active"),
            "created_at":                ev.get("created_at"),
            "dispatched_kw":             dispatched_kw,
            "duration_min":              ev.get("duration_min", 60),
            "energy_saved_kwh":          round(energy_kwh, 2),
            "peak_tariff_inr_per_kwh":   _PEAK_TARIFF_RATE,
            "cost_avoided_inr":          round(cost_avoided, 2),
            "incentive_rate_inr_per_kwh": _INCENTIVE_RATE,
            "incentives_paid_inr":       round(incentives, 2),
            "net_benefit_inr":           round(net_benefit, 2),
            "consumers_participated":    consumers_n,
            "avg_saving_per_consumer_inr": round(incentives / consumers_n, 2),
        })

    net_total = total_cost_avoided - total_incentives

    return {
        "summary": {
            "total_events":              len(cba_events),
            "total_energy_saved_kwh":    round(total_energy_kwh, 2),
            "total_cost_avoided_inr":    round(total_cost_avoided, 2),
            "total_incentives_paid_inr": round(total_incentives, 2),
            "net_benefit_inr":           round(net_total, 2),
            "benefit_cost_ratio":        round(total_cost_avoided / max(total_incentives, 1), 2),
            "peak_tariff_rate":          _PEAK_TARIFF_RATE,
            "incentive_rate":            _INCENTIVE_RATE,
        },
        "consumer_savings": [
            {
                "consumer_no":             c["consumer_no"],
                "name":                    c["name"],
                "events_participated":     c["events_participated"],
                "total_savings_inr":       c["total_savings_inr"],
                "avg_saving_per_event_inr": round(
                    c["total_savings_inr"] / max(c["events_participated"], 1), 2
                ),
            }
            for c in _CONSUMERS
            if c["enrolled"] and c["events_participated"] > 0
        ],
        "events": cba_events,
    }


# ---------------------------------------------------------------------------
# CBL Engine — 10-of-10 method (AEIC/DSMIG standard)
# In production: queries MDMS 15-min interval data for 10 comparable days
# ---------------------------------------------------------------------------

def _build_cbl_profile(consumer: dict, event_hour_start: int, event_duration_h: float,
                       apply_maf: bool = True) -> dict:
    """
    Build baseline load profile using 10-of-10 method with Morning Adjustment Factor (MAF).

    MAF = pre_event_demand_today / pre_event_demand_historical
    Applied multiplicatively to all baseline slots to account for event-day conditions.
    In production: pre_event_demand_today comes from MDMS 2-hour pre-event window readings.
    """
    avg_demand = consumer["avg_demand_kw"]
    rng = random.Random(hash(consumer["consumer_no"]) & 0xFFFFFFFF)

    # Compute MAF: ratio of event-day morning (2h pre-event) vs historical morning average
    # Simulated: event-day pre-event is avg × ±12% variability
    pre_event_hist_kw = avg_demand * (
        0.40 + 0.35 * math.exp(-0.5 * ((event_hour_start - 2 - 9) ** 2) / 2)
    )
    pre_event_today_kw = pre_event_hist_kw * rng.uniform(0.88, 1.12)
    maf = pre_event_today_kw / pre_event_hist_kw if pre_event_hist_kw > 0 else 1.0
    # Clamp MAF to ±25% (AEIC standard)
    maf = max(0.75, min(1.25, maf))

    slots = []
    for slot_idx in range(int(event_duration_h * 4)):
        hour_frac = event_hour_start + slot_idx * 0.25
        base = avg_demand * 0.40
        morning_peak = avg_demand * 0.35 * math.exp(-0.5 * ((hour_frac - 9) ** 2) / 1.5)
        evening_peak = avg_demand * 0.50 * math.exp(-0.5 * ((hour_frac - 19.5) ** 2) / 2)
        load_shape = base + morning_peak + evening_peak

        day_samples = [load_shape * rng.uniform(0.92, 1.08) for _ in range(10)]
        cbl_kw = sum(day_samples) / len(day_samples)
        # Apply MAF
        cbl_kw_adjusted = cbl_kw * maf if apply_maf else cbl_kw

        slots.append({
            "slot": f"{int(hour_frac):02d}:{int((hour_frac % 1) * 60):02d}",
            "cbl_kw": round(cbl_kw_adjusted, 2),
            "cbl_pre_maf_kw": round(cbl_kw, 2),
            "min_kw": round(min(day_samples) * (maf if apply_maf else 1.0), 2),
            "max_kw": round(max(day_samples) * (maf if apply_maf else 1.0), 2),
        })

    total_cbl_kwh = sum(s["cbl_kw"] * 0.25 for s in slots)
    return {
        "consumer_no": consumer["consumer_no"],
        "name": consumer["name"],
        "method": "10-of-10 (AEIC) + MAF",
        "comparable_days_used": 10,
        "maf": round(maf, 4),
        "pre_event_hist_kw": round(pre_event_hist_kw, 2),
        "pre_event_today_kw": round(pre_event_today_kw, 2),
        "event_start_hour": event_hour_start,
        "event_duration_h": event_duration_h,
        "cbl_slots": slots,
        "total_cbl_kwh": round(total_cbl_kwh, 3),
        "avg_demand_kw": avg_demand,
    }


@router.get("/cbl")
async def get_cbl(consumer_no: str = None, event_start_hour: int = 17, event_duration_h: float = 2.0):
    """
    Calculate CBL (Continuous Baseline Load) using 10-of-10 method.
    Returns per-slot baseline kW for enrolled consumers.
    In production: queries MDMS 15-min interval data.
    """
    if consumer_no:
        consumer = next((c for c in _CONSUMERS if c["consumer_no"] == consumer_no), None)
        if not consumer:
            raise HTTPException(status_code=404, detail=f"Consumer {consumer_no} not found")
        return _build_cbl_profile(consumer, event_start_hour, event_duration_h)

    enrolled = [c for c in _CONSUMERS if c["enrolled"]]
    profiles = [_build_cbl_profile(c, event_start_hour, event_duration_h) for c in enrolled]
    total_cbl_kw = sum(
        sum(s["cbl_kw"] for s in p["cbl_slots"]) / len(p["cbl_slots"])
        for p in profiles
    )
    return {
        "event_start_hour": event_start_hour,
        "event_duration_h": event_duration_h,
        "enrolled_consumers": len(enrolled),
        "aggregate_cbl_kw": round(total_cbl_kw, 1),
        "aggregate_cbl_kwh": round(sum(p["total_cbl_kwh"] for p in profiles), 3),
        "profiles": profiles,
    }


# ---------------------------------------------------------------------------
# DR Event Invitations — consumers Accept/Decline
# ---------------------------------------------------------------------------

_EVENT_INVITATIONS: dict = {}   # event_id → {consumer_no → {status, ts}}


@router.post("/events/{event_id}/invite")
async def invite_consumers(event_id: str, consumer_nos: List[str] = None):
    """Send DR event invitations to enrolled consumers (or all enrolled if none specified)."""
    targets = consumer_nos or [c["consumer_no"] for c in _CONSUMERS if c["enrolled"]]
    now_iso = datetime.now(timezone.utc).isoformat()
    _EVENT_INVITATIONS.setdefault(event_id, {})
    for cno in targets:
        _EVENT_INVITATIONS[event_id][cno] = {"status": "INVITED", "ts": now_iso}
    return {
        "event_id": event_id,
        "invited": len(targets),
        "consumer_nos": targets,
        "invited_at": now_iso,
    }


class EventResponse(BaseModel):
    consumer_no: str
    response: str   # "ACCEPTED" | "DECLINED"
    reason: Optional[str] = None


@router.post("/events/{event_id}/respond")
async def respond_to_event(event_id: str, body: EventResponse):
    """Consumer accepts or declines a DR event invitation."""
    if body.response not in ("ACCEPTED", "DECLINED"):
        raise HTTPException(status_code=400, detail="response must be ACCEPTED or DECLINED")
    invites = _EVENT_INVITATIONS.get(event_id, {})
    if body.consumer_no not in invites:
        # Auto-create invitation record if missing
        _EVENT_INVITATIONS.setdefault(event_id, {})[body.consumer_no] = {"status": "INVITED", "ts": datetime.now(timezone.utc).isoformat()}
        invites = _EVENT_INVITATIONS[event_id]
    invites[body.consumer_no] = {
        "status": body.response,
        "ts": datetime.now(timezone.utc).isoformat(),
        "reason": body.reason,
    }
    return {"event_id": event_id, "consumer_no": body.consumer_no, "status": body.response}


@router.get("/events/{event_id}/invitations")
async def get_event_invitations(event_id: str):
    """Get all consumer invitation responses for a DR event."""
    invites = _EVENT_INVITATIONS.get(event_id, {})
    rows = []
    for cno, inv in invites.items():
        c = next((x for x in _CONSUMERS if x["consumer_no"] == cno), {})
        rows.append({
            "consumer_no": cno,
            "name": c.get("name", cno),
            "avg_demand_kw": c.get("avg_demand_kw", 0),
            "status": inv["status"],
            "ts": inv["ts"],
            "reason": inv.get("reason"),
        })
    accepted = [r for r in rows if r["status"] == "ACCEPTED"]
    return {
        "event_id": event_id,
        "total_invited": len(rows),
        "accepted": len(accepted),
        "declined": len([r for r in rows if r["status"] == "DECLINED"]),
        "pending": len([r for r in rows if r["status"] == "INVITED"]),
        "committed_kw": round(sum(r["avg_demand_kw"] * 0.30 for r in accepted), 1),
        "invitations": rows,
    }


# ---------------------------------------------------------------------------
# MV&S — Measurement, Verification & Settlement
# In production: compares smart meter 15-min readings vs CBL
# ---------------------------------------------------------------------------

_SETTLEMENTS: dict = {}   # event_id → settlement record

# Phase 1: soft launch — settlement rate = ₹0 (PRD §22.3 confirmed decision)
# Phase 2: incentive rate activated after reviewing Phase 1 curtailment data
_SETTLEMENT_RATE_INR_PER_KWH = 0.0     # Phase 1: no payment
_PENALTY_RATE_INR_PER_KWH = 0.0        # Phase 1: no penalty


@router.post("/events/{event_id}/settle")
async def settle_event(event_id: str):
    """
    Run MV&S settlement for a completed DR event.
    Computes: CBL kWh − Actual kWh = Verified Reduction → Settlement ₹
    In production: pulls actual meter readings from MDMS/AMISP.
    """
    invites = _EVENT_INVITATIONS.get(event_id, {})
    accepted_consumers = [
        c for c in _CONSUMERS
        if c["consumer_no"] in invites and invites[c["consumer_no"]]["status"] == "ACCEPTED"
    ]
    if not accepted_consumers:
        accepted_consumers = [c for c in _CONSUMERS if c["enrolled"]]

    event_start_hour = 17
    event_duration_h = 2.0
    settlement_rows = []
    total_verified_kwh = 0.0
    total_incentive = 0.0

    for c in accepted_consumers:
        cbl = _build_cbl_profile(c, event_start_hour, event_duration_h)
        cbl_kwh = cbl["total_cbl_kwh"]

        # Simulate actual: 20-45% reduction from CBL (DR effect)
        rng = random.Random(hash(c["consumer_no"] + event_id) & 0xFFFFFFFF)
        reduction_frac = rng.uniform(0.20, 0.45)
        actual_kwh = round(cbl_kwh * (1 - reduction_frac), 3)
        verified_kwh = max(0.0, cbl_kwh - actual_kwh)
        incentive = round(verified_kwh * _SETTLEMENT_RATE_INR_PER_KWH, 2)
        committed_kwh = cbl_kwh * 0.30
        shortfall_kwh = max(0.0, committed_kwh - verified_kwh)
        penalty = round(shortfall_kwh * _PENALTY_RATE_INR_PER_KWH, 2) if shortfall_kwh > 0 else 0.0
        net_payment = round(incentive - penalty, 2)

        total_verified_kwh += verified_kwh
        total_incentive += net_payment

        settlement_rows.append({
            "consumer_no": c["consumer_no"],
            "name": c["name"],
            "cbl_kwh": cbl_kwh,
            "actual_kwh": actual_kwh,
            "verified_reduction_kwh": round(verified_kwh, 3),
            "committed_kwh": round(committed_kwh, 3),
            "shortfall_kwh": round(shortfall_kwh, 3),
            "incentive_inr": incentive,
            "penalty_inr": penalty,
            "net_payment_inr": net_payment,
            "settlement_status": "SETTLED" if net_payment >= 0 else "PENALTY",
        })

    settlement = {
        "event_id": event_id,
        "settled_at": datetime.now(timezone.utc).isoformat(),
        "consumers_settled": len(settlement_rows),
        "total_verified_kwh": round(total_verified_kwh, 3),
        "total_incentive_inr": round(total_incentive, 2),
        "cost_avoided_inr": round(total_verified_kwh * _PEAK_TARIFF_RATE, 2),
        "net_utility_benefit_inr": round(total_verified_kwh * _PEAK_TARIFF_RATE - total_incentive, 2),
        "settlement_rate_inr_kwh": _SETTLEMENT_RATE_INR_PER_KWH,
        "rows": settlement_rows,
    }
    _SETTLEMENTS[event_id] = settlement
    return settlement


@router.get("/events/{event_id}/settlement")
async def get_settlement(event_id: str):
    """Get existing settlement for an event."""
    s = _SETTLEMENTS.get(event_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"No settlement found for event {event_id}. Run POST /settle first.")
    return s


@router.get("/settlements")
async def list_settlements():
    """List all computed settlements."""
    return {"settlements": list(_SETTLEMENTS.values()), "count": len(_SETTLEMENTS)}


# ---------------------------------------------------------------------------
# Extended DR Event Lifecycle — 10 stages (PRD §G)
# DRAFT → APPROVED → NOTIFIED → COMMITTED → ACTIVE → PAUSED →
# COMPLETED → VERIFIED → SETTLED → ARCHIVED
# ---------------------------------------------------------------------------

_LIFECYCLE_TRANSITIONS = {
    "DRAFT":     ["APPROVED"],
    "APPROVED":  ["NOTIFIED", "DRAFT"],
    "NOTIFIED":  ["COMMITTED", "APPROVED"],
    "COMMITTED": ["ACTIVE", "NOTIFIED"],
    "ACTIVE":    ["PAUSED", "COMPLETED"],
    "PAUSED":    ["ACTIVE", "COMPLETED"],
    "COMPLETED": ["VERIFIED"],
    "VERIFIED":  ["SETTLED", "COMPLETED"],
    "SETTLED":   ["ARCHIVED"],
    "ARCHIVED":  [],
}

_EVENT_LIFECYCLE: dict = {}   # event_id → {stage, history: [...]}


class LifecycleTransition(BaseModel):
    stage: str
    user: str = "dr_manager"
    note: Optional[str] = None


@router.get("/events/{event_id}/lifecycle")
async def get_event_lifecycle(event_id: str):
    """Get current lifecycle stage and history for a DR event."""
    lc = _EVENT_LIFECYCLE.get(event_id)
    if not lc:
        # Bootstrap from dispatch module if event exists
        ev = next((e for e in dispatch.get_dr_events() if e["event_id"] == event_id), None)
        if not ev:
            raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
        stage = ev.get("status", "DRAFT").upper()
        if stage not in _LIFECYCLE_TRANSITIONS:
            stage = "DRAFT"
        lc = {
            "event_id": event_id,
            "stage": stage,
            "history": [{"stage": stage, "ts": ev.get("created_at", datetime.now(timezone.utc).isoformat()), "user": "system"}],
        }
        _EVENT_LIFECYCLE[event_id] = lc
    lc["allowed_transitions"] = _LIFECYCLE_TRANSITIONS.get(lc["stage"], [])
    return lc


@router.post("/events/{event_id}/lifecycle")
async def advance_lifecycle(event_id: str, body: LifecycleTransition):
    """Advance DR event to the next lifecycle stage."""
    lc = _EVENT_LIFECYCLE.get(event_id)
    if not lc:
        # Auto-create
        lc = {"event_id": event_id, "stage": "DRAFT", "history": []}
        _EVENT_LIFECYCLE[event_id] = lc

    current = lc["stage"]
    allowed = _LIFECYCLE_TRANSITIONS.get(current, [])
    if body.stage not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from {current} → {body.stage}. Allowed: {allowed}"
        )

    lc["stage"] = body.stage
    lc["history"].append({
        "stage": body.stage,
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": body.user,
        "note": body.note,
    })
    lc["allowed_transitions"] = _LIFECYCLE_TRANSITIONS.get(body.stage, [])
    return lc


# ---------------------------------------------------------------------------
# SLDC Day-Ahead Schedule (PRD §17.4 — manual CSV upload in Phase 1)
# ---------------------------------------------------------------------------

_SLDC_SCHEDULE: list = []   # list of 48-block records
_SLDC_UPLOADED_AT: Optional[str] = None
_SLDC_UPLOADED_BY: Optional[str] = None


class SLDCBlock(BaseModel):
    date: str
    block_number: int        # 1–48
    available_supply_mw: float
    scheduled_demand_mw: float
    shortfall_mw: float = 0.0


class SLDCUpload(BaseModel):
    date: str
    uploaded_by: str = "dr_manager"
    blocks: List[SLDCBlock]


@router.post("/sldc-schedule")
async def upload_sldc_schedule(body: SLDCUpload):
    """Manual upload of SLDC day-ahead 48-block schedule by DR Manager."""
    global _SLDC_SCHEDULE, _SLDC_UPLOADED_AT, _SLDC_UPLOADED_BY

    if not body.blocks:
        raise HTTPException(status_code=400, detail="No blocks provided")

    blocks = sorted([b.dict() for b in body.blocks], key=lambda x: x["block_number"])
    # Compute shortfall where not provided
    for b in blocks:
        if b["shortfall_mw"] == 0:
            b["shortfall_mw"] = round(max(0, b["scheduled_demand_mw"] - b["available_supply_mw"]), 3)

    _SLDC_SCHEDULE = blocks
    _SLDC_UPLOADED_AT = datetime.now(timezone.utc).isoformat()
    _SLDC_UPLOADED_BY = body.uploaded_by

    shortfall_blocks = [b for b in blocks if b["shortfall_mw"] > 0]
    total_shortfall_mwh = sum(b["shortfall_mw"] * 0.5 for b in shortfall_blocks)

    # Auto-alert if shortfall blocks exist
    if shortfall_blocks:
        import src.derms.fleet as fleet
        fleet.add_alert(
            "warning", "MEDIUM",
            f"A-15 SLDC schedule uploaded for {body.date}: "
            f"{len(shortfall_blocks)} blocks with shortfall, "
            f"total {total_shortfall_mwh:.1f} MWh deficit — DR event may be needed",
            "demand-response"
        )

    return {
        "status": "uploaded",
        "date": body.date,
        "blocks_uploaded": len(blocks),
        "shortfall_blocks": len(shortfall_blocks),
        "total_shortfall_mwh": round(total_shortfall_mwh, 3),
        "uploaded_by": body.uploaded_by,
        "uploaded_at": _SLDC_UPLOADED_AT,
    }


@router.get("/sldc-schedule")
async def get_sldc_schedule():
    """Get current SLDC day-ahead schedule."""
    if not _SLDC_SCHEDULE:
        # Return simulated schedule for demo
        now = datetime.now(timezone.utc)
        demo_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        blocks = []
        for b in range(1, 49):
            hour = (b - 1) * 0.5
            demand = 2.8 + 0.8 * math.exp(-0.5 * ((hour - 9) ** 2) / 4) + \
                     1.2 * math.exp(-0.5 * ((hour - 18.5) ** 2) / 3)
            avail = demand * (random.uniform(0.88, 0.97) if 17 <= hour <= 22 else random.uniform(0.95, 1.05))
            shortfall = round(max(0, demand - avail), 3)
            blocks.append({
                "date": demo_date, "block_number": b,
                "available_supply_mw": round(avail, 3),
                "scheduled_demand_mw": round(demand, 3),
                "shortfall_mw": shortfall,
            })
        return {
            "date": demo_date,
            "blocks": blocks,
            "source": "simulated (no upload yet)",
            "shortfall_blocks": sum(1 for b in blocks if b["shortfall_mw"] > 0),
        }

    return {
        "date": _SLDC_SCHEDULE[0]["date"] if _SLDC_SCHEDULE else None,
        "blocks": _SLDC_SCHEDULE,
        "uploaded_at": _SLDC_UPLOADED_AT,
        "uploaded_by": _SLDC_UPLOADED_BY,
        "shortfall_blocks": sum(1 for b in _SLDC_SCHEDULE if b["shortfall_mw"] > 0),
        "source": "manual_upload",
    }


# ---------------------------------------------------------------------------
# Live Event Monitoring — Stage 7 of DR lifecycle
# Real-time curtailment gauge during active event
# ---------------------------------------------------------------------------

@router.get("/events/{event_id}/monitor")
async def live_event_monitor(event_id: str):
    """
    Live monitoring dashboard for an active DR event (PRD Stage 7).
    Shows real-time curtailment vs CBL, per-consumer status.
    In production: pulls live smart meter 15-min readings from MDMS.
    """
    invites = _EVENT_INVITATIONS.get(event_id, {})
    accepted = [
        c for c in _CONSUMERS
        if c["consumer_no"] in invites and invites[c["consumer_no"]]["status"] == "ACCEPTED"
    ]
    if not accepted:
        accepted = [c for c in _CONSUMERS if c["enrolled"]]

    event_start_hour = 17
    now = datetime.now(timezone.utc)
    hour_ist = (now.hour + 5.5) % 24
    elapsed_min = max(0, (hour_ist - event_start_hour) * 60)
    elapsed_slots = int(elapsed_min / 15)

    rng_seed = hash(event_id + now.strftime("%Y%m%d%H")) & 0xFFFFFFFF
    rng = random.Random(rng_seed)

    consumer_status = []
    total_cbl_kw = 0
    total_actual_kw = 0

    for c in accepted:
        cbl_profile = _build_cbl_profile(c, event_start_hour, 2.0)
        slots_so_far = cbl_profile["cbl_slots"][:max(1, elapsed_slots)]
        current_cbl = slots_so_far[-1]["cbl_kw"] if slots_so_far else c["avg_demand_kw"]
        actual_kw = current_cbl * rng.uniform(0.55, 0.75)  # 25-45% reduction

        curtailed_kw = current_cbl - actual_kw
        curtailment_pct = curtailed_kw / current_cbl * 100 if current_cbl > 0 else 0
        committed_kw = current_cbl * 0.30
        on_track = curtailed_kw >= committed_kw * 0.90

        total_cbl_kw += current_cbl
        total_actual_kw += actual_kw

        consumer_status.append({
            "consumer_no": c["consumer_no"],
            "name": c["name"],
            "current_cbl_kw": round(current_cbl, 2),
            "current_actual_kw": round(actual_kw, 2),
            "curtailed_kw": round(curtailed_kw, 2),
            "curtailment_pct": round(curtailment_pct, 1),
            "committed_kw": round(committed_kw, 2),
            "on_track": on_track,
            "status": "ON_TRACK" if on_track else "UNDER_DELIVERING",
        })

    total_curtailed = total_cbl_kw - total_actual_kw
    aggregate_curtailment_pct = total_curtailed / total_cbl_kw * 100 if total_cbl_kw > 0 else 0

    return {
        "event_id": event_id,
        "event_start_hour": event_start_hour,
        "elapsed_min": round(elapsed_min, 0),
        "elapsed_slots": elapsed_slots,
        "monitoring_ts": now.isoformat(),
        "aggregate": {
            "total_cbl_kw": round(total_cbl_kw, 2),
            "total_actual_kw": round(total_actual_kw, 2),
            "total_curtailed_kw": round(total_curtailed, 2),
            "curtailment_pct": round(aggregate_curtailment_pct, 1),
            "consumers_on_track": sum(1 for c in consumer_status if c["on_track"]),
            "consumers_total": len(consumer_status),
        },
        "consumers": consumer_status,
    }


# ---------------------------------------------------------------------------
# Alert Catalogue — PRD A-01 through A-15
# ---------------------------------------------------------------------------

ALERT_CATALOGUE = [
    {"code": "A-01", "priority": "P1", "category": "Voltage", "description": "Voltage > 1.06 pu at any DT (CEA limit breach)", "channel": ["SMS", "in-app"]},
    {"code": "A-02", "priority": "P1", "category": "Voltage", "description": "Voltage < 0.94 pu at any DT (CEA limit breach)", "channel": ["SMS", "in-app"]},
    {"code": "A-03", "priority": "P2", "category": "RPF", "description": "Reverse Power Flow detected — DT exporting > 10% of rated capacity", "channel": ["in-app", "email"]},
    {"code": "A-04", "priority": "P1", "category": "Thermal", "description": "DT loading > 90% nameplate kVA", "channel": ["SMS", "in-app"]},
    {"code": "A-05", "priority": "P2", "category": "Thermal", "description": "DT loading 75–90% nameplate kVA (pre-alert)", "channel": ["in-app"]},
    {"code": "A-06", "priority": "P2", "category": "OE", "description": "Prosumer generating > OE limit for > 2 consecutive blocks", "channel": ["in-app", "WhatsApp to prosumer"]},
    {"code": "A-07", "priority": "P3", "category": "HC", "description": "HC utilisation > 80% on any DT (approaching limit)", "channel": ["in-app"]},
    {"code": "A-08", "priority": "P1", "category": "IoT", "description": "IoT gateway offline > 5 minutes (heartbeat timeout)", "channel": ["SMS", "in-app"]},
    {"code": "A-09", "priority": "P2", "category": "DER", "description": "DER zero output during daylight > 1 hour (fault / disconnect?)", "channel": ["in-app", "email"]},
    {"code": "A-10", "priority": "P2", "category": "CBL", "description": "CBL data missing for enrolled DR consumer (< 5 valid baseline days)", "channel": ["email"]},
    {"code": "A-11", "priority": "P3", "category": "Forecast", "description": "Actual generation deviates > 25% from D+1 forecast", "channel": ["in-app"]},
    {"code": "A-12", "priority": "P2", "category": "MDMS", "description": "DT meter data stale > 90 minutes (MDMS pipeline issue)", "channel": ["in-app", "email"]},
    {"code": "A-13", "priority": "P1", "category": "Outage", "description": "Grid outage — all DTs on feeder reporting offline simultaneously", "channel": ["SMS", "in-app"]},
    {"code": "A-14", "priority": "P3", "category": "DER", "description": "New prosumer connection request submitted via portal", "channel": ["in-app"]},
    {"code": "A-15", "priority": "P2", "category": "SLDC", "description": "SLDC day-ahead schedule received / manual upload confirmed", "channel": ["in-app"]},
]


@router.get("/alert-catalogue")
async def get_alert_catalogue():
    """PRD-defined 15-alert catalogue with priority and channel routing."""
    return {
        "total": len(ALERT_CATALOGUE),
        "catalogue": ALERT_CATALOGUE,
        "escalation_policy": {
            "P1_unacknowledged_15min": "Auto-SMS to Grid Operator Supervisor",
            "P1_unacknowledged_60min": "Auto-email to PuVVNL Nodal Officer",
            "P2_unacknowledged_60min": "Reminder in-app notification",
        },
    }
