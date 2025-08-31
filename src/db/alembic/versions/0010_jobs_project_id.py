from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_jobs_project_id"
down_revision = "0009_tenant_services"
branch_labels = None
depends_on = None

def upgrade():
    # idempotent: won't fail if column already exists
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS project_id text")

def downgrade():
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS project_id")
