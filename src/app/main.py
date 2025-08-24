# src/app/main.py
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware


from app.core.config import settings
from app.core.metrics import MetricsMiddleware, metrics_app
from app.core.observability import setup_otel
from app.core.request_size_middleware import MaxBodySizeMiddleware

from app.services.db import init_models

# Plugins / services
from app.api.v1 import jobs, events
from app.api.v1.services import router as services_router
from app.kernel.plugins.loader import registry as plugin_registry, start_watcher
from app.services.registry_store import sync_all_from_loader

from app.api.routes.slides import router as slides_router  
from app.api.routes.uploads import router as uploads_router  


app = FastAPI(title="Agentic Backend", version="1.0.0")

# ---- Middlewares (order matters) ----
app.add_middleware(MetricsMiddleware)
# app.add_middleware(MaxBodySizeMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure the artifacts dir exists (local store)
os.makedirs(settings.ARTIFACTS_DIR, exist_ok=True)
app.mount(
    "/artifacts",
    StaticFiles(directory=settings.ARTIFACTS_DIR, check_dir=False),
    name="artifacts",
)

# Prometheus metrics
app.mount("/metrics", metrics_app)

# Telemetry
setup_otel(app)


@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "env": settings.ENV})


# ---- Routers ----
app.include_router(jobs.router,     prefix="/v1", tags=["jobs"])
app.include_router(events.router,   prefix="/v1", tags=["events"])
app.include_router(services_router, prefix="/v1", tags=["services"])
app.include_router(slides_router)  
app.include_router(uploads_router)  




# ---- Startup ----
@app.on_event("startup")
async def on_startup():
    # DB models
    await init_models()

    # Load plugin registry from disk and sync to DB
    plugin_registry.refresh()
    await sync_all_from_loader()

    # Dev convenience: live-reload plugin manifests on dev-like envs
    env = (getattr(settings, "ENV", "dev") or "dev").lower()
    if env in {"dev", "local"}:
        start_watcher()
