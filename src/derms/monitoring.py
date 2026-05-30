"""
DERMS Monitoring Module
========================
Background task that polls GE ADMS for real-time grid state,
triggers voltage monitoring, and feeds dispatch engine.
Also sends aggregated DER status back to ADMS (DERMS → ADMS).
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

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


async def real_pilot_simulation_loop():
    """
    Drive simulated 15-min generation for real Lanka Feeder DERs.
    Uses irradiance model (sin curve, IST timezone) to compute
    per-DER current_kw from nameplate capacity — mimics what real
    MDMS 15-min data would provide. Replace with MDMS API polling
    when 15-min interval data becomes available.
    """
    import math
    from src.data.real_pilot_data import LANKA_DERS

    logger.info("Real pilot simulation loop started (Lanka Feeder LK1)")
    await asyncio.sleep(5)   # Wait for DER seed to complete

    while True:
        try:
            now = datetime.now(timezone.utc)
            hour_utc = now.hour + now.minute / 60.0
            hour_ist = (hour_utc + 5.5) % 24

            # Irradiance factor: sin curve, 6am–6pm IST
            if 6 <= hour_ist <= 18:
                solar_factor = max(0.0, math.sin(math.pi * (hour_ist - 6) / 12))
                # Add realistic variability (cloud cover ±10%)
                import random
                solar_factor *= random.uniform(0.88, 0.98)
            else:
                solar_factor = 0.0

            for der_data in LANKA_DERS:
                der_id = der_data["der_id"]
                if der_id not in fleet._der_cache:
                    continue
                nameplate = der_data["nameplate_kw"]
                current_kw = round(nameplate * solar_factor, 2)
                fleet._der_cache[der_id]["current_kw"] = current_kw
                fleet._der_cache[der_id]["status"] = "Online"
                fleet._der_cache[der_id]["last_update"] = now.isoformat()

                if nameplate > 0:
                    cuf = (current_kw / nameplate) * 100
                    fleet._der_cache[der_id]["cuf_pct"] = round(cuf, 1)
                    fleet._der_cache[der_id]["pr_pct"] = round(
                        (current_kw / (nameplate * solar_factor) * 100) if solar_factor > 0 else 100.0, 1
                    )

            # Auto-run load flow for LK1 on each 15-min simulated cycle
            try:
                from src.loadflow.engine import run_load_flow
                from src.api.loadflow_routes import _enrich_dts_with_realtime, _enrich_ders_with_realtime
                dts = _enrich_dts_with_realtime("LK1")
                ders = _enrich_ders_with_realtime("LK1")
                run_load_flow("LK1", dts, ders, label="quasi_realtime")
            except Exception as lf_err:
                logger.warning(f"Load flow cycle error: {lf_err}")

        except Exception as e:
            logger.error(f"Real pilot simulation error: {e}")

        await asyncio.sleep(900)   # 15-min cycle; replace with MDMS webhook when available


async def oe_monitoring_loop():
    """
    Compute Operating Envelope every 30 min from load flow results.
    Detects RPF (A-03) and OE exceedances (A-06) and raises alerts.
    Also rebuilds the 48-block daily schedule daily at midnight IST.
    """
    from src.loadflow.oe_engine import compute_oe, compute_oe_schedule
    logger.info("OE monitoring loop started")
    await asyncio.sleep(20)  # Wait for first load flow run

    last_schedule_date = None
    while True:
        try:
            compute_oe("LK1")

            # Rebuild daily schedule once per day at midnight IST
            now = datetime.now(timezone.utc)
            ist_date = (now + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            if ist_date != last_schedule_date:
                compute_oe_schedule("LK1")
                last_schedule_date = ist_date
                logger.info(f"OE 48-block schedule computed for {ist_date}")

        except Exception as e:
            logger.error(f"OE monitoring error: {e}")

        await asyncio.sleep(1800)  # 30-min cycle


async def alert_escalation_loop():
    """
    Check for P1 alerts unacknowledged > 15 min and > 60 min.
    PRD escalation policy:
      > 15 min: add 'Escalated to Supervisor' note
      > 60 min: add 'Escalated to Nodal Officer' note
    In production: send actual SMS/email via notification gateway.
    """
    logger.info("Alert escalation loop started")
    await asyncio.sleep(60)

    while True:
        try:
            now = datetime.now(timezone.utc)
            alerts = fleet.get_alerts(limit=200)
            for alert in alerts:
                if alert.get("resolved") or alert.get("state") == "RESOLVED":
                    continue
                priority = alert.get("priority", "LOW")
                if priority not in ("HIGH", "CRITICAL"):
                    continue
                created_iso = alert.get("created_at", "")
                try:
                    created = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except Exception:
                    continue

                age_min = (now - created).total_seconds() / 60
                esc = alert.get("escalation", [])

                if age_min > 60 and not any(e.get("level") == "nodal_officer" for e in esc):
                    if "escalation" not in alert:
                        alert["escalation"] = []
                    alert["escalation"].append({
                        "level": "nodal_officer",
                        "ts": now.isoformat(),
                        "action": "Auto-email sent to PuVVNL Nodal Officer",
                    })
                    logger.warning(
                        f"ESCALATION (60min): Alert {alert['id'][:8]} → Nodal Officer email"
                    )
                elif age_min > 15 and not any(e.get("level") == "supervisor" for e in esc):
                    if "escalation" not in alert:
                        alert["escalation"] = []
                    alert["escalation"].append({
                        "level": "supervisor",
                        "ts": now.isoformat(),
                        "action": "Auto-SMS sent to Grid Operator Supervisor",
                    })
                    logger.warning(
                        f"ESCALATION (15min): Alert {alert['id'][:8]} → Supervisor SMS"
                    )
        except Exception as e:
            logger.error(f"Alert escalation error: {e}")

        await asyncio.sleep(120)  # Check every 2 min


async def forecast_refresh_loop():
    """
    Refresh D+1 generation forecast nightly at 17:00 IST (11:30 UTC).
    Uses NASA POWER API → Ineichen-Perez model.
    """
    logger.info("Forecast refresh loop started")
    while True:
        try:
            now = datetime.now(timezone.utc)
            ist_hour = (now.hour + 5.5) % 24
            ist_minute = (now.minute + 30) % 60

            # Target 17:00 IST — sleep until next 17:00
            target_h, target_m = 17, 0
            minutes_until = ((target_h - int(ist_hour)) * 60 + (target_m - int(ist_minute))) % (24 * 60)
            if minutes_until == 0:
                minutes_until = 24 * 60  # Already past — wait full day

            await asyncio.sleep(minutes_until * 60)

            from src.api.forecast_routes import _refresh_forecast
            await _refresh_forecast()
            logger.info("D+1 generation forecast refreshed (nightly 17:00 IST)")
        except Exception as e:
            logger.error(f"Forecast refresh error: {e}")
            await asyncio.sleep(3600)  # Retry in 1h on error


async def start_background_tasks():
    """Start all monitoring and simulation background tasks."""
    tasks = [
        asyncio.create_task(adms_polling_loop(), name="adms-poll"),
        asyncio.create_task(dispatch_loop(), name="dispatch"),
        asyncio.create_task(offline_detection_loop(), name="offline-detect"),
        asyncio.create_task(real_pilot_simulation_loop(), name="lk1-real-pilot"),
        asyncio.create_task(oe_monitoring_loop(), name="oe-monitor"),
        asyncio.create_task(alert_escalation_loop(), name="alert-escalation"),
        asyncio.create_task(forecast_refresh_loop(), name="forecast-refresh"),
    ]

    # Start aggregator simulators (simulated feeders FDR-01, FDR-02 only)
    base_url = f"http://localhost:{settings.port}"
    from src.integrations.ieee2030_5.simulator import start_aggregator_simulators
    agg_tasks = await start_aggregator_simulators(base_url)
    tasks.extend(agg_tasks)

    return tasks
