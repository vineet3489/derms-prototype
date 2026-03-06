# DERMS → Production Readiness Guide

**Target:** L&T DES deployment for PUVVNL Varanasi pilot, with L&T Neural Grid Platform as global super-aggregator
**Current state:** Working prototype on Render.com (simulated data, SQLite, no auth)
**Goal:** Grid-connected, regulatory-compliant production system

---

## What Needs to Change — Summary

| Component | Prototype | Production |
|-----------|-----------|------------|
| Database | SQLite (ephemeral) | PostgreSQL (managed, HA) |
| Auth | None | mTLS + OAuth 2.0 JWT |
| ADMS | Simulator in same process | Real GE ADMS REST API (separate host) |
| DER telemetry | Internal simulator loop | Real IEEE 2030.5 device callbacks |
| Meter data | Simulated readings | AMI/MDMS polling (IEC 61968-9) |
| DR signals | Internal HTTP | OpenADR 2.0b VTN (separate service) |
| Market settlement | In-memory JSON | UN/EDIFACT MSCONS + ledger DB |
| Process | Single Uvicorn process | Gunicorn + Uvicorn workers + process manager |
| Secrets | Hardcoded / `.env` | Secrets manager (Vault / AWS SSM) |
| Logging | stderr | Structured JSON → ELK / Loki |
| Infra | Render.com free tier | Dedicated VM or Kubernetes (India region) |
| TLS | Render-provided | Proper cert chain for grid devices (X.509 PKI) |

Estimated engineering effort to production: **8–12 weeks** for a 2-person team.

---

## Phase 1 — Infrastructure (Week 1–2)

### 1.1 Choose a Cloud Region

For PUVVNL (government utility, UP), data must stay in India.
- **AWS:** `ap-south-1` (Mumbai)
- **Azure:** `Central India` (Pune)
- **GCP:** `asia-south1` (Mumbai)
- **On-premise at PUVVNL:** feasible but needs L&T to manage infra; recommend cloud-first

Recommended minimum VM spec for a single-site pilot:
- 2 × application VMs (4 vCPU, 8 GB RAM) behind a load balancer
- 1 × managed PostgreSQL (db.t3.medium or equivalent)
- 1 × Redis (for WebSocket pub/sub when scaling to multiple app nodes)

---

### 1.2 Switch from SQLite to PostgreSQL

**Step 1 — Install driver:**
```bash
pip install asyncpg sqlalchemy[asyncio]
```

**Step 2 — Update `requirements.txt`:**
```
asyncpg>=0.29.0
# remove: aiosqlite>=0.19.0
```

**Step 3 — Update `src/config.py`:**
```python
class Settings(BaseSettings):
    db_url: str = "postgresql+asyncpg://derms:PASSWORD@db-host:5432/derms"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    debug: bool = False          # MUST be False in production

    class Config:
        env_file = ".env"
```

**Step 4 — Update `src/database.py`:**
```python
engine = create_async_engine(
    settings.db_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,          # detect stale connections
    echo=False,
)
```

**Step 5 — Create DB and run migrations:**
```bash
# One-time schema creation (existing init_db() will work)
# For future changes, add Alembic:
pip install alembic
alembic init alembic
# Then generate migrations from model changes with: alembic revision --autogenerate
```

**Step 6 — Set environment variable (never hardcode):**
```bash
export DATABASE_URL="postgresql+asyncpg://derms:STRONGPASSWORD@db.internal:5432/derms"
```

---

### 1.3 Run with Multiple Workers

**Replace `run.py` with Gunicorn:**
```bash
pip install gunicorn
```

Create `gunicorn.conf.py`:
```python
bind = "0.0.0.0:8080"
workers = 4                    # 2 × CPU cores
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
keepalive = 5
timeout = 30
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"
```

Start command:
```bash
gunicorn src.main:app -c gunicorn.conf.py
```

**Important:** WebSocket broadcast and background tasks currently run in a single asyncio event loop. With multiple Gunicorn workers, each process has its own loop and its own `_der_cache`. You need Redis pub/sub to share state across workers. See §2.4.

