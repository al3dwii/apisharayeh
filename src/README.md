# Agentic Backend (FastAPI + LangGraph + Redis + Celery + Postgres)

A clean, tenant-first agentic backend with a jobs API, packs, tools, workers, and observability hooks.

## Quick start (local, Docker)
```bash
# 1) Copy .env
cp .env.example .env

# 2) Start local stack
make dev  # builds images + starts API, worker, Redis, Postgres

# 3) Apply DB migrations
make migrate

# 4) Hit the health & demo agent
curl -s localhost:8080/health
curl -s -X POST localhost:8080/v1/agents/demo/echo -H "Content-Type: application/json" -d '{"message":"ping"}'

# 5) Create a job (async)
curl -s -X POST localhost:8080/v1/jobs -H "Content-Type: application/json" -d '{
  "pack":"seo_factory",
  "agent":"keyword_researcher",
  "inputs":{"topic":"image to text","market":"en"}
}'
```

## Structure
See comments in the tree and inline docstrings. Packs let you swap verticals (`/v1/agents/{pack}/{agent}`).

## Notes
- LangGraph and LangChain are used for agent loops.
- Celery handles long-running jobs. Redis is the broker and scratchpad; Postgres is source of truth.
- OpenTelemetry + structlog stubs are wired; point OTLP to your collector if needed.


## Observability (Grafana + Loki + Prometheus)
- Start everything via `make dev` (compose includes grafana/loki/promtail/prometheus).
- Grafana at **http://localhost:3000** (anonymous Admin enabled for local). Dashboard is auto-provisioned.
- Prometheus at **http://localhost:9090**; Loki at **http://localhost:3100**.

## SDKs
Generate locally (requires API running):
```bash
make sdk
# TS → sdks/typescript/index.ts
# Python → sdks/python/
```
CI also generates SDKs on push and uploads them as artifacts.

## GitHub Actions
- **CI**: lint + tests + build image
- **Docker Publish**: builds & pushes `agentic-api` and `agentic-worker` to GHCR on tagged releases (vX.Y.Z)
- **SDKs**: spins API, generates TS & Python SDKs, uploads artifacts


## Tracing (Tempo + Grafana)
- Tempo is included in docker-compose. OTLP endpoint is `tempo:4317` (gRPC).
- Set `.env` → `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317` and `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`.
- Grafana has a **Tempo** datasource pre-provisioned; explore traces under "Explore → Tempo".

## Helm chart
- Path: `charts/agentic` (API Deployment + Service, Worker Deployment).
- On tag push `vX.Y.Z`, GitHub Actions packages and **pushes the chart to GHCR (OCI)** at `ghcr.io/<owner>/charts/agentic:<version>` and creates a GitHub Release with the `.tgz` attached.
- Edit `values.yaml` to point to your image repositories (replace `REPLACE_ME`).

## Releases
- Tag your repo: `git tag v0.1.0 && git push --tags` to publish **Docker images** and the **Helm chart** to GHCR.


## KEDA & HPA (Helm)
- **HPA**: CPU-based autoscaling for the API is enabled by default. Configure under `values.yaml → hpa.api`.
- **KEDA**: Worker scales by **Redis list length** (default queue `celery`). Configure under `values.yaml → keda.*`.
  - Install KEDA in your cluster first: `helm repo add kedacore https://kedacore.github.io/charts && helm install keda kedacore/keda`.
  - Make sure `keda.redisAddress` points to your Redis in-cluster address.

## Terraform (AWS)
Path: `infra/terraform/aws`

> ⚠️ For demo defaults only. Restrict `allowed_cidr`, use private subnets/NAT in prod, and enable multi-AZ.

### Usage
```bash
cd infra/terraform/aws
terraform init
terraform apply -auto-approve   -var 'region=eu-west-1'   -var 'project=agentic'   -var 'db_password=YOUR_STRONG_PASS'   -var 'allowed_cidr=YOUR_IP/32'
```

Outputs give you:
- `database_url` → set as `DATABASE_URL`
- `redis_url` → set as `REDIS_URL` and `REDIS_URL_QUEUE`
- `s3_bucket`  → set as `S3_BUCKET`

Then update your Helm `values.yaml` to point to those endpoints.


## Production on AWS (EKS + IRSA + External Secrets)
- Use **infra/terraform/aws-prod** to create a private VPC, EKS (IRSA), RDS, ElastiCache, and S3 (SSE-KMS).
- Install **External Secrets Operator** and **AWS Load Balancer Controller**.
- In `charts/agentic/values.yaml`:
  - Set `serviceAccount.annotations.eks.amazonaws.com/role-arn` to an IAM role that grants `secretsmanager:GetSecretValue` and S3 access.
  - Set `externalSecrets.dataFrom[0].extract.key` to the **Secrets Manager name** from Terraform outputs.
  - Optionally enable `ingress` for ALB.
- KEDA trigger can use `mode: streams` if you enqueue to a Redis Stream instead of a list.
