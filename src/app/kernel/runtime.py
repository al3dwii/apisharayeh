import os
from importlib import import_module
from app.kernel.dsl import run_dsl_flow

def plugin_file_path(manifest, name):
    base = os.getenv("PLUGINS_DIR", "plugins")
    return os.path.join(base, manifest.id.replace(".", "/"), name)

class KernelContext:
    def __init__(self, tenant_id, job_id, events, artifacts, models, tools):
        self.tenant_id = tenant_id
        self.job_id = job_id
        self.events = events
        self.artifacts = artifacts
        self.models = models
        self.tools = tools

def run_service(manifest, inputs, ctx: KernelContext):
    if manifest.runtime == "dsl":
        return run_dsl_flow(plugin_file_path(manifest, "flow.yaml"), inputs, ctx)
    if manifest.runtime == "inproc":
        module, func = manifest.entrypoint.split(":")
        runner = getattr(import_module(module), func)
        return runner(inputs, ctx)
    if manifest.runtime == "http":
        raise NotImplementedError("http runtime not wired yet")
    if manifest.runtime == "grpc":
        raise NotImplementedError("grpc runtime not wired yet")
    raise ValueError(f"Unknown runtime {manifest.runtime}")