---

### 1.4 Secrets Management

Never put credentials in `.env` files in production.

**Option A — AWS SSM Parameter Store (recommended for AWS):**
```python
# In src/config.py, pull from SSM at startup
import boto3

def get_secret(name: str) -> str:
    ssm = boto3.client("ssm", region_name="ap-south-1")
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

class Settings(BaseSettings):
    db_url: str = get_secret("/derms/prod/db_url")
    adms_api_key: str = get_secret("/derms/prod/adms_api_key")
    jwt_secret: str = get_secret("/derms/prod/jwt_secret")
```

**Option B — HashiCorp Vault:**
```bash
vault kv put secret/derms/prod db_url="postgresql+asyncpg://..."
```

**Option C — Kubernetes Secrets** (if deploying to K8s):
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: derms-secrets
type: Opaque
stringData:
  DATABASE_URL: "postgresql+asyncpg://..."
  ADMS_API_KEY: "..."
```

---

## Phase 2 — Authentication & Security (Week 2–3)

### 2.1 Add JWT Auth to the REST API

Install:
```bash
pip install python-jose[cryptography] passlib[bcrypt]
```

Add to `src/auth.py` (new file):
```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

JWT_SECRET  = settings.jwt_secret
JWT_ALG     = "RS256"   # use RS256 (asymmetric) not HS256 in production

bearer = HTTPBearer()

def verify_token(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALG])
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
```

Apply to sensitive routes in each router:
```python
# src/api/der_routes.py
@router.post("/dispatch/dr-event", dependencies=[Depends(verify_token)])
async def dispatch_dr_event(...):
    ...

# src/api/market_routes.py
@router.post("/events/{event_id}/clear", dependencies=[Depends(verify_token)])
async def clear_market(...):
    ...
```

Public (unauthenticated) routes: `GET /health`, `GET /`, `GET /ui`, `GET /market`, WebSocket `/ws` (authenticate via token query param).

---

### 2.2 mTLS for IEEE 2030.5 Device Connections

The IEEE 2030.5 standard requires mutual TLS between DER devices and the server. Each aggregator device gets an X.509 certificate signed by the DERMS Certificate Authority.

**PKI setup:**
```bash
# Create DERMS CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -out ca.crt -subj "/CN=DERMS-CA/O=LT-DES/C=IN"

# For each aggregator, issue a device certificate
openssl genrsa -out agg001.key 2048
openssl req -new -key agg001.key -out agg001.csr \
  -subj "/CN=AGG-001/O=TataPowerSolar/C=IN"
openssl x509 -req -in agg001.csr -CA ca.crt -CAkey ca.key \
  -CAcreateserial -out agg001.crt -days 365
```

**Enable mTLS in Uvicorn:**
```python
# gunicorn.conf.py
keyfile  = "/etc/ssl/derms/server.key"
certfile = "/etc/ssl/derms/server.crt"
ca_certs = "/etc/ssl/derms/ca.crt"
cert_reqs = 2   # ssl.CERT_REQUIRED — enforce client cert
```

**Update IEEE 2030.5 server** to extract device LFDI from the client certificate CN field:
```python
# src/integrations/ieee2030_5/server.py
from fastapi import Request

@router.post("/edev")
async def register_enddevice(request: Request, body: EndDevice):
    # In production, verify LFDI matches cert CN
    client_cert = request.scope.get("ssl_object")
    if client_cert:
        cert_cn = client_cert.getpeercert()["subject"][0][0][1]
        if cert_cn != body.lFDI:
            raise HTTPException(403, "Certificate CN does not match LFDI")
    ...
