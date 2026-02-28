"""
IEEE 2030.5 REST Server
========================
Implements key IEEE 2030.5 (SEP 2.0) endpoints for DER aggregator integration.
Aggregators connect here to register DERs, report status, and receive dispatch commands.

Endpoints follow IEEE 2030.5 resource hierarchy:
  /api/2030.5/dcap           - DeviceCapability (entry point)
  /api/2030.5/edev           - EndDeviceList
  /api/2030.5/edev/{id}      - EndDevice (aggregator)
  /api/2030.5/edev/{id}/fsa  - FunctionSetAssignments
  /api/2030.5/edev/{id}/der  - DERList
  /api/2030.5/edev/{id}/der/{did}/ders  - DERStatus (PUT)
  /api/2030.5/edev/{id}/der/{did}/dera  - DERAvailability (PUT)
  /api/2030.5/derp           - DERProgramList
  /api/2030.5/derp/{id}/derc - DERControlList (active dispatch commands)
  /api/2030.5/mup            - MirrorUsagePointList
  /api/2030.5/mup/{id}/mr    - Post meter readings
"""
import uuid
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from src.integrations.ieee2030_5.resources import (
    DeviceCapability, EndDevice, EndDeviceList, EndDeviceRegistration,
    FunctionSetAssignments, DERSubResource, DERRegistration, DERStatus,
    DERAvailability, DERCapability, DERSettings, DERProgram, DERProgramList,
    DERControlBase, DERControlList, DefaultDERControl,
    MirrorUsagePoint, MirrorUsagePointList, MirrorMeterReading,
    ActivePower, ReactivePower,
)
from src.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/2030.5", tags=["IEEE 2030.5"])

# ─── In-Memory IEEE 2030.5 Resource Store ────────────────────────────────────

# End Devices (aggregators)
_end_devices: Dict[str, EndDevice] = {}
# DER sub-resources per end device: {end_device_id: {der_sub_id: DERSubResource}}
_der_resources: Dict[str, Dict[str, DERSubResource]] = {}
# Mirror Usage Points
_mirror_usage_points: Dict[str, MirrorUsagePoint] = {}
# Active DER Controls
_der_controls: Dict[str, DERControlBase] = {}
# Mapping from lfdi to end_device_id
_lfdi_to_edev: Dict[str, str] = {}

# Shared state - injected by fleet manager
_fleet_store: Optional[Any] = None


def set_fleet_store(store):
    global _fleet_store
    _fleet_store = store


def get_active_controls_for_program(program_id: str) -> List[DERControlBase]:
    """Return non-expired DERControls for a program."""
    now = int(time.time())
    active = []
    for ctrl in _der_controls.values():
        if not ctrl.href.startswith(f"/api/2030.5/derp/{program_id}"):
            continue
        start = ctrl.interval.get("start", 0)
        duration = ctrl.interval.get("duration", 900)
        if start <= now <= start + duration:
            active.append(ctrl)
    return active


def add_der_control(control: DERControlBase):
    """Add or update a DER dispatch control."""
    _der_controls[control.mRID] = control
    logger.info(f"IEEE 2030.5: DERControl added {control.mRID} opModMaxLimW={control.opModMaxLimW}%")


def remove_expired_controls():
    """Prune expired controls."""
    now = int(time.time())
    expired = [
        mrid for mrid, ctrl in _der_controls.items()
        if ctrl.interval.get("start", 0) + ctrl.interval.get("duration", 900) < now
    ]
    for mrid in expired:
        _der_controls.pop(mrid, None)


def get_end_devices() -> Dict[str, EndDevice]:
    return _end_devices


def get_der_resources() -> Dict[str, Dict[str, DERSubResource]]:
    return _der_resources


# ─── DeviceCapability ─────────────────────────────────────────────────────────

@router.get("/dcap")
async def get_device_capability():
    """IEEE 2030.5 entry point — DeviceCapability resource."""
    edev_count = len(_end_devices)
    derp_count = 1  # One DER program
    mup_count = len(_mirror_usage_points)
    return DeviceCapability(
        EndDeviceListLink={"href": "/api/2030.5/edev", "all": edev_count},
        DERProgramListLink={"href": "/api/2030.5/derp", "all": derp_count},
        MirrorUsagePointListLink={"href": "/api/2030.5/mup", "all": mup_count},
    )


