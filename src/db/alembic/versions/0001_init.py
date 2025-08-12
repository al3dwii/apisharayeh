from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_init'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('jobs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), index=True, nullable=False),
        sa.Column('kind', sa.String(), index=True, nullable=False),
        sa.Column('status', sa.String(), index=True, nullable=False, server_default='queued'),
        sa.Column('input_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('output_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
    )
    op.create_table('runs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('tenant_id', sa.String(), index=True, nullable=False),
        sa.Column('agent', sa.String(), index=True, nullable=False),
        sa.Column('pack', sa.String(), index=True, nullable=False),
        sa.Column('status', sa.String(), index=True, nullable=False, server_default='started'),
        sa.Column('tokens_in', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tokens_out', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )

def downgrade():
    op.drop_table('runs')
    op.drop_table('jobs')
