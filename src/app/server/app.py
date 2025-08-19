from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.server.plugins import list_plugins, get_plugin
from app.server.jobs import JobStore
from app.kernel.service_runner import ServiceRunner
from app.kernel.errors import ProblemDetails
from app.server.validation import extract_schema, validate_inputs_against_schema
from app.server.artifacts import list_project_artifacts
from app.server.uploads import save_upload

PLUGINS_ROOT = Path(os.environ.get("PLUGINS_DIR", "./plugins")).resolve()
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "./artifacts")).resolve()

app = FastAPI(title="Kernel Local API", version="0.1.0")

# CORS for local FE dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (artifacts)
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")

job_store = JobStore()
service_runner = ServiceRunner(PLUGINS_ROOT)

@app.get("/healthz")
def healthz():
    return {"ok": True, "plugins_dir": str(PLUGINS_ROOT), "artifacts_dir": str(ARTIFACTS_DIR)}

# ---------------- Services ----------------

@app.get("/v1/services")
def services_list():
    return {"services": list_plugins(PLUGINS_ROOT)}

@app.get("/v1/services/{service_id}")
def service_detail(service_id: str):
    try:
        manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
    schema = extract_schema(manifest)
    return {
        "id": manifest.get("id"),
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "summary": manifest.get("summary"),
        "schema": schema,
        "permissions": list(perms),
    }

@app.post("/v1/services/{service_id}/validate")
def service_validate(service_id: str, body: Dict[str, Any] = Body(...)):
    inputs = (body.get("inputs") or {})
    try:
        manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
    schema = extract_schema(manifest)
    ok, errors = validate_inputs_against_schema(schema, inputs)
    return {"ok": ok, "errors": errors}

@app.post("/v1/services/{service_id}/jobs", status_code=201)
async def service_start(service_id: str, body: Dict[str, Any] = Body(...), validate: bool = True):
    inputs = (body.get("inputs") or {})

    # Preflight validation (on by default)
    try:
        manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")

    if validate:
        schema = extract_schema(manifest)
        ok, errors = validate_inputs_against_schema(schema, inputs)
        if not ok:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "title": "Input validation failed",
                        "code": "E_INPUTS_INVALID",
                        "detail": "One or more fields are invalid. See `errors`.",
                        "errors": errors,
                        "schema": schema,
                    }
                },
            )

    # Generate identifiers if not provided
    import uuid
    job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
    inputs["_job_id"] = job_id

    project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"
    inputs["project_id"] = project_id

    # Persist a job record
    await job_store.create(job_id, service_id, project_id, inputs)

    loop = asyncio.get_running_loop()

    async def on_event(event_type: str, payload: Dict[str, Any]):
        await job_store.append_event(job_id, event_type, payload)

    async def run_job():
        try:
            # Run DSL/service in a worker thread; event emitter posts back to this loop
            outputs = await asyncio.to_thread(service_runner.run, service_id, inputs, on_event, loop)
            await job_store.set_outputs(job_id, outputs)
        except ProblemDetails as e:
            await job_store.update_status(job_id, "failed", e.to_dict())
        except Exception as e:
            await job_store.update_status(job_id, "failed", {"title": "Unhandled", "detail": str(e)})

    asyncio.create_task(run_job())

    return {
        "job_id": job_id,
        "service_id": service_id,
        "project_id": project_id,
        "status": "running",
        "watch": f"/v1/jobs/{job_id}/events",
    }

# ---------------- Jobs ----------------

@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str):
    rec = job_store.get(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": rec.job_id,
        "service_id": rec.service_id,
        "project_id": rec.project_id,
        "status": rec.status,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "outputs": rec.outputs,
        "error": rec.error,
    }

@app.get("/v1/jobs/{job_id}/events")
async def job_events(job_id: str):
    rec = job_store.get(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Job not found")

    queue = await job_store.subscribe(job_id)

    async def event_stream():
        # Replay existing events first
        for evt in list(rec.events):
            yield _format_sse(evt)
        # Then stream new events
        try:
            while True:
                evt = await queue.get()
                yield _format_sse(evt)
        except asyncio.CancelledError:
            pass
        finally:
            await job_store.unsubscribe(job_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# ---------------- Artifacts ----------------

@app.get("/v1/projects/{project_id}/artifacts")
def project_artifacts(project_id: str):
    return list_project_artifacts(ARTIFACTS_DIR, project_id)

@app.get("/v1/jobs/{job_id}/artifacts")
def job_artifacts(job_id: str):
    rec = job_store.get(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Job not found")
    return list_project_artifacts(ARTIFACTS_DIR, rec.project_id)

# ---------------- Uploads ----------------

@app.post("/v1/uploads")
async def upload_file(file: UploadFile = File(...), project_id: Optional[str] = Form(None)):
    """
    Multipart upload. Saves the file under artifacts/{project_id}/input and returns:
    { project_id, filename, path, url, kind }
    """
    try:
        info = await save_upload(file, ARTIFACTS_DIR, project_id)
        return info
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Upload failed")

# ---------------- SSE formatting ----------------

def _format_sse(evt: Dict[str, Any]) -> str:
    data = json.dumps(evt, ensure_ascii=False)
    return f"event: {evt['type']}\n" + f"data: {data}\n\n"
