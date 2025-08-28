# src/app/kernel/service_runner.py
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple
from types import SimpleNamespace

from app.core.config import settings

from .manifest import load_plugin_snapshot
from .dsl_runner import DSLRunner
from .bootstrap_toolrouter import build_toolrouter
from .context import ExecutionContext
from .events import EventEmitter
from .errors import ProblemDetails


# ---------------------------------------------------------------------------
# DSL preflight validators (catch common flow.yaml mistakes early and clearly)
# ---------------------------------------------------------------------------

def _validate_flow_steps(flow: Dict[str, Any]) -> None:
    """
    Validate basic structure of the DSL flow before execution.
    Accepts either legacy ('steps' + 'run') or new style ('nodes' + 'op').
    """
    if not isinstance(flow, dict):
        raise ValueError("Flow must be a mapping/dict at the top level")

    steps = flow.get("steps")
    nodes = flow.get("nodes")

    if isinstance(steps, list) and steps:
        for i, st in enumerate(steps, start=1):
            if not isinstance(st, dict):
                raise ValueError(f"Flow step #{i}: must be a mapping/dict, got {type(st).__name__}")
            if "run" not in st and "op" not in st:
                raise ValueError(f"Flow step #{i}: missing required key 'run' (or 'op')")
            args = st.get("args")
            if args is None:
                continue
            if isinstance(args, list):
                raise ValueError(
                    f"Flow step #{i} '{st.get('run') or st.get('op')}': 'args' must be a mapping/dict, not a YAML list.\n"
                    f"Example of correct form:\n"
                    f"  args:\n"
                    f"    key: value\n"
                    f"(not)\n"
                    f"  args:\n"
                    f"    - key: value"
                )
            if not isinstance(args, dict):
                raise ValueError(
                    f"Flow step #{i} '{st.get('run') or st.get('op')}': 'args' must be a mapping/dict, got {type(args).__name__}"
                )
        return

    if isinstance(nodes, list) and nodes:
        for i, nd in enumerate(nodes, start=1):
            if not isinstance(nd, dict):
                raise ValueError(f"Flow node #{i}: must be a mapping/dict, got {type(nd).__name__}")
            if "op" not in nd and "run" not in nd:
                raise ValueError(f"Flow node #{i}: missing required key 'op' (or 'run')")
        return

    raise ValueError("Flow: must contain a non-empty 'steps' or 'nodes' list")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Artifacts context helpers — single source of truth for FS + URLs
# ---------------------------------------------------------------------------

