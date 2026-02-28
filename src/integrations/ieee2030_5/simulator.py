"""
IEEE 2030.5 DER Aggregator Simulator
=====================================
Simulates 3 DER aggregators that connect to the DERMS IEEE 2030.5 server,
register their DERs, and periodically send status/availability updates.

Aggregator definitions:
  AGG-001: 8 Solar PV + 2 BESS on FDR-01 (Sigra-Lanka)
  AGG-002: 10 Solar PV + 3 BESS on FDR-02 (Bhelupur-Cantonment)
  AGG-003: 6 Solar PV + 2 BESS + 2 EV Chargers on FDR-03 (Sarnath-Nadesar)

Each aggregator runs as an asyncio background task.
"""
import asyncio
import math
import random
import time
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Dict

import httpx

from src.config import settings
from src.integrations.ieee2030_5.resources import ActivePower, ReactivePower

logger = logging.getLogger(__name__)

# ─── DER Aggregator Definitions ──────────────────────────────────────────────

DER_LOCATIONS = {
    "FDR-01": [
        ("Sigra",        25.3240, 82.9770, "DT-VAR-0234"),
        ("Lanka",        25.2677, 82.9913, "DT-VAR-0156"),
        ("Assi Ghat",   25.2835, 83.0094, "DT-VAR-0312"),
    ],
    "FDR-02": [
        ("Bhelupur",    25.2994, 82.9980, "DT-VAR-0089"),
        ("Cantonment",  25.3310, 82.9540, "DT-VAR-0445"),
        ("Mahmoorganj", 25.3170, 82.9620, "DT-VAR-0267"),
    ],
    "FDR-03": [
        ("Sarnath",     25.3820, 83.0245, "DT-VAR-0378"),
        ("Nadesar",     25.3450, 82.9820, "DT-VAR-0491"),
        ("Ramnagar",    25.2610, 83.0420, "DT-VAR-0502"),
    ],
}

AGGREGATOR_DEFINITIONS = [
    {
        "agg_id": "AGG-001",
        "name": "Green Energy Varanasi - Aggregator 1",
        "feeder": "FDR-01",
        "lfdi": "A1B2C3D4E5F60001" * 2 + "00" * 4,
        "sfdi": 1001,
        "ders": [
            {"type": "Solar PV", "kw": 5.0,  "oem": "Huawei",  "idx": 0},
            {"type": "Solar PV", "kw": 7.5,  "oem": "Huawei",  "idx": 0},
            {"type": "Solar PV", "kw": 3.0,  "oem": "Solis",   "idx": 1},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Growatt", "idx": 1},
            {"type": "Solar PV", "kw": 10.0, "oem": "Huawei",  "idx": 2},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Solis",   "idx": 2},
            {"type": "Solar PV", "kw": 7.5,  "oem": "Fronius", "idx": 0},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Fronius", "idx": 1},
            {"type": "BESS",     "kw": 10.0, "oem": "Tesla",   "idx": 0},
            {"type": "BESS",     "kw": 5.0,  "oem": "BYD",     "idx": 2},
        ],
    },
    {
        "agg_id": "AGG-002",
        "name": "Solar Shakti - Aggregator 2",
        "feeder": "FDR-02",
        "lfdi": "B2C3D4E5F6A70002" * 2 + "00" * 4,
        "sfdi": 1002,
        "ders": [
            {"type": "Solar PV", "kw": 5.0,  "oem": "Huawei",  "idx": 0},
            {"type": "Solar PV", "kw": 7.5,  "oem": "Huawei",  "idx": 0},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Solis",   "idx": 0},
            {"type": "Solar PV", "kw": 3.0,  "oem": "Growatt", "idx": 1},
            {"type": "Solar PV", "kw": 10.0, "oem": "Huawei",  "idx": 1},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Solis",   "idx": 1},
            {"type": "Solar PV", "kw": 7.5,  "oem": "Fronius", "idx": 2},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Huawei",  "idx": 2},
            {"type": "Solar PV", "kw": 5.0,  "oem": "Growatt", "idx": 2},
            {"type": "Solar PV", "kw": 10.0, "oem": "Fronius", "idx": 0},
            {"type": "BESS",     "kw": 15.0, "oem": "Tesla",   "idx": 1},
            {"type": "BESS",     "kw": 10.0, "oem": "BYD",     "idx": 2},
            {"type": "BESS",     "kw": 5.0,  "oem": "Sonnen",  "idx": 0},
        ],
    },
    {
        "agg_id": "AGG-003",
        "name": "EcoGrid DER - Aggregator 3",
        "feeder": "FDR-03",
        "lfdi": "C3D4E5F6A7B80003" * 2 + "00" * 4,
        "sfdi": 1003,
        "ders": [
            {"type": "Solar PV",   "kw": 5.0,  "oem": "Huawei",  "idx": 0},
            {"type": "Solar PV",   "kw": 7.5,  "oem": "Solis",   "idx": 0},
            {"type": "Solar PV",   "kw": 5.0,  "oem": "Growatt", "idx": 1},
            {"type": "Solar PV",   "kw": 10.0, "oem": "Huawei",  "idx": 1},
            {"type": "Solar PV",   "kw": 5.0,  "oem": "Fronius", "idx": 2},
            {"type": "Solar PV",   "kw": 7.5,  "oem": "Huawei",  "idx": 2},
            {"type": "BESS",       "kw": 10.0, "oem": "Tesla",   "idx": 0},
            {"type": "BESS",       "kw": 7.5,  "oem": "BYD",     "idx": 1},
            {"type": "EV Charger", "kw": 7.4,  "oem": "ABB",     "idx": 1},
            {"type": "EV Charger", "kw": 11.0, "oem": "Schneider","idx": 2},
        ],
    },
]


