from __future__ import annotations
from typing import Callable, Dict, Any, Set, Optional
from dataclasses import dataclass
from .errors import ProblemDetails


@dataclass
class OpSpec:
    func: Callable
    name: str
    required_permissions: Set[str]


class ToolRouter:
    def __init__(self):
        self._registry: Dict[str, OpSpec] = {}

    def register(self, name: str, func: Callable, required_permissions: Optional[Set[str]] = None):
        if required_permissions is None:
            required_permissions = set()
        self._registry[name] = OpSpec(func=func, name=name, required_permissions=required_permissions)

    def ensure_permissions(self, plugin_permissions: Set[str], spec: OpSpec, op_name: str):
        missing = spec.required_permissions - plugin_permissions
        if missing:
            raise ProblemDetails(
                title="Permission denied",
                detail=f"Operation '{op_name}' requires permissions: {sorted(missing)}",
                code="E_PERM",
                op=op_name,
                status=403,
            )

    def call(self, op_name: str, ctx, **inputs) -> Dict[str, Any]:
        spec = self._registry.get(op_name)
        if not spec:
            raise ProblemDetails(
                title="Unknown operation",
                detail=f"Operation '{op_name}' is not registered",
                code="E_UNKNOWN_OP",
                op=op_name,
                status=400,
            )

        # Permission guard (ctx.permissions comes from manifest snapshot)
        self.ensure_permissions(ctx.permissions, spec, op_name)

        # Emit step started + tool_used
        ctx.emit("step", {"name": op_name, "status": "started"})
        ctx.emit("tool_used", {"name": op_name, "args": scrub_args(inputs)})

        try:
            result = spec.func(ctx, **inputs) or {}
            # Emit success
            ctx.emit("step", {"name": op_name, "status": "succeeded"})
            return result
        except ProblemDetails:
            # Pass through known problem
            ctx.emit("step", {"name": op_name, "status": "failed"})
            raise
        except Exception as e:
            ctx.emit("step", {"name": op_name, "status": "failed"})
            raise ProblemDetails(
                title="Unhandled op exception",
                detail=str(e),
                code="E_OP_EXCEPTION",
                op=op_name,
                status=500,
            )

def scrub_args(args: Dict[str, Any]) -> Dict[str, Any]:
    # Avoid dumping large blobs; strip binary/text bodies
    redacted = {}
    for k, v in args.items():
        if isinstance(v, (bytes, bytearray)):
            redacted[k] = f"<{len(v)} bytes>"
        else:
            redacted[k] = v
    return redacted
