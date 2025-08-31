from __future__ import annotations

from typing import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text, event
from sqlalchemy.pool import NullPool  # avoid loop-crossing issues by not reusing conns

from app.core.config import settings
from app.models.base import Base  # keeps imports consistent even if unused here


# --- Engine ------------------------------------------------------------------

# Expect settings.DATABASE_URL like: postgresql+asyncpg://user:pass@host:5432/dbname
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,  # important for Celery/async loops; don't share connections across loops
)


# Optional: per-connection session defaults (statement timeout, timezone, app name)
@event.listens_for(engine.sync_engine, "connect")
def _pg_on_connect(dbapi_conn, _):
    try:
        with dbapi_conn.cursor() as cur:
            # timeouts / basics
            cur.execute(f"SET statement_timeout = {int(settings.DB_STATEMENT_TIMEOUT_MS)}")
            cur.execute(f"SET idle_in_transaction_session_timeout = {int(settings.DB_IDLE_TX_TIMEOUT_MS)}")
            cur.execute("SET TIME ZONE 'UTC'")
            cur.execute(f"SET application_name = '{settings.APP_NAME}'")

            # In dev, disable row-level security globally for this connection
            if settings.DISABLE_TENANT:
                cur.execute("SET row_security = off;")
                # provide a benign default so any code that still reads the GUC doesn't blow up
                cur.execute("SET app.tenant_id = 'dev';")
    except Exception:
        # Don't crash if these GUCs are not permitted by the server
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
        yield session


@asynccontextmanager
async def tenant_session(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """
    Open an AsyncSession with the Postgres GUC set for Row-Level Security:
      SET LOCAL app.tenant_id = :tenant_id

    In dev (DISABLE_TENANT=1), we skip setting the tenant and rely on
    row_security = off (set on connect) so RLS does not hide rows.
    """
    async with SessionLocal() as session:
        try:
            if not settings.DISABLE_TENANT:
                # Ensure we're inside an active transaction before SET LOCAL
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": tenant_id},
                )
            yield session
        except Exception:
            await session.rollback()
            raise
        # session context will close the connection


async def init_models():
    """
    Alembic is authoritative for schema management; keep as no-op for tests/boot.
    """
    return
