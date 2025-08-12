from typing import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text, event

from app.core.config import settings
from app.models.base import Base  # keeps imports consistent even if unused here

# --- Engine ------------------------------------------------------------------

# Expect settings.DATABASE_URL like: postgresql+asyncpg://user:pass@host:5432/dbname
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

# Optional: per-connection session defaults (statement timeout, timezone, app name)
@event.listens_for(engine.sync_engine, "connect")
def _pg_on_connect(dbapi_conn, _):
    try:
        with dbapi_conn.cursor() as cur:
            # 30s default unless overridden in settings (ms)
            stmt_timeout = getattr(settings, "DB_STATEMENT_TIMEOUT_MS", 30000)
            idle_timeout = getattr(settings, "DB_IDLE_TX_TIMEOUT_MS", 60000)
            app_name = getattr(settings, "APP_NAME", "agenticBE")

            cur.execute(f"SET statement_timeout = {int(stmt_timeout)}")
            cur.execute(f"SET idle_in_transaction_session_timeout = {int(idle_timeout)}")
            cur.execute("SET TIME ZONE 'UTC'")
            cur.execute(f"SET application_name = '{app_name}'")
    except Exception:
        # Don't crash if the server doesn't allow these GUCs
        pass

# --- Session factory ----------------------------------------------------------

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

def get_async_sessionmaker():
    """Small helper so other modules (e.g., /readyz) can import the maker cleanly."""
    return SessionLocal

# --- Public helpers -----------------------------------------------------------

async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for non-tenant-scoped operations (admin tasks, health checks)."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            # Session context ensures close; rollback handled by callers if needed
            pass

@asynccontextmanager
async def tenant_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """
    Open an AsyncSession with the Postgres GUC set for Row-Level Security:
      SET LOCAL app.tenant_id = :tenant_id
    NOTE: The LOCAL setting applies to the current transaction; if you COMMIT,
    you must call SET LOCAL again before further queries. Keep all statements for
    a logical unit of work within this context block.
    """
    async with SessionLocal() as session:
        try:
            # Ensure we're inside an active transaction before SET LOCAL
            # SQLAlchemy 2.x autbegins a transaction on first execute.
            await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),{"tid": tenant_id},)
            yield session
        except Exception:
            # Safety: rollback on error so the connection isn't left in a bad state
            await session.rollback()
            raise
        # session context will close the connection

async def init_models():
    """
    Alembic is authoritative for schema management; keep as no-op for tests/boot.
    """
    return
