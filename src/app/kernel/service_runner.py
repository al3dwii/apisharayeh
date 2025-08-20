from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

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
            # If someone still uses historical "args", check shape (modern flows use 'in', 'needs', 'out')
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
        project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"

        # Build event emitter (thread-safe scheduling into FastAPI loop is handled by EventEmitter)
        emitter = EventEmitter(on_emit=on_event, loop=loop)

        # Context for ops
        ctx = ExecutionContext(
            job_id=job_id,
            project_id=project_id,
            permissions=set(perms),
            env={
                "DEV_OFFLINE": _env_bool("DEV_OFFLINE", True),
                "ARTIFACTS_DIR": os.environ.get("ARTIFACTS_DIR", "./artifacts"),
            },
            emitter=emitter,
        )

        # Emit initial UI hints (todos) + status
        todos = (manifest.get("events") or {}).get("todos") or []
        if isinstance(todos, list) and todos:
            ctx.emit("todo_set", {"items": todos})
        ctx.emit("status", {"state": "running"})

        # Build the ToolRouter (ops registry)
        tr = build_toolrouter()

        # Optional sugar: map file path -> public URL (for partial events)
        try:
            artifacts_dir = Path(ctx.env.get("ARTIFACTS_DIR") or "./artifacts")
            ctx.url_for = lambda rel: f"/artifacts/{project_id}/{rel}" if isinstance(rel, str) else rel
            ctx.url_for_item = lambda rel: f"/artifacts/{project_id}/{rel}" if isinstance(rel, str) else rel
        except Exception:
            pass

        # Execute the flow
        runner = DSLRunner(tr, ctx)
        try:
            outputs = runner.run(flow, inputs)
            # Let consumers know we’re done
            ctx.emit("status", {"state": "succeeded"})
            return outputs or {}
        except ProblemDetails as e:
            # ProblemDetails is a "known" error type we want to propagate & emit
            ctx.emit("status", {"state": "failed", "error": e.to_dict()})
            raise
        except Exception as e:
            # Unknown error: still emit a failure state with a compact detail
            ctx.emit("status", {"state": "failed", "error": {"title": "Unhandled", "detail": str(e)}})
            raise


# from __future__ import annotations

# import os
# import uuid
# from pathlib import Path
# from typing import Any, Dict, Optional, Set, Tuple

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
#     Validate basic structure of the DSL flow before execution to surface
#     clear, actionable errors (instead of cryptic attribute errors later).
#     """
#     if not isinstance(flow, dict):
#         raise ValueError("Flow must be a mapping/dict at the top level")

#     steps = flow.get("steps")
#     if not isinstance(steps, list) or not steps:
#         raise ValueError("Flow: 'steps' must be a non-empty list")

#     for i, st in enumerate(steps, start=1):
#         if not isinstance(st, dict):
#             raise ValueError(f"Flow step #{i}: must be a mapping/dict, got {type(st).__name__}")
#         if "run" not in st:
#             raise ValueError(f"Flow step #{i}: missing required key 'run'")

#         # The most common shape error: args provided as YAML *list* instead of dict
#         args = st.get("args")
#         if args is None:
#             continue
#         if isinstance(args, list):
#             raise ValueError(
#                 f"Flow step #{i} '{st.get('run')}': 'args' must be a mapping/dict, not a YAML list.\n"
#                 f"Example of correct form:\n"
#                 f"  args:\n"
#                 f"    key: value\n"
#                 f"(not)\n"
#                 f"  args:\n"
#                 f"    - key: value"
#             )
#         if not isinstance(args, dict):
#             raise ValueError(
#                 f"Flow step #{i} '{st.get('run')}': 'args' must be a mapping/dict, got {type(args).__name__}"
#             )


# def _env_bool(name: str, default: bool = False) -> bool:
#     v = os.environ.get(name)
#     if v is None:
#         return default
#     return str(v).strip().lower() in ("1", "true", "yes", "on")


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
#         project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"

#         # Build event emitter (thread-safe scheduling into FastAPI loop is handled by EventEmitter)
#         emitter = EventEmitter(on_emit=on_event, loop=loop)

#         # Context for ops
#         ctx = ExecutionContext(
#             job_id=job_id,
#             project_id=project_id,
#             permissions=set(perms),
#             env={
#                 "DEV_OFFLINE": _env_bool("DEV_OFFLINE", True),
#                 "ARTIFACTS_DIR": os.environ.get("ARTIFACTS_DIR", "./artifacts"),
#             },
#             emitter=emitter,
#         )

#         # Emit initial UI hints (todos) + status
#         todos = (manifest.get("events") or {}).get("todos") or []
#         if isinstance(todos, list) and todos:
#             ctx.emit("todo_set", {"items": todos})
#         ctx.emit("status", {"state": "running"})

#         # Build the ToolRouter (ops registry)
#         tr = build_toolrouter()

#         # Optional sugar: map file path -> public URL (for partial events)
#         def url_for_item(p):
#             from . import storage
#             from pathlib import Path as _P
#             return storage.url_for(_P(str(p)))
#         setattr(ctx, "url_for_item", url_for_item)

#         # Run the DSL
#         runner = DSLRunner(tr, ctx)
#         try:
#             outputs = runner.run(flow, inputs)
#             # Let consumers know we’re done
#             ctx.emit("status", {"state": "succeeded"})
#             return outputs or {}
#         except ProblemDetails as e:
#             # ProblemDetails is a "known" error type we want to propagate & emit
#             ctx.emit("status", {"state": "failed", "error": e.to_dict()})
#             raise
#         except Exception as e:
#             # Unknown error: still emit a failure state with a compact detail
#             ctx.emit("status", {"state": "failed", "error": {"title": "Unhandled", "detail": str(e)}})
#             raise
