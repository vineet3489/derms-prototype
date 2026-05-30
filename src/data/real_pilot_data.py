"""
Real pilot data seeded from:
  - DT REPORT.xlsx  : Lanka Feeder (LK1) — 10 DERs with commissioning data
  - Varanasi MI master.xlsb : 170k consumer master — Lanka slice (50 consumers)

Lanka Feeder (code: 40431410005LK1) originates from 33/11 KV BHU substation.
GPS coordinates are approximate — sourced from Google Maps for each locality.
DT kVA ratings are assumed (standard UP distribution sizes) and configurable.

When PUVVNL provides the DT asset register CSV, replace `rated_kva` values
and GPS coordinates here — everything else flows automatically.
"""

# ─── Substation ───────────────────────────────────────────────────────────────

LANKA_SUBSTATION = {
    "id": "SS-BHU-001",
    "name": "33/11 KV BHU Substation",
    "voltage_kv": 33,
    # GPS: BHU electrical sub-station, Lanka, Varanasi
    "lat": 25.2692,
    "lng": 82.9975,
}

# ─── Feeder ───────────────────────────────────────────────────────────────────

LANKA_FEEDER = {
    "id": "LK1",
    "name": "Lanka Feeder (LK1)",
    "code": "40431410005LK1",
    "substation_id": "SS-BHU-001",
    "rated_mva": 5.0,
    "voltage_kv": 11,
}

# ─── Distribution Transformers ────────────────────────────────────────────────
# Ordered source → feeder-end by electrical distance from BHU substation.
# `order` drives pandapower bus sequencing (0 = nearest to source).
# `rated_kva` = assumed; replace with DT asset register when available.
# `lat/lng` = approximate from Google Maps for each locality.

LANKA_DTS = [
    {
        "id": "LK1-DT-01",
        "code": "4000505103",
        "name": "LK1D-05",
        "feeder_id": "LK1",
        "rated_kva": 100,       # assumed — configurable
        "order": 1,             # nearest to substation
        "lat": 25.2688,
        "lng": 82.9948,
        "consumer_count": 12,
        "total_load_kw": 18.0,
    },
    {
        "id": "LK1-DT-02",
        "code": "4000505103",
        "name": "Madhav Market",
        "feeder_id": "LK1",
        "rated_kva": 250,
        "order": 2,
        "lat": 25.2670,
        "lng": 82.9912,
        "consumer_count": 15,
        "total_load_kw": 42.0,
    },
    {
        "id": "LK1-DT-03",
        "code": "400050586",
        "name": "Madhav Market-2",
        "feeder_id": "LK1",
        "rated_kva": 100,
        "order": 3,
        "lat": 25.2665,
        "lng": 82.9905,
        "consumer_count": 10,
        "total_load_kw": 22.0,
    },
    {
        "id": "LK1-DT-04",
        "code": "400050513",
        "name": "Saket Nagar",
        "feeder_id": "LK1",
        "rated_kva": 160,
        "order": 4,
        "lat": 25.2650,
        "lng": 82.9875,
        "consumer_count": 18,
        "total_load_kw": 31.0,
    },
    {
        "id": "LK1-DT-05",
        "code": "400050596",
        "name": "Rasmi Nagar",
        "feeder_id": "LK1",
        "rated_kva": 250,
        "order": 5,
        "lat": 25.2710,
        "lng": 82.9858,
        "consumer_count": 14,
        "total_load_kw": 48.0,
    },
    {
        "id": "LK1-DT-06",
        "code": "400080540",
        "name": "Tara Nagar Colony",
        "feeder_id": "LK1",
        "rated_kva": 100,
        "order": 6,
        "lat": 25.2748,
        "lng": 82.9882,
        "consumer_count": 8,
        "total_load_kw": 14.0,
    },
    {
        "id": "LK1-DT-07",
        "code": "400080518",
        "name": "Madrawa",
        "feeder_id": "LK1",
        "rated_kva": 100,
        "order": 7,
        "lat": 25.2520,
        "lng": 82.9850,
        "consumer_count": 9,
        "total_load_kw": 16.0,
    },
    {
        "id": "LK1-DT-08",
        "code": "400020592",
        "name": "Sanketmochan Purani Gali",
        "feeder_id": "LK1",
        "rated_kva": 160,
        "order": 8,             # farthest from substation
        "lat": 25.2838,
        "lng": 83.0093,
        "consumer_count": 11,
        "total_load_kw": 24.0,
    },
]

# ─── DER Assets (real data from DT REPORT.xlsx) ───────────────────────────────
# All 10 solar PV prosumers on Lanka Feeder, net-metered.
# Monthly kWh from MDMS master used to calibrate simulated 15-min profiles.

