from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_plugin_registry"
down_revision = "0006_services_columns"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "plugin_registry",
        sa.Column("service_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("staged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "tenant_plugins",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("service_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "service_id"),
        sa.ForeignKeyConstraint(["service_id"], ["plugin_registry.service_id"], ondelete="CASCADE"),
    )

    op.execute("ALTER TABLE tenant_plugins ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_plugins_rw ON tenant_plugins
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
    """)

def downgrade():
    op.drop_table("tenant_plugins")
    op.drop_table("plugin_registry")
