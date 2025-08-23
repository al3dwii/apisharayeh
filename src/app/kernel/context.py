from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Set, Union

from . import storage
from .events import EventEmitter


@dataclass
class ExecutionContext:
    job_id: str
    project_id: str
    permissions: Set[str]
    env: Dict[str, Any] = field(default_factory=dict)
    emitter: EventEmitter = field(default_factory=EventEmitter)

    @property
    def DEV_OFFLINE(self) -> bool:
        # honor both env var and ctx.env flag
        flag = self.env.get("DEV_OFFLINE")
        if flag is None:
            return os.environ.get("DEV_OFFLINE", "false").lower() in ("1", "true", "yes", "y")
        return bool(flag)

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        base = {"job_id": self.job_id, "project_id": self.project_id}
        base.update(payload or {})
        self.emitter.emit(event_type, base)

    # Storage helpers
    def artifacts_dir(self, *parts: str) -> Path:
        return storage.subdir(self.project_id, *parts)

    def write_text(self, rel_path: str, text: str) -> Path:
        return storage.write_text(self.project_id, rel_path, text)

    def write_bytes(self, rel_path: str, data: bytes) -> Path:
        return storage.write_bytes(self.project_id, rel_path, data)

    def copy_in(self, src_path: Union[str, Path], rel_dest: str) -> Path:
        return storage.copy_in(self.project_id, src_path, rel_dest)

    def url_for(self, path: Union[str, Path]) -> str:
        return storage.url_for(path)
