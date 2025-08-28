# src/app/api/routes/slides_edit.py
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Union, Literal, Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.kernel.ops import slides_stateful as slides_ops

router = APIRouter(prefix="/v1/projects", tags=["slides-edit"])


# --- tiny ctx for kernel ops (fs fallback paths will work) ---
class Ctx:
    def artifacts_dir(self) -> str:
        # kernel falls back to ./artifacts/<project_id> if this raises
        raise RuntimeError("no explicit artifacts_dir")

    def emit(self, *_a, **_k):  # best-effort, ignore
        pass


def _etag_of_state(state: Dict[str, Any]) -> str:
    payload = json.dumps(
        {"slides": state.get("slides", []), "updated_at": state.get("updated_at")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---- models (Pydantic v2, discriminated by `op`) ----
class OpReplace(BaseModel):
    op: Literal["replace"] = "replace"
    no: int
    patch: Dict[str, Any]


class OpMove(BaseModel):
    op: Literal["move"] = "move"
    frm: int
    to: int


class OpInsert(BaseModel):
    op: Literal["insert"] = "insert"
    at: int
    slide: Dict[str, Any] = {}


class OpDelete(BaseModel):
    op: Literal["delete"] = "delete"
    no: int


BulkOp = Annotated[Union[OpReplace, OpMove, OpInsert, OpDelete], Field(discriminator="op")]


class BulkEditBody(BaseModel):
    ops: List[BulkOp]


@router.patch("/{project_id}/slides")
def bulk_patch(project_id: str, body: BulkEditBody, response: Response):
    ctx = Ctx()
    # compute current ETag
    state, _ = slides_ops._state_read(ctx, project_id)  # type: ignore[attr-defined]
    etag_before = _etag_of_state(state)

    # convert models -> dicts for kernel (kernel expects .get/.items)
    ops_as_dicts = [op.model_dump() for op in body.ops]
    out = slides_ops.apply_ops(ctx, project_id=project_id, ops=ops_as_dicts)  # type: ignore[attr-defined]

    state_after, _ = slides_ops._state_read(ctx, project_id)  # type: ignore[attr-defined]
    response.headers["ETag"] = _etag_of_state(state_after)
    response.headers["ETag-Before"] = etag_before
    return out


@router.put("/{project_id}/slides/{idx}")
def put_one(
    project_id: str,
    idx: int,
    patch: Dict[str, Any],
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    ctx = Ctx()
    state, _ = slides_ops._state_read(ctx, project_id)  # type: ignore[attr-defined]
    current_etag = _etag_of_state(state)
    if if_match not in (None, "*", current_etag):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="ETag mismatch",
        )

    out = slides_ops.update_one(ctx, project_id=project_id, slide_no=idx, patch=patch)  # type: ignore[attr-defined]
    # new tag
    state_after, _ = slides_ops._state_read(ctx, project_id)  # type: ignore[attr-defined]
    response.headers["ETag"] = _etag_of_state(state_after)
    return out