```

---

### 2.3 HTTPS / TLS Termination

For production, terminate TLS at the load balancer (AWS ALB / nginx), not at the application level. The app itself runs plain HTTP on port 8080 internally.

**nginx reverse proxy config:**
```nginx
server {
    listen 443 ssl;
    server_name derms.ltdes.in;

    ssl_certificate     /etc/letsencrypt/live/derms.ltdes.in/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/derms.ltdes.in/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # Forward IEEE 2030.5 device connections with client cert passthrough
    location /api/2030.5/ {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   X-SSL-Client-Cert $ssl_client_escaped_cert;
    }

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";  # WebSocket support
        proxy_set_header   Host $host;
    }
}
```

---

### 2.4 WebSocket Scaling (Multi-Worker)

When running multiple Gunicorn workers, each has its own `ConnectionManager` and `_der_cache`. A WebSocket connection on worker 1 won't receive a broadcast sent from worker 2.

**Fix — Redis pub/sub:**
```bash
pip install redis[asyncio]
```

Replace `ConnectionManager` in `src/main.py`:
```python
import redis.asyncio as aioredis

redis_client = aioredis.from_url(settings.redis_url)

async def broadcast_loop():
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("fleet_updates")
    async for message in pubsub.listen():
        if message["type"] == "message":
            payload = message["data"]
            # broadcast to all local WebSocket connections
            for ws in manager.active.copy():
                await ws.send_text(payload)

# In fleet.py, after updating _der_cache:
async def publish_fleet_update(summary: dict):
    await redis_client.publish("fleet_updates", json.dumps({
        "type": "fleet_summary", "data": summary
    }))
```

Add to `src/config.py`:
```python
redis_url: str = "redis://redis.internal:6379/0"
```

---

## Phase 3 — Replace Simulators with Real Integrations (Week 3–8)

This is the most significant part of going to production. Each simulator is a separate plug-in point.

---

### 3.1 Replace ADMS Simulator → Real GE ADMS

**Current:** `src/integrations/adms/simulator.py` runs as a FastAPI sub-app at `/sim/adms/`.

**Production:** Point `settings.adms_base_url` at the real GE ADMS REST endpoint.

**Step 1 — Update `src/config.py`:**
```python
adms_base_url: str = "https://adms.puvvnl.gov.in/api/v2"
adms_api_key:  str = ""   # from secrets manager
adms_verify_ssl: bool = True
```

**Step 2 — Update `src/derms/fleet.py` — `initialize_fleet()`:**

The current code calls `/sim/adms/realtime/state`. Replace with the actual GE ADMS endpoint. GE ADMS exposes a REST API (typically SOAP/REST hybrid). The CIM topology export is usually at a fixed endpoint:

```python
async def initialize_fleet():
    async with httpx.AsyncClient(verify=settings.adms_verify_ssl) as client:
        # GE ADMS topology — IEC 61970-452 CIM XML
        r = await client.get(
            f"{settings.adms_base_url}/topology/export",
            headers={"X-API-Key": settings.adms_api_key},
        )
        cim_xml = r.text
        # parse with existing cim_parser.py — no changes needed there

        # GE ADMS SCADA state — typically a different endpoint
        r2 = await client.get(
            f"{settings.adms_base_url}/scada/feeders",
            headers={"X-API-Key": settings.adms_api_key},
        )
        feeder_state = r2.json()
        # map feeder_state to _feeder_cache using the same structure
```

**Step 3 — Remove the ADMS simulator from the registered routers in `src/main.py`:**
```python
# Remove these two lines when real ADMS is connected:
# from src.integrations.adms.simulator import router as adms_sim_router
# app.include_router(adms_sim_router)
```

Keep the `/sim/adms/` simulator available behind a feature flag during parallel-run testing:
```python
if settings.adms_simulator_enabled:
    from src.integrations.adms.simulator import router as adms_sim_router
    app.include_router(adms_sim_router)
