from __future__ import annotations

from datetime import datetime

from sqlalchemy import Text, Index, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base

JOB_STATUS_VALUES = ("queued", "running", "succeeded", "failed")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(nullable=False, index=True)
    # e.g. "office.pdf_to_pptx" or a service_id when using plugins
    kind: Mapped[str] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(nullable=False, index=True, default="queued")

    project_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # <-- add this

    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Service fields (plugins) ---
    # When a job is created with service_id, these snapshot the manifest for determinism.
    service_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    service_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_runtime: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Optional DB-side idempotency (kept None since you're using Redis for now)
    # idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),  # SQLAlchemy will set on UPDATE
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"status in {JOB_STATUS_VALUES}",
            name="jobs_status_check",
        ),
        Index("ix_jobs_tenant_created_desc", "tenant_id", "created_at"),
        Index("ix_jobs_tenant_status", "tenant_id", "status"),
        # Query by service identity/version efficiently
        Index("ix_jobs_service", "service_id", "service_version"),
        # If you add idempotency_key above, consider a partial unique index in alembic:
        # UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_idemp")
    )
