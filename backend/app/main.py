from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.tenant import TenantScopeMiddleware
from app.models import Base

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("invoiceiq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables if missing. In production Alembic owns schema evolution;
    # create_all is idempotent and keeps local/dev/test zero-setup.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "version": __version__, "app": settings.app_name}


app.include_router(api_router, prefix=settings.api_v1_prefix)
