"""
API routes for Demand Response management:
  - Power demand analysis vs SLDC schedule
  - Consumer enrollment registry
  - Cost-benefit analysis
"""
import math
import random
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter

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
