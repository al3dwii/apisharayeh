# src/app/server/app.py
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Form, Query
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

# PATCH endpoint router (slides.update_one)
from app.server.routes_edit import router as edit_router


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

# Mount edit routes (PATCH /v1/projects/{project_id}/slides/{no})
app.include_router(edit_router)

job_store = JobStore()
service_runner = ServiceRunner(PLUGINS_ROOT)


# ---------------- Health ----------------

@app.get("/health")
def health():
    return {"ok": True, "plugins_dir": str(PLUGINS_ROOT), "artifacts_dir": str(ARTIFACTS_DIR)}

@app.get("/healthz")
def healthz():
    return health()


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


# ---------------- Jobs (create/run) ----------------

def _make_sync_emitter(loop: asyncio.AbstractEventLoop, job_store: JobStore, job_id: str):
    """
    Return a sync callable that schedules append_event on the main loop
    even when called from a worker thread. Also mirrors status changes
    (running/succeeded/failed) immediately into JobStore for fast polling.
    """
    def emit(event_type: str, payload: Dict[str, Any]):
        try:
            asyncio.run_coroutine_threadsafe(
                job_store.append_event(job_id, event_type, payload),
                loop,
            )
            if event_type == "status":
                state = (payload or {}).get("state")
                if state in ("running", "succeeded", "failed"):
                    err = (payload or {}).get("error")
                    asyncio.run_coroutine_threadsafe(
                        job_store.update_status(job_id, state, err),
                        loop,
                    )
        except Exception as e:
            # Non-fatal; do not crash worker for logging failures
            print(f"[events] failed to schedule emit {event_type}: {e}")
    return emit


async def _start_job(service_id: str, inputs: Dict[str, Any], validate_inputs: bool, created_status: int = 202):
    # Validate service exists
    try:
        manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")

    # Optional schema validation
    if validate_inputs:
        schema = extract_schema(manifest)
        ok, errors = validate_inputs_against_schema(schema, inputs or {})
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

    # Identifiers
    import uuid
    job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
    inputs["_job_id"] = job_id
    project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"
    inputs["project_id"] = project_id

    # Persist job (status=running)
    await job_store.create(job_id, service_id, project_id, inputs)

    loop = asyncio.get_running_loop()
    on_event_sync = _make_sync_emitter(loop, job_store, job_id)

    async def run_job():
        try:
            outputs = await asyncio.to_thread(service_runner.run, service_id, inputs, on_event_sync)
            await job_store.set_outputs(job_id, outputs or {})
            await job_store.update_status(job_id, "succeeded", None)
        except ProblemDetails as e:
            await job_store.update_status(job_id, "failed", e.to_dict())
        except Exception as e:
            await job_store.update_status(job_id, "failed", {"title": "Unhandled", "detail": str(e)})

    asyncio.create_task(run_job())

    return JSONResponse(
        status_code=created_status,
        content={
            "job_id": job_id,
            "service_id": service_id,
            "project_id": project_id,
            "status": "running",
            "watch": f"/v1/jobs/{job_id}/events",
        },
    )


# New-style endpoint: explicit service path (201)
@app.post("/v1/services/{service_id}/jobs", status_code=201)
async def service_start(service_id: str, body: Dict[str, Any] = Body(...), validate: bool = Query(True)):
    inputs = (body.get("inputs") or {})
    return await _start_job(service_id, inputs, validate_inputs=validate, created_status=201)


# Legacy endpoint for compatibility with existing scripts (202)
@app.post("/v1/jobs", status_code=202)
async def jobs_start(body: Dict[str, Any] = Body(...), validate: bool = Query(True)):
    # Accept either `service_id` or legacy `kind`
    service_id = body.get("service_id") or body.get("kind")
    if not service_id:
        raise HTTPException(status_code=400, detail="Missing service_id/kind")
    inputs = (body.get("inputs") or {})
    return await _start_job(service_id, inputs, validate_inputs=validate, created_status=202)


