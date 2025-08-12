from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, Text, Index, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base

DELIVERY_STATUS = ("pending", "retrying", "sent", "failed")

class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(nullable=False, index=True)

    url: Mapped[str] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(nullable=False)  # e.g., job.succeeded, job.failed
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(nullable=False, default="pending", server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now(), index=True
    )

    __table_args__ = (
        CheckConstraint(f"status in {DELIVERY_STATUS}", name="wh_status_check"),
        Index("ix_wh_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_wh_job_created", "job_id", "created_at"),
    )
