# src/app/routes/routes_research.py
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from app.kernel.ops import research as research_ops

router = APIRouter(prefix="/v1/research", tags=["research"])

class ResearchReq(BaseModel):
    project_id: str = Field(..., description="Project id to store artifacts under")
    topic: Optional[str] = None
    urls: Optional[List[str]] = None
    language: Optional[str] = "en"
    max_docs: int = 8

@router.post("/preview")
def preview(body: ResearchReq):
    if not body.topic and not body.urls:
        raise HTTPException(status_code=400, detail="Provide 'topic' or 'urls'")
    # 'ctx' can be a simple object with emit_event no-op
    class Ctx: 
        def emit_event(self, *a, **k): 
            pass
    result = research_ops.research_pipeline(
        Ctx(), project_id=body.project_id, topic=body.topic, urls=body.urls, lang=body.language, max_docs=body.max_docs
    )
    return {"outline": result["outline"], "citations": result["citations"], "docs_count": len(result["docs"])}
