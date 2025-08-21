import os
import logging
from importlib import import_module
from pathlib import Path
from typing import Any, Optional

from app.kernel.dsl import run_dsl_flow

log = logging.getLogger(__name__)


def _plugins_root() -> Path:
    """
    Resolve the plugins root directory.
    Defaults to ./plugins but honors PLUGINS_DIR if set.
    """
    root = os.getenv("PLUGINS_DIR", "plugins")
    return Path(root).resolve()


def plugin_file_path(manifest: Any, name: str) -> Path:
    """
    Build an on-disk path for a file inside a plugin.
    For example, service id 'slides.generate' + 'flow.yaml' =>
      <PLUGINS_DIR>/slides/generate/flow.yaml
    """
    service_id = str(getattr(manifest, "id", "")).strip()
    if not service_id:
        raise ValueError("manifest.id is required to resolve plugin paths")
    rel = service_id.replace(".", "/")
    return (_plugins_root() / rel / name).resolve()


class KernelContext:
    """
    Context object passed to service runners.
    """
    def __init__(self, tenant_id: str, job_id: str, events, artifacts, models, tools) -> None:
        self.tenant_id = tenant_id
        self.job_id = job_id
        self.events = events
        self.artifacts = artifacts
        self.models = models
        self.tools = tools


def run_service(manifest: Any, inputs: Optional[dict], ctx: KernelContext):
    """
    Dispatch based on manifest.runtime:
      - "dsl": load flow.yaml and execute the DSL runner
      - "inproc": import and call the entrypoint function "pkg.mod:func"
      - "http"/"grpc": not implemented in this codebase
    """
    runtime = getattr(manifest, "runtime", None)
    inputs = inputs or {}

    if runtime == "dsl":
        flow_path = plugin_file_path(manifest, "flow.yaml")
        if not flow_path.exists():
            raise FileNotFoundError(
                f"DSL flow missing for service '{manifest.id}': {flow_path}"
            )
        log.info(
            "Running DSL service id=%s flow=%s input_keys=%s",
            getattr(manifest, "id", "?"),
            str(flow_path),
            list(inputs.keys()),
        )
        # run_dsl_flow expects a string path
        return run_dsl_flow(str(flow_path), inputs, ctx)

    if runtime == "inproc":
        entry = getattr(manifest, "entrypoint", None)
        if isinstance(entry, dict):
            # allow manifests that use structured entrypoint definitions (optional)
            entry = entry.get("value") or entry.get("path") or entry.get("target")
        if not entry or not isinstance(entry, str) or ":" not in entry:
            raise ValueError(
                f"Invalid or missing entrypoint for inproc service '{manifest.id}': {entry}"
            )
        module_name, func_name = entry.split(":", 1)
        module = import_module(module_name)
        runner = getattr(module, func_name)
        log.info(
            "Running inproc service id=%s entry=%s inputs=%s",
            getattr(manifest, "id", "?"),
            entry,
            list(inputs.keys()),
        )
        return runner(inputs, ctx)

    if runtime == "http":
        raise NotImplementedError("http runtime not wired yet")

    if runtime == "grpc":
        raise NotImplementedError("grpc runtime not wired yet")

    raise ValueError(f"Unknown runtime '{runtime}' for service '{getattr(manifest, 'id', '?')}'")
