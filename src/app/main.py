from app.api.v1.services import router as services_router
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.observability import setup_otel
from app.core.metrics import MetricsMiddleware, metrics_app

from app.api.v1 import jobs, events
from app.api.v1 import services as services_api  # NEW: services endpoints

from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import admin



from app.services.db import init_models
from app.kernel.plugins.loader import (
    registry as plugin_registry,  # NEW: plugin registry
    start_watcher,                # NEW: filesystem watcher for hot-reload
)

import os

app = FastAPI(title="Agentic Backend", version="1.0.0")
app.add_middleware(MetricsMiddleware)

# Ensure the artifacts dir exists at startup
os.makedirs(settings.ARTIFACTS_DIR, exist_ok=True)

app.mount(
    "/artifacts",
    StaticFiles(directory=settings.ARTIFACTS_DIR, check_dir=False),
    name="artifacts",
)

setup_otel(app)


@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "env": settings.ENV})


# API routers
app.include_router(jobs.router,     prefix="/v1", tags=["jobs"])
app.include_router(events.router,   prefix="/v1", tags=["events"])
app.include_router(services_api.router, prefix="/v1", tags=["services"])  # NEW
app.include_router(admin.router)


@app.on_event("startup")
async def on_startup():
    await init_models()
    # Boot plugin registry and begin hot-reloading manifests
    plugin_registry.refresh()
    start_watcher()


# Prometheus metrics
app.mount("/metrics", metrics_app)

from app.kernel.plugins.loader import registry as plugin_registry, start_watcher
from app.services.registry_store import sync_all_from_loader

@app.on_event("startup")
async def plugins_bootstrap():
    plugin_registry.refresh()
    try:
        env = (settings.ENV or "dev").lower()
    except Exception:
        env = "dev"
    if env == "dev":
        start_watcher()

app.include_router(services_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # includes Authorization, Accept, etc.
)