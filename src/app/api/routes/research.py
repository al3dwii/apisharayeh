# src/app/api/routes/research.py
from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter
from app.kernel.ops import research as research_ops

router = APIRouter(prefix="/v1/research", tags=["research"])

class ResearchReq(BaseModel):
    project_id: str
    topic: Optional[str] = None
    urls: Optional[List[str]] = None
    language: Optional[str] = "en"
    max_docs: int = 8
    # M10 knobs
    allow_domains: Optional[List[str]] = None
    block_domains: Optional[List[str]] = None
    per_host_cap: Optional[int] = 2
    dedupe_threshold: float = 0.82

@router.post("/preview")
def preview(body: ResearchReq) -> Dict[str, Any]:
    # tiny ctx to satisfy ops (events are optional)
    class Ctx:
        def emit_event(self, *_a, **_k):  # pragma: no cover
            pass
    ctx = Ctx()

    out = research_ops.research_pipeline(
        ctx,
        project_id=body.project_id,
        topic=body.topic,
        urls=body.urls,
        language=body.language or "en",
        max_docs=body.max_docs,
        allow_domains=body.allow_domains,
        block_domains=body.block_domains,
        per_host_cap=body.per_host_cap,
        dedupe_threshold=float(body.dedupe_threshold or 0.0),
    )

    # compact response for quick inspection
    return {
        "urls": out.get("urls"),
        "docs_count": len(out.get("docs") or []),
        "facts": out.get("facts"),
        "outline": out.get("outline"),
        "citations": out.get("citations"),
        "stats": out.get("stats"),
    }