def _setup_artifacts_ctx(ctx, project_id: str) -> None:
    """
    Attach storage + URL helpers to ctx, all pointing to settings.ARTIFACTS_DIR.
    Everything becomes project-scoped and consistent with the API's /artifacts mount.
    """
    ARTIFACTS_ROOT = Path(settings.ARTIFACTS_DIR).expanduser().resolve()
    project_dir = (ARTIFACTS_ROOT / project_id).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    # Expose a storage handle for older code paths
    ctx.storage = getattr(ctx, "storage", None) or SimpleNamespace()
    ctx.storage.root = str(ARTIFACTS_ROOT)

    # Path getters (project-scoped). Return Path for operator "/" compatibility.
    def artifacts_dir(*parts: Any) -> Path:
        if parts:
            segs = [str(p).lstrip("/") for p in parts if str(p)]
            p = project_dir.joinpath(*segs)
            p.mkdir(parents=True, exist_ok=True)
            return p
        return project_dir

    # String variant for callers that want a str
    def artifacts_dir_str(*parts: Any) -> str:
        return artifacts_dir(*parts).as_posix()

    # Also expose a Path-returning alias (kept for clarity)
    def artifacts_path(*parts: Any) -> Path:
        return artifacts_dir(*parts)

    # -----------------------
    # Text I/O (atomic write)
    # -----------------------
    def write_text(rel: Any, text: str) -> str:
        rel_path = rel if isinstance(rel, Path) else Path(str(rel))
        rel_s = rel_path.as_posix().lstrip("/")
        target = project_dir / rel_s
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            with open(tmp, "rb") as fh:
                os.fsync(fh.fileno())
        except Exception:
            pass
        os.replace(tmp, target)
        return target.as_posix()

    def read_text(rel: Any) -> str:
        rel_path = rel if isinstance(rel, Path) else Path(str(rel))
        rel_s = rel_path.as_posix().lstrip("/")
        p = project_dir / rel_s
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # -------------------------
    # Binary I/O (atomic write)
    # -------------------------
    def write_bytes(rel: Any, data: bytes) -> str:
        rel_path = rel if isinstance(rel, Path) else Path(str(rel))
        rel_s = rel_path.as_posix().lstrip("/")
        target = project_dir / rel_s
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        try:
            with open(tmp, "rb") as fh:
                os.fsync(fh.fileno())
        except Exception:
            pass
        os.replace(tmp, target)
        return target.as_posix()

    def read_bytes(rel: Any) -> bytes:
        rel_path = rel if isinstance(rel, Path) else Path(str(rel))
        rel_s = rel_path.as_posix().lstrip("/")
        p = project_dir / rel_s
        return p.read_bytes() if p.exists() else b""

    # URL builder — accepts absolute FS paths (under project_dir), '/artifacts/...', or project-relative paths
    def url_for(target: Any) -> str:
        # Normalize to string, but preserve Path semantics where relevant
        if isinstance(target, Path):
            # If absolute, try to make it project-relative
            if target.is_absolute():
                try:
                    s = target.relative_to(project_dir).as_posix()
                except Exception:
                    s = target.as_posix()
            else:
                s = target.as_posix()
        else:
            s = str(target).strip()
            try:
                p = Path(s)
                if p.is_absolute():
                    try:
                        s = p.relative_to(project_dir).as_posix()
                    except Exception:
                        s = p.as_posix()
            except Exception:
                pass

        # If caller passed '/artifacts/...', strip prefix and any duplicated project id
        if s.startswith("/artifacts/"):
            s = s[len("/artifacts/"):]
        s = s.lstrip("/")

        # Drop a leading '<project_id>/' if present to avoid duplication
        if s.startswith(f"{project_id}/"):
            s = s[len(project_id) + 1:]

        # Guard: default to state.json if empty
        if not s:
            s = "state.json"

        return f"/artifacts/{project_id}/{s}"

    # Bind to ctx
    ctx.project_id = project_id
    ctx.artifacts_dir = artifacts_dir           # returns Path
    ctx.artifacts_dir_str = artifacts_dir_str   # returns str
    ctx.artifacts_path = artifacts_path         # returns Path
    ctx.write_text = write_text
    ctx.read_text = read_text
    ctx.write_bytes = write_bytes               # NEW
    ctx.read_bytes = read_bytes                 # NEW
    ctx.url_for = url_for
    ctx.url_for_item = url_for


# ---------------------------------------------------------------------------
# ServiceRunner
# ---------------------------------------------------------------------------