```

Add to config:
```python
adms_simulator_enabled: bool = True   # set False in production .env
```

---

### 3.2 Replace IEEE 2030.5 Simulator → Real DER Aggregators

**Current:** `src/integrations/ieee2030_5/simulator.py` runs internal asyncio loops that POST to the same FastAPI process.

**Production:** Real aggregator devices (Tata Power Solar DES, Adani Green, ReNew Power gateways) connect via HTTPS/mTLS and POST to the same IEEE 2030.5 endpoints already built.

**Nothing changes in the server** — `src/integrations/ieee2030_5/server.py` is production-ready. It already handles:
- `POST /api/2030.5/edev` — EndDevice registration
- `PUT /api/2030.5/edev/{lfdi}/der/{id}/ders` — DER settings
- `PUT /api/2030.5/edev/{lfdi}/der/{id}/dera` — DER availability
- `POST /api/2030.5/mup/{lfdi}/mr` — meter readings

**What to do:**
1. Issue X.509 certificates to each real aggregator gateway (see §2.2)
2. Provide aggregators with the DERMS IEEE 2030.5 base URL + their certificate
3. Disable the internal simulator:
```python
# src/derms/monitoring.py — start_background_tasks()
if settings.ieee_simulator_enabled:
    from src.integrations.ieee2030_5.simulator import start_aggregator_simulators
    tasks.extend(await start_aggregator_simulators())
```

Add to config:
```python
ieee_simulator_enabled: bool = True   # set False in production
```

4. The real aggregators run their own client software (typically a DER Gateway or edge controller running IEEE 2030.5 client firmware) and will call the DERMS endpoints on their update schedule.

---

### 3.3 Add Real AMI/Meter Data (IEC 61968-9 MDMS)

**Current:** Meter readings are generated by the simulator. The `MeterReading` SQLAlchemy model already exists.

**Production:** Your AMI head-end system (HES) or MDMS pushes 15-minute interval data. The endpoint already exists: `POST /api/2030.5/mup/{lfdi}/mr`.

**Step 1 — Create a dedicated meter ingestion endpoint** in `src/api/der_routes.py`:
```python
@router.post("/meters/bulk-reading")
async def ingest_bulk_readings(readings: list[dict], db: AsyncSession = Depends(get_db)):
    """
    Accept bulk 15-min interval data from AMI head-end / MDMS.
    Body: [{"meter_id": "MTR-001", "timestamp": "...", "kwh": 1.23, "kw": 4.92}, ...]
    """
    for r in readings:
        db.add(MeterReading(
            meter_id=r["meter_id"],
            timestamp=r["timestamp"],
            value_kwh=r["kwh"],
            demand_kw=r["kw"],
        ))
    await db.commit()
    return {"ingested": len(readings)}
```

**Step 2 — Use meter data in DR baseline calculation** in `src/api/dr_routes.py`:

Replace the simulated demand curve with a DB query:
```python
async def get_baseline_mw(feeder_id: str, hour: int, db: AsyncSession) -> float:
    """ASHRAE 10-in-10: average of 10 highest-consumption days in prior 10 days."""
    ten_days_ago = datetime.now() - timedelta(days=10)
    result = await db.execute(
        select(func.avg(MeterReading.demand_kw))
        .where(MeterReading.feeder_id == feeder_id)
        .where(MeterReading.timestamp >= ten_days_ago)
        .where(func.extract("hour", MeterReading.timestamp) == hour)
        .order_by(MeterReading.demand_kw.desc())
        .limit(10)
    )
    return (result.scalar() or 0) / 1000  # kW → MW
```

---

### 3.4 Add Real OpenADR 2.0b VTN

OpenADR requires a separate VTN (Virtual Top Node) service. L&T DES or PUVVNL would run an OpenADR VTN server, and the DERMS acts as the VEN (Virtual End Node) client.

**Recommended:** Use an open-source OpenADR VTN like `openleadr-python`:
```bash
pip install openleadr
```

Create `src/integrations/openadr/client.py`:
```python
from openleadr import OpenADRClient

