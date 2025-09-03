# src/app/core/ctx.py
from __future__ import annotations
import contextvars
from typing import Optional, Mapping

_tenant_id = contextvars.ContextVar("tenant_id", default=None)
_run_id    = contextvars.ContextVar("run_id",    default=None)
_plan_id   = contextvars.ContextVar("plan_id",   default=None)
_node_id   = contextvars.ContextVar("node_id",   default=None)
_trace_id  = contextvars.ContextVar("trace_id",  default=None)

def set_ctx(*, tenant_id: Optional[str]=None, run_id: Optional[str]=None,
            plan_id: Optional[str]=None, node_id: Optional[str]=None,
            trace_id: Optional[str]=None) -> None:
    if tenant_id is not None: _tenant_id.set(tenant_id)
    if run_id is not None:    _run_id.set(run_id)
    if plan_id is not None:   _plan_id.set(plan_id)
    if node_id is not None:   _node_id.set(node_id)
    if trace_id is not None:  _trace_id.set(trace_id)

def get_ctx() -> Mapping[str, Optional[str]]:
    return {
        "tenant_id": _tenant_id.get(),
        "run_id":    _run_id.get(),
        "plan_id":   _plan_id.get(),
        "node_id":   _node_id.get(),
        "trace_id":  _trace_id.get(),
    }