class ServiceRunner:
    def __init__(self, plugins_root: Path):
        self.plugins_root = plugins_root

    def _load(self, service_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str], Path]:
        plugin_dir = self.plugins_root / service_id
        manifest, flow, perms = load_plugin_snapshot(plugin_dir)
        # Preflight validation of the DSL flow for clear error messages
        _validate_flow_steps(flow)
        return manifest, flow, perms, plugin_dir

    def run(
        self,
        service_id: str,
        inputs: Dict[str, Any],
        on_event=None,
        loop: Optional[object] = None
    ) -> Dict[str, Any]:
        """
        Execute a service plugin:
          - Loads manifest/flow
          - Validates the flow shape
          - Builds ToolRouter
          - Runs DSL with an ExecutionContext that emits SSE-compatible events
        Returns the 'outputs' dict from the DSL runner.
        """
        manifest, flow, perms, _plugin_dir = self._load(service_id)

        # Ensure identifiers
        job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
        project_id = inputs.get("project_id") or inputs.get("projectId") or inputs.get("project") or f"prj_{uuid.uuid4().hex[:8]}"
        inputs["project_id"] = project_id  # reflect synthesized project_id

        # Build event emitter
        emitter = EventEmitter(on_emit=on_event, loop=loop)

        # Context for ops
        ctx = ExecutionContext(
            job_id=job_id,
            project_id=project_id,
            permissions=set(perms),
            env={
                "DEV_OFFLINE": _env_bool("DEV_OFFLINE", True),
                "ARTIFACTS_DIR": settings.ARTIFACTS_DIR,
            },
            emitter=emitter,
        )

        # Normalize FS + URL helpers so every op reads/writes the SAME place the API serves
        _setup_artifacts_ctx(ctx, project_id)

        # Emit initial UI hints (todos) + status
        todos = (manifest.get("events") or {}).get("todos") or []
        if isinstance(todos, list) and todos:
            ctx.emit("todo_set", {"items": todos})
        ctx.emit("status", {"state": "running"})

        # Build the ToolRouter (ops registry)
        tr = build_toolrouter()

        # Execute the flow
        runner = DSLRunner(tr, ctx)
        try:
            outputs = runner.run(flow, inputs)
            ctx.emit("status", {"state": "succeeded"})
            return outputs or {}
        except ProblemDetails as e:
            ctx.emit("status", {"state": "failed", "error": e.to_dict()})
            raise
        except Exception as e:
            ctx.emit("status", {"state": "failed", "error": {"title": "Unhandled", "detail": str(e)}})
            raise

# # src/app/kernel/service_runner.py
# from __future__ import annotations

# import os
# import uuid
# from pathlib import Path
# from typing import Any, Dict, Optional, Set, Tuple
# from types import SimpleNamespace

# from app.core.config import settings

# from .manifest import load_plugin_snapshot
# from .dsl_runner import DSLRunner
# from .bootstrap_toolrouter import build_toolrouter
# from .context import ExecutionContext
# from .events import EventEmitter
# from .errors import ProblemDetails


# # ---------------------------------------------------------------------------
# # DSL preflight validators (catch common flow.yaml mistakes early and clearly)
# # ---------------------------------------------------------------------------

# def _validate_flow_steps(flow: Dict[str, Any]) -> None:
#     """
#     Validate basic structure of the DSL flow before execution.
#     Accepts either legacy ('steps' + 'run') or new style ('nodes' + 'op').
#     """
#     if not isinstance(flow, dict):
#         raise ValueError("Flow must be a mapping/dict at the top level")

#     steps = flow.get("steps")
#     nodes = flow.get("nodes")

#     if isinstance(steps, list) and steps:
#         for i, st in enumerate(steps, start=1):
#             if not isinstance(st, dict):
#                 raise ValueError(f"Flow step #{i}: must be a mapping/dict, got {type(st).__name__}")
#             if "run" not in st and "op" not in st:
#                 raise ValueError(f"Flow step #{i}: missing required key 'run' (or 'op')")
#             args = st.get("args")
#             if args is None:
#                 continue
#             if isinstance(args, list):
#                 raise ValueError(
#                     f"Flow step #{i} '{st.get('run') or st.get('op')}': 'args' must be a mapping/dict, not a YAML list.\n"
#                     f"Example of correct form:\n"
#                     f"  args:\n"
#                     f"    key: value\n"
#                     f"(not)\n"
#                     f"  args:\n"
#                     f"    - key: value"
#                 )
#             if not isinstance(args, dict):
#                 raise ValueError(
#                     f"Flow step #{i} '{st.get('run') or st.get('op')}': 'args' must be a mapping/dict, got {type(args).__name__}"
#                 )
#         return

#     if isinstance(nodes, list) and nodes:
#         for i, nd in enumerate(nodes, start=1):
#             if not isinstance(nd, dict):
#                 raise ValueError(f"Flow node #{i}: must be a mapping/dict, got {type(nd).__name__}")
#             if "op" not in nd and "run" not in nd:
#                 raise ValueError(f"Flow node #{i}: missing required key 'op' (or 'run')")
#         return

