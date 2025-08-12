from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005_webhooks'
down_revision = '0004_events'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('webhook_deliveries',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), nullable=False, index=True),
        sa.Column('job_id', sa.String(), nullable=False, index=True),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
    )
    op.execute("ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_webhook_deliveries ON webhook_deliveries
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS webhook_deliveries")
