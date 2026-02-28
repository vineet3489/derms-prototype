"""
IEEE 2030.5 (SEP 2.0) Resource Models
======================================
Pydantic models for IEEE 2030.5 Smart Energy Profile 2.0 resources.
Used for DER aggregator integration (northbound DER communication).

Key Function Sets implemented:
  - Function Set 10: DER (Distributed Energy Resources)
  - Function Set 9: Demand Response Load Control
  - Function Set 12: Metering (Mirror Usage Points)

Ref: IEEE Std 2030.5-2018 / CSIP (Common Smart Inverter Profile)
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field
import time


def now_ts() -> int:
    """Return current Unix timestamp (TimeType in IEEE 2030.5)."""
    return int(time.time())


# ─── IEEE 2030.5 Primitive Types ─────────────────────────────────────────────

class ActivePower(BaseModel):
    """
    IEEE 2030.5 ActivePower — value in watts with SI multiplier.
    multiplier: power of 10, so value is in (10^multiplier) watts.
    e.g. multiplier=0 → value in W; multiplier=3 → value in kW.
    """
    multiplier: int = 0   # 10^multiplier (0=W, 3=kW, 6=MW)
    value: int = 0        # Numeric value in units of 10^multiplier watts

    @classmethod
    def from_kw(cls, kw: float) -> "ActivePower":
        # multiplier=0 means value is in watts; store kW * 1000 = watts
        return cls(multiplier=0, value=int(kw * 1000))

    def to_kw(self) -> float:
        """Convert to kilowatts. value is always treated as watts / 10^(-multiplier)."""
        # If multiplier=0: value in W → /1000 for kW
        # If multiplier=3: value in kW → *1 for kW  (value already in kW)
        # General: watts = value * 10^multiplier; kW = watts / 1000
        watts = self.value * (10.0 ** self.multiplier)
        return watts / 1000.0


class ReactivePower(BaseModel):
    multiplier: int = 0
    value: int = 0


class VoltageRMS(BaseModel):
    multiplier: int = 0
    value: int = 0    # In volts * 10^multiplier


class FixedVar(BaseModel):
    refType: int = 0     # 0=VAr, 1=% nameplate
    value: int = 0


# ─── Device Capability ───────────────────────────────────────────────────────

class DeviceCapability(BaseModel):
    """DCAP — entry point for IEEE 2030.5 server."""
    href: str = "/api/2030.5/dcap"
    EndDeviceListLink: dict = Field(default_factory=lambda: {"href": "/api/2030.5/edev", "all": 0})
    DERProgramListLink: dict = Field(default_factory=lambda: {"href": "/api/2030.5/derp", "all": 0})
    MirrorUsagePointListLink: dict = Field(default_factory=lambda: {"href": "/api/2030.5/mup", "all": 0})
    # Supported function sets (bitmask)
    # Bit 9=DER, Bit 8=DR, Bit 11=Metering Mirror
    supportedByServer: str = "0xA00"  # DER + DR


# ─── EndDevice (Aggregator) ───────────────────────────────────────────────────

class EndDeviceRegistration(BaseModel):
    """Aggregator POSTs this to register with DERMS IEEE 2030.5 server."""
    lFDI: str                           # Long-Form Device Identifier (certificate hash)
    sFDI: int                           # Short-Form Device Identifier
    deviceCategory: str = "0x0000"     # Device category bitmap
    changedTime: int = Field(default_factory=now_ts)
    enabled: bool = True
    postRate: int = 900                 # DER update rate in seconds


class EndDevice(BaseModel):
    """IEEE 2030.5 EndDevice — represents one DER aggregator."""
    href: str
    lFDI: str
    sFDI: int
    deviceCategory: str = "0x0000"
    changedTime: int = Field(default_factory=now_ts)
    enabled: bool = True
    postRate: int = 900
    DERListLink: Optional[dict] = None
    FunctionSetAssignmentsListLink: Optional[dict] = None
    # Internal tracking
    id: Optional[str] = None
    agg_id: Optional[str] = None


class FunctionSetAssignments(BaseModel):
    """FSA — which function sets apply to this EndDevice."""
    href: str
    mRID: str
    description: str = "DER Aggregator Function Sets"
    DERProgramListLink: dict = Field(
        default_factory=lambda: {"href": "/api/2030.5/derp", "all": 0}
    )


# ─── DER Resources ───────────────────────────────────────────────────────────

class DERRegistration(BaseModel):
    """Aggregator POSTs this to register a DER under an EndDevice."""
    mRID: str
    description: str
    DERType: int = 83   # 83=PV, 85=Combined PV+Storage, 86=Commercial AC, 88=Battery
    nameplate_kw: float
    der_id_external: str  # DERMS internal DER ID for correlation


class DERCapability(BaseModel):
    """DERCapability — static nameplate data of a DER."""
    href: str
    modesSupported: str = "0x3FF"   # Bitmask of supported opMod
    rtgMaxW: ActivePower             # Rated max active power
    rtgMaxVar: Optional[ReactivePower] = None
    rtgMaxVA: Optional[ActivePower] = None
    rtgMinPF: Optional[float] = None  # Min power factor (cosφ)
    type: int = 83   # DERType: 83=PV


class DERSettings(BaseModel):
    """DERSettings — operational settings (can be overridden by DERControl)."""
    href: str
    setMaxW: ActivePower
    setMaxVar: Optional[ReactivePower] = None
    setMaxChrgW: Optional[ActivePower] = None   # For batteries
    updatedTime: int = Field(default_factory=now_ts)


class DERAvailability(BaseModel):
    """Aggregator PUTs this to report available flexibility."""
    href: str
    availabilityDuration: int = 900    # seconds available
    maxChrgDuration: Optional[int] = None
    reserveChargePercent: Optional[float] = None  # BESS reserve %
    reservePercent: float = 0.0  # % of nameplate held in reserve
    statWAvail: Optional[ActivePower] = None   # Available W for dispatch
    statVarAvail: Optional[ReactivePower] = None
    updatedTime: int = Field(default_factory=now_ts)


class DERStatus(BaseModel):
    """Aggregator PUTs this to report current DER operating state."""
    href: str
    genConnectStatus: Optional[dict] = None  # {value: int} connection status
    inverterStatus: Optional[dict] = None    # {value: int} 0=None, 1=Off, 2=Sleeping, 4=Grid-connected
    localControlModeStatus: Optional[dict] = None
    manufacturerStatus: Optional[dict] = None
    operationalModeStatus: Optional[dict] = None  # {value: int} 0=Operating, 1=Test, 2=Off, 3=Override
    readyForIslandStatus: Optional[dict] = None
    stateOfChargeStatus: Optional[dict] = None   # {value: pct} for BESS
    storageModeStatus: Optional[dict] = None
    updatedTime: int = Field(default_factory=now_ts)
    # Extension: current power (reported by aggregator)
    currentW: Optional[ActivePower] = None
    currentVar: Optional[ReactivePower] = None


class DERSubResource(BaseModel):
    """Represents a single DER registered under an EndDevice."""
    href: str
    mRID: str
    description: str
    DERType: int = 83
    DERCapabilityLink: Optional[dict] = None
    DERSettingsLink: Optional[dict] = None
    DERStatusLink: Optional[dict] = None
    DERAvailabilityLink: Optional[dict] = None
    # Internal
    id: Optional[str] = None
    der_id: Optional[str] = None
    end_device_id: Optional[str] = None
    nameplate_kw: Optional[float] = None
    capability: Optional[DERCapability] = None
    settings: Optional[DERSettings] = None
    status: Optional[DERStatus] = None
    availability: Optional[DERAvailability] = None


# ─── DER Program & Control ───────────────────────────────────────────────────

class DefaultDERControl(BaseModel):
    """Default DERControl — applies when no active DERControl exists."""
    href: str
    description: str = "Normal Operation - No Curtailment"
    opModConnect: bool = True
    opModEnergize: bool = True
    opModMaxLimW: int = 100    # 100% = no curtailment


class DERControlBase(BaseModel):
    """IEEE 2030.5 DERControl — dispatch command to DER aggregators."""
    href: str
    mRID: str
    description: str
    creationTime: int = Field(default_factory=now_ts)
    interval: dict   # {start: TimeType, duration: Uint32}
    # Control modes (only active when set)
    opModConnect: Optional[bool] = None
    opModEnergize: Optional[bool] = None
    opModFixedPF: Optional[float] = None
    opModMaxLimW: Optional[int] = None   # % of nameplate max W (0-100)
    opModTargetW: Optional[ActivePower] = None  # Target active power
    opModVoltVar: Optional[dict] = None  # Volt-VAR curve reference
    rampTms: int = 100   # Ramp time in hundredths of second (100 = 1s)
    randomizeStart: Optional[int] = None
    randomizeDuration: Optional[int] = None


class DERProgram(BaseModel):
    """DERProgram — groups DERControls and DefaultDERControl."""
    href: str
    mRID: str
    description: str = "DERMS Dispatch Program"
    DefaultDERControlLink: Optional[dict] = None
    DERControlListLink: Optional[dict] = None
    primacy: int = 1   # Priority (1=highest)


# ─── Mirror Usage Points (Metering) ──────────────────────────────────────────

class MirrorMeterReading(BaseModel):
    """Energy reading from aggregator metering system."""
    mRID: str
    description: str
    # Reading type: 0=Active Energy, 1=Reactive Energy, 2=Apparent Energy
    readingType: dict = Field(default_factory=lambda: {
        "accumulationBehaviour": 4,   # 4=Delta
        "commodity": 1,               # 1=Electricity
        "dataQualifier": 0,
        "kind": 12,                   # 12=Power, 37=Energy
        "uom": 72,                    # 72=Wh, 38=W
    })
    readings: List[dict] = Field(default_factory=list)


class MirrorUsagePoint(BaseModel):
    """MUP — aggregator posts meter readings here."""
    href: str
    mRID: str
    description: str
    deviceLFDI: str
    roleFlags: str = "0x1D"  # Bit flags for meter roles
    serviceCategoryKind: int = 0  # 0=Electricity
    status: int = 1  # 1=Active
    MirrorMeterReadingListLink: Optional[dict] = None
    readings: List[MirrorMeterReading] = Field(default_factory=list)
    # Internal
    end_device_id: Optional[str] = None


# ─── List Containers ─────────────────────────────────────────────────────────

class EndDeviceList(BaseModel):
    href: str = "/api/2030.5/edev"
    all: int = 0
    results: int = 0
    EndDevice: List[EndDevice] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class DERProgramList(BaseModel):
    href: str = "/api/2030.5/derp"
    all: int = 0
    results: int = 0
    DERProgram: List[DERProgram] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class DERControlList(BaseModel):
    href: str
    all: int = 0
    results: int = 0
    DERControl: List[DERControlBase] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class MirrorUsagePointList(BaseModel):
    href: str = "/api/2030.5/mup"
    all: int = 0
    results: int = 0
    MirrorUsagePoint: List[MirrorUsagePoint] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
