from alembic import op
import sqlalchemy as sa

revision = '0002_rls'
down_revision = '0001_init'
branch_labels = None
depends_on = None

def upgrade():
    # Enable RLS and create tenant policies
    op.execute("ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_jobs ON jobs
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)
    op.execute("ALTER TABLE runs ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_runs ON runs
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

def downgrade():
    op.execute("DROP POLICY IF EXISTS tenant_isolation_jobs ON jobs;")
    op.execute("ALTER TABLE jobs DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_runs ON runs;")
    op.execute("ALTER TABLE runs DISABLE ROW LEVEL SECURITY;")
