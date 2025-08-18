SHELL := /bin/bash
DC := docker compose
PROJECT := apisharayeh

.PHONY: help build up up-all down restart logs ps migrate seed fmt lint clean prune

help:
	@echo "Targets:"
	@echo "  build     - Build images"
	@echo "  up        - Start db, redis, migrate, api, worker"
	@echo "  up-all    - Start everything incl. caddy"
	@echo "  down      - Stop all"
	@echo "  restart   - Restart api & worker"
	@echo "  logs      - Tail api & worker logs"
	@echo "  ps        - List services"
	@echo "  migrate   - Run Alembic upgrade head"
	@echo "  seed      - Seed demo data"
	@echo "  clean     - Remove py caches"
	@echo "  prune     - Remove containers/images/volumes (DANGEROUS)"

build:
	$(DC) build

up:
	$(DC) up -d db redis
	$(DC) up -d migrate
	$(DC) up -d api worker

up-all:
	$(DC) up -d

down:
	$(DC) down

restart:
	$(DC) restart api worker

logs:
	$(DC) logs -f api worker

ps:
	$(DC) ps

migrate:
	$(DC) run --rm migrate sh -lc "python -m alembic -c db/alembic.ini upgrade head"

seed:
	$(DC) exec api python scripts/seed.py || true

fmt:
	python -m pip install --quiet black ruff
	ruff check --fix src || true
	black src || true

lint:
	python -m pip install --quiet ruff
	ruff check src

clean:
	find src -name "__pycache__" -type d -prune -exec rm -rf {} +
	find src -name "*.pyc" -delete

prune:
	$(DC) down -v --rmi local --remove-orphans
