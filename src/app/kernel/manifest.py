from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Set, Tuple

def _load_yaml_or_json(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except Exception:
        return json.loads(text)

def load_plugin_snapshot(plugin_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """
    Returns (manifest, flow, permissions_set)
    """
    man_path = plugin_dir / "manifest.yaml"
    if not man_path.exists():
        # allow json fallback
        man_path = plugin_dir / "manifest.json"
    if not man_path.exists():
        raise FileNotFoundError(f"manifest not found at {plugin_dir}")

    flow_path = plugin_dir / "flow.yaml"
    if not flow_path.exists():
        flow_path = plugin_dir / "flow.json"
    if not flow_path.exists():
        raise FileNotFoundError(f"flow not found at {plugin_dir}")

    manifest = _load_yaml_or_json(man_path) or {}
    flow = _load_yaml_or_json(flow_path) or {}

    # Basic sanity
    for k in ("id", "runtime", "entrypoint"):
        if k not in manifest:
            raise ValueError(f"manifest missing '{k}'")

    # Permissions: manifest.permissions is a mapping of name->bool
    perms: Set[str] = set()
    mp = manifest.get("permissions") or {}
    for name, val in mp.items():
        if val:
            perms.add(name)

    return manifest, flow, perms
