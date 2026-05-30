"""
Network parameter configuration for pandapower load flow.

Stored as JSON in config/network_config.json.
All values are editable via the dashboard API without code deployment.
When GE ADMS CIM model becomes available, set `use_cim_model: true` and
provide the CIM XML path — the load flow engine will switch automatically.
"""
import json
import os
import logging
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "network_config.json"
)

# ─── Conductor Library ────────────────────────────────────────────────────────
# Standard conductors used in UP distribution networks (11 kV feeders)
# R and X in Ω/km

CONDUCTOR_LIBRARY = {
    "ACSR_WEASEL_80": {
        "label": "ACSR Weasel 80mm²",
        "r_ohm_per_km": 0.68,
        "x_ohm_per_km": 0.36,
        "max_current_a": 200,
    },
    "ACSR_DOG_100": {
        "label": "ACSR Dog 100mm²",
        "r_ohm_per_km": 0.27,
        "x_ohm_per_km": 0.32,
        "max_current_a": 300,
    },
    "ACSR_RABBIT_50": {
        "label": "ACSR Rabbit 50mm²",
        "r_ohm_per_km": 0.64,
        "x_ohm_per_km": 0.38,
        "max_current_a": 150,
    },
    "XLPE_95": {
        "label": "XLPE 95mm² (underground)",
        "r_ohm_per_km": 0.193,
        "x_ohm_per_km": 0.08,
        "max_current_a": 290,
    },
    "CUSTOM": {
        "label": "Custom",
        "r_ohm_per_km": 0.68,
        "x_ohm_per_km": 0.36,
        "max_current_a": 200,
    },
}


@dataclass
class FeederNetworkConfig:
    feeder_id: str
    conductor_type: str = "ACSR_WEASEL_80"
    # Overrides for CUSTOM conductor type
    custom_r_ohm_per_km: Optional[float] = None
    custom_x_ohm_per_km: Optional[float] = None
    custom_max_current_a: Optional[float] = None
    # Feeder head voltage (slack bus) — from ADMS or manual entry
    feeder_head_voltage_pu: float = 1.0
    # DT transformer impedance (applied to all DTs unless per-DT override given)
    dt_transformer_z_pct: float = 4.5
    # Voltage limits (CEA ±6%)
    voltage_upper_pu: float = 1.06
    voltage_lower_pu: float = 0.94
    voltage_pre_alert_upper_pu: float = 1.04
    voltage_pre_alert_lower_pu: float = 0.96
    # DOC calculation step (kW) — smaller = more precise but slower
    doc_sweep_step_kw: float = 0.1
    # Whether to use CIM model (Phase 2) or assumed topology (MVP)
    use_cim_model: bool = False
    cim_xml_path: Optional[str] = None
    # Per-DT kVA overrides {dt_id: rated_kva}
    dt_kva_overrides: dict = field(default_factory=dict)

    @property
    def conductor(self) -> dict:
        if self.conductor_type == "CUSTOM":
            return {
                "label": "Custom",
                "r_ohm_per_km": self.custom_r_ohm_per_km or 0.68,
                "x_ohm_per_km": self.custom_x_ohm_per_km or 0.36,
                "max_current_a": self.custom_max_current_a or 200,
            }
        return CONDUCTOR_LIBRARY.get(self.conductor_type, CONDUCTOR_LIBRARY["ACSR_WEASEL_80"])


@dataclass
class NetworkConfig:
    feeders: dict = field(default_factory=dict)   # feeder_id → FeederNetworkConfig
    # Global defaults (applied when feeder-specific config not set)
    default_conductor_type: str = "ACSR_WEASEL_80"
    default_feeder_head_voltage_pu: float = 1.0
    default_dt_transformer_z_pct: float = 4.5
    diversity_factor: float = 0.80
    power_factor: float = 0.95
    # HC thresholds
    hc_green_pct: float = 60.0
    hc_amber_pct: float = 85.0


def _default_config() -> NetworkConfig:
    """Factory: sensible defaults for the Lanka pilot feeder."""
    cfg = NetworkConfig()
    cfg.feeders["LK1"] = FeederNetworkConfig(
        feeder_id="LK1",
        conductor_type="ACSR_WEASEL_80",
        feeder_head_voltage_pu=1.0,
        dt_transformer_z_pct=4.5,
    )
    return cfg


def load_config() -> NetworkConfig:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        cfg = _default_config()
        save_config(cfg)
        return cfg
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        cfg = NetworkConfig(
            default_conductor_type=data.get("default_conductor_type", "ACSR_WEASEL_80"),
            default_feeder_head_voltage_pu=data.get("default_feeder_head_voltage_pu", 1.0),
            default_dt_transformer_z_pct=data.get("default_dt_transformer_z_pct", 4.5),
            diversity_factor=data.get("diversity_factor", 0.80),
            power_factor=data.get("power_factor", 0.95),
            hc_green_pct=data.get("hc_green_pct", 60.0),
            hc_amber_pct=data.get("hc_amber_pct", 85.0),
        )
        for fid, fcfg in data.get("feeders", {}).items():
            cfg.feeders[fid] = FeederNetworkConfig(**fcfg)
        return cfg
    except Exception as e:
        logger.warning(f"Failed to load network config ({e}), using defaults")
        return _default_config()


def save_config(cfg: NetworkConfig):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    data = {
        "default_conductor_type": cfg.default_conductor_type,
        "default_feeder_head_voltage_pu": cfg.default_feeder_head_voltage_pu,
        "default_dt_transformer_z_pct": cfg.default_dt_transformer_z_pct,
        "diversity_factor": cfg.diversity_factor,
        "power_factor": cfg.power_factor,
        "hc_green_pct": cfg.hc_green_pct,
        "hc_amber_pct": cfg.hc_amber_pct,
        "feeders": {fid: asdict(f) for fid, f in cfg.feeders.items()},
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


def get_feeder_config(feeder_id: str) -> FeederNetworkConfig:
    cfg = load_config()
    if feeder_id in cfg.feeders:
        return cfg.feeders[feeder_id]
    # Return default for unknown feeder
    return FeederNetworkConfig(
        feeder_id=feeder_id,
        conductor_type=cfg.default_conductor_type,
        feeder_head_voltage_pu=cfg.default_feeder_head_voltage_pu,
        dt_transformer_z_pct=cfg.default_dt_transformer_z_pct,
    )