#     raise ValueError("Flow: must contain a non-empty 'steps' or 'nodes' list")


# def _env_bool(name: str, default: bool = False) -> bool:
#     v = os.environ.get(name)
#     if v is None:
#         return default
#     return str(v).strip().lower() in ("1", "true", "yes", "on")


# # ---------------------------------------------------------------------------
# # Artifacts context helpers — single source of truth for FS + URLs
# # ---------------------------------------------------------------------------

# def _setup_artifacts_ctx(ctx, project_id: str) -> None:
#     """
#     Attach storage + URL helpers to ctx, all pointing to settings.ARTIFACTS_DIR.
#     Everything becomes project-scoped and consistent with the API's /artifacts mount.
#     """
#     ARTIFACTS_ROOT = Path(settings.ARTIFACTS_DIR).expanduser().resolve()
#     project_dir = (ARTIFACTS_ROOT / project_id).resolve()
#     project_dir.mkdir(parents=True, exist_ok=True)

#     # Expose a storage handle for older code paths
#     ctx.storage = getattr(ctx, "storage", None) or SimpleNamespace()
#     ctx.storage.root = str(ARTIFACTS_ROOT)

#     # Path getters (project-scoped). Return Path for operator "/" compatibility.
#     def artifacts_dir(*parts: Any) -> Path:
#         if parts:
#             segs = [str(p).lstrip("/") for p in parts if str(p)]
#             p = project_dir.joinpath(*segs)
#             p.mkdir(parents=True, exist_ok=True)
#             return p
#         return project_dir

#     # String variant for callers that want a str
#     def artifacts_dir_str(*parts: Any) -> str:
#         return artifacts_dir(*parts).as_posix()

#     # Also expose a Path-returning alias (kept for clarity)
#     def artifacts_path(*parts: Any) -> Path:
#         return artifacts_dir(*parts)
    

#         # File IO (project-relative) — ATOMIC WRITE
#     def write_text(rel: Any, text: str) -> str:
#         rel_path = rel if isinstance(rel, Path) else Path(str(rel))
#         rel_s = rel_path.as_posix().lstrip("/")
#         target = project_dir / rel_s
#         target.parent.mkdir(parents=True, exist_ok=True)

#         # write to a temp file in the same directory, then replace
#         tmp = target.with_suffix(target.suffix + ".tmp")
#         tmp.write_text(text, encoding="utf-8")
#         # ensure bytes are flushed to disk before replace (best-effort)
#         try:
#             with open(tmp, "rb") as fh:
#                 os.fsync(fh.fileno())
#         except Exception:
#             pass
#         os.replace(tmp, target)  # atomic on the same filesystem
#         return target.as_posix()


#     # # File IO (project-relative)
#     # def write_text(rel: Any, text: str) -> str:
#     #     rel_path = rel if isinstance(rel, Path) else Path(str(rel))
#     #     rel_s = rel_path.as_posix().lstrip("/")
#     #     p = project_dir / rel_s
#     #     p.parent.mkdir(parents=True, exist_ok=True)
#     #     p.write_text(text, encoding="utf-8")
#     #     return p.as_posix()

#     def read_text(rel: Any) -> str:
#         rel_path = rel if isinstance(rel, Path) else Path(str(rel))
#         rel_s = rel_path.as_posix().lstrip("/")
#         p = project_dir / rel_s
#         return p.read_text(encoding="utf-8") if p.exists() else ""

#     # URL builder — accepts absolute FS paths (under project_dir), '/artifacts/...', or project-relative paths
#     def url_for(target: Any) -> str:
#         # Normalize to string, but preserve Path semantics where relevant
#         if isinstance(target, Path):
#             # If absolute, try to make it project-relative
#             if target.is_absolute():
#                 try:
#                     s = target.relative_to(project_dir).as_posix()
#                 except Exception:
#                     s = target.as_posix()
#             else:
#                 s = target.as_posix()
#         else:
#             s = str(target).strip()
#             try:
#                 p = Path(s)
#                 if p.is_absolute():
#                     try:
#                         s = p.relative_to(project_dir).as_posix()
#                     except Exception:
#                         s = p.as_posix()
#             except Exception:
#                 pass