# ---------------- Jobs (status/events) ----------------

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
        # Replay historical events first
        for evt in list(rec.events):
            yield _format_sse(evt)
        # Then stream new ones
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
    except Exception:
        raise HTTPException(status_code=500, detail="Upload failed")


# ---------------- SSE formatting ----------------

def _format_sse(evt: Dict[str, Any]) -> str:
    """
    Ensure the SSE 'data:' field contains ONLY the event payload
    (not the wrapper), matching the frontend contract.

    Accepts a few historical shapes:
      - {'type': 'partial', 'payload': {...}, 'ts': ...}
      - {'type': 'partial', 'data': {...}, 'ts': ...}
      - {'type': 'partial', ...inline keys...}  # fallback

    Produces:
      event: <evt['type']>
      data: <JSON of payload only>
    """
    # Prefer 'payload', fallback to 'data', then strip wrapper keys.
    payload = evt.get("payload")
    if payload is None and "data" in evt and isinstance(evt["data"], (dict, list, str, int, float, bool)) and evt["data"] != evt:
        payload = evt["data"]
    if payload is None:
        # Build a minimal payload from inline keys, excluding wrappers.
        payload = {k: v for k, v in evt.items() if k not in ("type", "ts", "timestamp", "event")}

    # Final safety: payload must be JSON-serializable
    try:
        data_json = json.dumps(payload or {}, ensure_ascii=False)
    except Exception:
        # Last resort: stringify the payload
        data_json = json.dumps({"text": str(payload)}, ensure_ascii=False)

    event_name = evt.get("type") or "message"
    return f"event: {event_name}\n" + f"data: {data_json}\n\n"


# # src/app/server/app.py
# from __future__ import annotations
# import asyncio
# import json
# import os
# from pathlib import Path
# from typing import Any, Dict, Optional

# from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Form, Query
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse, StreamingResponse
# from fastapi.staticfiles import StaticFiles

# from app.server.plugins import list_plugins, get_plugin
# from app.server.jobs import JobStore
# from app.kernel.service_runner import ServiceRunner
# from app.kernel.errors import ProblemDetails
# from app.server.validation import extract_schema, validate_inputs_against_schema
# from app.server.artifacts import list_project_artifacts
# from app.server.uploads import save_upload

# from app.server.routes_edit import router as edit_router


# PLUGINS_ROOT = Path(os.environ.get("PLUGINS_DIR", "./plugins")).resolve()
# ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "./artifacts")).resolve()

# app = FastAPI(title="Kernel Local API", version="0.1.0")

# # CORS for local FE dev
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # Static files (artifacts)
# ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
# app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")

# job_store = JobStore()
# service_runner = ServiceRunner(PLUGINS_ROOT)


# @app.get("/healthz")
# def healthz():
#     return {"ok": True, "plugins_dir": str(PLUGINS_ROOT), "artifacts_dir": str(ARTIFACTS_DIR)}


# # ---------------- Services ----------------

# @app.get("/v1/services")
# def services_list():
#     return {"services": list_plugins(PLUGINS_ROOT)}


# @app.get("/v1/services/{service_id}")
# def service_detail(service_id: str):
#     try:
#         manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
#     except Exception:
#         raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
#     schema = extract_schema(manifest)
#     return {
#         "id": manifest.get("id"),
#         "name": manifest.get("name"),
#         "version": manifest.get("version"),
#         "summary": manifest.get("summary"),
#         "schema": schema,
#         "permissions": list(perms),
#     }


# @app.post("/v1/services/{service_id}/validate")
# def service_validate(service_id: str, body: Dict[str, Any] = Body(...)):
#     inputs = (body.get("inputs") or {})
#     try:
#         manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
#     except Exception:
#         raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
#     schema = extract_schema(manifest)
#     ok, errors = validate_inputs_against_schema(schema, inputs)
#     return {"ok": ok, "errors": errors}


