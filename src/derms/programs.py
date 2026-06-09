"""
DERMS Program & Counterparty management.
Module C — DERMS Program (PRD v1.0)
"""
import uuid
import math
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

_programs: dict = {}
_enrollments: dict = {}   # program_id → list[enrollment]


def _init_demo():
    prog = {
        "program_id": "PROG-001",
        "program_name": "Varanasi Voltage Pilot 2026",
        "program_description": (
            "Managing rooftop solar DERs on Lanka Feeder LK1 to prevent reverse "
            "power flow and voltage violations under PM Surya Ghar."
        ),
        "feeder_scope": ["LK1"],
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "status": "ACTIVE",
        "incentive_type": "ADVISORY",
        "counterparty_name": "PuVVNL DER Pool — Varanasi 2026",
        "created_at": "2026-01-01T00:00:00+00:00",
        "enrolled_count": 0,
        "registered_count": 0,
    }
    _programs[prog["program_id"]] = prog

    # Seed prosumers from LK1 real data
    from src.data.real_pilot_data import LANKA_DERS
    now = datetime.now(timezone.utc).isoformat()
    for i, d in enumerate(LANKA_DERS):
        status = "REGISTERED" if i < 7 else "INVITED"
        enrollment = {
            "consumer_id": d["consumer_id"],
            "der_id": d["der_id"],
            "name": d.get("consumer_name", d["consumer_id"]),
            "mobile": f"98765{10000 + i:05d}",
            "dt_id": d["dt_id"],
            "capacity_kWp": d["nameplate_kw"],
            "metering_type": d.get("metering_type", "NET"),
            "enrolment_status": status,
            "invited_at": now,
            "registered_at": now if status == "REGISTERED" else None,
        }
        _enrollments.setdefault("PROG-001", []).append(enrollment)

    _programs["PROG-001"]["enrolled_count"] = len(LANKA_DERS)
    _programs["PROG-001"]["registered_count"] = 7


_init_demo()


def get_all_programs() -> list:
    return list(_programs.values())


def get_program(program_id: str) -> Optional[dict]:
    return _programs.get(program_id)


def create_program(data: dict) -> dict:
    program_id = f"PROG-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc).isoformat()
    prog = {
        "program_id": program_id,
        "program_name": data["program_name"],
        "program_description": data.get("program_description", ""),
        "feeder_scope": data.get("feeder_scope", []),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "status": "DRAFT",
        "incentive_type": "ADVISORY",
        "counterparty_name": data.get("counterparty_name") or f"{data['program_name']} Pool",
        "created_at": now,
        "enrolled_count": 0,
        "registered_count": 0,
    }
    _programs[program_id] = prog
    return prog


def update_program_status(program_id: str, status: str) -> Optional[dict]:
    if program_id not in _programs:
        return None
    _programs[program_id]["status"] = status
    return _programs[program_id]


def get_enrollments(program_id: str) -> list:
    return _enrollments.get(program_id, [])


def enroll_prosumers(program_id: str, prosumers: list) -> list:
    existing = {e["consumer_id"] for e in _enrollments.get(program_id, [])}
    now = datetime.now(timezone.utc).isoformat()
    added = []
    for p in prosumers:
        if p["consumer_id"] in existing:
            continue
        rec = {
            "consumer_id": p["consumer_id"],
            "der_id": p.get("der_id", ""),
            "name": p.get("name", p["consumer_id"]),
            "mobile": p.get("mobile", ""),
            "dt_id": p.get("dt_id", ""),
            "capacity_kWp": p.get("capacity_kWp", 0),
            "metering_type": p.get("metering_type", "NET"),
            "enrolment_status": "INVITED",
            "invited_at": now,
            "registered_at": None,
        }
        _enrollments.setdefault(program_id, []).append(rec)
        existing.add(p["consumer_id"])
        added.append(rec)

    if program_id in _programs:
        all_e = _enrollments.get(program_id, [])
        _programs[program_id]["enrolled_count"] = len(all_e)
        _programs[program_id]["registered_count"] = sum(
            1 for e in all_e if e["enrolment_status"] == "REGISTERED"
        )
    return added


def get_counterparty(program_id: str) -> Optional[dict]:
    prog = _programs.get(program_id)
    if not prog:
        return None
    import src.derms.fleet as fleet

    feeder_scope = prog.get("feeder_scope", [])
    all_ders = fleet.get_all_ders()
    ders = [d for d in all_ders if not feeder_scope or d.get("feeder_id") in feeder_scope]

    total_kWp = round(sum(d.get("nameplate_kw", 0) for d in ders), 1)
    total_gen_kW = round(sum(d.get("current_kw", 0) for d in ders), 1)

    now = datetime.now(timezone.utc)
    hour_ist = (now.hour + 5.5) % 24
    today_kwh = round(total_kWp * max(0, math.sin(math.pi * (hour_ist - 6) / 12)) * hour_ist * 0.4, 1) if 6 <= hour_ist <= 18 else 0.0

    # DT breakdown
    dt_map: dict = {}
    for d in ders:
        did = d.get("dt_id", "")
        if did not in dt_map:
            dt_map[did] = {"dt_id": did, "der_count": 0, "capacity_kWp": 0.0, "gen_kW": 0.0}
        dt_map[did]["der_count"] += 1
        dt_map[did]["capacity_kWp"] = round(dt_map[did]["capacity_kWp"] + d.get("nameplate_kw", 0), 1)
        dt_map[did]["gen_kW"] = round(dt_map[did]["gen_kW"] + d.get("current_kw", 0), 1)

    # 30-day chart (simulated)
    chart = []
    for i in range(30):
        d = now - timedelta(days=29 - i)
        sf = 0.65 + 0.2 * math.sin(math.pi * i / 15) + random.uniform(-0.05, 0.05)
        chart.append({
            "date": d.strftime("%m/%d"),
            "gen_kwh": round(total_kWp * sf * random.uniform(3.8, 5.2), 1),
        })

    return {
        "program_id": program_id,
        "program_name": prog["program_name"],
        "counterparty_name": prog["counterparty_name"],
        "status": prog["status"],
        "total_capacity_kWp": total_kWp,
        "total_gen_kW": total_gen_kW,
        "today_gen_kWh": today_kwh,
        "der_count": len(ders),
        "enrolled_count": prog["enrolled_count"],
        "registered_count": prog["registered_count"],
        "dt_breakdown": list(dt_map.values()),
        "gen_30d": chart,
    }
