# src/app/kernel/plugins/loader.py

import os
import glob
import time
import threading
import logging
from typing import Dict, Optional

import yaml
from jsonschema import Draft202012Validator as DraftValidator

from .spec import ServiceManifest

logger = logging.getLogger(__name__)


def _plugins_dir() -> str:
    """
    Resolve the plugins directory from env or settings (if available),
    falling back to 'plugins' at repo root.
    """
    # Prefer explicit env
    env_dir = os.getenv("PLUGINS_DIR")
    if env_dir:
        return env_dir

    # Lazy import to avoid circular imports on app startup
    try:
        from app.core.config import settings  # type: ignore
        return getattr(settings, "PLUGINS_DIR", "plugins") or "plugins"
    except Exception:
        return "plugins"


def _service_flags() -> set[str]:
    """
    Comma-separated allowlist in env or settings (e.g., "*,office.word_to_pptx").
    '*' means allow all.
    """
    raw = os.getenv("SERVICE_FLAGS")
    if not raw:
        try:
            from app.core.config import settings  # type: ignore
            raw = getattr(settings, "SERVICE_FLAGS", "*")
        except Exception:
            raw = "*"
    return set((raw or "*").split(","))


class PluginRegistry:
    """
    Minimal in-process plugin registry:
      - Loads manifests from PLUGINS_DIR/**/manifest.yaml
      - Validates into ServiceManifest
      - Keeps a jsonschema validator for each service's 'inputs' schema
    """

    def __init__(self) -> None:
        self.services: Dict[str, ServiceManifest] = {}
        self.validators: Dict[str, DraftValidator] = {}
        self._lock = threading.RLock()

    # ---------- helpers ----------

    def _allowed(self, sid: str) -> bool:
        flags = _service_flags()
        return "*" in flags or sid in flags

    # ---------- public API ----------

    def refresh(self) -> None:
        """
        Scan the plugins directory and rebuild the in-memory registry.
        Silently skips malformed manifests (logs at DEBUG/WARN).
        """
        base = _plugins_dir()
        pattern = os.path.join(base, "**", "manifest.yaml")
        paths = glob.glob(pattern, recursive=True)

        services: Dict[str, ServiceManifest] = {}
        validators: Dict[str, DraftValidator] = {}

        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                mf = ServiceManifest.model_validate(data)
            except Exception as e:
                logger.warning("Skipping invalid manifest %s: %s", path, e)
                continue

            if not self._allowed(mf.id):
                logger.debug("Service '%s' filtered by flags", mf.id)
                continue

            services[mf.id] = mf
            try:
                schema = mf.inputs or {"type": "object"}
                validators[mf.id] = DraftValidator(schema)
            except Exception as e:
                logger.warning("Inputs schema invalid for %s: %s", mf.id, e)
                # Fallback to permissive validator
                validators[mf.id] = DraftValidator({"type": "object"})

        with self._lock:
            self.services = services
            self.validators = validators

        logger.info("Plugin registry refreshed: %d service(s) loaded from %s", len(services), base)

    def list(self) -> list[ServiceManifest]:
        with self._lock:
            return list(self.services.values())

    def get(self, service_id: str) -> ServiceManifest:
        with self._lock:
            mf: Optional[ServiceManifest] = self.services.get(service_id)
            if not mf:
                raise KeyError(f"Unknown service '{service_id}'")
            return mf

    def validate_inputs(self, service_id: str, payload: dict) -> None:
        with self._lock:
            v = self.validators.get(service_id)
        if not v:
            raise KeyError(f"No validator for '{service_id}'")
        v.validate(payload or {})


# Singleton registry instance
registry = PluginRegistry()


def start_watcher(interval: float = 2.0) -> None:
    """
    Start a background thread that refreshes the registry when any file
    under PLUGINS_DIR changes (by mtime snapshot). Safe to call multiple times.
    """

    def _snapshot(root: str) -> Dict[str, float]:
        files = glob.glob(os.path.join(root, "**", "*"), recursive=True)
        return {p: os.path.getmtime(p) for p in files if os.path.isfile(p)}

    def loop() -> None:
        base = _plugins_dir()
        if not os.path.isdir(base):
            logger.warning("Plugins directory %s does not exist; watcher running anyway", base)
        prev: Dict[str, float] = {}
        while True:
            try:
                snap = _snapshot(base)
                if snap != prev:
                    registry.refresh()
                    prev = snap
            except Exception as e:
                logger.error("Plugin watcher error: %s", e)
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="plugin-registry-watcher")
    t.start()