# # ---------------- Jobs (create/run) ----------------

# def _make_sync_emitter(loop: asyncio.AbstractEventLoop, job_store: JobStore, job_id: str):
#     """
#     Return a sync callable that schedules append_event on the main loop
#     even when called from a worker thread. Also mirrors status changes
#     (running/succeeded/failed) immediately into JobStore for fast polling.
#     """
#     def emit(event_type: str, payload: Dict[str, Any]):
#         try:
#             # Always record the event
#             asyncio.run_coroutine_threadsafe(
#                 job_store.append_event(job_id, event_type, payload),
#                 loop,
#             )
#             # Mirror status into JobStore
#             if event_type == "status":
#                 state = (payload or {}).get("state")
#                 if state in ("running", "succeeded", "failed"):
#                     err = (payload or {}).get("error")
#                     asyncio.run_coroutine_threadsafe(
#                         job_store.update_status(job_id, state, err),
#                         loop,
#                     )
#         except Exception as e:
#             # Non-fatal; do not crash worker for logging failures
#             print(f"[events] failed to schedule emit {event_type}: {e}")
#     return emit


# @app.post("/v1/services/{service_id}/jobs", status_code=201)
# async def service_start(
#     service_id: str,
#     body: Dict[str, Any] = Body(...),
#     validate: bool = Query(True),
# ):
#     inputs = (body.get("inputs") or {})

#     # Preflight validation (on by default)
#     try:
#         manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
#     except Exception:
#         raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")

#     if validate:
#         schema = extract_schema(manifest)
#         ok, errors = validate_inputs_against_schema(schema, inputs)
#         if not ok:
#             return JSONResponse(
#                 status_code=422,
#                 content={
#                     "error": {
#                         "title": "Input validation failed",
#                         "code": "E_INPUTS_INVALID",
#                         "detail": "One or more fields are invalid. See `errors`.",
#                         "errors": errors,
#                         "schema": schema,
#                     }
#                 },
#             )

#     # Generate identifiers if not provided
#     import uuid
#     job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
#     inputs["_job_id"] = job_id

#     project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"
#     inputs["project_id"] = project_id

#     # Persist a job record (sets status=running and stores initial inputs)
#     await job_store.create(job_id, service_id, project_id, inputs)

#     loop = asyncio.get_running_loop()
#     on_event_sync = _make_sync_emitter(loop, job_store, job_id)

#     async def run_job():
#         try:
#             # Run DSL/service in a worker thread; it will call on_event_sync (sync) from that thread.
#             outputs = await asyncio.to_thread(service_runner.run, service_id, inputs, on_event_sync)
#             # Store outputs
#             await job_store.set_outputs(job_id, outputs or {})
#             # Belt & suspenders: in case no 'succeeded' status event was emitted/seen
#             await job_store.update_status(job_id, "succeeded", None)
#         except ProblemDetails as e:
#             await job_store.update_status(job_id, "failed", e.to_dict())
#         except Exception as e:
#             await job_store.update_status(job_id, "failed", {"title": "Unhandled", "detail": str(e)})

#     asyncio.create_task(run_job())

#     return {
#         "job_id": job_id,
#         "service_id": service_id,
#         "project_id": project_id,
#         "status": "running",
#         "watch": f"/v1/jobs/{job_id}/events",
#     }


# # ---------------- Jobs (status/events) ----------------

# @app.get("/v1/jobs/{job_id}")
# def job_status(job_id: str):
#     rec = job_store.get(job_id)
#     if not rec:
#         raise HTTPException(status_code=404, detail="Job not found")
#     return {
#         "job_id": rec.job_id,
#         "service_id": rec.service_id,
#         "project_id": rec.project_id,
#         "status": rec.status,
#         "created_at": rec.created_at,
#         "updated_at": rec.updated_at,
#         "outputs": rec.outputs,
#         "error": rec.error,
#     }