async def start_openadr_client():
    client = OpenADRClient(
        ven_name="DERMS-Varanasi",
        vtn_url="https://vtn.puvvnl.gov.in/OpenADR2/Simple/2.0b",
        cert="/etc/ssl/derms/ven.crt",
        key="/etc/ssl/derms/ven.key",
        ca_file="/etc/ssl/derms/ca.crt",
    )

    async def on_event(event):
        """Called when VTN issues a demand response event."""
        signal = event["event_signals"][0]
        target_kw = float(signal["current_value"]["value"]) * 1000  # MW → kW
        duration_min = signal["intervals"][0]["duration"].total_seconds() // 60
        # Trigger the existing DR dispatch
        await dispatch_dr_event_internal(target_kw=target_kw, duration_min=int(duration_min))
        return "optIn"

    client.add_handler("on_event", on_event)
    await client.run()
```

Register in `src/derms/monitoring.py` — `start_background_tasks()`:
```python
if settings.openadr_enabled:
    from src.integrations.openadr.client import start_openadr_client
    tasks.append(asyncio.create_task(start_openadr_client()))
```

---

### 3.5 Real Market Settlement (UN/EDIFACT MSCONS)

**Current:** Settlement records are stored as JSON dicts in `_settlements` (in-memory).

**Production steps:**

**Step 1 — Persist settlements to PostgreSQL.** Add a `settlements` table to `src/models.py`:
```python
class Settlement(Base):
    __tablename__ = "settlements"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id        = Column(String, ForeignKey("dr_events.id"), nullable=False)
    aggregator_id   = Column(String, nullable=False)
    contracted_mw   = Column(Float)
    delivered_mw    = Column(Float)
    performance_pct = Column(Float)
    energy_mwh      = Column(Float)
    base_payment_usd= Column(Float)
    incentive_usd   = Column(Float)
    penalty_usd     = Column(Float)
    total_payout_usd= Column(Float)
    status          = Column(String, default="Pending")   # Pending | Approved | Paid | Disputed
    created_at      = Column(DateTime, default=datetime.utcnow)
    paid_at         = Column(DateTime, nullable=True)
```

**Step 2 — Generate MSCONS EDI file** after each event settles:
```python
def generate_mscons(settlement: Settlement, aggregator: dict) -> str:
    """
    UN/EDIFACT MSCONS D96A — Metered Services Consumption Report
    This is the standard electronic format for energy settlement.
    """
    lines = [
        "UNB+UNOA:3+LTDES:14+PUVVNL:14+260306:0800+1++MSCONS'",
        f"UNH+1+MSCONS:D:96A:UN'",
        f"BGM+7+{settlement.id}+9'",
        f"DTM+137:{datetime.utcnow().strftime('%Y%m%d%H%M')}:203'",
        f"NAD+MS+LTDES::92'",       # Message Sender: L&T DES
        f"NAD+MR+{aggregator['id']}::92'",  # Message Receiver: aggregator
        f"LOC+172+VARANASI-FDR01::ZZZ'",
        f"LIN+1'",
        f"QTY+220:{settlement.energy_mwh:.3f}:KWH'",  # 220 = active energy
        f"DTM+324:{settlement.event_id}:ZZZ'",
        f"MOA+128:{settlement.total_payout_usd:.2f}:USD'",
        f"UNT+12+1'",
        f"UNZ+1+1'",
    ]
    return "\n".join(lines)
```

**Step 3 — Deliver MSCONS to aggregators** via SFTP or HTTPS:
```python
import asyncssh

async def deliver_settlement(agg_id: str, mscons_content: str):
    agg_config = AGGREGATOR_SFTP_CONFIG[agg_id]  # host, user, key
    async with asyncssh.connect(agg_config["host"], username=agg_config["user"],
                                client_keys=[agg_config["key"]]) as conn:
        async with conn.start_sftp_client() as sftp:
            filename = f"MSCONS_{agg_id}_{datetime.now().strftime('%Y%m%d')}.edi"
            await sftp.put(io.BytesIO(mscons_content.encode()), f"/inbox/{filename}")