IST_OFFSET = 5.5  # India Standard Time = UTC + 5.5 hours


def _solar_factor(hour_utc: float) -> float:
    """Bell-curve solar factor using IST hour (sunrise 6AM IST, sunset 18:00 IST)."""
    hour = (hour_utc + IST_OFFSET) % 24  # Convert UTC → IST
    if hour < 6 or hour > 18:
        return 0.0
    f = math.sin(math.pi * (hour - 6) / 12)
    return max(0.0, f) * (0.85 + random.uniform(0, 0.15))


def _der_current_kw(der_def: dict, hour: float, curtailment_pct: float = 100.0) -> float:
    """Calculate realistic current output for a DER."""
    der_type = der_def["type"]
    nameplate = der_def["kw"]
    limit_factor = curtailment_pct / 100.0

    if der_type == "Solar PV":
        solar_f = _solar_factor(hour)
        # Add cloud variations
        cloud = random.uniform(0.85, 1.0)
        output = nameplate * solar_f * cloud * limit_factor
        return round(max(0.0, output + random.uniform(-0.1, 0.1)), 2)

    elif der_type == "BESS":
        solar_f = _solar_factor(hour)
        if solar_f > 0.3:
            # Charging during peak solar
            return round(-nameplate * 0.6 * solar_f * random.uniform(0.7, 1.0), 2)
        elif hour > 17:
            # Discharging in evening
            discharge = nameplate * 0.8 * random.uniform(0.6, 0.9) * limit_factor
            return round(discharge, 2)
        return round(random.uniform(-0.5, 0.5), 2)

    elif der_type == "EV Charger":
        # EVs charge mostly at night and morning
        if 7 <= hour <= 9 or 18 <= hour <= 23:
            charge = nameplate * random.uniform(0.4, 0.9)
            return round(-charge, 2)  # negative = consuming
        return 0.0

    return 0.0