LANKA_DERS = [
    {
        "der_id": "LK1-DER-001",
        "consumer_id": "4494701000",
        "consumer_name": "Consumer 4494701000",
        "meter_id": "AL2869568",
        "dt_id": "LK1-DT-02",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 4.0,
        "sanctioned_load_kw": 4.0,
        "inverter_oem": "Unknown",
        "commission_date": "2026-03-09",
        "metering_type": "NET",
        "monthly_kwh": None,        # not yet in MDMS master
    },
    {
        "der_id": "LK1-DER-002",
        "consumer_id": "6892375045",
        "consumer_name": "Consumer 6892375045",
        "meter_id": "AL2864249",
        "dt_id": "LK1-DT-02",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 3.0,
        "sanctioned_load_kw": 5.0,
        "inverter_oem": "Unknown",
        "commission_date": "2025-12-22",
        "metering_type": "NET",
        "monthly_kwh": 238.79,      # from MDMS master
    },
    {
        "der_id": "LK1-DER-003",
        "consumer_id": "794502752",
        "consumer_name": "Consumer 0794502752",
        "meter_id": "AL2864513",
        "dt_id": "LK1-DT-07",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 8.0,
        "sanctioned_load_kw": 8.0,
        "inverter_oem": "Unknown",
        "commission_date": "2025-12-09",
        "metering_type": "NET",
        "monthly_kwh": 506.30,
    },
    {
        "der_id": "LK1-DER-004",
        "consumer_id": "487810661",
        "consumer_name": "Consumer 0487810661",
        "meter_id": "SC10400972",
        "dt_id": "LK1-DT-05",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 50.0,       # largest system on feeder
        "sanctioned_load_kw": 73.0,
        "inverter_oem": "Unknown",
        "commission_date": "2026-01-19",
        "metering_type": "NET",
        "monthly_kwh": 14722.0,
    },
    {
        "der_id": "LK1-DER-005",
        "consumer_id": "129311000",
        "consumer_name": "Consumer 0129311000",
        "meter_id": "AL2860667",
        "dt_id": "LK1-DT-06",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 6.0,
        "sanctioned_load_kw": 12.0,
        "inverter_oem": "Unknown",
        "commission_date": "2025-06-07",
        "metering_type": "NET",
        "monthly_kwh": None,
    },
    {
        "der_id": "LK1-DER-006",
        "consumer_id": "4263011000",
        "consumer_name": "Consumer 4263011000",
        "meter_id": "AL2860666",
        "dt_id": "LK1-DT-08",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 8.0,
        "sanctioned_load_kw": 12.0,
        "inverter_oem": "Unknown",
        "commission_date": "2025-06-07",
        "metering_type": "NET",
        "monthly_kwh": None,
    },
    {
        "der_id": "LK1-DER-007",
        "consumer_id": "3287621000",
        "consumer_name": "Consumer 3287621000",
        "meter_id": "AL2859275",
        "dt_id": "LK1-DT-04",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 9.0,
        "sanctioned_load_kw": 9.0,
        "inverter_oem": "Unknown",
        "commission_date": "2025-06-17",
        "metering_type": "NET",
        "monthly_kwh": None,
    },
    {
        "der_id": "LK1-DER-008",
        "consumer_id": "2782005224",
        "consumer_name": "Consumer 2782005224",
        "meter_id": "IVN8614",
        "dt_id": "LK1-DT-04",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 10.0,
        "sanctioned_load_kw": 10.0,
        "inverter_oem": "Unknown",
        "commission_date": "2025-04-21",
        "metering_type": "NET",
        "monthly_kwh": None,
    },
    {
        "der_id": "LK1-DER-009",
        "consumer_id": "9023421000",
        "consumer_name": "Consumer 9023421000",
        "meter_id": "IVN8384",
        "dt_id": "LK1-DT-01",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 12.0,
        "sanctioned_load_kw": 23.0,
        "inverter_oem": "Unknown",
        "commission_date": "2024-09-04",
        "metering_type": "NET",
        "monthly_kwh": None,
    },
    {
        "der_id": "LK1-DER-010",
        "consumer_id": "5852964426",
        "consumer_name": "Consumer 5852964426",
        "meter_id": "AL6221797",
        "dt_id": "LK1-DT-05",
        "feeder_id": "LK1",
        "der_type": "Solar PV",
        "nameplate_kw": 4.0,
        "sanctioned_load_kw": 4.0,
        "inverter_oem": "Unknown",
        "commission_date": "2026-03-31",
        "metering_type": "NET",
        "monthly_kwh": None,
    },
]

# Convenience lookups
LANKA_DT_BY_ID = {dt["id"]: dt for dt in LANKA_DTS}
LANKA_DER_BY_ID = {der["der_id"]: der for der in LANKA_DERS}
TOTAL_SOLAR_KWP = sum(d["nameplate_kw"] for d in LANKA_DERS)