```

---

### 3.6 Live FX Rate & Weather Feed

**FX Rate** — replace static 83.5 in `_sim_config`:
```python
# src/api/market_routes.py — add a background refresh
async def refresh_fx_rate():
    while True:
        await asyncio.sleep(3600)  # refresh hourly
        r = await httpx.AsyncClient().get(
            "https://api.exchangerate-api.com/v4/latest/USD"
        )
        rate = r.json()["rates"]["INR"]
        _sim_config["market"]["exchange_rate_inr_per_usd"] = rate
```

**Weather / Solar Forecast** — replace `sin()` curve:
```python
async def get_solar_forecast(lat: float, lon: float) -> list[float]:
    r = await httpx.AsyncClient().get(
        "https://api.openweathermap.org/data/2.5/forecast",
        params={"lat": lat, "lon": lon, "appid": settings.openweather_api_key, "units": "metric"}
    )
    hourly = r.json()["list"]
    # extract GHI (Global Horizontal Irradiance) from cloud cover
    return [max(0, (1 - h["clouds"]["all"]/100) * 800) for h in hourly[:24]]  # W/m²
```

---

## Phase 4 — Observability (Week 5–6)

### 4.1 Structured Logging

Replace the current `logging.basicConfig` with structured JSON:
```bash
pip install python-json-logger
```

In `src/main.py`:
```python
import logging
from pythonjsonlogger import jsonlogger

handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter(
    "%(asctime)s %(name)s %(levelname)s %(message)s"
))
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
```

Forward logs to ELK Stack (Elasticsearch + Logstash + Kibana) or Grafana Loki.

---

### 4.2 Metrics (Prometheus + Grafana)

```bash
pip install prometheus-fastapi-instrumentator
```

In `src/main.py`:
```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

Add custom metrics for grid operations:
```python
from prometheus_client import Gauge, Counter

FLEET_ONLINE_DERS    = Gauge("derms_online_ders", "Number of online DERs")
FLEET_GENERATION_KW  = Gauge("derms_generation_kw", "Total fleet generation in kW")
DR_EVENTS_DISPATCHED = Counter("derms_dr_events_total", "Total DR events dispatched")
FEEDER_LOADING_PCT   = Gauge("derms_feeder_loading_pct", "Feeder loading %", ["feeder_id"])

# Update in monitoring.py after each cycle:
FLEET_ONLINE_DERS.set(summary["online_ders"])
FLEET_GENERATION_KW.set(summary["total_generation_kw"])
for f in summary["feeders"]:
    FEEDER_LOADING_PCT.labels(feeder_id=f["feeder_id"]).set(f["loading_pct"])
```

**Grafana dashboards to build:**
- Fleet overview: online DERs, total generation, feeder loading heatmap
- DR performance: events dispatched, curtailment delivered vs. contracted
- Market: MCP over time, aggregator earnings, settlement status
- System: API latency (p50/p95/p99), DB query time, WebSocket connections

---

### 4.3 Health Checks & Alerting

Update `/health` endpoint to do real dependency checks:
```python
@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    checks = {}

    # DB connectivity
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # ADMS reachability
    try:
        r = await httpx.AsyncClient(timeout=5).get(f"{settings.adms_base_url}/status")
        checks["adms"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
    except Exception:
        checks["adms"] = "unreachable"

    # Redis (if used)
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unreachable"

    ders = fleet_module.get_all_ders()
    checks["registered_ders"] = len(ders)

    overall = "healthy" if all(v == "ok" for v in checks.values() if isinstance(v, str)) else "degraded"
    return {"status": overall, **checks}
```

Configure your load balancer to poll `/health` every 30 seconds and remove unhealthy instances automatically.

**PagerDuty / alerting rules (Prometheus AlertManager):**
```yaml
groups:
- name: derms_alerts
  rules:
  - alert: FeederOverloaded
    expr: derms_feeder_loading_pct > 90
    for: 2m
    labels:
      severity: critical
    annotations:
      summary: "Feeder {{ $labels.feeder_id }} overloaded at {{ $value }}%"

  - alert: DERsOffline
    expr: derms_online_ders < 25
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Only {{ $value }} DERs online (expected 33)"

  - alert: APIHighLatency
    expr: http_request_duration_seconds{quantile="0.95"} > 2
    for: 5m
    labels:
      severity: warning
```

