from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.tenant import TenantScopeMiddleware
from app.models import Base

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("invoiceiq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev/test: create tables directly for zero-setup. Production owns schema
    # evolution through Alembic (`alembic upgrade head`, run before boot), so we
    # do NOT create_all there — it would race the migrations' version tracking.
    if settings.environment != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        log.info("Production: schema is managed by Alembic (run `alembic upgrade head`)")

    # Seed the bundled ECB rate snapshot if the cache is empty, so FX conversion
    # works before the first live refresh. Best-effort — never blocks startup.
    from datetime import date

    from app.core.database import SessionLocal
    from app.services import fx

    try:
        async with SessionLocal() as db:
            seeded = await fx.ensure_seed_rates(db, date.today())
            if seeded:
                log.info("Seeded %d bundled ECB fallback rates", seeded)
            # Guarantee full European-currency coverage (incl. the non-ECB ones).
            covered = await fx.ensure_european_coverage(db, date.today())
            if covered:
                log.info("Seeded %d indicative rows for European currencies", covered)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("ECB rate seeding skipped: %s", exc)

    log.info("%s %s ready (%s)", settings.app_name, __version__, settings.environment)
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    summary="Invoice Data Analytics Platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Outermost: bound the tenant-scope ContextVar to each request.
app.add_middleware(TenantScopeMiddleware)
# Security response headers (HSTS on HTTPS, nosniff, frame-deny, referrer policy).
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": __version__, "app": settings.app_name}


app.include_router(api_router, prefix=settings.api_v1_prefix)