# @app.get("/v1/jobs/{job_id}/events")
# async def job_events(job_id: str):
#     rec = job_store.get(job_id)
#     if not rec:
#         raise HTTPException(status_code=404, detail="Job not found")

#     queue = await job_store.subscribe(job_id)

#     async def event_stream():
#         # Replay historical events first
#         for evt in list(rec.events):
#             yield _format_sse(evt)
#         # Then stream new ones
#         try:
#             while True:
#                 evt = await queue.get()
#                 yield _format_sse(evt)
#         except asyncio.CancelledError:
#             pass
#         finally:
#             await job_store.unsubscribe(job_id, queue)

#     return StreamingResponse(event_stream(), media_type="text/event-stream")


# # ---------------- Artifacts ----------------

# @app.get("/v1/projects/{project_id}/artifacts")
# def project_artifacts(project_id: str):
#     return list_project_artifacts(ARTIFACTS_DIR, project_id)


# @app.get("/v1/jobs/{job_id}/artifacts")
# def job_artifacts(job_id: str):
#     rec = job_store.get(job_id)
#     if not rec:
#         raise HTTPException(status_code=404, detail="Job not found")
#     return list_project_artifacts(ARTIFACTS_DIR, rec.project_id)


# # ---------------- Uploads ----------------

# @app.post("/v1/uploads")
# async def upload_file(file: UploadFile = File(...), project_id: Optional[str] = Form(None)):
#     """
#     Multipart upload. Saves the file under artifacts/{project_id}/input and returns:
#     { project_id, filename, path, url, kind }
#     """
#     try:
#         info = await save_upload(file, ARTIFACTS_DIR, project_id)
#         return info
#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=str(e))
#     except Exception:
#         raise HTTPException(status_code=500, detail="Upload failed")


# # ---------------- SSE formatting ----------------

# # ---------------- SSE formatting ----------------

# def _format_sse(evt: Dict[str, Any]) -> str:
#     """
#     Ensure the SSE 'data:' field contains ONLY the event payload
#     (not the wrapper), matching the frontend contract.

#     Accepts a few historical shapes:
#       - {'type': 'partial', 'payload': {...}, 'ts': ...}
#       - {'type': 'partial', 'data': {...}, 'ts': ...}
#       - {'type': 'partial', ...inline keys...}  # fallback

#     Produces:
#       event: <evt['type']>
#       data: <JSON of payload only>
#     """
#     # Prefer 'payload', fallback to 'data', then strip wrapper keys.
#     payload = evt.get("payload")
#     if payload is None and "data" in evt and isinstance(evt["data"], (dict, list, str, int, float, bool)) and evt["data"] != evt:
#         payload = evt["data"]
#     if payload is None:
#         # Build a minimal payload from inline keys, excluding wrappers.
#         payload = {k: v for k, v in evt.items() if k not in ("type", "ts", "timestamp", "event")}

#     # Final safety: payload must be JSON-serializable
#     try:
#         data_json = json.dumps(payload or {}, ensure_ascii=False)
#     except Exception:
#         # Last resort: stringify the payload
#         data_json = json.dumps({"text": str(payload)}, ensure_ascii=False)

#     event_name = evt.get("type") or "message"
#     return f"event: {event_name}\n" + f"data: {data_json}\n\n"




# # src/app/server/app.py
# from __future__ import annotations
# import asyncio
# import json
# import os
# from pathlib import Path
# from typing import Any, Dict, Optional

# from fastapi import FastAPI, HTTPException, Body, UploadFile, File, Form, Query
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse, StreamingResponse
# from fastapi.staticfiles import StaticFiles

# from app.server.plugins import list_plugins, get_plugin
# from app.server.jobs import JobStore
# from app.kernel.service_runner import ServiceRunner
# from app.kernel.errors import ProblemDetails
# from app.server.validation import extract_schema, validate_inputs_against_schema
# from app.server.artifacts import list_project_artifacts
# from app.server.uploads import save_upload

