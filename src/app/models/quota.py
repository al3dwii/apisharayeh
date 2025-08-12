from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Date, PrimaryKeyConstraint
from app.models.base import Base

class Quota(Base):
    __tablename__ = "quotas"
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    day: Mapped[str] = mapped_column(Date, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (PrimaryKeyConstraint('tenant_id','day', name='pk_quotas'),)
