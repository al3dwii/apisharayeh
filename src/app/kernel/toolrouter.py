from __future__ import annotations

import inspect
from typing import Callable, Dict, Any, Optional, Set

from .errors import ProblemDetails


class ToolRouter:
    """
    Minimal op registry + invoker used by the DSLRunner.

    - register(name, func, required_permissions?)
    - call(name, ctx, **kwargs) -> Dict[str, Any]
    - list() -> [names]
    - has(name) -> bool
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable[..., Dict[str, Any] | Any]] = {}
        self._perms: Dict[str, Set[str]] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Dict[str, Any] | Any],
        required_permissions: Optional[Set[str]] = None,
    ) -> "ToolRouter":
        if not callable(func):
            raise ValueError(f"Tool '{name}' must be callable")
        self._tools[name] = func

        # Pull required permissions from arg or from function annotation if present
        perms = set(required_permissions or getattr(func, "required_permissions", set()) or set())
        self._perms[name] = perms
        return self

    # Convenience API
    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[str]:
        return sorted(self._tools.keys())

    def _emit(self, ctx, topic: str, payload: Dict[str, Any]) -> None:
        """Emit an event if the context supports it."""
        emit = getattr(ctx, "emit", None)
        if callable(emit):
            try:
                emit(topic, payload)
            except Exception:
                # Emission must never break the op
                pass

    def _call_with_ctx_if_needed(self, fn: Callable, ctx, **kwargs) -> Any:
        """
        Call `fn` with or without ctx depending on its signature.
        Supports:
          def op(ctx, **kwargs) ...
          def op(**kwargs) ...
        """
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            # Builtins or C-extensions: assume they accept ctx first (best effort)
            return fn(ctx, **kwargs)

        params = list(sig.parameters.values())
        pass_ctx = False
        if params:
            p0 = params[0]
            if p0.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                # Heuristic: treat first param named 'ctx' or 'context' as a context param
                if p0.name in ("ctx", "context"):
                    pass_ctx = True

        if pass_ctx:
            return fn(ctx, **kwargs)
        else:
            return fn(**kwargs)

    def call(self, name: str, ctx, **kwargs) -> Dict[str, Any]:
        """
        Invoke a registered op:
        - Emits step started/succeeded/failed events
        - Enforces required permissions (if any)
        - Returns a dict (empty dict if tool returns None)
        """
        if name not in self._tools:
            raise ProblemDetails(
                title="Unknown op",
                detail=f"'{name}' is not registered",
                code="E_OP_UNKNOWN",
                status=400,
            )

        # Permission check (ops can double-check internally too)
        required = self._perms.get(name, set())
        ctx_perms = set(getattr(ctx, "permissions", set()) or set())
        if required and not required.issubset(ctx_perms):
            missing = sorted(required - ctx_perms)
            raise ProblemDetails(
                title="Permission denied",
                detail=f"'{name}' requires: {', '.join(missing)}",
                code="E_PERM",
                status=403,
            )

        fn = self._tools[name]

        # Emit step lifecycle with a tiny bit of context (argument keys only to avoid PII)
        self._emit(ctx, "step", {"name": name, "status": "started", "args": sorted(kwargs.keys())})
        try:
            out = self._call_with_ctx_if_needed(fn, ctx, **kwargs)
            if out is None:
                out = {}
            if not isinstance(out, dict):
                # Normalize unexpected returns
                out = {"result": out}
            self._emit(ctx, "step", {"name": name, "status": "succeeded"})
            return out
        except ProblemDetails:
            self._emit(ctx, "step", {"name": name, "status": "failed"})
            raise
        except Exception as e:
            self._emit(ctx, "step", {"name": name, "status": "failed", "error": str(e)})
            raise
