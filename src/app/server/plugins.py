from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List, Tuple
from app.kernel.manifest import load_plugin_snapshot

def _iter_plugins(plugins_root: Path):
    """Yield (dirpath, manifest, flow, perms) for all valid plugins."""
    if not plugins_root.exists():
        return
    for sub in plugins_root.iterdir():
        if not sub.is_dir():
            continue
        man = sub / "manifest.yaml"
        flow = sub / "flow.yaml"
        if not man.exists() or not flow.exists():
            continue
        try:
            manifest, flow_doc, perms = load_plugin_snapshot(sub)
        except Exception:
            # Skip unreadable plugin folders
            continue
        yield sub, manifest, flow_doc, perms

def list_plugins(plugins_root: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for sub, manifest, flow_doc, perms in _iter_plugins(plugins_root):
        items.append({
            "id": manifest.get("id") or sub.name,
            "name": manifest.get("name") or sub.name,
            "version": manifest.get("version") or "0.0.0",
            "category": manifest.get("category"),
            "summary": manifest.get("summary"),
            "runtime": manifest.get("runtime"),
            "entrypoint": manifest.get("entrypoint"),
        })
    return items

def get_plugin(plugins_root: Path, service_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], set]:
    """
    Robust lookup:
    1) Try exact folder match: <PLUGINS_DIR>/<service_id>
    2) Fallback: scan all plugins and match on manifest.id
    """
    # 1) direct folder
    direct = plugins_root / service_id
    try:
        manifest, flow, perms = load_plugin_snapshot(direct)
        return manifest, flow, perms
    except Exception:
        pass

    # 2) fallback by manifest.id (and subdir name normalization)
    for sub, manifest, flow_doc, perms in _iter_plugins(plugins_root):
        if manifest.get("id") == service_id or sub.name == service_id:
            return manifest, flow_doc, perms

    raise FileNotFoundError(f"Service not found: {service_id}")
