"""
GE ADMS Simulator
================
Simulates a GE Advanced Distribution Management System (ADMS) with:
 - CIM XML network model export (IEC 61970-452)
 - Real-time SCADA state API (voltage, loading per feeder/DT)
 - DER status receive endpoint (DERMS → ADMS)

In production, replace the simulator endpoints with actual GE ADMS APM REST
API calls (authenticated with mTLS) and IEC 61968 message bus subscriptions.
"""
import math
import random
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sim/adms", tags=["ADMS Simulator"])

# ─── Simulated Network Topology ─────────────────────────────────────────────

SUBSTATIONS = [
    {"id": "S-VAR-001", "name": "Varanasi Main 132kV Substation", "voltage_kv": 132},
]

FEEDERS = [
    {"id": "FDR-01", "name": "Sigra-Lanka Feeder",       "substation_id": "S-VAR-001", "rated_mva": 8.0,  "voltage_kv": 33},
    {"id": "FDR-02", "name": "Bhelupur-Cant Feeder",     "substation_id": "S-VAR-001", "rated_mva": 10.0, "voltage_kv": 33},
    {"id": "FDR-03", "name": "Sarnath-Nadesar Feeder",   "substation_id": "S-VAR-001", "rated_mva": 6.0,  "voltage_kv": 33},
]

DTS = [
    # Feeder 1
    {"id": "DT-VAR-0234", "name": "Sigra DT-1",       "feeder_id": "FDR-01", "rated_kva": 400, "lat": 25.3240, "lng": 82.9770},
    {"id": "DT-VAR-0156", "name": "Lanka DT-2",        "feeder_id": "FDR-01", "rated_kva": 250, "lat": 25.2677, "lng": 82.9913},
    {"id": "DT-VAR-0312", "name": "Assi Ghat DT-3",   "feeder_id": "FDR-01", "rated_kva": 315, "lat": 25.2835, "lng": 83.0094},
    # Feeder 2
    {"id": "DT-VAR-0089", "name": "Bhelupur DT-4",    "feeder_id": "FDR-02", "rated_kva": 500, "lat": 25.2994, "lng": 82.9980},
    {"id": "DT-VAR-0445", "name": "Cantonment DT-5",  "feeder_id": "FDR-02", "rated_kva": 400, "lat": 25.3310, "lng": 82.9540},
    {"id": "DT-VAR-0267", "name": "Mahmoorganj DT-6", "feeder_id": "FDR-02", "rated_kva": 315, "lat": 25.3170, "lng": 82.9620},
    # Feeder 3
    {"id": "DT-VAR-0378", "name": "Sarnath DT-7",     "feeder_id": "FDR-03", "rated_kva": 250, "lat": 25.3820, "lng": 83.0245},
    {"id": "DT-VAR-0491", "name": "Nadesar DT-8",     "feeder_id": "FDR-03", "rated_kva": 315, "lat": 25.3450, "lng": 82.9820},
    {"id": "DT-VAR-0502", "name": "Ramnagar DT-9",    "feeder_id": "FDR-03", "rated_kva": 200, "lat": 25.2610, "lng": 83.0420},
]


def _solar_factor(hour: float) -> float:
    """Bell-curve solar generation factor by IST hour (0 at night, 1.0 at noon IST)."""
    hour_ist = (hour + 5.5) % 24  # Convert UTC → IST
    if hour_ist < 6 or hour_ist > 18:
        return 0.0
    return max(0.0, math.sin(math.pi * (hour_ist - 6) / 12))


def _load_factor(hour: float) -> float:
    """Typical residential load factor by IST hour (morning/evening peaks)."""
    hour_ist = (hour + 5.5) % 24  # Convert UTC → IST
    base = 0.4
    morning = 0.4 * math.exp(-0.5 * ((hour_ist - 8.5) ** 2))
    evening = 0.5 * math.exp(-0.5 * ((hour_ist - 19.0) ** 2) / 2)
    return min(1.0, base + morning + evening + random.uniform(-0.03, 0.03))


