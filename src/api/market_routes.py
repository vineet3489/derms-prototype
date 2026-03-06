"""
L&T Neural Grid Platform — Flexibility Energy Market API
Global-scale DER aggregation and flexibility market simulation.

Architecture:
  Utility / TSO/DSO
      └── L&T Neural Grid Platform (Super-Aggregator / VPP Operator)
              ├── AGG-EU-01  SolarMax Europe  (Germany)
              ├── AGG-US-01  FlexGrid Americas (USA)
              ├── AGG-IN-01  L&T DES India   (Varanasi)
              ├── AGG-AU-01  GridFlex Pacific  (Australia)
              ├── AGG-JP-01  Denki Flex Japan  (Japan)
              └── AGG-GB-01  BritFlex UK       (UK)
"""
import math
import random
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/market", tags=["Energy Market"])

# ── Editable Simulation Configuration ────────────────────────────────────────
_sim_config = {
    "market": {
        "base_rate_usd_per_mwh":        85.0,
        "incentive_rate_usd_per_mwh":   35.0,
        "penalty_rate_usd_per_mwh":     20.0,
        "performance_threshold_pct":    90.0,
        "min_bid_mw":                    0.1,
        "max_bid_mw":                  100.0,
        "bid_gate_closure_min":         30,
        "settlement_period_min":        30,
        "pricing_mechanism":          "Uniform (MCP)",   # or "Pay-as-Bid"
        "currency":                   "USD",
        "exchange_rate_inr_per_usd":   83.5,
    },
    "grid": {
        "system_peak_mw":               1200.0,
        "base_load_mw":                  750.0,
        "renewable_penetration_pct":      28.0,
        "grid_frequency_hz":              50.0,
        "nominal_voltage_kv":            220.0,
        "flexibility_target_mw":          80.0,
        "dr_trigger_shortfall_pct":        5.0,
        "sldc_schedule_source":         "UP SLDC (simulated)",
    },
    "baseline": {
        "method":                     "ASHRAE 10-in-10",
        "lookback_days":              10,
        "adjustment_band_pct":        10.0,
        "symmetric_adjustment":       True,
        "meter_data_granularity_min": 15,
        "meter_data_source":          "AMI / Smart Meter (15-min intervals)",
        "baseline_type":              "Day-matching (similar weekday/weekend)",
    },
    "settlement": {
        "payment_cycle":              "T+2 (2 business days post-event)",
        "performance_measurement":    "Average MW over settlement period",
        "underperformance_threshold_pct": 90.0,
        "overperformance_cap_pct":    110.0,
        "dispute_window_days":        5,
        "regulatory_framework":       "CERC (India) / ENTSO-E (EU) / FERC Order 2222 (US)",
        "audit_standard":             "ISO 50001 / IEC 61968-9",
    },
    "aggregators": [
        {"id":"AGG-EU-01","name":"SolarMax Europe",    "country":"Germany",   "flag":"🇩🇪","region":"EU",   "capacity_mw":45.0,"min_price":75,"portfolio":"Solar PV + BESS","ders":180,"status":"Active"},
        {"id":"AGG-US-01","name":"FlexGrid Americas",  "country":"USA",       "flag":"🇺🇸","region":"AMER", "capacity_mw":80.0,"min_price":90,"portfolio":"Industrial DR + BESS","ders":320,"status":"Active"},
        {"id":"AGG-IN-01","name":"L&T DES — Varanasi", "country":"India",     "flag":"🇮🇳","region":"APAC", "capacity_mw":33.0,"min_price":55,"portfolio":"Solar PV + BESS + EV","ders":33,"status":"Active"},
        {"id":"AGG-AU-01","name":"GridFlex Pacific",   "country":"Australia", "flag":"🇦🇺","region":"APAC", "capacity_mw":25.0,"min_price":80,"portfolio":"Solar + EV Fleet","ders":95,"status":"Active"},
        {"id":"AGG-JP-01","name":"Denki Flex Japan",   "country":"Japan",     "flag":"🇯🇵","region":"APAC", "capacity_mw":15.0,"min_price":95,"portfolio":"BESS + Industrial DR","ders":60,"status":"Active"},
        {"id":"AGG-GB-01","name":"BritFlex UK",        "country":"UK",        "flag":"🇬🇧","region":"EU",   "capacity_mw":20.0,"min_price":85,"portfolio":"Wind + BESS","ders":75,"status":"Active"},
    ],
    "integration": {
        "dr_signal":         {"protocol":"OpenADR 2.0b",                "format":"XML / EiEvent",       "auth":"OAuth 2.0",     "endpoint":"POST /oadr/EiEvent"},
        "der_control":       {"protocol":"IEEE 2030.5 / SEP 2.0",       "format":"REST+XML",            "auth":"mTLS + X.509",  "endpoint":"POST /api/2030.5/derp/{id}/derc"},
        "topology":          {"protocol":"CIM XML IEC 61970-452",        "format":"RDF/XML",             "auth":"API Key",       "endpoint":"GET /api/cim/export"},
        "metering":          {"protocol":"IEC 61968-9 (MDMS)",           "format":"XML / CIM",           "auth":"mTLS",          "endpoint":"GET /mdms/readings/{meter_id}"},
        "market_bid":        {"protocol":"OASIS EML / REST",             "format":"JSON",                "auth":"OAuth 2.0 JWT", "endpoint":"POST /api/market/bids"},
        "settlement":        {"protocol":"UN/EDIFACT MSCONS",            "format":"EDI / JSON",          "auth":"mTLS",          "endpoint":"GET /api/market/settlements"},
        "telemetry":         {"protocol":"MQTT 5.0 / IEC 61850 GOOSE",  "format":"JSON / Binary",       "auth":"TLS + certs",   "endpoint":"mqtt://broker/lt-ngp/{agg_id}/telemetry"},
        "weather_forecast":  {"protocol":"REST / JSON",                  "format":"JSON (OpenAPI 3.0)",  "auth":"API Key",       "endpoint":"GET https://api.openweathermap.org/data/2.5/forecast"},
    },
}

