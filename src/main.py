"""
DERMS Prototype - Main Application
====================================
FastAPI application serving:
  - DERMS REST API (/api/*)
  - IEEE 2030.5 Server (/api/2030.5/*)
  - GE ADMS Simulator (/sim/adms/*)
  - Dashboard UI (/ui)
  - WebSocket for real-time updates (/ws)

Start: python run.py
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import List, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import settings
from src.database import init_db
from src.derms.fleet import initialize_fleet, get_fleet_summary
from src.derms.monitoring import start_background_tasks

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── WebSocket Connection Manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, data: dict):
        if not self.active:
            return
        payload = json.dumps(data)
        dead = set()
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()
_bg_tasks: List[asyncio.Task] = []


async def broadcast_loop():
    """Broadcast real-time fleet summary to all WebSocket clients every 5s."""
    while True:
        await asyncio.sleep(5)
        if manager.active:
            try:
                summary = get_fleet_summary()
                await manager.broadcast({"type": "fleet_summary", "data": summary})
            except Exception as e:
                logger.error(f"Broadcast error: {e}")


# ─── Application Lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=" * 60)
    logger.info(" DERMS Prototype starting...")
    logger.info("=" * 60)

    # Init database
    await init_db()
    logger.info("Database initialized")

    # Inject fleet store into IEEE 2030.5 server
    import src.derms.fleet as fleet_module
    from src.integrations.ieee2030_5.server import set_fleet_store
    set_fleet_store(fleet_module)

    # Initialize fleet (load topology from ADMS)
    await initialize_fleet()
    logger.info("Fleet manager initialized")

    # Start background tasks
    tasks = await start_background_tasks()
    _bg_tasks.extend(tasks)

    # Start WebSocket broadcast loop
    ws_task = asyncio.create_task(broadcast_loop(), name="ws-broadcast")
    _bg_tasks.append(ws_task)

    logger.info("=" * 60)
    logger.info(f" DERMS API:        http://localhost:{settings.port}/api")
    logger.info(f" IEEE 2030.5:      http://localhost:{settings.port}/api/2030.5/dcap")
    logger.info(f" ADMS Simulator:   http://localhost:{settings.port}/sim/adms/status")
    logger.info(f" Dashboard:        http://localhost:{settings.port}/ui")
    logger.info(f" Market Portal:    http://localhost:{settings.port}/market")
    logger.info(f" API Docs:         http://localhost:{settings.port}/docs")
    logger.info("=" * 60)

    yield  # Application runs

    # Shutdown
    for task in _bg_tasks:
        task.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)
    logger.info("DERMS shutdown complete")


# ─── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="DERMS Prototype",
    description=(
        "Distributed Energy Resource Management System with GE ADMS (CIM) "
        "and IEEE 2030.5 DER Aggregator integration."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Include Routers ──────────────────────────────────────────────────────────

from src.api.der_routes import router as der_router
from src.api.adms_routes import router as adms_router
from src.api.dashboard_routes import router as dashboard_router
from src.api.dr_routes import router as dr_router
from src.api.market_routes import router as market_router
from src.integrations.adms.simulator import router as adms_sim_router
from src.integrations.ieee2030_5.server import router as ieee_router

app.include_router(der_router)
app.include_router(adms_router)
app.include_router(dashboard_router)
app.include_router(dr_router)
app.include_router(market_router)
app.include_router(adms_sim_router)
app.include_router(ieee_router)

# ─── Static Files & Dashboard ─────────────────────────────────────────────────

static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/ui", response_class=HTMLResponse)
async def dashboard():
    """Serve the DERMS dashboard UI."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path) as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Dashboard not found</h1>")


@app.get("/market", response_class=HTMLResponse)
async def market_portal():
    """Serve the L&T Neural Grid Platform market portal."""
    market_path = os.path.join(static_dir, "market.html")
    if os.path.exists(market_path):
        with open(market_path) as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Market portal not found</h1>")


@app.get("/")
async def root():
    return {
        "service": "DERMS Prototype",
        "version": "1.0.0",
        "integrations": {
            "adms": "GE ADMS (CIM XML / IEC 61970-452) — Simulated",
            "der_aggregators": "IEEE 2030.5 / SEP 2.0",
        },
        "endpoints": {
            "dashboard": "/ui",
            "market_portal": "/market",
            "api_docs": "/docs",
            "ieee2030_5": "/api/2030.5/dcap",
            "adms_sim": "/sim/adms/status",
            "fleet_summary": "/api/dashboard/summary",
            "market_status": "/api/market/status",
        },
    }


# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time WebSocket for dashboard live updates."""
    await manager.connect(websocket)
    try:
        # Send initial state
        summary = get_fleet_summary()
        await websocket.send_text(json.dumps({"type": "fleet_summary", "data": summary}))
        # Keep alive
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    import src.derms.fleet as fleet_module
    ders = fleet_module.get_all_ders()
    return {
        "status": "healthy",
        "registered_ders": len(ders),
        "feeders": len(fleet_module.get_all_feeders()),
        "aggregators": len(fleet_module.get_all_aggregators()),
    }
