"""
Counterparty Integration Engine
================================
Makes actual HTTP calls to third-party DER aggregator APIs.
Standard API schema (what counterparties must expose):

  GET {base}/health    → {status, provider, der_count}
  GET {base}/assets    → {assets: [{id, name, type, capacity_kw, feeder_id, dt_id, lat, lng}]}
  GET {base}/telemetry → {telemetry: [{id, current_kw, voltage_v, status}]}
  GET {base}/forecast  → {forecast: [{hour_ist, forecast_kw, p10_kw, p90_kw}]}
  GET {base}/baseline  → {baseline: [{id, hourly_expected_kw, daily_expected_kwh}]}
"""
import uuid
import logging
import httpx
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── In-memory store ────────────────────────────────────────────────────────────
_counterparties: dict = {}   # cp_id → config dict
_cp_ders: dict = {}          # cp_id → list of raw asset dicts from counterparty


# ── URL resolution ─────────────────────────────────────────────────────────────

def _resolve_base(cp: dict) -> str:
    """Convert relative /sim/cp base to full localhost URL for self-calls."""
    base = cp["api_base_url"].rstrip("/")
    if base.startswith("/") or not base.startswith("http"):
        from src.config import settings
        base = f"http://127.0.0.1:{settings.port}/{base.lstrip('/')}"
    return base


def _headers(cp: dict) -> dict:
    auth = cp.get("auth_type", "api_key")
    if auth == "api_key":
        return {cp.get("api_key_header", "X-API-Key"): cp.get("api_key", "")}
    if auth == "bearer":
        return {"Authorization": f"Bearer {cp.get('api_key', '')}"}
    return {}


# ── API calls ──────────────────────────────────────────────────────────────────

async def test_connection(cp: dict) -> dict:
    url = f"{_resolve_base(cp)}/health"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers=_headers(cp))
        if r.status_code == 200:
            data = r.json()
            return {
                "ok": True,
                "http_status": 200,
                "provider": data.get("provider", "Unknown"),
                "der_count": data.get("der_count"),
                "api_version": data.get("api_version"),
            }
        return {"ok": False, "http_status": r.status_code, "error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def sync_assets(cp: dict) -> list:
    """
    Pull asset list from counterparty, create entries in DERMS fleet cache.
    DERs are tagged data_source='counterparty_api' for audit trail.
    """
    import src.derms.fleet as fleet

    url = f"{_resolve_base(cp)}/assets"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers=_headers(cp))
    if r.status_code != 200:
        raise ValueError(f"Assets endpoint returned HTTP {r.status_code}")

    data = r.json()
    assets = data.get("assets", data) if isinstance(data, dict) else data
    _cp_ders[cp["id"]] = assets

    registered = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for a in assets:
        der_id = f"{cp['id']}-{a['id']}"
        if der_id not in fleet._der_cache:
            fleet._der_cache[der_id] = {
                "der_id": der_id,
                "aggregator_id": cp["id"],
                "feeder_id": a.get("feeder_id", ""),
                "dt_id": a.get("dt_id", ""),
                "der_type": a.get("type", "Solar PV"),
                "nameplate_kw": float(a.get("capacity_kw", 0)),
                "location_name": a.get("name", a["id"]),
                "status": "Online",
                "current_kw": 0.0,
                "current_kvar": 0.0,
                "voltage_v": 230.0,
                "soc_pct": None,
                "cuf_pct": 0.0,
                "pr_pct": 0.0,
                "available_kw": float(a.get("capacity_kw", 0)),
                "curtailment_pct": 0.0,
                "lat": a.get("lat", 25.27),
                "lng": a.get("lng", 82.99),
                "consumer_id": a.get("consumer_id", ""),
                "meter_id": a.get("id", der_id),
                "commission_date": a.get("commission_date"),
                "metering_type": a.get("metering_type", "NET"),
                "data_source": "counterparty_api",
                "source_id": a["id"],
                "cp_name": cp["name"],
                "last_update": now_iso,
            }
            logger.info(f"Registered DER {der_id} from counterparty {cp['name']}")
        else:
            # Update metadata but keep live telemetry
            fleet._der_cache[der_id]["location_name"] = a.get("name", der_id)
            fleet._der_cache[der_id]["nameplate_kw"] = float(a.get("capacity_kw", 0))
        registered.append(der_id)

    return registered


