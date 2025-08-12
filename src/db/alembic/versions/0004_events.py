from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004_events'
down_revision = '0003_quotas'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('events',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), nullable=False, index=True),
        sa.Column('job_id', sa.String(), nullable=False, index=True),
        sa.Column('step', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
    )
    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_events ON events
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS events")