# ─── EndDevice (Aggregator) ───────────────────────────────────────────────────

@router.get("/edev")
async def list_end_devices():
    devs = list(_end_devices.values())
    return EndDeviceList(all=len(devs), results=len(devs), EndDevice=devs)


@router.post("/edev", status_code=201)
async def register_end_device(reg: EndDeviceRegistration, response: Response):
    """Aggregator registers itself as an IEEE 2030.5 EndDevice."""
    # Check if already registered
    if reg.lFDI in _lfdi_to_edev:
        edev_id = _lfdi_to_edev[reg.lFDI]
        edev = _end_devices[edev_id]
        response.headers["Location"] = edev.href
        return edev

    edev_id = str(uuid.uuid4())[:8]
    href = f"/api/2030.5/edev/{edev_id}"
    edev = EndDevice(
        href=href,
        lFDI=reg.lFDI,
        sFDI=reg.sFDI,
        deviceCategory=reg.deviceCategory,
        changedTime=reg.changedTime,
        enabled=reg.enabled,
        postRate=reg.postRate,
        id=edev_id,
        DERListLink={"href": f"{href}/der", "all": 0},
        FunctionSetAssignmentsListLink={"href": f"{href}/fsa", "all": 1},
    )
    _end_devices[edev_id] = edev
    _der_resources[edev_id] = {}
    _lfdi_to_edev[reg.lFDI] = edev_id
    response.headers["Location"] = href
    logger.info(f"IEEE 2030.5: EndDevice registered sFDI={reg.sFDI} id={edev_id}")

    # Notify fleet store
    if _fleet_store:
        await _fleet_store.on_aggregator_connected(edev_id, reg.lFDI, reg.sFDI)

    return edev


@router.get("/edev/{edev_id}")
async def get_end_device(edev_id: str):
    if edev_id not in _end_devices:
        raise HTTPException(status_code=404, detail="EndDevice not found")
    edev = _end_devices[edev_id]
    # Update DERListLink count
    der_count = len(_der_resources.get(edev_id, {}))
    edev.DERListLink = {"href": f"/api/2030.5/edev/{edev_id}/der", "all": der_count}
    return edev


@router.get("/edev/{edev_id}/fsa")
async def get_function_set_assignments(edev_id: str):
    if edev_id not in _end_devices:
        raise HTTPException(status_code=404, detail="EndDevice not found")
    return FunctionSetAssignments(
        href=f"/api/2030.5/edev/{edev_id}/fsa/0",
        mRID=f"FSA-{edev_id}",
        DERProgramListLink={"href": "/api/2030.5/derp", "all": 1},
    )


# ─── DER Sub-Resources ────────────────────────────────────────────────────────

@router.get("/edev/{edev_id}/der")
async def list_ders(edev_id: str):
    if edev_id not in _end_devices:
        raise HTTPException(status_code=404, detail="EndDevice not found")
    ders = list(_der_resources.get(edev_id, {}).values())
    return {"href": f"/api/2030.5/edev/{edev_id}/der", "all": len(ders), "results": len(ders), "DER": ders}


