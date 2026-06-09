"""
DERMS Program & Counterparty API
Module C — PRD v1.0
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

import src.derms.programs as prog_store

router = APIRouter(prefix="/api/programs", tags=["Programs"])


@router.get("")
async def list_programs():
    return {"programs": prog_store.get_all_programs(), "count": len(prog_store.get_all_programs())}


@router.get("/{program_id}/counterparty")
async def get_counterparty(program_id: str):
    data = prog_store.get_counterparty(program_id)
    if not data:
        raise HTTPException(404, f"Program {program_id} not found")
    return data


@router.get("/{program_id}/prosumers")
async def list_prosumers(program_id: str):
    if not prog_store.get_program(program_id):
        raise HTTPException(404, f"Program {program_id} not found")
    enr = prog_store.get_enrollments(program_id)
    return {"enrollments": enr, "count": len(enr)}


@router.get("/{program_id}")
async def get_program(program_id: str):
    prog = prog_store.get_program(program_id)
    if not prog:
        raise HTTPException(404, f"Program {program_id} not found")
    return prog


class CreateProgram(BaseModel):
    program_name: str
    program_description: str = ""
    feeder_scope: List[str] = []
    start_date: str = ""
    end_date: str = ""
    counterparty_name: str = ""


@router.post("")
async def create_program(body: CreateProgram):
    prog = prog_store.create_program(body.dict())
    return {"status": "created", "program": prog}


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{program_id}/status")
async def update_status(program_id: str, body: StatusUpdate):
    valid = {"DRAFT", "ACTIVE", "PAUSED", "CLOSED"}
    if body.status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    prog = prog_store.update_program_status(program_id, body.status)
    if not prog:
        raise HTTPException(404, f"Program {program_id} not found")
    return {"status": "updated", "program": prog}


class EnrollBatch(BaseModel):
    prosumers: List[dict]


@router.post("/{program_id}/prosumers")
async def enroll_prosumers(program_id: str, body: EnrollBatch):
    if not prog_store.get_program(program_id):
        raise HTTPException(404, f"Program {program_id} not found")
    added = prog_store.enroll_prosumers(program_id, body.prosumers)
    return {"status": "enrolled", "added": len(added)}


@router.post("/{program_id}/prosumers/seed-demo")
async def seed_demo_prosumers(program_id: str):
    """Enroll all LK1 DERs as invited prosumers in this program."""
    if not prog_store.get_program(program_id):
        raise HTTPException(404, f"Program {program_id} not found")
    from src.data.real_pilot_data import LANKA_DERS
    prosumers = [
        {
            "consumer_id": d["consumer_id"],
            "der_id": d["der_id"],
            "name": d.get("consumer_name", d["consumer_id"]),
            "mobile": f"98765{i:05d}",
            "dt_id": d["dt_id"],
            "capacity_kWp": d["nameplate_kw"],
            "metering_type": d.get("metering_type", "NET"),
        }
        for i, d in enumerate(LANKA_DERS)
    ]
    added = prog_store.enroll_prosumers(program_id, prosumers)
    return {"status": "seeded", "added": len(added)}
