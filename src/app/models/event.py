from __future__ import annotations

from datetime import datetime
from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base

# Optional enums (keep loose to avoid needless migrations if you add steps/statuses later)
# STEP_VALUES = ("plan", "act", "run")
# STATUS_VALUES = ("started", "progress", "finished", "succeeded", "failed")

class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(nullable=False, index=True)

    step: Mapped[str] = mapped_column(nullable=False)     # e.g. plan / act / run
    status: Mapped[str] = mapped_column(nullable=False)   # started / progress / finished / failed / succeeded
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        # Fast playback for a single job, in order:
        Index("ix_events_job_created", "job_id", "created_at"),
        # Common filter: per-tenant recent events
        Index("ix_events_tenant_created", "tenant_id", "created_at"),
        # If you later add enums, you can enforce them with a CHECK here.
        # CheckConstraint(f"step in {STEP_VALUES}", name="events_step_check"),
        # CheckConstraint(f"status in {STATUS_VALUES}", name="events_status_check"),
    )