async def refresh_telemetry(cp: dict) -> int:
    """Pull current generation from counterparty and update fleet cache."""
    import src.derms.fleet as fleet

    url = f"{_resolve_base(cp)}/telemetry"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_headers(cp))
        if r.status_code != 200:
            return 0
        items = r.json().get("telemetry", [])
        now_iso = datetime.now(timezone.utc).isoformat()
        updated = 0
        for t in items:
            der_id = f"{cp['id']}-{t['id']}"
            if der_id in fleet._der_cache:
                fleet._der_cache[der_id]["current_kw"] = float(t.get("current_kw", 0))
                fleet._der_cache[der_id]["voltage_v"] = float(t.get("voltage_v", 230))
                fleet._der_cache[der_id]["status"] = t.get("status", "Online")
                fleet._der_cache[der_id]["cuf_pct"] = float(t.get("cuf_pct", 0))
                fleet._der_cache[der_id]["last_update"] = now_iso
                updated += 1
        return updated
    except Exception as e:
        logger.warning(f"Telemetry refresh failed for {cp['name']}: {e}")
        return 0


async def fetch_telemetry(cp: dict) -> list:
    url = f"{_resolve_base(cp)}/telemetry"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_headers(cp))
        if r.status_code == 200:
            return r.json().get("telemetry", [])
    except Exception:
        pass
    return []


async def fetch_forecast(cp: dict) -> list:
    url = f"{_resolve_base(cp)}/forecast"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_headers(cp))
        if r.status_code == 200:
            return r.json().get("forecast", [])
    except Exception:
        pass
    return []


async def fetch_baseline(cp: dict) -> list:
    url = f"{_resolve_base(cp)}/baseline"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=_headers(cp))
        if r.status_code == 200:
            return r.json().get("baseline", [])
    except Exception:
        pass
    return []


# ── CRUD ───────────────────────────────────────────────────────────────────────

def list_counterparties() -> list:
    return list(_counterparties.values())


def get_counterparty(cp_id: str) -> dict | None:
    return _counterparties.get(cp_id)


def create_counterparty(data: dict) -> dict:
    cp_id = f"CP-{str(uuid.uuid4())[:8].upper()}"
    cp = {
        "id": cp_id,
        "name": data["name"],
        "type": data.get("type", "DER_AGGREGATOR"),
        "api_base_url": data["api_base_url"],
        "auth_type": data.get("auth_type", "api_key"),
        "api_key_header": data.get("api_key_header", "X-API-Key"),
        "api_key": data.get("api_key", ""),
        "program_ids": data.get("program_ids", []),
        "status": "pending",
        "health": "unknown",
        "last_sync": None,
        "der_count": 0,
        "notes": data.get("notes", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _counterparties[cp_id] = cp
    return cp


def update_counterparty(cp_id: str, data: dict) -> dict | None:
    cp = _counterparties.get(cp_id)
    if not cp:
        return None
    for k, v in data.items():
        if k not in ("id", "created_at"):
            cp[k] = v
    return cp


def delete_counterparty(cp_id: str) -> bool:
    if cp_id in _counterparties:
        del _counterparties[cp_id]
        _cp_ders.pop(cp_id, None)
        return True
    return False


def get_cp_ders(cp_id: str) -> list:
    return _cp_ders.get(cp_id, [])


def _seed_demo():
    """Pre-seed a demo counterparty pointing to the built-in simulator."""
    from src.config import settings
    cp = create_counterparty({
        "name": "GreenAlt Energy Pvt Ltd",
        "type": "DER_AGGREGATOR",
        "api_base_url": f"http://127.0.0.1:{settings.port}/sim/cp",
        "auth_type": "api_key",
        "api_key_header": "X-API-Key",
        "api_key": "demo-key-greenalt-001",
        "notes": (
            "Demo counterparty — backed by built-in simulator. "
            "Replace api_base_url with the real aggregator API endpoint."
        ),
    })
    _counterparties[cp["id"]]["status"] = "active"
    _counterparties[cp["id"]]["health"] = "ok"


_seed_demo()
