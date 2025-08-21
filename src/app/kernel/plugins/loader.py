# src/app/kernel/plugins/loader.py
from __future__ import annotations

import glob
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from jsonschema import Draft202012Validator as DraftValidator

from .spec import ServiceManifest

logger = logging.getLogger(__name__)


# -------------------------
# Configuration helpers
# -------------------------

def _plugins_dir() -> Path:
    """
    Resolve the plugins directory with robust fallbacks, always returning an absolute Path.

    Precedence:
      1) $PLUGINS_DIR (if set)
      2) settings.PLUGINS_DIR (if set AND exists)
      3) ./plugins (cwd)           (if exists)
      4) nearest ancestor that has a 'plugins' folder (relative to this file)
      5) ./plugins (even if missing, as a last resort)
    """
    # 1) Explicit env
    env_dir = os.getenv("PLUGINS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser().resolve()
        if not p.exists():
            logger.warning("PLUGINS_DIR=%s does not exist; using it anyway (may be empty).", p)
        return p

    # 2) Settings value, only if it exists
    try:
        from app.core.config import settings  # type: ignore
        settings_dir = getattr(settings, "PLUGINS_DIR", None)
        if settings_dir:
            p = Path(str(settings_dir)).expanduser().resolve()
            if p.exists():
                return p
            else:
                logger.warning("settings.PLUGINS_DIR=%s does not exist; falling back to local plugins/", p)
    except Exception:
        pass

    # 3) ./plugins under CWD
    cwd_plugins = Path("plugins").resolve()
    if cwd_plugins.exists():
        return cwd_plugins

    # 4) Walk up from this file to find a nearest 'plugins' folder
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:8]:
        candidate = parent / "plugins"
        try:
            if candidate.exists():
                return candidate.resolve()
        except Exception:
            continue

    # 5) Last resort: ./plugins (even if missing)
    return cwd_plugins


def _parse_service_flags(raw: Optional[str]) -> List[str]:
    """
    Parse comma-separated allowlist (e.g., "*,office.word_to_pptx" or "*, office.word_to_pptx").
    Returns a list of cleaned tokens; empty items are dropped.
    """
    if not raw:
        return ["*"]
    parts = [p.strip() for p in str(raw).split(",")]
    return [p for p in parts if p]


def _service_flags() -> set[str]:
    """
    Comma-separated allowlist from env or settings. '*' means allow all.
    """
    raw = os.getenv("SERVICE_FLAGS")
    if not raw:
        try:
            from app.core.config import settings  # type: ignore
            raw = getattr(settings, "SERVICE_FLAGS", "*")
        except Exception:
            raw = "*"
    return set(_parse_service_flags(raw))


# -------------------------
# Manifest normalization
# -------------------------

def _normalize_permissions(perms: object) -> Dict[str, bool]:
    """
    Accepts:
      - {'fs_read': true, 'fs_write': true}  (preferred)
      - ['fs_read', 'fs_write']              (legacy list)
      - {'required': ['fs_read', 'fs_write']} (legacy dict)
    Returns a mapping[str,bool].
    """
    if isinstance(perms, dict):
        # legacy form {'required': [...]}
        if "required" in perms and isinstance(perms["required"], list):
            return {str(k): True for k in perms["required"]}
        # already correct shape
        return {str(k): bool(v) for k, v in perms.items()}
    if isinstance(perms, list):
        return {str(k): True for k in perms}
    return {}


def _normalize_entrypoint(spec: Dict) -> Tuple[str, str]:
    """
    Accepts:
      - runtime: 'dsl', entrypoint: 'flow.yaml'
      - entrypoint: {'type': 'dsl', 'path': 'flow.yaml'}
    Returns (runtime, entrypoint_path).
    """
    runtime = spec.get("runtime")
    entry = spec.get("entrypoint")
    if isinstance(entry, dict):
        rt = entry.get("type") or runtime or "dsl"
        path = entry.get("path") or "flow.yaml"
        return str(rt), str(path)
    if isinstance(entry, str):
        return str(runtime or "dsl"), entry
    return "dsl", "flow.yaml"


