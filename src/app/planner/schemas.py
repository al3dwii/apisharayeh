# src/app/planner/schemas.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

class NodeSpec(BaseModel):
    id: str
    tool_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    retry_policy: Dict[str, Any] = Field(default_factory=dict)
    resource_profile: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None

class PlanSpec(BaseModel):
    id: str
    goal_id: str
    nodes: List[NodeSpec]
    invariants: Dict[str, Any] = Field(default_factory=dict)
    max_concurrency: int = 4