# ── Procurement Events ────────────────────────────────────────────────────────
_events = [
    {
        "event_id":          "PROC-2026-031",
        "title":             "Evening Peak Shaving",
        "type":              "Peak Shaving",
        "flexibility_type":  "Upward",
        "procurement_mw":    50.0,
        "window_start":      "18:00 UTC",
        "window_end":        "21:00 UTC",
        "duration_h":         3.0,
        "max_price_usd_per_mwh": 130.0,
        "gate_closure":      "2026-03-06T16:00:00Z",
        "region":            "EU",
        "status":            "Open",
        "baseline_mw":        920.0,
        "forecast_mw":        870.0,
        "note":              "High wind curtailment expected; demand uplift needed",
        "created_at":        "2026-03-06T08:00:00Z",
    },
    {
        "event_id":          "PROC-2026-030",
        "title":             "Morning Ramp Support",
        "type":              "Frequency Response",
        "flexibility_type":  "Upward",
        "procurement_mw":    20.0,
        "window_start":      "07:00 UTC",
        "window_end":        "09:00 UTC",
        "duration_h":         2.0,
        "max_price_usd_per_mwh": 150.0,
        "gate_closure":      "2026-03-06T06:00:00Z",
        "region":            "AMER",
        "status":            "Cleared",
        "baseline_mw":        680.0,
        "forecast_mw":        700.0,
        "note":              "Fast-ramp BESS preferred; response < 2 min",
        "created_at":        "2026-03-05T20:00:00Z",
    },
    {
        "event_id":          "PROC-2026-029",
        "title":             "Midday Solar Curtailment Relief",
        "type":              "Downward Flex",
        "flexibility_type":  "Downward",
        "procurement_mw":    30.0,
        "window_start":      "11:00 UTC",
        "window_end":        "14:00 UTC",
        "duration_h":         3.0,
        "max_price_usd_per_mwh": 100.0,
        "gate_closure":      "2026-03-05T09:00:00Z",
        "region":            "APAC",
        "status":            "Settled",
        "baseline_mw":        550.0,
        "forecast_mw":        510.0,
        "note":              "High solar generation — need load flex to absorb surplus",
        "created_at":        "2026-03-04T18:00:00Z",
    },
]

