"""API routes for ADMS integration status and topology."""
from fastapi import APIRouter
import src.derms.fleet as fleet

router = APIRouter(prefix="/api/adms", tags=["ADMS Integration"])


@router.get("/status")
async def adms_integration_status():
    """ADMS integration health and last sync info."""
    import httpx
    from src.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.adms_base_url}/status")
            adms_info = r.json()
    except Exception as e:
        adms_info = {"error": str(e), "status": "Unreachable"}

    return {
        "integration_type": "GE ADMS",
        "protocol": "CIM XML (IEC 61970-452) + REST",
        "adms_info": adms_info,
        "derms_feeders": len(fleet.get_all_feeders()),
        "derms_dts": len(fleet.get_all_dts()),
    }


@router.get("/topology")
async def get_topology():
    """Current grid topology as known to DERMS (parsed from CIM)."""
    return {
        "feeders": fleet.get_all_feeders(),
        "distribution_transformers": fleet.get_all_dts(),
    }


@router.get("/grid-state")
async def get_grid_state():
    """Real-time grid state from ADMS."""
    import httpx
    from src.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.adms_base_url}/realtime/state")
            return r.json()
    except Exception as e:
        return {"error": str(e)}


@router.get("/hosting-capacity")
async def get_hosting_capacity():
    """Hosting capacity from ADMS (EPRI DRIVE methodology)."""
    import httpx
    from src.config import settings
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.adms_base_url}/hosting-capacity")
            hc = r.json()
            # Overlay DER registration data
            for item in hc.get("hosting_capacity", []):
                dt_id = item["dt_id"]
                dt = next((d for d in fleet.get_all_dts() if d["dt_id"] == dt_id), None)
                if dt:
                    item["used_capacity_kw"] = dt.get("used_capacity_kw", 0)
                    item["available_kw"] = item["hosting_capacity_kw"] - item["used_capacity_kw"]
                    item["utilization_pct"] = round(
                        (item["used_capacity_kw"] / item["hosting_capacity_kw"]) * 100, 1
                    ) if item["hosting_capacity_kw"] > 0 else 0
            return hc
    except Exception as e:
        return {"error": str(e)}


@router.get("/cim-topology")
async def get_cim_topology():
    """Raw CIM XML topology from ADMS simulator."""
    import httpx
    from src.config import settings
    from fastapi.responses import Response
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.adms_base_url}/topology/cim")
            return Response(content=r.text, media_type="application/xml")
    except Exception as e:
        return {"error": str(e)}
