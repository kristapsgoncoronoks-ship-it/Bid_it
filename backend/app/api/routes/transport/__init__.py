"""Transport-vertical route package (ADR-P3: `api/routes/transport/`).

WHY THE PACKAGE EXPOSES ONE AGGREGATING `router`
--------------------------------------------------
`tests/test_authz_coverage.py` enumerates `pkgutil.iter_modules(app.api.
routes.__path__)` and reads each entry's top-level `router` attribute — for
a PACKAGE that means THIS module. Aggregating every slice's router here
keeps the transport routes inside the structural-coverage net (a package
without a top-level `router` would silently escape the CI check — the exact
unclassified-route failure mode ADR-0024 exists to prevent). Future slices
(`fuel.py`, `recovery.py`, `excise.py`, `overcharges.py` — the ARCH_plan
file list) include themselves HERE, not in `app/api/router.py`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.transport import admin, claims, customers, fuel

router = APIRouter()
router.include_router(claims.router)
router.include_router(admin.router)
router.include_router(customers.router)
router.include_router(fuel.router)
