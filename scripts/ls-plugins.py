#!/usr/bin/env python3
"""
List plugins discovered by the registry.

Examples:
  ./scripts/ls-plugins.py
  ./scripts/ls-plugins.py --ids-only
  ./scripts/ls-plugins.py --json
  ./scripts/ls-plugins.py --schema
  ./scripts/ls-plugins.py --manifest-paths
  ./scripts/ls-plugins.py --root ./plugins
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# --- ensure we can import the app package (src/ layout) ---
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[1]  # .../apisharayeh
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.kernel.plugins.loader import registry, _plugins_dir  # type: ignore  # noqa: E402
import yaml  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="List discovered plugins.")
    p.add_argument("--root", type=str, default=None, help="Override PLUGINS_DIR (default: env/settings).")
    p.add_argument("--json", action="store_true", help="Output as JSON.")
    p.add_argument("--ids-only", action="store_true", help="Print only service IDs (one per line).")
    p.add_argument("--schema", action="store_true", help="Include inputs/outputs schema in the output.")
    p.add_argument("--manifest-paths", action="store_true", help="Include manifest.yaml path per service.")
    return p.parse_args()


def _scan_manifest_paths(plugins_root: Path) -> Dict[str, Path]:
    """
    Build a map: service_id -> manifest.(yaml|yml) absolute path by scanning PLUGINS_DIR.
    Uses Path.rglob from the *root* to avoid pathlib's 'absolute glob' restriction.
    """
    mapping: Dict[str, Path] = {}
    if not plugins_root.exists():
        return mapping

    for fname in ("manifest.yaml", "manifest.yml"):
        for p in plugins_root.rglob(fname):
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                sid = data.get("id")
                if isinstance(sid, str) and sid not in mapping:
                    mapping[sid] = p.resolve()
            except Exception:
                # ignore malformed; registry will skip these too
                continue
    return mapping


def main() -> int:
    args = _parse_args()

    if args.root:
        os.environ["PLUGINS_DIR"] = args.root

    plugins_root = _plugins_dir()
    registry.refresh()
    services = registry.list()

    if args.ids_only:
        for s in services:
            print(s.id)
        return 0

    manifest_paths: Dict[str, Path] = {}
    if args.manifest_paths:
        manifest_paths = _scan_manifest_paths(plugins_root)

    if args.json:
        out: List[Dict[str, Any]] = []
        for s in services:
            item: Dict[str, Any] = {
                "id": s.id,
                "name": getattr(s, "name", None),
                "version": getattr(s, "version", None),
                "summary": getattr(s, "summary", None),
                "runtime": getattr(s, "runtime", None),
                "entrypoint": getattr(s, "entrypoint", None),
                "permissions": getattr(s, "permissions", None),
            }
            if args.schema:
                item["inputs"] = getattr(s, "inputs", None)
                item["outputs"] = getattr(s, "outputs", None)
            if args.manifest_paths and s.id in manifest_paths:
                item["manifest_path"] = str(manifest_paths[s.id])
            out.append(item)
        print(json.dumps({"plugins_dir": str(plugins_root), "services": out}, ensure_ascii=False, indent=2))
        return 0

    # Pretty text output
    print(f"PLUGINS_DIR: {plugins_root}")
    if not services:
        print("(no services found)")
        return 1

    for s in services:
        print(f"- {s.id}")
        print(f"    name:        {getattr(s, 'name', '')}")
        print(f"    version:     {getattr(s, 'version', '')}")
        print(f"    summary:     {getattr(s, 'summary', '')}")
        print(f"    runtime:     {getattr(s, 'runtime', '')}")
        print(f"    entrypoint:  {getattr(s, 'entrypoint', '')}")
        perms = getattr(s, "permissions", None)
        print(f"    permissions: {perms if perms is not None else '{}'}")
        if args.manifest_paths:
            mp = manifest_paths.get(s.id)
            if mp:
                print(f"    manifest:    {mp}")
        if args.schema:
            inputs = getattr(s, "inputs", None)
            outputs = getattr(s, "outputs", None)
            print("    inputs:")
            print("      " + json.dumps(inputs or {}, ensure_ascii=False))
            print("    outputs:")
            print("      " + json.dumps(outputs or {}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
