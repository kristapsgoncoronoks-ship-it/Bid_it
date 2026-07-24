# ADR-0002 — FastAPI + async SQLAlchemy 2.0 + Pydantic v2

**Status:** Accepted

## Context
We need a typed, async-capable Python web stack with a mature ORM and automatic API schema generation, suited to a team that values correctness and speed of change.

## Selected approach
**FastAPI** (async, OpenAPI auto-generated) + **async SQLAlchemy 2.0** (asyncpg/aiosqlite) + **Alembic** migrations + **Pydantic v2** for request/response validation. JWT via `jose`, passwords via `passlib[bcrypt]`.

## Alternatives considered
- **Django + DRF** — batteries included, but heavier, sync-first, and its ORM is less explicit for our aggregate modelling.
- **Node/TypeScript (NestJS) + Prisma** — the product context's default; rejected because it would be a full rewrite of a working, tested stack for no requirement gain (see PRD §1 stack decision).
- **Go** — great runtime, but slower iteration for this team and weaker OCR/PDF/data ecosystem.

## Why appropriate
Non-blocking I/O scales on stateless replicas; SQLAlchemy 2.0 gives explicit, typed data access and the `do_orm_execute` hook that powers our tenant guard; FastAPI's generated OpenAPI is our API contract for free; Pydantic v2 validates at the edge. The team is productive in it and the test suite is green.

## Risks
- Async foot-guns (sync I/O in an async path, greenlet errors) → discipline + `run_in_threadpool` for CPU/blocking work.
- ORM guard depends on SQLAlchemy internals (`with_loader_criteria`) → covered by isolation tests; pinned versions.

## Revisit when
A hard performance ceiling in the Python runtime is measured for a specific hot path (extract that path to a worker/service in another language) — not a reason to move the whole stack.