def _get_realtime_state() -> Dict[str, Any]:
    """Generate realistic real-time grid state for current IST time of day."""
    now = datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60.0  # UTC hour (passed to factor functions which convert to IST)
    solar_f = _solar_factor(hour)
    load_f = _load_factor(hour)

    feeder_states = []
    for feeder in FEEDERS:
        # Net load = gross load - solar generation
        gross_load_mw = feeder["rated_mva"] * 0.6 * load_f
        solar_offset_mw = feeder["rated_mva"] * 0.12 * solar_f
        net_load_mw = max(0.1, gross_load_mw - solar_offset_mw)
        loading_pct = (net_load_mw / feeder["rated_mva"]) * 100 + random.uniform(-2, 2)
        feeder_states.append({
            "feeder_id": feeder["id"],
            "name": feeder["name"],
            "loading_pct": round(min(105, max(5, loading_pct)), 1),
            "net_load_mw": round(net_load_mw, 3),
            "solar_offset_mw": round(solar_offset_mw, 3),
            "voltage_pu": round(1.0 + random.uniform(-0.04, 0.04), 4),
            "timestamp": now.isoformat(),
        })

    dt_states = []
    for dt in DTS:
        feeder = next(f for f in FEEDERS if f["id"] == dt["feeder_id"])
        rated_kva = dt["rated_kva"]
        dt_load_kw = rated_kva * 0.5 * load_f + random.uniform(-20, 20)
        dt_solar_kw = rated_kva * 0.08 * solar_f + random.uniform(-5, 5)
        net_kw = max(10, dt_load_kw - dt_solar_kw)
        loading_pct = (net_kw / rated_kva) * 100
        # Voltage droops with loading, rises with solar
        v_base = 230.0
        v_droop = -0.05 * loading_pct / 100  # droop
        v_rise = 0.04 * dt_solar_kw / (rated_kva * 0.2 + 1)  # PV voltage rise
        voltage = v_base * (1 + v_droop + v_rise) + random.uniform(-2, 2)
        dt_states.append({
            "dt_id": dt["id"],
            "feeder_id": dt["feeder_id"],
            "loading_pct": round(min(110, max(5, loading_pct)), 1),
            "net_load_kw": round(net_kw, 1),
            "solar_kw": round(max(0, dt_solar_kw), 1),
            "voltage_l1": round(voltage + random.uniform(-1, 1), 1),
            "voltage_l2": round(voltage + random.uniform(-1, 1), 1),
            "voltage_l3": round(voltage + random.uniform(-1, 1), 1),
            "timestamp": now.isoformat(),
        })

    return {
        "timestamp": now.isoformat(),
        "scada_cycle": "1-min",
        "solar_factor": round(solar_f, 3),
        "load_factor": round(load_f, 3),
        "feeders": feeder_states,
        "distribution_transformers": dt_states,
    }


