"""
DERMS Monitoring Module
========================
Background task that polls GE ADMS for real-time grid state,
triggers voltage monitoring, and feeds dispatch engine.
Also sends aggregated DER status back to ADMS (DERMS → ADMS).
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx

import src.derms.fleet as fleet
import src.derms.dispatch as dispatch
from src.config import settings

logger = logging.getLogger(__name__)


async def adms_polling_loop():
    """
    Poll simulated GE ADMS every N seconds for real-time grid state.
    In production: poll GE APM REST API or subscribe to IEC 61968 message bus.
    """
    logger.info("ADMS polling loop started")
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{settings.adms_base_url}/realtime/state")
                if r.status_code == 200:
                    state = r.json()
                    await fleet.update_grid_state(state)
                    logger.debug(
                        f"ADMS poll: {len(state.get('feeders', []))} feeders, "
                        f"{len(state.get('distribution_transformers', []))} DTs updated"
                    )
                    # Report DER status back to ADMS
                    await _report_ders_to_adms(client)
                else:
                    logger.warning(f"ADMS poll HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"ADMS polling error: {e}")

        await asyncio.sleep(settings.adms_poll_interval)


async def _report_ders_to_adms(client: httpx.AsyncClient):
    """
    Report aggregated DER status back to GE ADMS.
    ADMS uses this for updated load flow and hosting capacity calculations.
    Production: POST to ADMS IEC 61968 message bus endpoint.
    """
    ders = fleet.get_all_ders()
    feeders = fleet.get_all_feeders()

    # Aggregate per feeder
    feeder_gen = {}
    for der in ders:
        fid = der["feeder_id"]
        feeder_gen[fid] = feeder_gen.get(fid, 0) + der.get("current_kw", 0)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "DERMS",
        "ders": [
            {
                "der_id": d["der_id"],
                "feeder_id": d["feeder_id"],
                "dt_id": d["dt_id"],
                "status": d["status"],
                "current_kw": d.get("current_kw", 0),
                "nameplate_kw": d["nameplate_kw"],
                "der_type": d["der_type"],
            }
            for d in ders
        ],
        "feeder_aggregation": [
            {"feeder_id": fid, "total_gen_kw": round(gen, 2)}
            for fid, gen in feeder_gen.items()
        ],
    }

    try:
        await client.post(f"{settings.adms_base_url}/der-status", json=payload)
    except Exception:
        pass


async def dispatch_loop():
    """
    Periodically run the dispatch evaluation engine.
    Checks grid conditions and issues DERControls as needed.
    """
    logger.info("Dispatch evaluation loop started")
    await asyncio.sleep(15)  # Wait for fleet to populate first

    while True:
        try:
            await dispatch.run_dispatch_cycle()
        except Exception as e:
            logger.error(f"Dispatch cycle error: {e}")

        await asyncio.sleep(settings.dispatch_check_interval)


async def offline_detection_loop():
    """
    Check for DERs that haven't sent status updates recently.
    Marks them as Offline/Degraded and raises alerts.
    """
    from datetime import timedelta
    logger.info("DER offline detection loop started")
    await asyncio.sleep(30)

    while True:
        try:
            now = datetime.now(timezone.utc)
            ders = fleet.get_all_ders()
            for der in ders:
                last_update_str = der.get("last_update")
                if not last_update_str:
                    continue
                # Parse last update
                try:
                    last_update = datetime.fromisoformat(last_update_str.replace("Z", "+00:00"))
                    if last_update.tzinfo is None:
                        last_update = last_update.replace(tzinfo=timezone.utc)
                except Exception:
                    continue

                age_min = (now - last_update).total_seconds() / 60

                if age_min > 30 and der.get("status") == "Online":
                    # Mark as Degraded
                    fleet._der_cache[der["der_id"]]["status"] = "Degraded"
                    fleet.add_alert(
                        "warning", "MEDIUM",
                        f"DER {der['der_id']} no update for {age_min:.0f} min — marked Degraded",
                        "Monitoring", der["der_id"], "DER"
                    )
                elif age_min > 60 and der.get("status") == "Degraded":
                    fleet._der_cache[der["der_id"]]["status"] = "Offline"
                    fleet.add_alert(
                        "critical", "HIGH",
                        f"DER {der['der_id']} offline (no update for {age_min:.0f} min)",
                        "Monitoring", der["der_id"], "DER"
                    )
        except Exception as e:
            logger.error(f"Offline detection error: {e}")

        await asyncio.sleep(60)


async def start_background_tasks():
    """Start all monitoring and simulation background tasks."""
    tasks = [
        asyncio.create_task(adms_polling_loop(), name="adms-poll"),
        asyncio.create_task(dispatch_loop(), name="dispatch"),
        asyncio.create_task(offline_detection_loop(), name="offline-detect"),
    ]

    # Start aggregator simulators
    base_url = f"http://localhost:{settings.port}"
    from src.integrations.ieee2030_5.simulator import start_aggregator_simulators
    agg_tasks = await start_aggregator_simulators(base_url)
    tasks.extend(agg_tasks)

    return tasks