def _normalize_manifest_shape(data: Dict) -> Dict:
    """
    Produce a dict that conforms to ServiceManifest expectations, without mutating 'data' in place.
    """
    out = dict(data or {})
    rt, ep = _normalize_entrypoint(out)
    out["runtime"] = rt
    out["entrypoint"] = ep
    out["permissions"] = _normalize_permissions(out.get("permissions"))
    return out


# -------------------------
# Registry
# -------------------------

class PluginRegistry:
    """
    Minimal in-process plugin registry:
      - Loads manifests from PLUGINS_DIR/**/manifest.yaml|yml
      - Normalizes legacy fields (entrypoint/permissions)
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

    def _glob_manifests(self, base: Path) -> List[Path]:
        # Support both manifest.yaml and manifest.yml
        patterns = [
            str(base / "**" / "manifest.yaml"),
            str(base / "**" / "manifest.yml"),
        ]
        paths: List[Path] = []
        for pat in patterns:
            for p in glob.glob(pat, recursive=True):
                try:
                    paths.append(Path(p))
                except Exception:
                    continue
        # Deduplicate and sort for deterministic order
        uniq = sorted({p.resolve() for p in paths})
        return list(uniq)

    # ---------- public API ----------

    def refresh(self) -> None:
        """
        Scan the plugins directory and rebuild the in-memory registry.
        Silently skips malformed manifests (logs at DEBUG/WARN).
        """
        base = _plugins_dir()
        manifest_paths = self._glob_manifests(base)

        services: Dict[str, ServiceManifest] = {}
        validators: Dict[str, DraftValidator] = {}

        for mpath in manifest_paths:
            try:
                raw = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
                spec = _normalize_manifest_shape(raw)
                mf = ServiceManifest.model_validate(spec)
            except Exception as e:
                logger.warning("Skipping invalid manifest %s: %s", mpath, e)
                continue

            if not self._allowed(mf.id):
                logger.debug("Service '%s' filtered by SERVICE_FLAGS", mf.id)
                continue

            services[mf.id] = mf
            # Build inputs validator (fallback to permissive object)
            try:
                schema = mf.inputs or {"type": "object"}
                validators[mf.id] = DraftValidator(schema)
            except Exception as e:
                logger.warning("Inputs schema invalid for %s: %s (using permissive object)", mf.id, e)
                validators[mf.id] = DraftValidator({"type": "object"})

        with self._lock:
            self.services = services
            self.validators = validators

        logger.info(
            "Plugin registry refreshed: %d service(s) loaded from %s",
            len(services),
            str(base),
        )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Loaded service IDs: %s", ", ".join(sorted(services.keys())))

    def list(self) -> List[ServiceManifest]:
        with self._lock:
            # Return a copy sorted by id for determinism (helps tests)
            return sorted(self.services.values(), key=lambda m: m.id)

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


# Singleton registry instance (imported by tests)
registry = PluginRegistry()


# -------------------------
# Watcher (optional)
# -------------------------

def start_watcher(interval: float = 2.0) -> None:
    """
    Start a background thread that refreshes the registry when any file
    under PLUGINS_DIR changes (by mtime snapshot). Safe to call multiple times.
    """

    def _snapshot(root: Path) -> Dict[str, float]:
        files = glob.glob(str(root / "**" / "*"), recursive=True)
        out: Dict[str, float] = {}
        for p in files:
            try:
                rp = Path(p).resolve()
                if rp.is_file():
                    out[str(rp)] = rp.stat().st_mtime
            except Exception:
                continue
        return out

    def loop() -> None:
        base = _plugins_dir()
        if not base.exists():
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


# # src/app/kernel/plugins/loader.py
# from __future__ import annotations

# import glob
# import logging
# import os
# import threading
# import time
# from pathlib import Path
# from typing import Dict, List, Optional, Tuple

# import yaml
# from jsonschema import Draft202012Validator as DraftValidator

# from .spec import ServiceManifest

# logger = logging.getLogger(__name__)


# # -------------------------
# # Configuration helpers
# # -------------------------

# def _plugins_dir() -> Path:
#     """
#     Resolve the plugins directory from env or settings (if available),
#     falling back to 'plugins' at repo root. Always returns an absolute Path.
#     """
#     # Prefer explicit env
#     env_dir = os.getenv("PLUGINS_DIR")
#     if env_dir:
#         return Path(env_dir).expanduser().resolve()