class AggregatorSimulator:
    """
    Simulates a single DER aggregator connecting via IEEE 2030.5.
    Runs as a background asyncio task.
    """

    def __init__(self, defn: dict, base_url: str):
        self.defn = defn
        self.agg_id = defn["agg_id"]
        self.base_url = base_url
        self.edev_id: str = ""
        self.der_registrations: List[dict] = []  # {der_def, der_sub_id, der_internal_id, mup_id}
        self.registered = False
        self.soc: Dict[str, float] = {}  # battery SoC per DER index

    async def _post(self, path: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base_url}{path}", json=data)
            r.raise_for_status()
            return r.json()

    async def _put(self, path: str, data: dict) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.put(f"{self.base_url}{path}", json=data)
            r.raise_for_status()
            return r.json()

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{self.base_url}{path}")
            r.raise_for_status()
            return r.json()

    async def register(self):
        """Register this aggregator as an IEEE 2030.5 EndDevice."""
        logger.info(f"[{self.agg_id}] Registering with IEEE 2030.5 server...")
        resp = await self._post("/api/2030.5/edev", {
            "lFDI": self.defn["lfdi"],
            "sFDI": self.defn["sfdi"],
            "deviceCategory": "0x0080",  # 0x0080 = Aggregator
            "changedTime": int(time.time()),
            "enabled": True,
            "postRate": settings.aggregator_poll_interval,
        })
        self.edev_id = resp["id"]
        logger.info(f"[{self.agg_id}] Registered as EndDevice id={self.edev_id}")

        # Register each DER
        feeder = self.defn["feeder"]
        locations = DER_LOCATIONS[feeder]

        for i, der_def in enumerate(self.defn["ders"]):
            loc_idx = der_def["idx"]
            location = locations[loc_idx % len(locations)]
            loc_name, lat, lng, dt_id = location

            # Build internal DER ID
            der_internal_id = f"{self.agg_id}-DER-{i+1:03d}"
            der_type_code = 83 if der_def["type"] == "Solar PV" else (
                85 if der_def["type"] == "BESS" else 99
            )

            # Initialize battery SoC
            if der_def["type"] == "BESS":
                self.soc[der_internal_id] = random.uniform(40, 80)

            # Create DER in DERMS fleet first (via internal route)
            try:
                await self._post("/api/ders/create-from-aggregator", {
                    "der_id": der_internal_id,
                    "aggregator_id": self.agg_id,
                    "aggregator_name": self.defn.get("name", self.agg_id),
                    "dt_id": dt_id,
                    "feeder_id": feeder,
                    "consumer_id": f"CON-{random.randint(10000, 99999)}",
                    "meter_id": f"GMR-{random.randint(100000, 999999)}",
                    "der_type": der_def["type"],
                    "nameplate_kw": der_def["kw"],
                    "inverter_oem": der_def["oem"],
                    "location_name": f"{loc_name}, Varanasi",
                    "lat": lat + random.uniform(-0.002, 0.002),
                    "lng": lng + random.uniform(-0.002, 0.002),
                })
            except Exception as e:
                logger.debug(f"[{self.agg_id}] Fleet create: {e}")

            # Register DER via IEEE 2030.5
            mrid = f"{self.agg_id}-{der_internal_id}"
            resp = await self._post(f"/api/2030.5/edev/{self.edev_id}/der", {
                "mRID": mrid,
                "description": f"{der_def['type']} {der_def['kw']}kW at {loc_name}",
                "DERType": der_type_code,
                "nameplate_kw": der_def["kw"],
                "der_id_external": der_internal_id,
            })
            der_sub_id = resp["id"]

            # Create MirrorUsagePoint for metering
            mup_resp = await self._post("/api/2030.5/mup", {
                "href": "",
                "mRID": f"MUP-{der_internal_id}",
                "description": f"Meter for {der_internal_id}",
                "deviceLFDI": self.defn["lfdi"],
                "roleFlags": "0x1D",
                "serviceCategoryKind": 0,
                "status": 1,
                "end_device_id": self.edev_id,
            })

            self.der_registrations.append({
                "der_def": der_def,
                "der_internal_id": der_internal_id,
                "der_sub_id": der_sub_id,
                "mup_id": mup_resp["href"].split("/")[-1],
                "dt_id": dt_id,
                "loc_name": loc_name,
            })
            await asyncio.sleep(0.1)  # Stagger registrations

        self.registered = True
        logger.info(f"[{self.agg_id}] Fully registered: {len(self.der_registrations)} DERs")

    async def send_status_updates(self):
        """Send DERStatus + DERAvailability for all registered DERs."""
        now = datetime.now(timezone.utc)
        hour = now.hour + now.minute / 60.0

        for reg in self.der_registrations:
            der_def = reg["der_def"]
            der_internal_id = reg["der_internal_id"]
            der_sub_id = reg["der_sub_id"]

            current_kw = _der_current_kw(der_def, hour)
            soc = None

            # Update battery SoC
            if der_def["type"] == "BESS":
                soc = self.soc.get(der_internal_id, 50.0)
                # SoC changes based on charge/discharge
                delta = current_kw * settings.aggregator_poll_interval / (3600.0 * der_def["kw"] / 100)
                soc = max(10.0, min(95.0, soc - delta))
                self.soc[der_internal_id] = soc

            # DERStatus
            op_mode = 0 if current_kw != 0 else 2  # 0=Operating, 2=Off
            inverter_status = 4 if current_kw > 0 else 1  # 4=Grid-connected, 1=Off

            # Encode as watts (multiplier=0): value = kW * 1000
            status_payload = {
                "href": f"/api/2030.5/edev/{self.edev_id}/der/{der_sub_id}/ders",
                "operationalModeStatus": {"value": op_mode},
                "inverterStatus": {"value": inverter_status},
                "genConnectStatus": {"value": 1 if current_kw >= 0 else 0},
                "updatedTime": int(time.time()),
                "currentW": {
                    "multiplier": 0,          # 10^0 = W
                    "value": int(abs(current_kw) * 1000) if current_kw > 0 else 0,
                },
                "currentVar": {"multiplier": 0, "value": 0},
            }
            if soc is not None:
                status_payload["stateOfChargeStatus"] = {"value": int(soc)}

            try:
                await self._put(
                    f"/api/2030.5/edev/{self.edev_id}/der/{der_sub_id}/ders",
                    status_payload,
                )
            except Exception as e:
                logger.warning(f"[{self.agg_id}] DERStatus PUT failed: {e}")

            # DERAvailability — how much flex is available
            if der_def["type"] == "BESS":
                # BESS: available discharge power based on SoC
                soc_frac = (soc / 100.0) if soc is not None else 0.5
                avail_kw = round(der_def["kw"] * soc_frac * 0.9, 2)  # 90% efficiency
            else:
                # Solar/EV: curtailment headroom (how much we can reduce)
                avail_kw = max(0.0, current_kw) if current_kw else 0.0
            try:
                await self._put(
                    f"/api/2030.5/edev/{self.edev_id}/der/{der_sub_id}/dera",
                    {
                        "href": f"/api/2030.5/edev/{self.edev_id}/der/{der_sub_id}/dera",
                        "availabilityDuration": 900,
                        "reservePercent": 10.0,
                        "statWAvail": {"multiplier": 0, "value": int(avail_kw * 1000)},
                        "updatedTime": int(time.time()),
                    },
                )
            except Exception as e:
                logger.warning(f"[{self.agg_id}] DERAvailability PUT failed: {e}")

            # Post meter reading every update cycle
            reading_kwh = abs(current_kw) * settings.aggregator_poll_interval / 3600.0
            try:
                await self._post(f"/api/2030.5/mup/{reg['mup_id']}/mr", {
                    "mRID": f"MR-{der_internal_id}-{int(time.time())}",
                    "description": f"15-min energy reading for {der_internal_id}",
                    "readingType": {
                        "accumulationBehaviour": 4,
                        "commodity": 1,
                        "dataQualifier": 0,
                        "kind": 12,
                        "uom": 72,
                    },
                    "readings": [
                        {
                            "value": int(reading_kwh * 1000),  # mWh
                            "multiplier": -3,
                            "timePeriod": {
                                "start": int(time.time()) - settings.aggregator_poll_interval,
                                "duration": settings.aggregator_poll_interval,
                            },
                            "qualityFlags": "0x0000",
                        }
                    ],
                })
            except Exception:
                pass

            await asyncio.sleep(0.05)

    async def run(self):
        """Main simulation loop — register then periodically send updates."""
        # Wait for server to be ready
        await asyncio.sleep(3 + random.uniform(0, 2))

        # Register
        for attempt in range(5):
            try:
                await self.register()
                break
            except Exception as e:
                logger.warning(f"[{self.agg_id}] Registration attempt {attempt+1} failed: {e}")
                await asyncio.sleep(5)

        if not self.registered:
            logger.error(f"[{self.agg_id}] Failed to register after 5 attempts")
            return

        # Periodic status updates
        while True:
            try:
                await self.send_status_updates()
                logger.info(f"[{self.agg_id}] Status updates sent for {len(self.der_registrations)} DERs")
            except Exception as e:
                logger.error(f"[{self.agg_id}] Status update error: {e}")

            await asyncio.sleep(settings.aggregator_poll_interval)


async def start_aggregator_simulators(base_url: str):
    """Start all aggregator simulators as background tasks."""
    tasks = []
    for defn in AGGREGATOR_DEFINITIONS:
        sim = AggregatorSimulator(defn, base_url)
        task = asyncio.create_task(sim.run(), name=f"aggregator-{defn['agg_id']}")
        tasks.append(task)
        logger.info(f"Aggregator simulator started: {defn['agg_id']}")
    return tasks
