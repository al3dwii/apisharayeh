create table if not exists runs(
  tenant_id text,
  run_id uuid,
  plan_id uuid,
  goal_id uuid,
  status text,
  started_at timestamptz,
  updated_at timestamptz,
  primary key (tenant_id, run_id)
);

create table if not exists nodes(
  tenant_id text,
  run_id uuid,
  node_id text,
  status text,
  updated_at timestamptz,
  primary key (tenant_id, run_id, node_id)
);
