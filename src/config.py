"""DERMS Prototype Configuration"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str = "DERMS Prototype"
    host: str = "0.0.0.0"
    port: int = int(os.environ.get("PORT", 8080))
    debug: bool = True

    # Database
    db_url: str = "sqlite+aiosqlite:///./derms.db"

    # Simulation
    adms_poll_interval: int = 30       # seconds between ADMS state updates
    aggregator_poll_interval: int = 20  # seconds between aggregator DER updates
    dispatch_check_interval: int = 15   # seconds between dispatch evaluations

    # Grid thresholds
    voltage_nominal: float = 230.0      # V (single-phase)
    voltage_high_warn: float = 244.0    # +6% warning
    voltage_low_warn: float = 216.0     # -6% warning
    voltage_high_trip: float = 253.0    # +10% trip
    voltage_low_trip: float = 207.0     # -10% trip
    feeder_loading_warn: float = 80.0   # % loading warning
    feeder_loading_max: float = 100.0   # % loading critical
    hosting_capacity_warn: float = 85.0 # % HC utilization warning

    # IEEE 2030.5
    ieee2030_5_base_url: str = "/api/2030.5"
    der_program_id: str = "DERP-001"

    # ADMS simulator (simulated GE ADMS REST endpoint)
    adms_base_url: str = f"http://localhost:{os.environ.get('PORT', 8080)}/sim/adms"

    class Config:
        env_file = ".env"


settings = Settings()
