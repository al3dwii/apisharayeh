import os, glob, yaml, threading, time
from jsonschema import Draft202012Validator as DraftValidator
from .spec import ServiceManifest

PLUGINS_DIR = os.getenv("PLUGINS_DIR", "plugins")
SERVICE_FLAGS = set((os.getenv("SERVICE_FLAGS") or "*").split(","))

class PluginRegistry:
    def __init__(self):
        self.services: dict[str, ServiceManifest] = {}
        self.validators: dict[str, DraftValidator] = {}
        self._lock = threading.RLock()

    def _allowed(self, sid: str) -> bool:
        return "*" in SERVICE_FLAGS or sid in SERVICE_FLAGS

    def refresh(self):
        services, validators = {}, {}
        for path in glob.glob(f"{PLUGINS_DIR}/**/manifest.yaml", recursive=True):
            with open(path, "r", encoding="utf-8") as f:
                mf = ServiceManifest.model_validate(yaml.safe_load(f))
            if self._allowed(mf.id):
                services[mf.id] = mf
                validators[mf.id] = DraftValidator(mf.inputs or {"type":"object"})
        with self._lock:
            self.services, self.validators = services, validators

    def list(self):
        with self._lock:
            return list(self.services.values())

    def get(self, sid: str) -> ServiceManifest:
        with self._lock:
            mf = self.services.get(sid)
            if not mf:
                raise KeyError(f"Unknown service '{sid}'")
            return mf

    def validate_inputs(self, sid: str, payload: dict):
        with self._lock:
            v = self.validators.get(sid)
        if not v:
            raise KeyError(f"No validator for '{sid}'")
        v.validate(payload or {})

registry = PluginRegistry()

def start_watcher(interval=2.0):
    def loop():
        prev = {}
        while True:
            snapshot = {p: os.path.getmtime(p) for p in glob.glob(f"{PLUGINS_DIR}/**/*", recursive=True)}
            if snapshot != prev:
                registry.refresh()
                prev = snapshot
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
