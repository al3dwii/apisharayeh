from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Set, Tuple


def _load_yaml_or_json(path: Path) -> Dict[str, Any]:
    """
    Load YAML if PyYAML is available; otherwise fallback to JSON.
    Returns {} for empty files.
    """
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
        return data or {}
    except Exception:
        # Fallback to JSON parse
        data = json.loads(text)
        return data or {}


def _normalize_permissions(manifest: Dict[str, Any]) -> Set[str]:
    """
    Accept any of the following and normalize to a set of strings:
      permissions:
        fs_read: true
        fs_write: true
    OR
      permissions:
        - fs_read
        - fs_write
    OR
      permissions: fs_read
    OR mixed list entries like [{"fs_read": true}, "fs_write"]
    """
    perms: Set[str] = set()
    raw = manifest.get("permissions")

    if raw in (None, False, {}, [], ""):
        return perms

    if isinstance(raw, dict):
        for name, val in raw.items():
            if val:
                perms.add(str(name))
        return perms

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                perms.add(item)
            elif isinstance(item, dict):
                for name, val in item.items():
                    if val:
                        perms.add(str(name))
            else:
                raise ValueError("manifest.permissions list items must be strings or mappings")
        return perms

    if isinstance(raw, str):
        perms.add(raw)
        return perms

    raise ValueError("manifest.permissions must be a mapping, list, string, or null")


def load_plugin_snapshot(plugin_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """
    Returns (manifest, flow, permissions_set)
    - manifest: plugin manifest dict
    - flow: flow definition dict (for DSL runtime)
    - permissions_set: normalized permission names
    """
    if not plugin_dir.exists():
        raise FileNotFoundError(f"plugin directory not found: {plugin_dir}")

    # ----- manifest.{yaml|json}
    man_path = plugin_dir / "manifest.yaml"
    if not man_path.exists():
        man_path = plugin_dir / "manifest.json"
    if not man_path.exists():
        raise FileNotFoundError(f"manifest not found at {plugin_dir}")

    # ----- flow.{yaml|json}
    flow_path = plugin_dir / "flow.yaml"
    if not flow_path.exists():
        flow_path = plugin_dir / "flow.json"
    if not flow_path.exists():
        raise FileNotFoundError(f"flow not found at {plugin_dir}")

    manifest = _load_yaml_or_json(man_path) or {}
    flow = _load_yaml_or_json(flow_path) or {}

    # ----- Basic sanity
    missing = [k for k in ("id", "runtime", "entrypoint") if k not in manifest]
    if missing:
        raise ValueError(f"manifest missing required key(s): {', '.join(missing)} (plugin: {plugin_dir})")

    # flow should be a mapping/dict for DSL
    if not isinstance(flow, dict):
        raise ValueError(f"flow must be a mapping/dict (plugin: {plugin_dir})")

    # ----- Normalize permissions (accept mapping or list)
    perms = _normalize_permissions(manifest)

    return manifest, flow, perms