def _generate_cim_xml() -> str:
    """
    Generate IEC 61970-452 CIM XML representing the simulated network.
    In production this is fetched from GE ADMS via SFTP or IEC 61968 message bus.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rdf:RDF',
        '  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"',
        '  xmlns:cim="http://iec.ch/TC57/2013/CIM-schema-cim16#"',
        '  xmlns:md="http://iec.ch/TC57/61970-552/ModelDescription/1#">',
        '',
        '  <!-- Model Header -->',
        '  <md:FullModel rdf:about="urn:uuid:derms-varanasi-network-v1">',
        '    <md:Model.description>Varanasi DERMS Pilot Network - Simulated GE ADMS Export</md:Model.description>',
        '    <md:Model.version>1</md:Model.version>',
        f'   <md:Model.created>{datetime.utcnow().isoformat()}Z</md:Model.created>',
        '  </md:FullModel>',
        '',
        '  <!-- Base Voltages -->',
        '  <cim:BaseVoltage rdf:ID="BV_132kV">',
        '    <cim:IdentifiedObject.name>132 kV</cim:IdentifiedObject.name>',
        '    <cim:BaseVoltage.nominalVoltage>132</cim:BaseVoltage.nominalVoltage>',
        '  </cim:BaseVoltage>',
        '  <cim:BaseVoltage rdf:ID="BV_33kV">',
        '    <cim:IdentifiedObject.name>33 kV</cim:IdentifiedObject.name>',
        '    <cim:BaseVoltage.nominalVoltage>33</cim:BaseVoltage.nominalVoltage>',
        '  </cim:BaseVoltage>',
        '  <cim:BaseVoltage rdf:ID="BV_11kV">',
        '    <cim:IdentifiedObject.name>11 kV</cim:IdentifiedObject.name>',
        '    <cim:BaseVoltage.nominalVoltage>11</cim:BaseVoltage.nominalVoltage>',
        '  </cim:BaseVoltage>',
        '  <cim:BaseVoltage rdf:ID="BV_415V">',
        '    <cim:IdentifiedObject.name>415 V LV</cim:IdentifiedObject.name>',
        '    <cim:BaseVoltage.nominalVoltage>0.415</cim:BaseVoltage.nominalVoltage>',
        '  </cim:BaseVoltage>',
        '',
    ]

    # Substations
    for s in SUBSTATIONS:
        lines += [
            f'  <cim:Substation rdf:ID="{s["id"]}">',
            f'    <cim:IdentifiedObject.name>{s["name"]}</cim:IdentifiedObject.name>',
            f'    <cim:IdentifiedObject.description>132/{33} kV Grid Substation</cim:IdentifiedObject.description>',
            '  </cim:Substation>',
            '',
        ]

    # Feeders
    for f in FEEDERS:
        lines += [
            f'  <cim:Feeder rdf:ID="{f["id"]}">',
            f'    <cim:IdentifiedObject.name>{f["name"]}</cim:IdentifiedObject.name>',
            f'    <cim:Feeder.NormalEnergizingSubstation rdf:resource="#{f["substation_id"]}"/>',
            f'    <cim:Feeder.ratedMVA>{f["rated_mva"]}</cim:Feeder.ratedMVA>',
            '  </cim:Feeder>',
            '',
        ]

    # Distribution Transformers
    for dt in DTS:
        pt_id = f"PT-{dt['id']}"
        lines += [
            f'  <cim:PowerTransformer rdf:ID="{pt_id}">',
            f'    <cim:IdentifiedObject.name>{dt["name"]} Transformer</cim:IdentifiedObject.name>',
            f'    <cim:Equipment.EquipmentContainer rdf:resource="#{dt["feeder_id"]}"/>',
            '  </cim:PowerTransformer>',
            f'  <cim:PowerTransformerEnd rdf:ID="{pt_id}-HV">',
            f'    <cim:PowerTransformerEnd.PowerTransformer rdf:resource="#{pt_id}"/>',
            f'    <cim:PowerTransformerEnd.ratedS>{dt["rated_kva"] / 1000}</cim:PowerTransformerEnd.ratedS>',
            '    <cim:PowerTransformerEnd.ratedU>11000</cim:PowerTransformerEnd.ratedU>',
            f'    <cim:TransformerEnd.BaseVoltage rdf:resource="#BV_11kV"/>',
            '  </cim:PowerTransformerEnd>',
            f'  <cim:PowerTransformerEnd rdf:ID="{pt_id}-LV">',
            f'    <cim:PowerTransformerEnd.PowerTransformer rdf:resource="#{pt_id}"/>',
            f'    <cim:PowerTransformerEnd.ratedS>{dt["rated_kva"] / 1000}</cim:PowerTransformerEnd.ratedS>',
            '    <cim:PowerTransformerEnd.ratedU>415</cim:PowerTransformerEnd.ratedU>',
            f'    <cim:TransformerEnd.BaseVoltage rdf:resource="#BV_415V"/>',
            '  </cim:PowerTransformerEnd>',
            '',
        ]

    lines.append('</rdf:RDF>')
    return "\n".join(lines)


# Track what DERMS has reported back to ADMS
_der_status_store: Dict[str, Any] = {}


# ─── ADMS Simulator API Endpoints ───────────────────────────────────────────

@router.get("/topology/cim", response_class=None)
async def get_cim_topology():
    """
    [SIMULATED] GE ADMS CIM topology export endpoint.
    Returns IEC 61970-452 CIM XML.
    Production: Fetched from GE ADMS via SFTP or IEC 61968 message bus.
    """
    from fastapi.responses import Response
    xml = _generate_cim_xml()
    return Response(content=xml, media_type="application/xml")


@router.get("/realtime/state")
async def get_realtime_state():
    """
    [SIMULATED] GE ADMS real-time SCADA state.
    Returns voltage and loading for all feeders and DTs.
    Production: GE APM REST API or IEC 61968 message bus subscription.
    """
    return _get_realtime_state()


@router.get("/topology/feeders")
async def get_feeders():
    """[SIMULATED] Get feeder list from ADMS."""
    return {"feeders": FEEDERS}


@router.get("/topology/dts")
async def get_dts():
    """[SIMULATED] Get distribution transformer list from ADMS."""
    return {"distribution_transformers": DTS}


@router.post("/der-status")
async def receive_der_status(payload: dict):
    """
    [SIMULATED] Receive DER fleet status from DERMS → ADMS.
    Production: DERMS POSTs aggregated DER data back to ADMS for load flow.
    """
    _der_status_store.update({
        "last_update": datetime.utcnow().isoformat(),
        "data": payload,
    })
    logger.info(f"ADMS received DER status: {len(payload.get('ders', []))} DERs")
    return {"status": "accepted", "message": "DER status ingested into ADMS load flow model"}


@router.get("/hosting-capacity")
async def get_hosting_capacity():
    """
    [SIMULATED] Get hosting capacity limits from ADMS (EPRI DRIVE methodology).
    Production: Fetched from ADMS power flow engine.
    """
    hc_data = []
    for dt in DTS:
        hc_kw = dt["rated_kva"] * 0.8  # 80% of transformer kVA as static HC
        hc_data.append({
            "dt_id": dt["id"],
            "feeder_id": dt["feeder_id"],
            "hosting_capacity_kw": hc_kw,
            "basis": "Static_80pct_kVA",
            "voltage_constraint_kw": hc_kw * 0.9,
            "thermal_constraint_kw": hc_kw,
        })
    return {"hosting_capacity": hc_data, "methodology": "EPRI-DRIVE-Static", "timestamp": datetime.utcnow().isoformat()}


@router.get("/status")
async def adms_status():
    """[SIMULATED] ADMS integration health check."""
    return {
        "adms_type": "GE-ADMS",
        "version": "2023.1 (Simulated)",
        "status": "Connected",
        "cim_version": "CIM16",
        "last_topology_export": datetime.utcnow().isoformat(),
        "scada_latency_ms": random.randint(50, 200),
        "substations": len(SUBSTATIONS),
        "feeders": len(FEEDERS),
        "distribution_transformers": len(DTS),
    }
