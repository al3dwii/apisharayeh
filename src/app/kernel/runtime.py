# src/app/kernel/runtime.py
"""
Runtime helpers to execute a plugin service (DSL / in-proc).
- Resolves plugin files from PLUGINS_DIR without altering service IDs.
- Provides a small KernelContext container passed to runners.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from app.kernel.dsl import run_dsl_flow
from app.kernel.plugins.loader import _plugins_dir
from app.kernel.plugins.spec import ServiceManifest


# --------------------------- path resolution helpers ---------------------------

def plugin_root_dir(manifest: ServiceManifest) -> Path:
    """
    Directory on disk for this plugin.

    IMPORTANT: directory name == manifest.id exactly.
    e.g., PLUGINS_DIR/slides.generate  (not 'slides/generate')
    """
    return Path(_plugins_dir()) / manifest.id


def plugin_file_path(manifest: ServiceManifest, *parts: str) -> str:
    """Join the plugin root with relative parts and return as str."""
    return str(plugin_root_dir(manifest).joinpath(*parts))


def _resolve_dsl_flow_path(manifest: ServiceManifest) -> Path:
    """
    Determine which YAML to run for DSL plugins.
    Accepts:
      - manifest.entrypoint: "flow.yaml"
      - manifest.entrypoint: {"type":"dsl","path":"flow.yaml"}
      - or falls back to "flow.yaml"
    """
    name: str = "flow.yaml"
    ep = getattr(manifest, "entrypoint", None)

    if isinstance(ep, str) and (ep.endswith(".yaml") or ep.endswith(".yml")):
        name = ep.strip()
    elif isinstance(ep, dict):
        # tolerate {"type":"dsl","path":"..."}
        p = ep.get("path")
        if isinstance(p, str) and (p.endswith(".yaml") or p.endswith(".yml")):
            name = p.strip()

    p = Path(plugin_file_path(manifest, name))
    if not p.exists():
        raise FileNotFoundError(
            f"DSL flow not found: {p} "
            f"(PLUGINS_DIR={_plugins_dir()}, service_id={manifest.id}, entrypoint={ep})"
        )
    return p


def _resolve_inproc_target(manifest: ServiceManifest) -> Tuple[str, str]:
    """
    Resolve module:function for in-process runners.
    Supports:
      - entrypoint: "pkg.mod:run"
      - entrypoint: {"type":"inproc","target":"pkg.mod:run"}  (or {"module": "...", "func":"..."})
    """
    ep = getattr(manifest, "entrypoint", None)

    if isinstance(ep, str) and ":" in ep:
        module, func = ep.split(":", 1)
        return module.strip(), func.strip()

    if isinstance(ep, dict):
        target = ep.get("target")
        if isinstance(target, str) and ":" in target:
            module, func = target.split(":", 1)
            return module.strip(), func.strip()
        # alt shape
        module = ep.get("module")
        func = ep.get("func")
        if isinstance(module, str) and isinstance(func, str):
            return module.strip(), func.strip()

    raise ValueError(
        f"Invalid inproc entrypoint for service '{manifest.id}': {ep!r}. "
        f"Expected 'module:func' or a dict with 'target' or ('module','func')."
    )


# --------------------------------- context ------------------------------------

@dataclass
class KernelContext:
    tenant_id: str
    job_id: str
    events: Any
    artifacts: Any
    models: Any
    tools: Any


# --------------------------------- runtime ------------------------------------

def run_service(manifest: ServiceManifest, inputs: Dict[str, Any], ctx: KernelContext) -> Any:
    """
    Dispatch to the correct runtime based on manifest.runtime.
    """
    runtime = (manifest.runtime or "dsl").lower()

    if runtime == "dsl":
        flow_path = _resolve_dsl_flow_path(manifest)
        return run_dsl_flow(str(flow_path), inputs or {}, ctx)

    if runtime == "inproc":
        module_name, func_name = _resolve_inproc_target(manifest)
        module = import_module(module_name)
        runner: Optional[Callable[[Dict[str, Any], KernelContext], Any]] = getattr(module, func_name, None)  # type: ignore[assignment]
        if not callable(runner):
            raise AttributeError(
                f"Inproc runner not found or not callable: {module_name}:{func_name}"
            )
        return runner(inputs or {}, ctx)

    if runtime == "http":
        raise NotImplementedError("HTTP runtime not wired yet")

    if runtime == "grpc":
        raise NotImplementedError("gRPC runtime not wired yet")

    raise ValueError(f"Unknown runtime '{manifest.runtime}' for service '{manifest.id}'")
