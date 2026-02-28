"""
All data models: SQLAlchemy ORM tables + Pydantic schemas
Grid topology, DER assets, IEEE 2030.5 resources, events, alerts
"""
import uuid
from datetime import datetime
from typing import Optional, List
from enum import Enum

from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from pydantic import BaseModel, Field

from src.database import Base


# ─── Enums ──────────────────────────────────────────────────────────────────

class DERType(str, Enum):
    SOLAR_PV = "Solar PV"
    BESS = "BESS"
    EV_CHARGER = "EV Charger"
    WIND = "Wind"

class DERStatus(str, Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    DEGRADED = "Degraded"
    CURTAILED = "Curtailed"
    UNKNOWN = "Unknown"

class AlertType(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"

class AlertPriority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ─── SQLAlchemy ORM Models ───────────────────────────────────────────────────

class Feeder(Base):
    __tablename__ = "feeders"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    feeder_id = Column(String, unique=True, nullable=False)  # e.g. FDR-01
    name = Column(String, nullable=False)
    substation_id = Column(String, nullable=False)
    voltage_kv = Column(Float, default=33.0)
    rated_mva = Column(Float, default=10.0)
    current_loading_pct = Column(Float, default=0.0)
    hosting_capacity_kw = Column(Float, default=2000.0)
    used_capacity_kw = Column(Float, default=0.0)
    cim_id = Column(String, nullable=True)  # CIM rdf:ID from ADMS
    created_at = Column(DateTime, default=datetime.utcnow)

    dts = relationship("DistributionTransformer", back_populates="feeder")


class DistributionTransformer(Base):
    __tablename__ = "distribution_transformers"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    dt_id = Column(String, unique=True, nullable=False)   # e.g. DT-VAR-0234
    feeder_id = Column(String, ForeignKey("feeders.feeder_id"), nullable=False)
    name = Column(String, nullable=False)
    rated_kva = Column(Float, default=250.0)
    current_loading_pct = Column(Float, default=0.0)
    voltage_l1 = Column(Float, default=230.0)
    voltage_l2 = Column(Float, default=230.0)
    voltage_l3 = Column(Float, default=230.0)
    hosting_capacity_kw = Column(Float, default=400.0)
    used_capacity_kw = Column(Float, default=0.0)
    lat = Column(Float, default=25.317645)
    lng = Column(Float, default=82.973915)
    cim_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    feeder = relationship("Feeder", back_populates="dts")
    ders = relationship("DERAsset", back_populates="dt")


class Aggregator(Base):
    __tablename__ = "aggregators"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    agg_id = Column(String, unique=True, nullable=False)   # e.g. AGG-001
    name = Column(String, nullable=False)
    # IEEE 2030.5 EndDevice info
    lfdi = Column(String, nullable=True)    # Long-Form Device Identifier
    sfdi = Column(String, nullable=True)    # Short-Form Device Identifier
    device_category = Column(String, default="0x0000")
    status = Column(String, default="Online")
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    ders = relationship("DERAsset", back_populates="aggregator")


class DERAsset(Base):
    __tablename__ = "der_assets"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    der_id = Column(String, unique=True, nullable=False)
    aggregator_id = Column(String, ForeignKey("aggregators.agg_id"), nullable=False)
    dt_id = Column(String, ForeignKey("distribution_transformers.dt_id"), nullable=False)
    feeder_id = Column(String, nullable=False)
    consumer_id = Column(String, nullable=False)
    meter_id = Column(String, nullable=False)

    # DER properties
    der_type = Column(String, default=DERType.SOLAR_PV)
    nameplate_kw = Column(Float, nullable=False)
    inverter_oem = Column(String, default="Huawei")
    model = Column(String, default="SUN2000-5KTL")
    commission_date = Column(String, nullable=True)
    tilt_deg = Column(Float, default=20.0)
    azimuth_deg = Column(Float, default=180.0)
    location_name = Column(String, nullable=False)
    lat = Column(Float, default=25.317645)
    lng = Column(Float, default=82.973915)

    # Real-time state (updated by IEEE 2030.5 or OEM API)
    status = Column(String, default=DERStatus.ONLINE)
    current_kw = Column(Float, default=0.0)      # Active generation/consumption
    current_kvar = Column(Float, default=0.0)    # Reactive power
    voltage_v = Column(Float, default=230.0)
    soc_pct = Column(Float, nullable=True)       # Battery State of Charge
    cuf_pct = Column(Float, default=0.0)         # Capacity Utilization Factor
    pr_pct = Column(Float, default=0.0)          # Performance Ratio
    available_kw = Column(Float, default=0.0)    # Available flex for dispatch

    # Dispatch state
    curtailment_pct = Column(Float, default=100.0)  # 100 = no curtailment
    dispatch_target_kw = Column(Float, nullable=True)
    last_control_ts = Column(DateTime, nullable=True)

    # IEEE 2030.5 EndDevice sub-resource IDs
    ieee_end_device_id = Column(String, nullable=True)
    ieee_der_sub_id = Column(String, nullable=True)

    last_update = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    aggregator = relationship("Aggregator", back_populates="ders")
    dt = relationship("DistributionTransformer", back_populates="ders")


class DERControl(Base):
    """IEEE 2030.5 DERControl - dispatch commands sent to aggregators"""
    __tablename__ = "der_controls"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    control_id = Column(String, unique=True, nullable=False)
    der_program_id = Column(String, nullable=False)
    der_id = Column(String, ForeignKey("der_assets.der_id"), nullable=True)
    aggregator_id = Column(String, nullable=True)  # if targeting whole aggregator

    # Control modes (IEEE 2030.5 opMod fields)
    op_mod_max_lim_w_pct = Column(Float, nullable=True)   # Curtailment % (0-100)
    op_mod_target_w = Column(Float, nullable=True)         # Target watts
    op_mod_connect = Column(Boolean, nullable=True)         # Connect/disconnect
    ramp_time_ms = Column(Integer, default=10000)          # Ramp in ms

    start_time = Column(DateTime, nullable=False)
    duration_s = Column(Integer, default=900)   # 15 min default
    priority = Column(Integer, default=1)
    status = Column(String, default="Active")   # Active, Acknowledged, Expired
    reason = Column(String, nullable=True)      # Why this control was issued

    created_at = Column(DateTime, default=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    alert_type = Column(String, nullable=False)
    priority = Column(String, default=AlertPriority.MEDIUM)
    message = Column(Text, nullable=False)
    module = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)  # feeder/DT/DER ID
    resource_type = Column(String, nullable=True)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class MeterReading(Base):
    __tablename__ = "meter_readings"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    der_id = Column(String, ForeignKey("der_assets.der_id"), nullable=False)
    meter_id = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    energy_export_kwh = Column(Float, default=0.0)
    energy_import_kwh = Column(Float, default=0.0)
    power_kw = Column(Float, default=0.0)
    voltage_v = Column(Float, default=230.0)
    source = Column(String, default="IEEE2030.5")  # IEEE2030.5, MDMS, OEM_API


# ─── Pydantic Response Schemas ───────────────────────────────────────────────

class FeederSchema(BaseModel):
    feeder_id: str
    name: str
    voltage_kv: float
    rated_mva: float
    current_loading_pct: float
    hosting_capacity_kw: float
    used_capacity_kw: float
    available_capacity_kw: float
    status: str

    class Config:
        from_attributes = True


class DTSchema(BaseModel):
    dt_id: str
    feeder_id: str
    name: str
    rated_kva: float
    current_loading_pct: float
    voltage_l1: float
    voltage_l2: float
    voltage_l3: float
    hosting_capacity_kw: float
    used_capacity_kw: float
    lat: float
    lng: float
    voltage_status: str

    class Config:
        from_attributes = True


class DERSchema(BaseModel):
    der_id: str
    aggregator_id: str
    dt_id: str
    feeder_id: str
    consumer_id: str
    meter_id: str
    der_type: str
    nameplate_kw: float
    inverter_oem: str
    location_name: str
    lat: float
    lng: float
    status: str
    current_kw: float
    voltage_v: float
    soc_pct: Optional[float] = None
    cuf_pct: float
    pr_pct: float
    available_kw: float
    curtailment_pct: float
    last_update: datetime

    class Config:
        from_attributes = True


class AlertSchema(BaseModel):
    id: str
    alert_type: str
    priority: str
    message: str
    module: str
    resource_id: Optional[str] = None
    resolved: bool
    created_at: datetime

    class Config:
        from_attributes = True


class DashboardSummary(BaseModel):
    total_ders: int
    online_ders: int
    offline_ders: int
    degraded_ders: int
    curtailed_ders: int
    total_generation_kw: float
    total_capacity_kw: float
    system_cuf_pct: float
    active_alerts: int
    feeders: List[FeederSchema]
    recent_alerts: List[AlertSchema]
    adms_sync_status: str
    adms_last_sync: Optional[str]
    ieee_aggregators_online: int
    active_der_controls: int


class DERControlSchema(BaseModel):
    control_id: str
    der_id: Optional[str]
    aggregator_id: Optional[str]
    op_mod_max_lim_w_pct: Optional[float]
    op_mod_target_w: Optional[float]
    op_mod_connect: Optional[bool]
    ramp_time_ms: int
    start_time: datetime
    duration_s: int
    status: str
    reason: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
