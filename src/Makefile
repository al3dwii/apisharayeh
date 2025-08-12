PHONY: dev migrate seed test lint fmt openapi

dev: ## run local stack
docker compose -f infra/docker-compose.yml up --build

migrate: ## run alembic migrations
docker compose -f infra/docker-compose.yml exec api alembic upgrade head

seed:
docker compose -f infra/docker-compose.yml exec api python scripts/seed.py

test:
docker compose -f infra/docker-compose.yml exec api pytest -q

lint:
docker compose -f infra/docker-compose.yml exec api ruff check .

fmt:
docker compose -f infra/docker-compose.yml exec api ruff check --fix .

openapi:
curl -s localhost:8080/openapi.json | jq '.' > openapi.json

sdk: ## generate TypeScript + Python SDKs (requires API running locally)
	bash scripts/generate_sdks.sh

dash: ## start Grafana/Loki/Prometheus stack (included in docker-compose)
	echo "Grafana → http://localhost:3000  Prometheus → http://localhost:9090  Loki API → :3100"