#     # Lazy import to avoid circular imports on app startup
#     try:
#         from app.core.config import settings  # type: ignore
#         base = getattr(settings, "PLUGINS_DIR", "plugins") or "plugins"
#         return Path(base).expanduser().resolve()
#     except Exception:
#         return Path("plugins").resolve()


# def _parse_service_flags(raw: Optional[str]) -> List[str]:
#     """
#     Parse comma-separated allowlist (e.g., "*,office.word_to_pptx" or "*, office.word_to_pptx").
#     Returns a list of cleaned tokens; empty items are dropped.
#     """
#     if not raw:
#         return ["*"]
#     parts = [p.strip() for p in str(raw).split(",")]
#     return [p for p in parts if p]


# def _service_flags() -> set[str]:
#     """
#     Comma-separated allowlist from env or settings. '*' means allow all.
#     """
#     raw = os.getenv("SERVICE_FLAGS")
#     if not raw:
#         try:
#             from app.core.config import settings  # type: ignore
#             raw = getattr(settings, "SERVICE_FLAGS", "*")
#         except Exception:
#             raw = "*"
#     return set(_parse_service_flags(raw))


# # -------------------------
# # Manifest normalization
# # -------------------------

# def _normalize_permissions(perms: object) -> Dict[str, bool]:
#     """
#     Accepts:
#       - {'fs_read': true, 'fs_write': true}  (preferred)
#       - ['fs_read', 'fs_write']              (legacy list)
#       - {'required': ['fs_read', 'fs_write']} (legacy dict)
#     Returns a mapping[str,bool].
#     """
#     if isinstance(perms, dict):
#         # legacy form {'required': [...]}
#         if "required" in perms and isinstance(perms["required"], list):
#             return {str(k): True for k in perms["required"]}
#         # already correct shape
#         return {str(k): bool(v) for k, v in perms.items()}
#     if isinstance(perms, list):
#         return {str(k): True for k in perms}
#     # default permissive? better be strict and return empty; validator/policy can enforce later
#     return {}


# def _normalize_entrypoint(spec: Dict) -> Tuple[str, str]:
#     """
#     Accepts:
#       - runtime: 'dsl', entrypoint: 'flow.yaml'
#       - entrypoint: {'type': 'dsl', 'path': 'flow.yaml'}
#     Returns (runtime, entrypoint_path).
#     """
#     runtime = spec.get("runtime")
#     entry = spec.get("entrypoint")
#     if isinstance(entry, dict):
#         rt = entry.get("type") or runtime or "dsl"
#         path = entry.get("path") or "flow.yaml"
#         return str(rt), str(path)
#     if isinstance(entry, str):
#         return str(runtime or "dsl"), entry
#     # nothing provided -> defaults
#     return "dsl", "flow.yaml"


# def _normalize_manifest_shape(data: Dict) -> Dict:
#     """
#     Produce a dict that conforms to ServiceManifest expectations, without mutating 'data' in place.
#     """
#     out = dict(data or {})
#     # entrypoint/runtime normalization
#     rt, ep = _normalize_entrypoint(out)
#     out["runtime"] = rt
#     out["entrypoint"] = ep
#     # permissions normalization
#     out["permissions"] = _normalize_permissions(out.get("permissions"))
#     return out


# # -------------------------
# # Registry
# # -------------------------

# class PluginRegistry:
#     """
#     Minimal in-process plugin registry:
#       - Loads manifests from PLUGINS_DIR/**/manifest.yaml|yml
#       - Normalizes legacy fields (entrypoint/permissions)
#       - Validates into ServiceManifest
#       - Keeps a jsonschema validator for each service's 'inputs' schema
#     """

#     def __init__(self) -> None:
#         self.services: Dict[str, ServiceManifest] = {}
#         self.validators: Dict[str, DraftValidator] = {}
#         self._lock = threading.RLock()

#     # ---------- helpers ----------

#     def _allowed(self, sid: str) -> bool:
#         flags = _service_flags()
#         return "*" in flags or sid in flags