# ── Bids ──────────────────────────────────────────────────────────────────────
_bids = [
    # PROC-2026-031 Open bids
    {"bid_id":"BID-031-01","event_id":"PROC-2026-031","aggregator_id":"AGG-IN-01","quantity_mw":12.0,"price_usd_per_mwh": 78,"response_time_min":8,"status":"Submitted","submitted_at":"2026-03-06T09:00:00Z"},
    {"bid_id":"BID-031-02","event_id":"PROC-2026-031","aggregator_id":"AGG-EU-01","quantity_mw":15.0,"price_usd_per_mwh": 88,"response_time_min":5,"status":"Submitted","submitted_at":"2026-03-06T10:30:00Z"},
    {"bid_id":"BID-031-03","event_id":"PROC-2026-031","aggregator_id":"AGG-GB-01","quantity_mw": 8.0,"price_usd_per_mwh": 92,"response_time_min":3,"status":"Submitted","submitted_at":"2026-03-06T11:00:00Z"},
    {"bid_id":"BID-031-04","event_id":"PROC-2026-031","aggregator_id":"AGG-US-01","quantity_mw":20.0,"price_usd_per_mwh":105,"response_time_min":2,"status":"Submitted","submitted_at":"2026-03-06T12:00:00Z"},
    # PROC-2026-030 Cleared bids
    {"bid_id":"BID-030-01","event_id":"PROC-2026-030","aggregator_id":"AGG-US-01","quantity_mw":12.0,"price_usd_per_mwh":115,"response_time_min":1,"status":"Selected","submitted_at":"2026-03-05T22:00:00Z"},
    {"bid_id":"BID-030-02","event_id":"PROC-2026-030","aggregator_id":"AGG-AU-01","quantity_mw": 8.0,"price_usd_per_mwh":118,"response_time_min":2,"status":"Selected","submitted_at":"2026-03-05T23:00:00Z"},
    {"bid_id":"BID-030-03","event_id":"PROC-2026-030","aggregator_id":"AGG-JP-01","quantity_mw": 5.0,"price_usd_per_mwh":135,"response_time_min":2,"status":"Rejected","submitted_at":"2026-03-05T23:30:00Z"},
    # PROC-2026-029 Settled bids
    {"bid_id":"BID-029-01","event_id":"PROC-2026-029","aggregator_id":"AGG-IN-01","quantity_mw":12.0,"price_usd_per_mwh": 68,"response_time_min":6,"status":"Settled","submitted_at":"2026-03-04T20:00:00Z"},
    {"bid_id":"BID-029-02","event_id":"PROC-2026-029","aggregator_id":"AGG-JP-01","quantity_mw":10.0,"price_usd_per_mwh": 82,"response_time_min":3,"status":"Settled","submitted_at":"2026-03-04T21:00:00Z"},
    {"bid_id":"BID-029-03","event_id":"PROC-2026-029","aggregator_id":"AGG-EU-01","quantity_mw": 8.0,"price_usd_per_mwh": 79,"response_time_min":4,"status":"Settled","submitted_at":"2026-03-04T21:30:00Z"},
]

# ── Settlements ───────────────────────────────────────────────────────────────
_settlements = [
    {
        "settlement_id":          "SET-029-01",
        "event_id":               "PROC-2026-029",
        "bid_id":                 "BID-029-01",
        "aggregator_id":          "AGG-IN-01",
        "contracted_mw":           12.0,
        "delivered_mw":            11.4,
        "performance_pct":         95.0,
        "duration_h":               3.0,
        "energy_mwh":              34.2,
        "bid_price_usd_per_mwh":   68.0,
        "mcp_usd_per_mwh":         82.0,
        "base_payment_usd":      2325.6,
        "incentive_payment_usd":  376.2,
        "penalty_usd":              0.0,
        "total_payment_usd":     2701.8,
        "settled_at":            "2026-03-05T10:00:00Z",
        "performance_status":    "Excellent",
    },
    {
        "settlement_id":          "SET-029-02",
        "event_id":               "PROC-2026-029",
        "bid_id":                 "BID-029-02",
        "aggregator_id":          "AGG-JP-01",
        "contracted_mw":           10.0,
        "delivered_mw":             9.6,
        "performance_pct":         96.0,
        "duration_h":               3.0,
        "energy_mwh":              28.8,
        "bid_price_usd_per_mwh":   82.0,
        "mcp_usd_per_mwh":         82.0,
        "base_payment_usd":      2361.6,
        "incentive_payment_usd":  354.2,
        "penalty_usd":              0.0,
        "total_payment_usd":     2715.8,
        "settled_at":            "2026-03-05T10:00:00Z",
        "performance_status":    "Excellent",
    },
    {
        "settlement_id":          "SET-029-03",
        "event_id":               "PROC-2026-029",
        "bid_id":                 "BID-029-03",
        "aggregator_id":          "AGG-EU-01",
        "contracted_mw":            8.0,
        "delivered_mw":             7.2,
        "performance_pct":         90.0,
        "duration_h":               3.0,
        "energy_mwh":              21.6,
        "bid_price_usd_per_mwh":   79.0,
        "mcp_usd_per_mwh":         82.0,
        "base_payment_usd":      1706.4,
        "incentive_payment_usd":    0.0,
        "penalty_usd":              0.0,
        "total_payment_usd":     1706.4,
        "settled_at":            "2026-03-05T10:00:00Z",
        "performance_status":    "Pass",
    },
]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    """Get all editable simulation parameters."""
    return _sim_config


