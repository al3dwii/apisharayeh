# src/db/alembic/versions/0011_cqrs_core.py
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# Revision identifiers, used by Alembic.
revision = "0011_cqrs_core"
down_revision = "0010_jobs_project_id"
branch_labels = None
depends_on = None

def upgrade():
    # --- goals ---
    op.create_table(
        "goals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("inputs_json", JSONB, nullable=True),
        sa.Column("constraints_json", JSONB, nullable=True),
        sa.Column("requester", sa.String(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="queued", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_goals_tenant_created", "goals", ["tenant_id", "created_at"])

    # --- plans ---
    op.create_table(
        "plans",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("goal_id", sa.String(), nullable=False, index=True),
        sa.Column("graph_spec_json", JSONB, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("invariants_json", JSONB, nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="created", index=True),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("cost_budget_cents", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_plans_goal", "plans", ["goal_id"])
    op.create_index("ix_plans_tenant_created", "plans", ["tenant_id", "created_at"])

    # --- plan_nodes ---
    op.create_table(
        "plan_nodes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False, index=True),
        sa.Column("plan_id", sa.String(), nullable=False, index=True),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("inputs_json", JSONB, nullable=True),
        sa.Column("outputs_ref_json", JSONB, nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending", index=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depends_on_json", JSONB, nullable=True),
        sa.Column("retry_policy_json", JSONB, nullable=True),
        sa.Column("resource_profile_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_plan_nodes_plan", "plan_nodes", ["plan_id"])
    op.create_index("ix_plan_nodes_tenant_status", "plan_nodes", ["tenant_id", "status"])

    # --- node_cache (idempotency short-circuit) ---
    op.create_table(
        "node_cache",
        sa.Column("idempotency_key", sa.String(), primary_key=True),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("outputs_ref_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )

    # ---- RLS (tenant isolation via app.tenant_id) ----
    for table in ("goals", "plans", "plan_nodes", "node_cache"):
        # node_cache doesn't have tenant_id, keep RLS disabled for it
        if table == "node_cache":
            continue
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true));
        """)

def downgrade():
    for table in ("plan_nodes", "plans", "goals"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.drop_table("node_cache")
    op.drop_index("ix_plan_nodes_tenant_status", table_name="plan_nodes")
    op.drop_index("ix_plan_nodes_plan", table_name="plan_nodes")
    op.drop_table("plan_nodes")
    op.drop_index("ix_plans_tenant_created", table_name="plans")
    op.drop_index("ix_plans_goal", table_name="plans")
    op.drop_table("plans")
    op.drop_index("ix_goals_tenant_created", table_name="goals")
    op.drop_table("goals")
