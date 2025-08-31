"""add tenant_services gating + services view

Revision ID: 0009_tenant_services
Revises: 0008_jobs_heartbeat
Create Date: 2025-08-29
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_tenant_services"
down_revision = "0008_jobs_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_services",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("service_id", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("exposed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("tenant_id", "service_id"),
    )

    # Enable RLS and add policy based on app.tenant_id GUC
    op.execute("ALTER TABLE tenant_services ENABLE ROW LEVEL SECURITY;")
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public' AND tablename='tenant_services' AND policyname='tenant_services_rls'
      ) THEN
        CREATE POLICY tenant_services_rls
          ON tenant_services
          USING ( current_setting('app.tenant_id', true) = tenant_id );
      END IF;
    END$$;
    """)

    # Compatibility view: this is what the code queries
    op.execute("""
    CREATE OR REPLACE VIEW services AS
      SELECT tenant_id, service_id, enabled, exposed, created_at, updated_at
      FROM tenant_services;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS services;")
    op.execute("""
    DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public' AND tablename='tenant_services' AND policyname='tenant_services_rls'
      ) THEN
        DROP POLICY tenant_services_rls ON tenant_services;
      END IF;
    END$$;
    """)
    op.execute("ALTER TABLE tenant_services DISABLE ROW LEVEL SECURITY;")
    op.drop_table("tenant_services")