---

## Phase 5 — Regulatory Compliance (India / CERC)

### 5.1 CERC Requirements for DERMS

For grid-connected DR in India under Central Electricity Regulatory Commission rules:

| Requirement | What to implement |
|-------------|------------------|
| DER registration | Each DER must be registered with the respective SERC (UP SERC for Varanasi). Store SERC registration numbers in `DERDevice.serc_reg_no` |
| Metering standard | AMI meters must be IS 16444 certified. Verify with meter vendor |
| Settlement timeline | CERC mandates T+2 working days for energy settlement. Matches current design |
| Scheduling | Day-ahead scheduling with SLDC by 10 AM for next-day dispatch |
| UI timezones | All timestamps in IST (UTC+5:30). The DERMS already uses IST for solar simulation — apply consistently to all event logs |
| Data retention | Meter data: 5 years. Event logs: 7 years. Add a data archival job |
| Audit trail | All dispatch commands must be logged with operator ID, timestamp, reason |

### 5.2 Add Operator Audit Trail

Every DR dispatch must record who issued it. Add to the `DREvent` model:
```python
class DREvent(Base):
    __tablename__ = "dr_events"
    # ... existing fields ...
    operator_id    = Column(String)       # JWT sub claim of the operator
    operator_name  = Column(String)
    approval_id    = Column(String)       # 4-eyes approval workflow (future)
    regulatory_ref = Column(String)       # CERC order / SLDC reference number
```

Update the dispatch endpoint:
```python
@router.post("/dispatch/dr-event", dependencies=[Depends(verify_token)])
async def dispatch_dr_event(body: DREventRequest, token=Depends(verify_token),
                             db: AsyncSession = Depends(get_db)):
    event = DREvent(
        ...
        operator_id=token["sub"],
        operator_name=token.get("name", "Unknown"),
    )
```

### 5.3 Data Residency

