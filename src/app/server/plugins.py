# src/app/server/plugins.py
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple, Optional
import yaml
import traceback

def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}

def _plugin_dirs(root: Path) -> List[Path]:
    if not root.exists():
        print(f"[plugins] PLUGINS_ROOT does not exist: {root}")
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir()]

def list_plugins(root: Path) -> List[Dict[str, Any]]:
    """
    Enumerate plugins under root. Returns a short manifest list suitable for /v1/services.
    Skips invalid plugins but logs the reason.
    """
    out: List[Dict[str, Any]] = []
    for p in _plugin_dirs(root):
        mf = p / "manifest.yaml"
        if not mf.exists():
            # Not a plugin folder
            continue
        try:
            man = _load_yaml(mf)
            sid = man.get("id")
            entry = man.get("entrypoint")
            runtime = man.get("runtime")
            if not sid or not entry or not runtime:
                print(f"[plugins] skip '{p.name}': missing id/entrypoint/runtime in manifest.yaml")
                continue
            out.append({
                "id": sid,
                "name": man.get("name", sid),
                "version": man.get("version", "0.0.0"),
                "category": man.get("category"),
                "summary": man.get("summary", ""),
                "runtime": runtime,
                "entrypoint": entry,
            })
        except Exception as e:
            print(f"[plugins] error loading {mf}: {e}")
            traceback.print_exc()
    if not out:
        print(f"[plugins] No plugins discovered under {root}")
    return out

def get_plugin(root: Path, service_id: str) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Set[str]]:
    """
    Load a specific plugin by id. Returns (manifest, flow, permissions)
    - For runtime=dsl, loads the YAML flow file at entrypoint.
    - Raises FileNotFoundError if not found or invalid.
    """
    for p in _plugin_dirs(root):
        mf = p / "manifest.yaml"
        if not mf.exists():
            continue
        try:
            man = _load_yaml(mf)
        except Exception as e:
            print(f"[plugins] error parsing {mf}: {e}")
            continue

        if man.get("id") != service_id:
            continue

        entry = man.get("entrypoint")
        runtime = man.get("runtime")
        if not entry or not runtime:
            raise FileNotFoundError(f"[plugins] Invalid manifest for {service_id}: missing runtime/entrypoint")

        # Resolve entrypoint relative to the plugin dir
        entry_path = (p / Path(entry)).resolve()
        flow: Optional[Dict[str, Any]] = None
        if runtime == "dsl":
            if not entry_path.exists():
                raise FileNotFoundError(f"[plugins] Entrypoint not found for {service_id}: {entry_path}")
            try:
                flow = _load_yaml(entry_path)
            except Exception as e:
                raise FileNotFoundError(f"[plugins] Could not parse flow at {entry_path}: {e}") from e

        perms = set(man.get("permissions") or [])
        # Attach plugin dir for debugging
        man["_plugin_dir"] = str(p.resolve())
        return man, flow, perms

    raise FileNotFoundError(f"Service not found: {service_id}")