@router.post("/edev/{edev_id}/der", status_code=201)
async def register_der(edev_id: str, reg: DERRegistration, response: Response):
    """Aggregator registers a DER under its EndDevice."""
    if edev_id not in _end_devices:
        raise HTTPException(status_code=404, detail="EndDevice not found")

    # Check duplicate
    for existing in _der_resources.get(edev_id, {}).values():
        if existing.mRID == reg.mRID:
            response.headers["Location"] = existing.href
            return existing

    der_sub_id = str(uuid.uuid4())[:8]
    href = f"/api/2030.5/edev/{edev_id}/der/{der_sub_id}"
    rtg_max_w = ActivePower.from_kw(reg.nameplate_kw)

    der_res = DERSubResource(
        href=href,
        mRID=reg.mRID,
        description=reg.description,
        DERType=reg.DERType,
        id=der_sub_id,
        der_id=reg.der_id_external,
        end_device_id=edev_id,
        nameplate_kw=reg.nameplate_kw,
        DERCapabilityLink={"href": f"{href}/dercap"},
        DERSettingsLink={"href": f"{href}/derg"},
        DERStatusLink={"href": f"{href}/ders"},
        DERAvailabilityLink={"href": f"{href}/dera"},
        capability=DERCapability(href=f"{href}/dercap", rtgMaxW=rtg_max_w, type=reg.DERType),
        settings=DERSettings(href=f"{href}/derg", setMaxW=rtg_max_w),
    )
    _der_resources[edev_id][der_sub_id] = der_res
    # Update DERListLink count
    _end_devices[edev_id].DERListLink = {
        "href": f"/api/2030.5/edev/{edev_id}/der",
        "all": len(_der_resources[edev_id]),
    }
    response.headers["Location"] = href
    logger.info(f"IEEE 2030.5: DER registered mRID={reg.mRID} under edev={edev_id} der_id={reg.der_id_external}")
    return der_res


@router.get("/edev/{edev_id}/der/{der_sub_id}")
async def get_der(edev_id: str, der_sub_id: str):
    der = _der_resources.get(edev_id, {}).get(der_sub_id)
    if not der:
        raise HTTPException(status_code=404, detail="DER not found")
    return der


@router.put("/edev/{edev_id}/der/{der_sub_id}/ders")
async def update_der_status(edev_id: str, der_sub_id: str, status: DERStatus):
    """Aggregator PUTs DERStatus — current operating state of DER."""
    der = _der_resources.get(edev_id, {}).get(der_sub_id)
    if not der:
        raise HTTPException(status_code=404, detail="DER not found")

    der.status = status
    logger.debug(f"IEEE 2030.5: DERStatus update edev={edev_id} der={der_sub_id} W={status.currentW}")

    # Notify fleet store to update DER state
    if _fleet_store and der.der_id:
        kw = status.currentW.to_kw() if status.currentW else None
        kvar = 0.0
        if status.currentVar:
            kvar = status.currentVar.value * (10 ** status.currentVar.multiplier) / 1000

        online = True
        if status.operationalModeStatus and status.operationalModeStatus.get("value", 0) in [2, 3]:
            online = False
        await _fleet_store.on_der_status_update(
            der_id=der.der_id,
            current_kw=kw,
            current_kvar=kvar,
            online=online,
            soc_pct=status.stateOfChargeStatus.get("value") if status.stateOfChargeStatus else None,
        )

    return {"status": "updated"}


@router.put("/edev/{edev_id}/der/{der_sub_id}/dera")
async def update_der_availability(edev_id: str, der_sub_id: str, avail: DERAvailability):
    """Aggregator PUTs DERAvailability — available flexibility for dispatch."""
    der = _der_resources.get(edev_id, {}).get(der_sub_id)
    if not der:
        raise HTTPException(status_code=404, detail="DER not found")
    der.availability = avail

    if _fleet_store and der.der_id and avail.statWAvail:
        avail_kw = avail.statWAvail.to_kw()
        await _fleet_store.on_der_availability_update(der.der_id, avail_kw)

    return {"status": "updated"}


@router.get("/edev/{edev_id}/der/{der_sub_id}/dercap")
async def get_der_capability(edev_id: str, der_sub_id: str):
    der = _der_resources.get(edev_id, {}).get(der_sub_id)
    if not der:
        raise HTTPException(status_code=404, detail="DER not found")
    return der.capability


# ─── DER Programs & Controls ─────────────────────────────────────────────────

@router.get("/derp")
async def list_der_programs():
    program = DERProgram(
        href=f"/api/2030.5/derp/{settings.der_program_id}",
        mRID=settings.der_program_id,
        description="DERMS Grid Support Dispatch Program",
        DefaultDERControlLink={"href": f"/api/2030.5/derp/{settings.der_program_id}/derca"},
        DERControlListLink={"href": f"/api/2030.5/derp/{settings.der_program_id}/derc", "all": 0},
        primacy=1,
    )
    return DERProgramList(all=1, results=1, DERProgram=[program])


