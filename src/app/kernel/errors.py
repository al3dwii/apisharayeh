from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ProblemDetails(Exception):
    type: str = "about:blank"
    title: str = "Kernel operation failed"
    detail: str = ""
    status: int = 400
    op: Optional[str] = None
    code: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "type": self.type,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
        }
        if self.op is not None:
            data["op"] = self.op
        if self.code is not None:
            data["code"] = self.code
        if self.meta is not None:
            data["meta"] = self.meta
        return data

    def __str__(self) -> str:
        return f"{self.title} ({self.code or ''}): {self.detail}"