# PLUGINS_ROOT = Path(os.environ.get("PLUGINS_DIR", "./plugins")).resolve()
# ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "./artifacts")).resolve()

# app = FastAPI(title="Kernel Local API", version="0.1.0")

# # CORS for local FE dev
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # Static files (artifacts)
# ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
# app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")

# job_store = JobStore()
# service_runner = ServiceRunner(PLUGINS_ROOT)


# @app.get("/healthz")
# def healthz():
#     return {"ok": True, "plugins_dir": str(PLUGINS_ROOT), "artifacts_dir": str(ARTIFACTS_DIR)}


# # ---------------- Services ----------------

# @app.get("/v1/services")
# def services_list():
#     return {"services": list_plugins(PLUGINS_ROOT)}


# @app.get("/v1/services/{service_id}")
# def service_detail(service_id: str):
#     try:
#         manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
#     except Exception:
#         raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
#     schema = extract_schema(manifest)
#     return {
#         "id": manifest.get("id"),
#         "name": manifest.get("name"),
#         "version": manifest.get("version"),
#         "summary": manifest.get("summary"),
#         "schema": schema,
#         "permissions": list(perms),
#     }


# @app.post("/v1/services/{service_id}/validate")
# def service_validate(service_id: str, body: Dict[str, Any] = Body(...)):
#     inputs = (body.get("inputs") or {})
#     try:
#         manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
#     except Exception:
#         raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")
#     schema = extract_schema(manifest)
#     ok, errors = validate_inputs_against_schema(schema, inputs)
#     return {"ok": ok, "errors": errors}

# # in src/app/server/app.py

# def _make_sync_emitter(loop: asyncio.AbstractEventLoop, job_store: JobStore, job_id: str):
#     """
#     Return a sync callable that schedules append_event on the main loop and
#     mirrors status changes into the JobStore (even when called from a worker thread).
#     """
#     def emit(event_type: str, payload: Dict[str, Any]):
#         try:
#             # Always record the event
#             asyncio.run_coroutine_threadsafe(
#                 job_store.append_event(job_id, event_type, payload),
#                 loop,
#             )
#             # Mirror status events into JobStore.status for fast polling
#             if event_type == "status":
#                 state = (payload or {}).get("state")
#                 if state in ("succeeded", "failed"):
#                     err = (payload or {}).get("error")
#                     asyncio.run_coroutine_threadsafe(
#                         job_store.update_status(job_id, state, err),
#                         loop,
#                     )
#         except Exception as e:
#             # Non-fatal; don't crash worker on log/emit failure
#             print(f"[events] failed to schedule emit {event_type}: {e}")
#     return emit



# @app.post("/v1/services/{service_id}/jobs", status_code=201)
# async def service_start(
#     service_id: str,
#     body: Dict[str, Any] = Body(...),
#     validate: bool = Query(True),
# ):
#     inputs = (body.get("inputs") or {})

#     # Preflight validation (on by default)
#     try:
#         manifest, flow, perms = get_plugin(PLUGINS_ROOT, service_id)
#     except Exception:
#         raise HTTPException(status_code=404, detail=f"Service not found: {service_id}")

#     if validate:
#         schema = extract_schema(manifest)
#         ok, errors = validate_inputs_against_schema(schema, inputs)
#         if not ok:
#             return JSONResponse(
#                 status_code=422,
#                 content={
#                     "error": {
#                         "title": "Input validation failed",
#                         "code": "E_INPUTS_INVALID",
#                         "detail": "One or more fields are invalid. See `errors`.",
#                         "errors": errors,
#                         "schema": schema,
#                     }
#                 },
#             )

#     # Generate identifiers if not provided
#     import uuid
#     job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
#     inputs["_job_id"] = job_id