@router.put("/config")
async def update_config(updates: dict):
    """
    Update simulation parameters.
    Supports partial updates — only keys present in updates are changed.
    """
    def deep_update(base: dict, patch: dict):
        for k, v in patch.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                deep_update(base[k], v)
            elif k in base:
                base[k] = v
    deep_update(_sim_config, updates)
    return {"status": "updated", "config": _sim_config}


@router.get("/status")
async def get_market_status():
    """High-level market dashboard status."""
    open_events   = [e for e in _events if e["status"] == "Open"]
    cleared       = [e for e in _events if e["status"] == "Cleared"]
    total_cap_mw  = sum(a["capacity_mw"] for a in _sim_config["aggregators"])
    total_ders    = sum(a["ders"]         for a in _sim_config["aggregators"])
    open_vol_mw   = sum(e["procurement_mw"] for e in open_events)
    settled_vol   = sum(s["energy_mwh"] for s in _settlements)
    settled_usd   = sum(s["total_payment_usd"] for s in _settlements)

    return {
        "platform":               "L&T Neural Grid Platform",
        "vpp_operator":           "Larsen & Toubro Limited — DES Division",
        "as_of":                  datetime.now(timezone.utc).isoformat(),
        "active_aggregators":     len(_sim_config["aggregators"]),
        "total_portfolio_mw":     total_cap_mw,
        "total_der_count":        total_ders,
        "open_events":            len(open_events),
        "cleared_events":         len(cleared),
        "open_volume_mw":         open_vol_mw,
        "total_bids":             len(_bids),
        "pending_bids":           len([b for b in _bids if b["status"] == "Submitted"]),
        "total_energy_settled_mwh": round(settled_vol, 2),
        "total_payments_usd":     round(settled_usd, 2),
        "base_rate_usd_per_mwh":  _sim_config["market"]["base_rate_usd_per_mwh"],
        "incentive_rate_usd_per_mwh": _sim_config["market"]["incentive_rate_usd_per_mwh"],
    }


@router.get("/events")
async def get_events():
    return {"events": _events, "count": len(_events)}


@router.post("/events")
async def create_event(body: dict):
    """Utility creates a new procurement event."""
    event = {
        "event_id":              f"PROC-{datetime.now().strftime('%Y-%m%d%H%M')}",
        "title":                 body.get("title", "New Procurement Event"),
        "type":                  body.get("type", "Peak Shaving"),
        "flexibility_type":      body.get("flexibility_type", "Upward"),
        "procurement_mw":        float(body.get("procurement_mw", 10)),
        "window_start":          body.get("window_start", "18:00 UTC"),
        "window_end":            body.get("window_end", "21:00 UTC"),
        "duration_h":            float(body.get("duration_h", 3)),
        "max_price_usd_per_mwh": float(body.get("max_price_usd_per_mwh", 130)),
        "gate_closure":          body.get("gate_closure", datetime.now(timezone.utc).isoformat()),
        "region":                body.get("region", "Global"),
        "status":                "Open",
        "baseline_mw":           float(body.get("baseline_mw", 800)),
        "forecast_mw":           float(body.get("forecast_mw", 760)),
        "note":                  body.get("note", ""),
        "created_at":            datetime.now(timezone.utc).isoformat(),
    }
    _events.insert(0, event)
    return event