#     def _glob_manifests(self, base: Path) -> List[Path]:
#         # Support both manifest.yaml and manifest.yml
#         patterns = [
#             str(base / "**" / "manifest.yaml"),
#             str(base / "**" / "manifest.yml"),
#         ]
#         paths: List[Path] = []
#         for pat in patterns:
#             for p in glob.glob(pat, recursive=True):
#                 try:
#                     paths.append(Path(p))
#                 except Exception:
#                     continue
#         # Deduplicate and sort for deterministic order
#         uniq = sorted({p.resolve() for p in paths})
#         return list(uniq)

#     # ---------- public API ----------

#     def refresh(self) -> None:
#         """
#         Scan the plugins directory and rebuild the in-memory registry.
#         Silently skips malformed manifests (logs at DEBUG/WARN).
#         """
#         base = _plugins_dir()
#         manifest_paths = self._glob_manifests(base)

#         services: Dict[str, ServiceManifest] = {}
#         validators: Dict[str, DraftValidator] = {}

#         for mpath in manifest_paths:
#             try:
#                 raw = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
#                 spec = _normalize_manifest_shape(raw)
#                 mf = ServiceManifest.model_validate(spec)
#             except Exception as e:
#                 logger.warning("Skipping invalid manifest %s: %s", mpath, e)
#                 continue

#             if not self._allowed(mf.id):
#                 logger.debug("Service '%s' filtered by SERVICE_FLAGS", mf.id)
#                 continue

#             services[mf.id] = mf
#             # Build inputs validator (fallback to permissive object)
#             try:
#                 schema = mf.inputs or {"type": "object"}
#                 validators[mf.id] = DraftValidator(schema)
#             except Exception as e:
#                 logger.warning("Inputs schema invalid for %s: %s (using permissive object)", mf.id, e)
#                 validators[mf.id] = DraftValidator({"type": "object"})

#         with self._lock:
#             self.services = services
#             self.validators = validators

#         logger.info(
#             "Plugin registry refreshed: %d service(s) loaded from %s",
#             len(services),
#             str(base),
#         )
#         if logger.isEnabledFor(logging.DEBUG):
#             logger.debug("Loaded service IDs: %s", ", ".join(sorted(services.keys())))

#     def list(self) -> List[ServiceManifest]:
#         with self._lock:
#             # Return a copy sorted by id for determinism (helps tests)
#             return sorted(self.services.values(), key=lambda m: m.id)

#     def get(self, service_id: str) -> ServiceManifest:
#         with self._lock:
#             mf: Optional[ServiceManifest] = self.services.get(service_id)
#             if not mf:
#                 raise KeyError(f"Unknown service '{service_id}'")
#             return mf

#     def validate_inputs(self, service_id: str, payload: dict) -> None:
#         with self._lock:
#             v = self.validators.get(service_id)
#         if not v:
#             raise KeyError(f"No validator for '{service_id}'")
#         v.validate(payload or {})


# # Singleton registry instance (imported by tests)
# registry = PluginRegistry()


# # -------------------------
# # Watcher (optional)
# # -------------------------

# def start_watcher(interval: float = 2.0) -> None:
#     """
#     Start a background thread that refreshes the registry when any file
#     under PLUGINS_DIR changes (by mtime snapshot). Safe to call multiple times.
#     """

#     def _snapshot(root: Path) -> Dict[str, float]:
#         files = glob.glob(str(root / "**" / "*"), recursive=True)
#         out: Dict[str, float] = {}
#         for p in files:
#             try:
#                 rp = Path(p).resolve()
#                 if rp.is_file():
#                     out[str(rp)] = rp.stat().st_mtime
#             except Exception:
#                 # ignore files that disappear mid-scan, permission issues, etc.
#                 continue
#         return out

#     def loop() -> None:
#         base = _plugins_dir()
#         if not base.exists():
#             logger.warning("Plugins directory %s does not exist; watcher running anyway", base)
#         prev: Dict[str, float] = {}
#         while True:
#             try:
#                 snap = _snapshot(base)
#                 if snap != prev:
#                     registry.refresh()
#                     prev = snap
#             except Exception as e:
#                 logger.error("Plugin watcher error: %s", e)
#             time.sleep(interval)

#     t = threading.Thread(target=loop, daemon=True, name="plugin-registry-watcher")
#     t.start()
