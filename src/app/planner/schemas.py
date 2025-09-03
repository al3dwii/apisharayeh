from __future__ import annotations

"""
Pydantic v2 data models for Plan/Node specs used inside workflows.

Usage:
- plan = PlanSpec.model_validate(plan_spec_dict)
"""

from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field, model_validator


class NodeSpec(BaseModel):
    id: str = Field(..., description="Unique node id within the plan")
    tool_id: str = Field(..., description="Registered tool identifier")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    # Optional UX: explicit approval flag (HITL). You also support this via inputs.requires_approval
    requires_approval: Optional[bool] = Field(default=None, description="If true, wait for HITL approval")

    @model_validator(mode="after")
    def _normalize(self) -> "NodeSpec":
        # De-duplicate depends_on while preserving order
        seen: Set[str] = set()
        unique: List[str] = []
        for d in self.depends_on:
            if d not in seen:
                seen.add(d)
                unique.append(d)
        self.depends_on = unique
        return self


class PlanSpec(BaseModel):
    id: str
    goal_id: str
    nodes: List[NodeSpec]
    max_concurrency: Optional[int] = Field(default=None, ge=1, description="Plan-level concurrency cap")
    default_tool_concurrency: Optional[int] = Field(default=None, ge=1, description="Default per-tool cap")

    @model_validator(mode="after")
    def _validate_graph(self) -> "PlanSpec":
        # Ensure unique node ids
        ids: Set[str] = set()
        for n in self.nodes:
            if n.id in ids:
                raise ValueError(f"duplicate node id={n.id}")
            ids.add(n.id)

        # Ensure all dependencies reference existing nodes
        for n in self.nodes:
            for d in n.depends_on:
                if d not in ids:
                    raise ValueError(f"node {n.id} depends on unknown node {d}")

        # Optional: detect trivial cycles (DFS would be more complete)
        # We keep it light here; Temporal will fail fast if deadlocked.

        return self

    def tool_ids(self) -> List[str]:
        return [n.tool_id for n in self.nodes]

    def node_map(self) -> Dict[str, NodeSpec]:
        return {n.id: n for n in self.nodes}
