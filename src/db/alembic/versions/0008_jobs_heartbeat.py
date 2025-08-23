"""add jobs.last_heartbeat_at

Revision ID: 0008_jobs_heartbeat
Revises: 0007_plugin_registry
Create Date: 2025-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_jobs_heartbeat'
down_revision = '0007_plugin_registry'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('jobs', sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_jobs_status_heartbeat', 'jobs', ['status', 'last_heartbeat_at'], unique=False)

def downgrade() -> None:
    op.drop_index('ix_jobs_status_heartbeat', table_name='jobs')
    op.drop_column('jobs', 'last_heartbeat_at')