All data (meter readings, settlement records, DR event logs) must remain on India-region servers. Confirm:
- PostgreSQL in `ap-south-1` (Mumbai) or on-premise
- No logging to non-India endpoints (e.g., don't use a US-region Datadog account without DPA)
- Backup storage in same region (`ap-south-1` S3 bucket with server-side encryption)

---

## Phase 6 — CI/CD Pipeline (Week 6–7)

### 6.1 GitHub Actions Workflow

Create `.github/workflows/deploy.yml`:
```yaml
name: Deploy DERMS

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt pytest pytest-asyncio httpx
      - run: pytest tests/ -v

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to production
        run: |
          # SSH to app server and pull + restart
          ssh deploy@derms.ltdes.in "
            cd /opt/derms &&
            git pull origin main &&
            pip install -r requirements.txt &&
            systemctl restart derms
          "
```

### 6.2 Write Tests First

Create `tests/` directory with at minimum:

```python
# tests/test_fleet.py
import pytest
from src.derms.fleet import get_fleet_summary

def test_fleet_summary_structure():
    summary = get_fleet_summary()
    assert "total_ders" in summary
    assert "feeders" in summary
    assert isinstance(summary["feeders"], list)

# tests/test_market_routes.py
import pytest
from httpx import AsyncClient
from src.main import app

@pytest.mark.asyncio
async def test_market_status():
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get("/api/market/status")
    assert r.status_code == 200
    data = r.json()
    assert data["active_aggregators"] == 6
    assert data["total_portfolio_mw"] == 218.0

@pytest.mark.asyncio
async def test_merit_order_sorting():
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get("/api/market/merit-order/PROC-2026-031")
    assert r.status_code == 200
    bids = r.json()["bids"]
    prices = [b["price_usd_per_mwh"] for b in bids]
    assert prices == sorted(prices)   # must be ascending
```

---

## Phase 7 — Production Deployment Checklist

Run through this before go-live:

### Security
- [ ] All secrets in secrets manager (no `.env` file in repo)
- [ ] `debug=False` in production `Settings`
- [ ] TLS 1.2+ enforced, TLS 1.0/1.1 disabled
- [ ] mTLS certificates issued to all aggregator gateways
- [ ] JWT RS256 signing key generated and stored in HSM or Vault
- [ ] `/docs` (Swagger UI) disabled in production: `app = FastAPI(docs_url=None, redoc_url=None)`
- [ ] CORS `allow_origins` restricted from `["*"]` to specific domains
- [ ] Rate limiting on dispatch endpoints (max 10 DR events/hour)
- [ ] WAF (Web Application Firewall) in front of public endpoints

### Data
- [ ] PostgreSQL with daily automated backups (point-in-time recovery)
- [ ] Alembic migrations tested end-to-end
- [ ] GDPR/PDPB consumer data fields identified and masked in logs

### Operations
- [ ] `/health` endpoint returns correct status for all dependencies
- [ ] Prometheus metrics scraped by Grafana
- [ ] Alert rules configured for feeder overload, DER offline, API latency
- [ ] On-call rotation configured in PagerDuty
- [ ] Runbook written for common incidents (DER offline, ADMS disconnect, DR dispatch failure)
- [ ] DR event log retained for 7 years (CERC requirement)

### Testing
- [ ] Load test: 100 concurrent WebSocket connections, fleet summary broadcast every 5s
- [ ] Failover test: kill one app VM, confirm traffic shifts to the other
- [ ] ADMS disconnect test: confirm graceful degradation (last known state held, alerts raised)
- [ ] Full DR dispatch end-to-end test: event → merit order → IEEE 2030.5 DERControls → settlement

### Go-Live
- [ ] Parallel-run period: run simulator and real ADMS simultaneously, compare outputs
- [ ] Soft launch: monitor-only mode (no actual DERControls issued) for 2 weeks
- [ ] First live DR event: with PUVVNL operator and L&T team on-site

---

## Cost Estimate (AWS ap-south-1, Monthly)

| Resource | Spec | ~Cost/month |
|----------|------|------------|
| EC2 (2× app servers) | t3.medium, 2 vCPU, 4 GB | ~$60 |
| RDS PostgreSQL | db.t3.medium, 100 GB, Multi-AZ | ~$120 |
| ElastiCache Redis | cache.t3.micro | ~$25 |
| ALB (Application Load Balancer) | + data transfer | ~$30 |
| S3 (backups, logs) | 100 GB | ~$3 |
| CloudWatch / monitoring | basic | ~$15 |
| SSL Certificate (ACM) | free | $0 |
| **Total** | | **~$253/month** |

For on-premise (PUVVNL data center): hardware capex ~₹15–20 lakhs, then operational cost only.

---

## Quick Reference — Environment Variables

Full `.env` for production (store values in secrets manager, not this file):

```ini
# Application
DEBUG=false
PORT=8080
HOST=0.0.0.0

# Database
DATABASE_URL=postgresql+asyncpg://derms:STRONGPASS@db.internal:5432/derms
DB_POOL_SIZE=20

# Redis
REDIS_URL=redis://redis.internal:6379/0

# Auth
JWT_SECRET_KEY=/etc/ssl/derms/jwt-private.pem
JWT_ALGORITHM=RS256
JWT_EXPIRE_MINUTES=480

# ADMS (real)
ADMS_BASE_URL=https://adms.puvvnl.gov.in/api/v2
ADMS_API_KEY=<from secrets manager>
ADMS_VERIFY_SSL=true
ADMS_SIMULATOR_ENABLED=false

# IEEE 2030.5
IEEE_SIMULATOR_ENABLED=false
DER_PROGRAM_ID=DERP-VARANASI-001

# OpenADR
OPENADR_ENABLED=true
OPENADR_VTN_URL=https://vtn.puvvnl.gov.in/OpenADR2/Simple/2.0b

# Market
OPENWEATHER_API_KEY=<from secrets manager>

# Monitoring
SENTRY_DSN=<from secrets manager>
```
