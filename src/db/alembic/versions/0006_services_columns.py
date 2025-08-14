from alembic import op
import sqlalchemy as sa

revision = "0006_services_columns"
down_revision = "0005_webhooks"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("jobs", sa.Column("service_id", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("service_version", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("service_runtime", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("manifest_snapshot", sa.JSON(), nullable=True))
    op.create_index("ix_jobs_service", "jobs", ["service_id", "service_version"])

def downgrade():
    op.drop_index("ix_jobs_service", table_name="jobs")
    op.drop_column("jobs", "manifest_snapshot")
    op.drop_column("jobs", "service_runtime")
    op.drop_column("jobs", "service_version")
    op.drop_column("jobs", "service_id")
