from alembic import op
import sqlalchemy as sa

revision = '0003_quotas'
down_revision = '0002_rls'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('quotas',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('tokens_used', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('tenant_id','day', name='pk_quotas')
    )
    op.execute("ALTER TABLE quotas ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_quotas ON quotas
        USING (tenant_id = current_setting('app.tenant_id', true));
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS quotas")