#     project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"
#     inputs["project_id"] = project_id

#     # Persist a job record (JobStore likely defaults to status='queued')
#     await job_store.create(job_id, service_id, project_id, inputs)

#     # Immediately mark as 'running' so pollers don't get stuck on 'queued'
#     await job_store.update_status(job_id, "running")

#     loop = asyncio.get_running_loop()
#     on_event_sync = _make_sync_emitter(loop, job_store, job_id)

#     async def run_job():
#         try:
#             # Run DSL/service in a worker thread; it will call on_event_sync (sync) from that thread.
#             outputs = await asyncio.to_thread(service_runner.run, service_id, inputs, on_event_sync)
#             await job_store.set_outputs(job_id, outputs)
#             # Ensure terminal state is reflected even if the flow didn't emit a final status
#             await job_store.update_status(job_id, "succeeded")
#         except ProblemDetails as e:
#             await job_store.update_status(job_id, "failed", e.to_dict())
#         except Exception as e:
#             await job_store.update_status(job_id, "failed", {"title": "Unhandled", "detail": str(e)})

#     asyncio.create_task(run_job())

#     return {
#         "job_id": job_id,
#         "service_id": service_id,
#         "project_id": project_id,
#         "status": "running",
#         "watch": f"/v1/jobs/{job_id}/events",
#     }


# # ---------------- Jobs (status/events) ----------------

# @app.get("/v1/jobs/{job_id}")
# def job_status(job_id: str):
#     rec = job_store.get(job_id)
#     if not rec:
#         raise HTTPException(status_code=404, detail="Job not found")
#     return {
#         "job_id": rec.job_id,
#         "service_id": rec.service_id,
#         "project_id": rec.project_id,
#         "status": rec.status,
#         "created_at": rec.created_at,
#         "updated_at": rec.updated_at,
#         "outputs": rec.outputs,
#         "error": rec.error,
#     }


# @app.get("/v1/jobs/{job_id}/events")
# async def job_events(job_id: str):
#     rec = job_store.get(job_id)
#     if not rec:
#         raise HTTPException(status_code=404, detail="Job not found")

#     queue = await job_store.subscribe(job_id)

#     async def event_stream():
#         # Replay historical events first
#         for evt in list(rec.events):
#             yield _format_sse(evt)
#         # Then stream new ones
#         try:
#             while True:
#                 evt = await queue.get()
#                 yield _format_sse(evt)
#         except asyncio.CancelledError:
#             pass
#         finally:
#             await job_store.unsubscribe(job_id, queue)

#     return StreamingResponse(event_stream(), media_type="text/event-stream")


# # ---------------- Artifacts ----------------

# @app.get("/v1/projects/{project_id}/artifacts")
# def project_artifacts(project_id: str):
#     return list_project_artifacts(ARTIFACTS_DIR, project_id)


# @app.get("/v1/jobs/{job_id}/artifacts")
# def job_artifacts(job_id: str):
#     rec = job_store.get(job_id)
#     if not rec:
#         raise HTTPException(status_code=404, detail="Job not found")
#     return list_project_artifacts(ARTIFACTS_DIR, rec.project_id)


# # ---------------- Uploads ----------------

# @app.post("/v1/uploads")
# async def upload_file(file: UploadFile = File(...), project_id: Optional[str] = Form(None)):
#     """
#     Multipart upload. Saves the file under artifacts/{project_id}/input and returns:
#     { project_id, filename, path, url, kind }
#     """
#     try:
#         info = await save_upload(file, ARTIFACTS_DIR, project_id)
#         return info
#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=str(e))
#     except Exception:
#         raise HTTPException(status_code=500, detail="Upload failed")


# # ---------------- SSE formatting ----------------

# def _format_sse(evt: Dict[str, Any]) -> str:
#     data = json.dumps(evt, ensure_ascii=False)
#     return f"event: {evt['type']}\n" + f"data: {data}\n\n"
