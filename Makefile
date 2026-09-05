.PHONY: help install backend frontend worker seed test perf perf-shape lint fmt typecheck check openapi build up down logs migrate migration

help:
	@echo "InvoiceIQ — dev commands"
	@echo "  make install    install backend (venv) + frontend deps"
	@echo "  make backend    run FastAPI on :8000 (SQLite by default)"
	@echo "  make frontend   run Vite dev server on :5173"
	@echo "  make worker     run the background job worker"
	@echo "  make seed       load the demo tenant (demo@invoiceiq.app / demo1234)"
	@echo "  make test       run the backend test suite"
	@echo "  make lint       ruff lint + format check (backend)"
	@echo "  make fmt        ruff auto-format + lint autofix (backend)"
	@echo "  make typecheck  mypy on the foundation layer (app/core)"
	@echo "  make check      lint + typecheck + test (the CI gate, locally)"
	@echo "  make openapi    regenerate docs/api/openapi.json (the checked-in API contract) from the live schema"
	@echo "  make build      typecheck + production build of the frontend"
	@echo "  make up          docker-compose up (postgres + api + web on :8080)"
	@echo "  make down        docker-compose down"

install:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
	cd frontend && npm install

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

worker:
	cd backend && . .venv/bin/activate && python -m app.worker

seed:
	cd backend && . .venv/bin/activate && python -m app.seed

lint:
	cd backend && . .venv/bin/activate && ruff check app tests && ruff format --check app tests

fmt:
	cd backend && . .venv/bin/activate && ruff check --fix app tests && ruff format app tests

typecheck:
	cd backend && . .venv/bin/activate && mypy app/core

check: lint typecheck test

openapi:
	cd backend && . .venv/bin/activate && python -m app.openapi ../docs/api/openapi.json

migrate:            ## apply DB migrations (production schema source of truth)
	cd backend && . .venv/bin/activate && alembic upgrade head

migration:          ## autogenerate a migration: make migration m="add x"
	cd backend && . .venv/bin/activate && alembic revision --autogenerate -m "$(m)"

test:
	cd backend && . .venv/bin/activate && python -m pytest -q

perf:               ## measure the read paths: make perf PERF_URL=postgresql+asyncpg://... [SCALE=400]
	@test -n "$(PERF_URL)" || { echo "set PERF_URL to a MIGRATED Postgres URL (see docs/perf/)"; exit 2; }
	cd backend && . .venv/bin/activate && \
		DATABASE_URL="$(PERF_URL)" python scripts/perf_harness.py --scale $(or $(SCALE),400)

perf-shape:         ## the gate: 4x the data, cap the slowdown. make perf-shape PERF_URL=... [SCALE=2000]
	@test -n "$(PERF_URL)" || { echo "set PERF_URL to a MIGRATED Postgres URL (see docs/perf/)"; exit 2; }
	cd backend && . .venv/bin/activate && \
		DATABASE_URL="$(PERF_URL)" python scripts/perf_harness.py --shape --scale $(or $(SCALE),2000)

build:
	cd frontend && npm run build

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f
