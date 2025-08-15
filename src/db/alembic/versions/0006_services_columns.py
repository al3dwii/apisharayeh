"""add plugin columns to jobs"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as psql

# Alembic identifiers
revision = "0006_services_columns"
down_revision = "0005_webhooks"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("jobs", sa.Column("service_id", sa.String(length=255), nullable=True))
    op.add_column("jobs", sa.Column("service_version", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("service_runtime", sa.String(length=32), nullable=True))
    op.add_column("jobs", sa.Column("manifest_snapshot", psql.JSONB(astext_type=sa.Text()), nullable=True))
    # optional indexes (uncomment if you want)
    # op.create_index("ix_jobs_service_id", "jobs", ["service_id"])

def downgrade():
    # if you created indexes, drop them first
    # op.drop_index("ix_jobs_service_id", table_name="jobs")
    op.drop_column("jobs", "manifest_snapshot")
    op.drop_column("jobs", "service_runtime")
    op.drop_column("jobs", "service_version")
    op.drop_column("jobs", "service_id")