#         # If caller passed '/artifacts/...', strip prefix and any duplicated project id
#         if s.startswith("/artifacts/"):
#             s = s[len("/artifacts/"):]
#         s = s.lstrip("/")

#         # Drop a leading '<project_id>/' if present to avoid duplication
#         if s.startswith(f"{project_id}/"):
#             s = s[len(project_id) + 1:]

#         # Guard: default to state.json if empty
#         if not s:
#             s = "state.json"

#         return f"/artifacts/{project_id}/{s}"

#     # Bind to ctx
#     ctx.project_id = project_id
#     ctx.artifacts_dir = artifacts_dir           # returns Path
#     ctx.artifacts_dir_str = artifacts_dir_str   # returns str
#     ctx.artifacts_path = artifacts_path         # returns Path
#     ctx.write_text = write_text
#     ctx.read_text = read_text
#     ctx.url_for = url_for
#     ctx.url_for_item = url_for


# # ---------------------------------------------------------------------------
# # ServiceRunner
# # ---------------------------------------------------------------------------

# class ServiceRunner:
#     def __init__(self, plugins_root: Path):
#         self.plugins_root = plugins_root

#     def _load(self, service_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str], Path]:
#         plugin_dir = self.plugins_root / service_id
#         manifest, flow, perms = load_plugin_snapshot(plugin_dir)
#         # Preflight validation of the DSL flow for clear error messages
#         _validate_flow_steps(flow)
#         return manifest, flow, perms, plugin_dir

#     def run(
#         self,
#         service_id: str,
#         inputs: Dict[str, Any],
#         on_event=None,
#         loop: Optional[object] = None
#     ) -> Dict[str, Any]:
#         """
#         Execute a service plugin:
#           - Loads manifest/flow
#           - Validates the flow shape
#           - Builds ToolRouter
#           - Runs DSL with an ExecutionContext that emits SSE-compatible events
#         Returns the 'outputs' dict from the DSL runner.
#         """
#         manifest, flow, perms, _plugin_dir = self._load(service_id)

#         # Ensure identifiers
#         job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
#         project_id = inputs.get("project_id") or inputs.get("projectId") or inputs.get("project") or f"prj_{uuid.uuid4().hex[:8]}"
#         inputs["project_id"] = project_id  # reflect synthesized project_id

#         # Build event emitter
#         emitter = EventEmitter(on_emit=on_event, loop=loop)

#         # Context for ops
#         ctx = ExecutionContext(
#             job_id=job_id,
#             project_id=project_id,
#             permissions=set(perms),
#             env={
#                 "DEV_OFFLINE": _env_bool("DEV_OFFLINE", True),
#                 "ARTIFACTS_DIR": settings.ARTIFACTS_DIR,
#             },
#             emitter=emitter,
#         )

#         # Normalize FS + URL helpers so every op reads/writes the SAME place the API serves
#         _setup_artifacts_ctx(ctx, project_id)

#         # Emit initial UI hints (todos) + status
#         todos = (manifest.get("events") or {}).get("todos") or []
#         if isinstance(todos, list) and todos:
#             ctx.emit("todo_set", {"items": todos})
#         ctx.emit("status", {"state": "running"})

#         # Build the ToolRouter (ops registry)
#         tr = build_toolrouter()

#         # Execute the flow
#         runner = DSLRunner(tr, ctx)
#         try:
#             outputs = runner.run(flow, inputs)
#             ctx.emit("status", {"state": "succeeded"})
#             return outputs or {}
#         except ProblemDetails as e:
#             ctx.emit("status", {"state": "failed", "error": e.to_dict()})
#             raise
#         except Exception as e:
#             ctx.emit("status", {"state": "failed", "error": {"title": "Unhandled", "detail": str(e)}})
#             raise