@router.get("/derp/{program_id}")
async def get_der_program(program_id: str):
    remove_expired_controls()
    active = get_active_controls_for_program(program_id)
    return DERProgram(
        href=f"/api/2030.5/derp/{program_id}",
        mRID=program_id,
        description="DERMS Grid Support Dispatch Program",
        DefaultDERControlLink={"href": f"/api/2030.5/derp/{program_id}/derca"},
        DERControlListLink={"href": f"/api/2030.5/derp/{program_id}/derc", "all": len(active)},
        primacy=1,
    )


@router.get("/derp/{program_id}/derca")
async def get_default_der_control(program_id: str):
    """DefaultDERControl — normal operation, no curtailment."""
    return DefaultDERControl(
        href=f"/api/2030.5/derp/{program_id}/derca",
        opModConnect=True,
        opModEnergize=True,
        opModMaxLimW=100,
    )


@router.get("/derp/{program_id}/derc")
async def list_der_controls(program_id: str):
    """
    Aggregators poll this for active dispatch commands.
    Returns DERControls currently in effect — curtailment or dispatch targets.
    """
    remove_expired_controls()
    active = get_active_controls_for_program(program_id)
    return DERControlList(
        href=f"/api/2030.5/derp/{program_id}/derc",
        all=len(active),
        results=len(active),
        DERControl=active,
    )


@router.get("/derp/{program_id}/derc/{control_id}")
async def get_der_control(program_id: str, control_id: str):
    ctrl = _der_controls.get(control_id)
    if not ctrl:
        raise HTTPException(status_code=404, detail="DERControl not found")
    return ctrl


# ─── Mirror Usage Points (Metering) ──────────────────────────────────────────

@router.get("/mup")
async def list_mirror_usage_points():
    mups = list(_mirror_usage_points.values())
    return MirrorUsagePointList(all=len(mups), results=len(mups), MirrorUsagePoint=mups)


@router.post("/mup", status_code=201)
async def create_mirror_usage_point(mup: MirrorUsagePoint, response: Response):
    """Aggregator creates a MirrorUsagePoint for posting meter readings."""
    mup_id = str(uuid.uuid4())[:8]
    mup.href = f"/api/2030.5/mup/{mup_id}"
    mup.MirrorMeterReadingListLink = {"href": f"/api/2030.5/mup/{mup_id}/mr", "all": 0}
    _mirror_usage_points[mup_id] = mup
    response.headers["Location"] = mup.href
    return mup


@router.post("/mup/{mup_id}/mr", status_code=201)
async def post_meter_reading(mup_id: str, reading: MirrorMeterReading):
    """
    Aggregator POSTs energy meter readings (IEEE 2030.5 Function Set 12).
    DERMS stores these and forwards to MDMS.
    """
    mup = _mirror_usage_points.get(mup_id)
    if not mup:
        raise HTTPException(status_code=404, detail="MirrorUsagePoint not found")
    mup.readings.append(reading)
    logger.debug(f"IEEE 2030.5: MeterReading received mup={mup_id} readings={len(reading.readings)}")

    # Notify fleet store to record readings
    if _fleet_store and mup.end_device_id:
        await _fleet_store.on_meter_reading(mup.end_device_id, reading)

    return {"status": "accepted", "href": f"/api/2030.5/mup/{mup_id}/mr/{reading.mRID}"}


# ─── Status Endpoint ─────────────────────────────────────────────────────────

@router.get("/status")
async def ieee2030_5_status():
    """IEEE 2030.5 server health + statistics."""
    remove_expired_controls()
    total_ders = sum(len(ders) for ders in _der_resources.values())
    active_controls = len([
        c for c in _der_controls.values()
        if c.interval.get("start", 0) + c.interval.get("duration", 900) >= int(time.time())
    ])
    return {
        "server": "IEEE 2030.5 / SEP 2.0",
        "version": "2018",
        "profile": "CSIP (Common Smart Inverter Profile)",
        "end_devices": len(_end_devices),
        "registered_ders": total_ders,
        "active_der_controls": active_controls,
        "mirror_usage_points": len(_mirror_usage_points),
        "der_program": settings.der_program_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
