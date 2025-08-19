from __future__ import annotations
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple, Optional
from .manifest import load_plugin_snapshot
from .dsl_runner import DSLRunner
from .bootstrap_toolrouter import build_toolrouter
from .context import ExecutionContext
from .events import EventEmitter

class ServiceRunner:
    def __init__(self, plugins_root: Path):
        self.plugins_root = plugins_root

    def _load(self, service_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], set, Path]:
        plugin_dir = self.plugins_root / service_id
        manifest, flow, perms = load_plugin_snapshot(plugin_dir)
        return manifest, flow, perms, plugin_dir

    def run(self, service_id: str, inputs: Dict[str, Any], on_event=None, loop: Optional[object] = None) -> Dict[str, Any]:
        manifest, flow, perms, _ = self._load(service_id)

        job_id = inputs.get("_job_id") or f"job_{uuid.uuid4().hex[:8]}"
        project_id = inputs.get("project_id") or f"prj_{uuid.uuid4().hex[:8]}"

        emitter = EventEmitter(on_emit=on_event, loop=loop)
        ctx = ExecutionContext(
            job_id=job_id,
            project_id=project_id,
            permissions=set(perms),
            env={"DEV_OFFLINE": True},
            emitter=emitter,
        )

        # Emit todos + start status
        todos = (manifest.get("events") or {}).get("todos") or []
        if todos:
            ctx.emit("todo_set", {"items": todos})
        ctx.emit("status", {"state": "running"})

        tr = build_toolrouter()

        # Hook url_for for DSL helper (optional sugar)
        def url_for_item(p):
            from . import storage
            from pathlib import Path as _P
            return storage.url_for(_P(str(p)))
        setattr(ctx, "url_for_item", url_for_item)

        runner = DSLRunner(tr, ctx)
        try:
            outputs = runner.run(flow, inputs)
            ctx.emit("status", {"state": "succeeded"})
            return outputs
        except Exception as e:
            try:
                from .errors import ProblemDetails
                if isinstance(e, ProblemDetails):
                    ctx.emit("status", {"state": "failed", "error": e.to_dict()})
                    raise
            finally:
                ctx.emit("status", {"state": "failed"})
            raise