@router.get("/bids")
async def get_bids(event_id: str = None):
    bids = _bids if not event_id else [b for b in _bids if b["event_id"] == event_id]
    # Attach aggregator info
    agg_map = {a["id"]: a for a in _sim_config["aggregators"]}
    enriched = []
    for b in bids:
        agg = agg_map.get(b["aggregator_id"], {})
        enriched.append({**b, "aggregator_name": agg.get("name","?"),
                         "country": agg.get("country","?"), "flag": agg.get("flag","")})
    return {"bids": enriched, "count": len(enriched)}


@router.post("/bids")
async def submit_bid(body: dict):
    """Aggregator submits a flexibility bid."""
    event_id = body.get("event_id")
    event = next((e for e in _events if e["event_id"] == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    if event["status"] != "Open":
        raise HTTPException(status_code=400, detail=f"Event {event_id} is not open for bidding (status: {event['status']})")

    qty = float(body.get("quantity_mw", 1))
    price = float(body.get("price_usd_per_mwh", _sim_config["market"]["base_rate_usd_per_mwh"]))
    cfg = _sim_config["market"]

    if qty < cfg["min_bid_mw"] or qty > cfg["max_bid_mw"]:
        raise HTTPException(status_code=400, detail=f"Bid quantity must be {cfg['min_bid_mw']}–{cfg['max_bid_mw']} MW")
    if price > event["max_price_usd_per_mwh"]:
        raise HTTPException(status_code=400, detail=f"Bid price ${price} exceeds event ceiling ${event['max_price_usd_per_mwh']}")

    bid = {
        "bid_id":               f"BID-{uuid.uuid4().hex[:8].upper()}",
        "event_id":             event_id,
        "aggregator_id":        body.get("aggregator_id", "AGG-EU-01"),
        "quantity_mw":          qty,
        "price_usd_per_mwh":    price,
        "response_time_min":    int(body.get("response_time_min", 5)),
        "status":               "Submitted",
        "submitted_at":         datetime.now(timezone.utc).isoformat(),
    }
    _bids.insert(0, bid)
    return bid


@router.get("/merit-order/{event_id}")
async def get_merit_order(event_id: str):
    """Return bids sorted by price (merit order) for a procurement event."""
    event = next((e for e in _events if e["event_id"] == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    agg_map = {a["id"]: a for a in _sim_config["aggregators"]}
    bids = [b for b in _bids if b["event_id"] == event_id and b["status"] in ("Submitted","Selected","Settled","Rejected")]
    bids_sorted = sorted(bids, key=lambda b: b["price_usd_per_mwh"])

    target = event["procurement_mw"]
    cumulative = 0.0
    mcp = None
    for b in bids_sorted:
        agg = agg_map.get(b["aggregator_id"], {})
        b_out = {**b,
                 "aggregator_name": agg.get("name","?"),
                 "country":         agg.get("country","?"),
                 "flag":            agg.get("flag",""),
                 "cumulative_mw":   round(cumulative + b["quantity_mw"], 2),
                 "selected":        cumulative < target,
                 "partial":         cumulative < target < cumulative + b["quantity_mw"]}
        cumulative += b["quantity_mw"]
        if b_out["selected"] and mcp is None and cumulative >= target:
            mcp = b["price_usd_per_mwh"]
        b["_out"] = b_out

    mcp = mcp or (bids_sorted[-1]["price_usd_per_mwh"] if bids_sorted else 0)
    result = [b["_out"] for b in bids_sorted]
    for b in bids_sorted:
        del b["_out"]

    return {
        "event_id":        event_id,
        "target_mw":       target,
        "bids":            result,
        "total_offered_mw": round(sum(b["quantity_mw"] for b in bids), 2),
        "mcp_usd_per_mwh": mcp,
        "covered":         cumulative >= target,
    }


@router.post("/events/{event_id}/clear")
async def clear_market(event_id: str):
    """Utility clears the market — selects bids in merit order until target met."""
    event = next((e for e in _events if e["event_id"] == event_id), None)
    if not event or event["status"] != "Open":
        raise HTTPException(status_code=400, detail="Event not open")

    eligible = sorted(
        [b for b in _bids if b["event_id"] == event_id and b["status"] == "Submitted"],
        key=lambda b: b["price_usd_per_mwh"]
    )
    target = event["procurement_mw"]
    cumulative = 0.0
    selected_ids = []
    mcp = 0.0
    for b in eligible:
        if cumulative >= target:
            b["status"] = "Rejected"
        else:
            b["status"] = "Selected"
            selected_ids.append(b["bid_id"])
            cumulative += b["quantity_mw"]
            mcp = b["price_usd_per_mwh"]

    event["status"] = "Cleared"
    event["mcp_usd_per_mwh"] = mcp
    event["cleared_mw"] = round(min(cumulative, target), 2)
    return {"status": "cleared", "event_id": event_id, "mcp_usd_per_mwh": mcp,
            "selected_bids": selected_ids, "cleared_mw": event["cleared_mw"]}


@router.get("/settlements")
async def get_settlements():
    agg_map = {a["id"]: a for a in _sim_config["aggregators"]}
    enriched = []
    for s in _settlements:
        agg = agg_map.get(s["aggregator_id"], {})
        enriched.append({**s, "aggregator_name": agg.get("name","?"),
                         "country": agg.get("country","?"), "flag": agg.get("flag","")})
    total_usd = sum(s["total_payment_usd"] for s in _settlements)
    return {"settlements": enriched, "total_usd": round(total_usd, 2), "count": len(enriched)}


@router.get("/aggregators")
async def get_aggregators():
    """Aggregator portfolio including real-time availability estimate."""
    agg_map = {a["id"]: a for a in _sim_config["aggregators"]}
    result = []
    for agg in _sim_config["aggregators"]:
        my_bids = [b for b in _bids if b["aggregator_id"] == agg["id"]]
        my_settlements = [s for s in _settlements if s["aggregator_id"] == agg["id"]]
        total_earned = sum(s["total_payment_usd"] for s in my_settlements)
        avg_perf = (sum(s["performance_pct"] for s in my_settlements) /
                    max(len(my_settlements), 1))
        result.append({
            **agg,
            "bids_submitted":    len(my_bids),
            "events_settled":    len(my_settlements),
            "total_earned_usd":  round(total_earned, 2),
            "avg_performance_pct": round(avg_perf, 1),
            "available_now_mw":  round(agg["capacity_mw"] * random.uniform(0.7, 0.95), 1),
        })
    return {"aggregators": result}


@router.get("/forecast")
async def get_forecast():
    """24-hour system demand forecast + baseline for utility view."""
    now = datetime.now(timezone.utc)
    cfg = _sim_config["grid"]
    peak = cfg["system_peak_mw"]
    base = cfg["base_load_mw"]
    points = []
    for h in range(24):
        morning = 0.20 * math.exp(-0.5 * ((h - 9) ** 2) / 2)
        evening = 0.30 * math.exp(-0.5 * ((h - 19) ** 2) / 2)
        forecast = base + (peak - base) * (0.4 + morning + evening) + random.uniform(-5, 5)
        baseline = forecast * random.uniform(0.97, 1.03)   # 10-in-10 baseline ≈ forecast ± 3%
        renew = (peak * cfg["renewable_penetration_pct"] / 100 *
                 (max(0, math.sin(math.pi * (h - 6) / 12)) if 6 <= h <= 18 else 0))
        net_load = max(0, forecast - renew)
        shortfall = max(0, net_load - (forecast * 0.95))
        points.append({
            "hour":         f"{h:02d}:00",
            "forecast_mw":  round(forecast, 1),
            "baseline_mw":  round(baseline, 1),
            "renewable_mw": round(renew, 1),
            "net_load_mw":  round(net_load, 1),
            "shortfall_mw": round(shortfall, 1),
            "flex_needed_mw": round(shortfall * 1.2, 1),
        })
    return {
        "as_of": now.isoformat(),
        "system_peak_mw": round(max(p["forecast_mw"] for p in points), 1),
        "max_shortfall_mw": round(max(p["shortfall_mw"] for p in points), 1),
        "hourly": points,
    }
