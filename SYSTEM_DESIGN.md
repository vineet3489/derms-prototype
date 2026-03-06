# DERMS Prototype — System Design Document

**Project:** Distributed Energy Resource Management System (DERMS) Prototype
**Operator:** PUVVNL — Varanasi Pilot (DERMS) + L&T DES — Global (Market Portal)
**Repository:** https://github.com/vneet3489/derms-prototype
**Live Demo:** https://derms-prototype.onrender.com
**Portals:** `/ui` (DERMS Dashboard) · `/market` (L&T Neural Grid Platform)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Module Reference — Backend](#4-module-reference--backend)
5. [Module Reference — Frontend](#5-module-reference--frontend)
6. [Integration Protocols](#6-integration-protocols)
7. [DERMS Dashboard — Detailed Design](#7-derms-dashboard--detailed-design)
8. [Demand Response Module](#8-demand-response-module)
9. [L&T Neural Grid Platform — Market Portal](#9-lt-neural-grid-platform--market-portal)
10. [Data Models](#10-data-models)
11. [API Reference](#11-api-reference)
12. [Simulation Methodology](#12-simulation-methodology)
13. [Deployment](#13-deployment)
14. [Known Limitations & Plug-in Points](#14-known-limitations--plug-in-points)

---

## 1. Overview

This prototype demonstrates a production-grade DERMS architecture for a two-tier deployment:

**Tier 1 — DERMS Operator (PUVVNL Varanasi Pilot)**
Manages a fleet of 33 distributed energy resources across 3 feeders and 9 distribution transformers in Varanasi. Integrates with a GE ADMS simulator (CIM XML / IEC 61970-452) for topology data and three IEEE 2030.5 DER aggregators for device-level control.

**Tier 2 — Super-Aggregator VPP (L&T Neural Grid Platform)**
L&T acts as a global Virtual Power Plant operator, aggregating portfolios from 6 sub-aggregators across Germany, USA, India, Australia, Japan, and the UK (218 MW combined). Runs a flexibility energy market with merit-order clearing, bid management, and T+2 UN/EDIFACT settlement.

Both tiers run as a single FastAPI application with two separate React frontends served as static HTML.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Application                               │
│  /ui  ─→  static/index.html  (DERMS Dashboard React SPA)               │
│  /market ─→ static/market.html (L&T Market Portal React SPA)           │
│                                                                          │
│  ┌──────────────────────────────────┐  ┌───────────────────────────┐   │
│  │  DERMS API  /api/*               │  │  Market API  /api/market/ │   │
│  │  ├── /api/ders/*                 │  │  ├── /status              │   │
│  │  ├── /api/dashboard/*            │  │  ├── /events              │   │
│  │  ├── /api/dr/*                   │  │  ├── /bids                │   │
│  │  └── /api/adms/*                 │  │  ├── /merit-order/{id}    │   │
│  │                                  │  │  ├── /settlements          │   │
│  │  IEEE 2030.5  /api/2030.5/*      │  │  ├── /aggregators         │   │
│  │  ├── /dcap  /edev  /derp         │  │  ├── /forecast            │   │
│  │  └── /mup  /derc                 │  │  └── /config              │   │
│  │                                  │  └───────────────────────────┘   │
│  │  ADMS Simulator  /sim/adms/*     │                                   │
│  │  ├── /status  /topology/cim      │  WebSocket  /ws                  │
│  │  └── /realtime/state             │  (fleet summary broadcast, 5s)   │
│  └──────────────────────────────────┘                                   │
│                                                                          │
│  Background Tasks:                                                       │
│  ├── IEEE 2030.5 simulator  (aggregator registration loop, 30s)        │
│  ├── ADMS state sync        (SCADA polling loop, 30s)                  │
│  ├── Alert monitor          (threshold check loop, 60s)                │
│  └── WebSocket broadcast    (fleet summary push, 5s)                   │
│                                                                          │
│  Database: SQLite (aiosqlite) — DERs, feeders, aggregators, events     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow: DER Registration

```
IEEE 2030.5 Aggregator Simulator
    │
    ├── POST /api/2030.5/edev          (register EndDevice)
    ├── PUT  /api/2030.5/edev/{id}/der (push DER capability)
    └── POST /api/2030.5/mup/{id}/mr  (push meter reading)
            │
            ▼
    on_aggregator_connected()  [fleet.py]
            │
            ├── creates DER record in _der_cache (in-memory)
            ├── writes to SQLite via SQLAlchemy async
            └── assigns feeder + DT from ADMS topology

    ADMS Simulator
    │
    ├── GET /sim/adms/realtime/state    (SCADA state: voltage, loading)
    └── POST /sim/adms/der-status       (push DER operational state)
            │
            ▼
    monitoring.py background task      (polls every 30s)
            │
            └── updates _der_cache with live power, soc, voltage
```

### Data Flow: DR Dispatch

```
User (Utility Operator)
    │
    └── POST /api/ders/dispatch/dr-event  {target_kw, duration_min, reason}
            │
            ▼
    dispatch.py — select_ders_for_dr()
            │
            ├── ranks DERs by availability (BESS first, then Solar)
            ├── computes curtailment_kw per DER
            └── for each selected DER:
                    └── POST /api/2030.5/derp/{prog_id}/derc
                            (IEEE 2030.5 DERControl: opModEnergize, setMaxW)
```

---

## 3. Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Web framework | FastAPI 0.110 | Async, auto OpenAPI docs at `/docs` |
| Database ORM | SQLAlchemy 2.x (async) | `aiosqlite` driver, SQLite file `derms.db` |
| Background tasks | asyncio native tasks | No Celery — keeps deployment simple |
| WebSocket | FastAPI WebSocket + ConnectionManager | 5-second broadcast loop |
| Frontend | React 18 + Babel (inline CDN) | No build step — pure static HTML |
| Charts | Custom SVG (polyline, polygon, animateMotion) | No chart library dependency |
| Hosting | Render.com (free tier) | Auto-deploy from GitHub `main` branch |
| Protocol: DR signals | IEEE 2030.5 / SEP 2.0 (simulated) | mTLS + X.509 in production |
| Protocol: Grid topology | CIM XML IEC 61970-452 | Parsed by `cim_parser.py` |
| Protocol: Market bids | OASIS Energy Market Language (REST/JSON) | Simulated |
| Protocol: Settlement | UN/EDIFACT MSCONS | Format described, not encoded |
| Protocol: DR events | OpenADR 2.0b | Simulated via HTTP callbacks |

---

## 4. Module Reference — Backend

### `src/main.py`
Application entry point and lifespan manager.

**Startup sequence:**
1. `await init_db()` — creates SQLite tables
2. `set_fleet_store(fleet_module)` — injects fleet cache into IEEE 2030.5 server
3. `await initialize_fleet()` — fetches ADMS topology, creates feeder/DT/aggregator records
4. `await start_background_tasks()` — launches monitoring loops
5. Mounts `/static`, registers all routers, starts WebSocket broadcast loop

**Key routes registered:**
- `der_router` — `/api/ders/*`
- `adms_router` — `/api/adms/*`
- `dashboard_router` — `/api/dashboard/*`
- `dr_router` — `/api/dr/*`
- `market_router` — `/api/market/*`
- `adms_sim_router` — `/sim/adms/*`
- `ieee_router` — `/api/2030.5/*`
- GET `/ui` → serves `static/index.html`
- GET `/market` → serves `static/market.html`
- WebSocket `/ws` → live fleet summary

---

### `src/database.py`
SQLAlchemy async engine and session factory.

```python
DATABASE_URL = "sqlite+aiosqlite:///./derms.db"
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine)
```

`init_db()` runs `Base.metadata.create_all()` on startup.

---

### `src/models.py`
SQLAlchemy ORM models. Key tables:

| Model | Table | Purpose |
|-------|-------|---------|
| `DERDevice` | `der_devices` | Physical DER records: type, capacity, feeder, DT, aggregator |
| `Feeder` | `feeders` | Feeder metadata: name, capacity, ADMS ID |
| `DistributionTransformer` | `distribution_transformers` | DT metadata: kVA rating, GPS coords |
| `Aggregator` | `aggregators` | IEEE 2030.5 aggregator registration |
| `DREvent` | `dr_events` | Demand response event log |
| `DERControl` | `der_controls` | Per-DER control commands issued |
| `MeterReading` | `meter_readings` | 15-min meter data from IEEE 2030.5 MUP |

---

### `src/derms/fleet.py`
In-memory fleet state cache (source of truth for live data).

**Global stores:**
```python
_der_cache:        dict[str, dict]   # der_id → live DER state
_feeder_cache:     dict[str, dict]   # feeder_id → feeder state
_dt_cache:         dict[str, dict]   # dt_id → DT state
_aggregator_cache: dict[str, dict]   # agg_id → aggregator state
```

**Key functions:**

`get_fleet_summary()` — builds the dashboard summary on-the-fly from `_der_cache`:
- Groups DERs by feeder → computes `used_capacity_kw` per feeder
- Groups DERs by DT → computes `used_capacity_kw` per DT
- Counts online / offline / degraded / curtailed
- Returns `system_cuf_pct` = `(total_generation_kw / total_capacity_kw) * 100`

`initialize_fleet()` — called at startup:
1. Fetches `GET /sim/adms/realtime/state` (feeder + DT SCADA state)
2. Fetches `GET /sim/adms/topology/cim` (CIM XML)
3. Parses CIM to extract feeder names and DT locations
4. Seeds `_feeder_cache` and `_dt_cache`

`create_der_from_aggregator(payload)` — called by IEEE 2030.5 server on DER registration:
- Assigns DER to feeder + DT based on `aggregator_id → feeder_id` mapping
- Infers DER type from `portfolio` field (Solar PV / BESS / EV Charger)
- Stores in `_der_cache` and writes to SQLite

---

### `src/derms/monitoring.py`
Background monitoring loops launched at startup.

**`monitor_der_telemetry()`** — runs every 30s:
- Calls `GET /sim/adms/realtime/state` → updates voltage, loading per feeder
- Simulates per-DER power fluctuation with `random.uniform(0.85, 1.05)` multiplier
- Checks alert thresholds:
  - Loading > 85% → "High Loading" alert
  - Voltage < 216V or > 245V → "Voltage Violation" alert
  - SOC < 15% → "BESS Low SOC" alert

**`monitor_aggregator_heartbeats()`** — runs every 60s:
- Marks aggregators offline if no heartbeat within 90s

---

### `src/derms/dispatch.py`
DR event dispatch logic.

**`select_ders_for_dr(target_kw, feeder_id=None)`**:
1. Filters available DERs (online, not curtailed)
2. Sorts: BESS first (highest dispatchability), then Solar
3. Greedily selects until `target_kw` met
4. Returns list of `(der_id, curtailment_kw)` pairs

**`issue_der_control(der_id, setpoint_kw)`**:
- Calls IEEE 2030.5 server: `POST /api/2030.5/derp/{prog_id}/derc`
- Payload: `DERControl` with `opModEnergize=True`, `setMaxW=ActivePower(kw=setpoint_kw)`
- Logs to `der_controls` table

---

### `src/integrations/adms/simulator.py`
GE ADMS simulator — exposes a realistic CIM/SCADA API.

**Endpoints served at `/sim/adms/`:**

| Endpoint | Data returned |
|----------|---------------|
| `GET /status` | ADMS version, connection state, sync timestamp |
| `GET /topology/cim` | CIM XML (IEC 61970-452) with 3 feeders, 9 DTs, 33 ACLineSegments |
| `GET /realtime/state` | Per-feeder: voltage, loading%, available_mw, SCADA state |
| `POST /der-status` | Accepts DER telemetry updates from IEEE 2030.5 aggregators |

**Simulation:**
- 3 feeders: `FDR-01` (Sigra), `FDR-02` (Lanka), `FDR-03` (Assi)
- Each feeder has 3 DTs (e.g., `DT-VAR-0234`, `DT-VAR-0235`, `DT-VAR-0236`)
- Feeder loading follows a realistic daily curve:
  ```python
  morning = 0.15 * exp(-0.5 * ((hour - 9)²) / 4)
  evening = 0.30 * exp(-0.5 * ((hour - 20)²) / 2)
  load_factor = 0.55 + morning + evening + noise
  ```
- Solar output:
  ```python
  hour_ist = (hour_utc + 5.5) % 24     # IST correction
  irradiance = max(0, sin(π*(hour_ist-6)/12))  # 6am–6pm
  solar_kw = nameplate_kw * 0.8 * irradiance
  ```

---

### `src/integrations/adms/cim_parser.py`
Parses IEC 61970-452 CIM XML to extract:
- `Feeder` objects → `mRID`, `name`
- `ACLineSegment` → length, resistance, reactance
- `ConnectivityNode` → bus connections
- `EnergyConsumer` → load points

Used in `initialize_fleet()` to populate feeder metadata from ADMS topology.

---

### `src/integrations/ieee2030_5/server.py`
Full IEEE 2030.5 / SEP 2.0 REST server.

**Resource endpoints:**

| Path | Method | Description |
|------|--------|-------------|
| `/api/2030.5/dcap` | GET | Device Capability (entry point) |
| `/api/2030.5/edev` | GET, POST | EndDevice list / registration |
| `/api/2030.5/edev/{lfdi}` | GET | EndDevice detail |
| `/api/2030.5/edev/{lfdi}/der` | GET | DER list for device |
| `/api/2030.5/edev/{lfdi}/der/{id}/ders` | PUT | DER Settings (capacity, mode) |
| `/api/2030.5/edev/{lfdi}/der/{id}/dera` | PUT | DER Availability (available power) |
| `/api/2030.5/derp` | GET | DER Program list |
| `/api/2030.5/derp/{id}/derc` | GET, POST | DER Controls (dispatch commands) |
| `/api/2030.5/mup/{lfdi}` | GET | MirrorUsagePoint (meter endpoint) |
| `/api/2030.5/mup/{lfdi}/mr` | POST | Push meter reading |

**DER registration callback:**
When a device registers via `POST /api/2030.5/edev`, the server calls `fleet.create_der_from_aggregator()` to add the DER to the live fleet cache and SQLite.

---

### `src/integrations/ieee2030_5/resources.py`
Pydantic v2 models for all IEEE 2030.5 XML/JSON resources.

**Key models:**
- `DeviceCapability` — DCAP entry point with links to all resource lists
- `EndDevice` — physical device with LFDI (long-form device identifier)
- `DERSettings` — nameplate: `modesSupported`, `rtgMaxW` (rated max watts)
- `DERAvailability` — real-time: `statWAvail` (available watts), `availabilityDuration`
- `DERControl` — dispatch command: `opModEnergize`, `setMaxW`, `duration`
- `MirrorMeterReading` — meter: `value`, `uom` (unit of measure), `multiplier`
- `ActivePower` — encoded as `{multiplier: 0, value: watts_int}` (NOT multiplier=3)

**ActivePower encoding (critical):**
```python
# CORRECT — multiplier=0 means value is in watts directly
ActivePower(multiplier=0, value=int(kw * 1000))

# WRONG — multiplier=3 would be kilo × kilo = megawatts
ActivePower(multiplier=3, value=int(kw))
```

---

### `src/integrations/ieee2030_5/simulator.py`
Simulates three DER aggregators that register with the IEEE 2030.5 server.

**Aggregators:**
| ID | Name | Portfolio | DERs | Feeder |
|----|------|-----------|------|--------|
| AGG-001 | Tata Power Solar DES | 11 Solar PV + 1 BESS | 12 | FDR-01 |
| AGG-002 | Adani Green Varanasi | 10 Solar PV + 1 BESS | 11 | FDR-02 |
| AGG-003 | ReNew Power UP Grid | 8 Solar PV + 2 BESS | 10 + 2 EV | FDR-03 |

**Registration loop (every 30s):**
1. `POST /api/2030.5/edev` — register EndDevice
2. `PUT /api/2030.5/edev/{lfdi}/der/{id}/ders` — push DER settings (nameplate)
3. `PUT /api/2030.5/edev/{lfdi}/der/{id}/dera` — push DER availability
4. `POST /api/2030.5/mup/{lfdi}/mr` — push meter reading with current power

**Power simulation:**
```python
# Solar (during daylight hours)
hour_ist = (hour_utc + 5.5) % 24
irradiance = max(0, sin(π*(hour_ist-6)/12)) if 6 <= hour_ist <= 18 else 0
current_kw = nameplate_kw * 0.8 * irradiance * random.uniform(0.9, 1.1)

# BESS
soc_frac = random.uniform(0.3, 0.95)
current_kw = -(nameplate_kw * 0.5) if hour_ist < 12 else nameplate_kw * 0.7  # charge/discharge
available_kw = nameplate_kw * soc_frac * 0.9  # what can be dispatched
```

---

### `src/api/dr_routes.py`
Demand Response module API.

**In-memory consumer registry (`_CONSUMERS`):**
8 PuVVNL billing consumers with full data:
- Consumer number, name, address, contact
- Tariff category (LT Commercial / LT Industrial / HT Industrial)
- Contractual demand (kVA), max demand (kW), average demand (kW)
- Monthly consumption (12 months, kWh)
- Enrolled status, enrollment date, feeder/DT assignment
- Events participated, total savings (INR)

**Endpoints:**

| Endpoint | Logic |
|----------|-------|
| `GET /api/dr/demand-analysis` | Builds 24h hourly demand vs SLDC schedule. Compares feeder load to SLDC allocation. Shortfall > 5% triggers DR recommendation |
| `GET /api/dr/consumers` | Returns all consumers with enrollment stats |
| `GET /api/dr/consumers/lookup?consumer_no=` | Simulates billing system lookup (deterministic RNG from hash of consumer_no) |
| `POST /api/dr/consumers/{no}/enroll` | Sets `enrolled=True`, records enrollment date |
| `POST /api/dr/consumers/{no}/unenroll` | Sets `enrolled=False` |
| `GET /api/dr/cost-benefit` | Computes per-event and per-consumer savings at ₹8.50/kWh tariff, ₹3.00/kWh incentive |

**Demand simulation:**
```python
# Varanasi residential/commercial demand curve
morning_peak = 0.20 * exp(-0.5 * ((hour - 9)²) / 2)
evening_peak = 0.35 * exp(-0.5 * ((hour - 20)²) / 2)
demand_mw = base_mw * (0.55 + morning_peak + evening_peak + noise)

# SLDC allocation (slightly lower than demand during peak)
sldc_avail = demand_mw * uniform(0.90, 1.05)
shortfall_kw = max(0, demand_mw - sldc_avail) * 1000
trigger_dr = shortfall_kw > (demand_mw * 1000 * 0.05)  # > 5% shortfall
```

---

### `src/api/market_routes.py`
L&T Neural Grid Platform — global flexibility market.

**In-memory state:**
- `_sim_config` — all editable parameters (market rates, grid config, baseline method, settlement rules, aggregator registry, integration endpoints)
- `_events` — procurement events list
- `_bids` — bid list (across all events)
- `_settlements` — settlement records

**Merit order clearing:**
```python
bids_sorted = sorted(eligible_bids, key=lambda b: b["price_usd_per_mwh"])
cumulative = 0.0
for bid in bids_sorted:
    if cumulative >= target_mw:
        bid["status"] = "Rejected"
    else:
        bid["status"] = "Selected"
        cumulative += bid["quantity_mw"]
        mcp = bid["price_usd_per_mwh"]   # MCP = last accepted bid price
event["status"] = "Cleared"
event["mcp_usd_per_mwh"] = mcp
```

**Settlement formula (applied in `_settlements` seed data):**
```
Base Payment  = Delivered MWh × $85/MWh
Incentive     = Delivered MWh × $35/MWh     (if Performance ≥ 90%)
Penalty       = Underdelivered MWh × $20/MWh (if Performance < 90%)
Net Payout    = Base Payment + Incentive − Penalty
```

---

## 5. Module Reference — Frontend

### `static/index.html` — DERMS Dashboard (2,264 lines)
React 18 SPA, no build step. All JSX transpiled by Babel CDN at runtime.

**State architecture (App component):**
```javascript
const [summary, setSummary]       // /api/dashboard/summary — full fleet state
const [ders, setDers]             // /api/ders — DER list with live power
const [genProfile, setGenProfile] // /api/dashboard/generation-profile — 24h hourly
const [connected, setConnected]   // WebSocket connection state
```

**WebSocket client:**
```javascript
const ws = new WebSocket(wsUrl);
ws.onmessage = ({data}) => {
    const msg = JSON.parse(data);
    if (msg.type === "fleet_summary") setSummary(msg.data);
};
// Ping every 25s to keep alive
setInterval(() => ws.send("ping"), 25000);
```

**Navigation screens:**
| Screen | Component | Key Data |
|--------|-----------|----------|
| Dashboard | `DashboardScreen` | KPIs, feeder loading bars, DT voltage grid, solar generation sparkline |
| DER Fleet | `DERFleetScreen` | Table of all 33 DERs with live power, SOC, status |
| Planning | `PlanningScreen` | Hosting capacity per DT, DER placement recommendations |
| Scheduling | `SchedulingScreen` | SCADA schedule table with 15-min slots |
| Demand Response | `DemandResponseScreen` | 6-tab DR module (see §8) |
| Control | `ControlScreen` | Manual dispatch form, active DERControl list |
| Analytics | `AnalyticsScreen` | CUF & PR bar chart, generation vs capacity |
| ADMS / CIM | `ADMSScreen` | Raw CIM XML viewer, ADMS connection state |

**Chart implementation:**
All charts are hand-coded SVG — no Recharts or Chart.js dependency.

```jsx
// Example: Generation profile sparkline
<svg viewBox="0 0 100 60">
  <polyline
    points={hourly.map((h,i) => `${i/23*100},${60 - h.generation_kw/maxGen*55}`).join(" ")}
    fill="none" stroke="#FCD34D" strokeWidth="1.5"
  />
</svg>
```

---

### `static/market.html` — L&T Neural Grid Platform (1,262 lines)
Separate React 18 SPA at `/market`. Same pattern (Babel CDN, no build).

**L&T Brand:**
- Primary red: `#C8202F`
- Navy: `#1E3A5F`
- Dark background: `#070C18`
- SVG text logo (L&T in red rounded rect)

**Navigation screens:**
| Screen | Key Features |
|--------|-------------|
| Dashboard | Global KPIs, 24h forecast SVG chart, aggregator portfolio bars, performance table |
| Utility Portal | Forecast chart with shortfall shading (amber columns), publish event form |
| Aggregator Bids | Horizontal merit order stack chart, bid submission, market clear button |
| Energy Flow | Animated SVG: DERs → VPP → Grid → Consumers. `<animateMotion>` particles for energy (green), money (amber dashed), control signals (cyan dashed) |
| Settlement | Statement list + monospace formula breakdown panel |
| Sim Config | Editable `<input>` fields for all sim parameters, Save → `PUT /api/market/config` |
| Glossary | 10 term entries with formula blocks |
| Integration Guide | Protocol cards + regulatory framework table |

**Energy flow animation:**
```jsx
<circle r="4" fill="#22c55e" opacity="0.9">
  <animateMotion dur="2s" repeatCount="indefinite"
    path={`M 108,${y} L 310,210`}/>   // DER node → VPP hub
</circle>
```

---

## 6. Integration Protocols

### IEEE 2030.5 / SEP 2.0
Standard for DER communication. Used for:
- **EndDevice registration** — each aggregator registers as an EndDevice
- **DER capability reporting** — nameplate kW, supported operating modes
- **DER availability reporting** — real-time available kW, SOC
- **Meter readings** — 15-min interval energy data
- **DER Controls** — dispatch commands: `opModEnergize`, `setMaxW`, `setMinW`

**Authentication (production):** mTLS with X.509 certificates (simulated here)

### CIM XML / IEC 61970-452
Common Information Model for grid topology. Used for:
- Exporting feeder topology from ADMS: feeders, DTs, conductors, buses
- Parsed by `cim_parser.py` to seed feeder/DT records at startup

**Served at:** `GET /sim/adms/topology/cim`

### OpenADR 2.0b
Open Automated Demand Response protocol. Used for:
- Utility publishes EiEvent (procurement event) → aggregators receive via pull
- In this prototype: simulated as HTTP callback after `POST /api/market/events`

**Production endpoint:** `POST /oadr/EiEvent` (XML payload)

### OASIS Energy Market Language (EML)
REST/JSON bidding protocol for flexibility markets.
- `POST /api/market/bids` — aggregator submits bid
- `GET /api/market/merit-order/{event_id}` — merit order calculation
- `POST /api/market/events/{id}/clear` — utility clears market

### UN/EDIFACT MSCONS
Electronic data interchange format for energy quantity settlement statements.
- Used for T+2 settlement reports after each DR event
- In prototype: settlement data in JSON at `GET /api/market/settlements`
- Production: generates MSCONS EDI files for transmission to aggregators

### IEC 61968-9 (MDMS)
Meter Data Management System interface for AMI data.
- 15-minute interval meter readings from smart meters
- Used for baseline calculation (ASHRAE 10-in-10) and settlement verification
- Endpoint: `GET /mdms/readings/{meter_id}` (documented, not yet integrated)

---

## 7. DERMS Dashboard — Detailed Design

### Dashboard Screen KPIs
Computed in `get_fleet_summary()` from `_der_cache`:

| KPI | Formula |
|-----|---------|
| Total DERs | `len(_der_cache)` |
| Online | `count(status == "online")` |
| Total Generation | `sum(current_kw for all online DERs)` |
| System CUF | `total_generation_kw / total_capacity_kw × 100` |
| Active Alerts | `count(alerts generated in last monitor cycle)` |

### Feeder Health Cards
Per feeder:
- **Loading %** = `used_capacity_kw / nameplate_capacity_kw × 100`
- **Used capacity** = `sum(current_kw for DERs on this feeder)`
- Color coding: <70% green, 70–85% amber, >85% red

### DT Voltage Grid
9 cards (one per DT):
- `voltage_l1/l2/l3` from ADMS SCADA
- Status: Normal (216–245V), High (>245V), Low (<216V)
- `current_loading_pct` from ADMS SCADA

### Generation Profile Chart
`GET /api/dashboard/generation-profile`:
- 24 hourly points for each DER type (Solar, BESS, EV)
- Stacked area chart in SVG (filled polylines)
- Regenerated each request with IST-aware solar simulation

---

## 8. Demand Response Module

The DR module is a 6-tab panel in the DERMS dashboard.

### Tab: Demand Analysis
- Fetches `GET /api/dr/demand-analysis`
- Displays 24h demand vs SLDC allocation bar/line chart
- Highlights shortfall hours in amber
- Shows DR recommendation banner when `dr_recommendation.recommended === true`

### Tab: Dispatch
- Standard DR event dispatch form
- `POST /api/ders/dispatch/dr-event` → `dispatch.py` selects DERs → IEEE 2030.5 DERControls

### Tab: Consumer Enrollment
The `ConsumerEnrollmentPanel` component implements a 4-step workflow:

```
Step 1: Enter consumer number → GET /api/dr/consumers/lookup
Step 2: Review billing data (tariff, demand, monthly consumption heatmap)
Step 3: Check consent checkbox
Step 4: POST /api/dr/consumers/{no}/enroll
```

**Prospects panel:** unenrolled consumers displayed with "Review & Enroll" quick-fill button.
**Enrolled table:** per-consumer: enrollment date, events participated, total savings, Unenroll button.

### Tab: Cost-Benefit Analysis
`GET /api/dr/cost-benefit` returns:
- `summary`: total events, energy saved (kWh), cost avoided (₹), incentives paid (₹), net benefit (₹), BCR
- `events[]`: per-event breakdown
- `consumer_savings[]`: per-consumer breakdown, sorted by total savings

**Rates used:**
```
Peak tariff:  ₹8.50 / kWh  (PuVVNL LT Commercial peak rate)
DR incentive: ₹3.00 / kWh  (incentive paid to enrolled consumers)
Net benefit:  cost_avoided − incentives_paid
```

---

## 9. L&T Neural Grid Platform — Market Portal

### Architecture

```
Utility / TSO
    │
    └── POST /api/market/events  (publish procurement requirement)
            │
            ▼
    L&T Neural Grid Platform (VPP Operator)
            │
            ├── GET /api/market/bids?event_id=  (collect aggregator bids)
            ├── GET /api/market/merit-order/{id} (sort by price, compute MCP)
            └── POST /api/market/events/{id}/clear  (issue awards)
                    │
                    ▼
        Selected aggregators receive IEEE 2030.5 DERControls
                    │
                    ▼
        POST-event metering → /api/market/settlements  (T+2)
```

### Aggregator Portfolio

| Aggregator | Country | Capacity | Portfolio | Min Price |
|-----------|---------|----------|-----------|-----------|
| AGG-EU-01 SolarMax Europe | Germany | 45 MW | Solar PV + BESS | $75/MWh |
| AGG-US-01 FlexGrid Americas | USA | 80 MW | Industrial DR + BESS | $90/MWh |
| AGG-IN-01 L&T DES — Varanasi | India | 33 MW | Solar + BESS + EV | $55/MWh |
| AGG-AU-01 GridFlex Pacific | Australia | 25 MW | Solar + EV Fleet | $80/MWh |
| AGG-JP-01 Denki Flex Japan | Japan | 15 MW | BESS + Industrial DR | $95/MWh |
| AGG-GB-01 BritFlex UK | UK | 20 MW | Wind + BESS | $85/MWh |
| **Total** | 6 countries | **218 MW** | | |

### Market Clearing — Uniform MCP Pricing

1. Sort all bids by `price_usd_per_mwh` ascending (cheapest first)
2. Allocate MW cumulatively until `procurement_mw` is met
3. MCP = price of the last accepted bid
4. All selected bidders receive MCP (not their individual bid price)
5. Rejected bidders (above MCP or excess MW) receive nothing

### Settlement Calculation

```
Performance %  = Delivered MW ÷ Contracted MW × 100

If Performance ≥ 90%:
    Net Payout = (Delivered MWh × $85) + (Delivered MWh × $35)

If Performance < 90%:
    Net Payout = (Delivered MWh × $85) − (Underdelivered MWh × $20)

Overperformance cap: 110% of contracted MW
Payment cycle: T+2 business days
```

### Editable Simulation Configuration

`GET /api/market/config` returns all simulation parameters.
`PUT /api/market/config` deep-updates any subset of parameters.

The `SimConfigScreen` in the market portal renders these as editable input fields. When real data is available, operators update values here without code changes — it's the "plug-in point" for real operational data.

**Config sections:**
- `market` — rates, bid bounds, gate closure, settlement period, pricing mechanism
- `grid` — system peak, base load, renewable penetration, flexibility target
- `baseline` — ASHRAE method, lookback days, adjustment band, meter granularity
- `settlement` — payment cycle, underperformance threshold, dispute window, regulatory framework
- `aggregators` — full registry of all 6 aggregators with capacity and pricing
- `integration` — protocol, format, auth, endpoint for each data feed

---

## 10. Data Models

### DER States (`_der_cache` entry)
```python
{
    "id":              str,          # UUID
    "name":            str,          # "Solar_FDR01_001"
    "type":            str,          # "Solar PV" | "BESS" | "EV Charger"
    "feeder_id":       str,          # "FDR-01"
    "dt_id":           str,          # "DT-VAR-0234"
    "aggregator_id":   str,          # "AGG-001"
    "nameplate_kw":    float,        # from IEEE 2030.5 DERSettings.rtgMaxW
    "current_kw":      float,        # live output (updated every 30s)
    "available_kw":    float,        # dispatchable capacity
    "soc_pct":         float | None, # battery SOC (BESS only)
    "status":          str,          # "online" | "offline" | "degraded" | "curtailed"
    "voltage":         float,        # terminal voltage (V)
    "last_seen":       datetime,     # last IEEE 2030.5 heartbeat
}
```

### Market Event
```python
{
    "event_id":              str,   # "PROC-2026-031"
    "type":                  str,   # "Peak Shaving" | "Emergency DR" | ...
    "procurement_mw":        float,
    "duration_h":            float,
    "max_price_usd_per_mwh": float,
    "status":                str,   # "Open" | "Cleared" | "Settled"
    "mcp_usd_per_mwh":       float, # set after clearing
    "cleared_mw":            float, # set after clearing
}
```

### Settlement Record
```python
{
    "settlement_id":        str,
    "event_id":             str,
    "aggregator_id":        str,
    "contracted_mw":        float,
    "delivered_mw":         float,
    "performance_pct":      float,  # delivered/contracted × 100
    "energy_mwh":           float,  # delivered_mw × duration_h
    "base_payment_usd":     float,
    "incentive_payment_usd":float,
    "penalty_usd":          float,
    "total_payment_usd":    float,
    "performance_status":   str,    # "Excellent" | "Pass" | "Underperformed"
}
```

---

## 11. API Reference

### DERMS APIs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ders` | All DERs with live state |
| GET | `/api/ders/{id}` | Single DER detail |
| POST | `/api/ders/dispatch/dr-event` | Trigger DR event |
| GET | `/api/ders/dispatch/dr-events` | DR event history |
| GET | `/api/ders/dispatch/der-controls` | Active DER control commands |
| GET | `/api/dashboard/summary` | Fleet summary (KPIs, feeders, DTs, alerts) |
| GET | `/api/dashboard/generation-profile` | 24h hourly generation by type |
| GET | `/api/dashboard/voltage-monitoring` | Per-DT voltage data |
| GET | `/api/dashboard/aggregators` | Aggregator connection status |
| GET | `/api/dr/demand-analysis` | 24h demand vs SLDC + shortfall |
| GET | `/api/dr/consumers` | Consumer enrollment registry |
| GET | `/api/dr/consumers/lookup` | Billing system lookup |
| POST | `/api/dr/consumers/{no}/enroll` | Enroll consumer |
| POST | `/api/dr/consumers/{no}/unenroll` | Unenroll consumer |
| GET | `/api/dr/cost-benefit` | Per-event and per-consumer CBA |

### IEEE 2030.5 APIs (`/api/2030.5/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dcap` | Device Capability (protocol entry point) |
| GET/POST | `/edev` | EndDevice list / registration |
| GET | `/derp` | DER Program list |
| POST | `/derp/{id}/derc` | Issue DERControl (dispatch command) |
| POST | `/mup/{id}/mr` | Push MirrorMeterReading |

### ADMS Simulator (`/sim/adms/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/status` | ADMS connection and sync state |
| GET | `/topology/cim` | CIM XML (IEC 61970-452) |
| GET | `/realtime/state` | Per-feeder SCADA state |
| POST | `/der-status` | Update DER operational state |

### Market APIs (`/api/market/`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/status` | Market dashboard KPIs |
| GET/PUT | `/config` | Simulation config (editable) |
| GET | `/events` | Procurement events |
| POST | `/events` | Create new event |
| GET | `/bids` | Bids (filter by `event_id`) |
| POST | `/bids` | Submit bid |
| GET | `/merit-order/{id}` | Sorted bids + MCP calculation |
| POST | `/events/{id}/clear` | Clear market (merit order award) |
| GET | `/settlements` | Settlement statements |
| GET | `/aggregators` | Portfolio + performance + earnings |
| GET | `/forecast` | 24h demand + renewable + shortfall forecast |

---

## 12. Simulation Methodology

### Solar Generation (IST-aware)
```python
hour_ist = (hour_utc + 5.5) % 24              # India Standard Time offset
irradiance = max(0, sin(π*(hour_ist-6)/12))   # 0 outside 6am–6pm
output_kw = nameplate_kw × 0.8 × irradiance × noise(0.9, 1.1)
```

### BESS Dispatch Simulation
```python
soc_frac = uniform(0.3, 0.95)
# Daytime: charging from solar surplus (negative current_kw)
if 9 <= hour_ist <= 15:
    current_kw = -nameplate_kw × 0.5   # charging
# Evening: discharging to support peak
else:
    current_kw = nameplate_kw × 0.7    # discharging

available_kw = nameplate_kw × soc_frac × 0.9   # dispatchable capacity
```

### ASHRAE 10-in-10 Baseline
The baseline for a given day is the average consumption of the 10 highest-consumption days among the prior 10 business days with similar day-type (weekday/weekend). An adjustment band of ±10% is applied to account for temperature and occupancy variance.

In this simulation: `baseline_mw ≈ forecast_mw × uniform(0.97, 1.03)` (±3% noise).

### Merit Order Pricing
Bids are sorted cheapest-first. The marginal bid (last one needed to fill the required MW) sets the Market Clearing Price. Under Uniform Pricing, all selected bidders earn MCP regardless of their individual bid.

```
MCP = min(price | cumulative_selected_mw ≥ procurement_mw)
```

### DR Demand Simulation (Varanasi)
```python
base_mw = system_peak_mw × 0.6   # base load at 60% of peak
morning = 0.20 × exp(-0.5 × ((hour-9)²)/2)   # 9am commercial peak
evening = 0.35 × exp(-0.5 × ((hour-20)²)/2)  # 8pm residential peak
demand = base_mw × (0.55 + morning + evening + noise)
sldc_available = demand × uniform(0.90, 1.05)  # SLDC schedule ±10%
shortfall_kw = max(0, demand - sldc_available) × 1000
trigger_dr = shortfall_kw > (demand × 1000 × 0.05)   # > 5% shortfall
```

---

## 13. Deployment

### Local Development
```bash
cd /path/to/derms-prototype
pip install -r requirements.txt
python run.py
# Server: http://localhost:8080
# Dashboard: http://localhost:8080/ui
# Market: http://localhost:8080/market
# API docs: http://localhost:8080/docs
```

### Render.com (Production)
- **Service type:** Web Service
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python run.py`
- **Auto-deploy:** on push to `main` branch
- **URL:** https://derms-prototype.onrender.com

The app uses SQLite (`derms.db`) as the database. On Render free tier, the file system is ephemeral — the DB is re-initialised on each deploy. This is acceptable for a prototype where all state is either simulated or in-memory.

### Startup Time
~35 seconds — the IEEE 2030.5 simulator needs to complete registration of all 33 DERs (3 aggregators × ~11 DERs each, 30s cycle) before the fleet appears fully populated.

---

## 14. Known Limitations & Plug-in Points

| Item | Current (Simulation) | Plug-in When Real |
|------|---------------------|-------------------|
| DER telemetry | Simulated by `ieee2030_5/simulator.py` every 30s | Replace with real IEEE 2030.5 device callbacks; disable simulator |
| ADMS topology | Hardcoded CIM XML in `adms/simulator.py` | Point `GET /sim/adms/topology/cim` to real GE ADMS REST API |
| SCADA state | Simulated load curve in `adms/simulator.py` | Feed real SCADA values via `POST /sim/adms/der-status` |
| Consumer billing | Deterministic RNG in `dr_routes.py lookup` | Replace with `GET https://puvvnl-billing.api/consumer/{no}` |
| Baseline (ASHRAE) | `forecast_mw × uniform(0.97, 1.03)` | Query AMI/MDMS: `GET /mdms/readings/{meter_id}?days=10` |
| DR incentive dispatch | Simulated event records | Connect to OpenADR 2.0b VTN for real EiEvent dispatch |
| Weather/solar forecast | `sin()` daylight curve | `GET https://api.openweathermap.org/data/2.5/forecast` |
| Market settlement | In-memory `_settlements` list | Generate UN/EDIFACT MSCONS files, push to aggregator SFTP |
| FX rate (INR/USD) | Static `83.5` in `_sim_config` | Live feed: `GET https://api.exchangerate-api.com/v4/latest/USD` |
| Auth | None | IEEE 2030.5: mTLS + X.509; Market API: OAuth 2.0 JWT |
| DB | SQLite (single-file, ephemeral on Render) | PostgreSQL (e.g., Supabase) with SQLAlchemy `postgresql+asyncpg://` |
| Time sync | `datetime.now(UTC)` | NTP-synced, POSIX timestamps in all IEEE 2030.5 payloads |
