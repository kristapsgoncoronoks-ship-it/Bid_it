.PHONY: help install backend frontend seed test build up down logs fmt migrate migration

help:
	@echo "InvoiceIQ — dev commands"
	@echo "  make install    install backend (venv) + frontend deps"
	@echo "  make backend    run FastAPI on :8000 (SQLite by default)"
	@echo "  make frontend   run Vite dev server on :5173"
	@echo "  make seed       load the demo tenant (demo@invoiceiq.app / demo1234)"
	@echo "  make test       run the backend test suite"
	@echo "  make build      typecheck + production build of the frontend"
	@echo "  make up          docker-compose up (postgres + api + web on :8080)"
	@echo "  make down        docker-compose down"

install:
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

seed:
	cd backend && . .venv/bin/activate && python -m app.seed

migrate:            ## apply DB migrations (production schema source of truth)
	cd backend && . .venv/bin/activate && alembic upgrade head

migration:          ## autogenerate a migration: make migration m="add x"
	cd backend && . .venv/bin/activate && alembic revision --autogenerate -m "$(m)"

test:
	cd backend && . .venv/bin/activate && python -m pytest -q

build:
	cd frontend && npm run build

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f
