from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.observability import setup_otel
from app.core.metrics import MetricsMiddleware, metrics_app
from app.api.v1 import agents, jobs, events, packs
from app.services.db import init_models

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
app.include_router(agents.router, prefix="/v1", tags=["agents"])
app.include_router(jobs.router,   prefix="/v1", tags=["jobs"])
app.include_router(events.router, prefix="/v1", tags=["events"])
app.include_router(packs.router)  # GET /v1/packs

@app.on_event("startup")
async def on_startup():
    await init_models()

# Prometheus metrics
app.mount("/metrics", metrics_app)
