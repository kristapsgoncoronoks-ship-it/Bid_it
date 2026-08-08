"""Transport-vertical route package (ADR-P3: `api/routes/transport/`).

WHY THE PACKAGE EXPOSES ONE AGGREGATING `router`
--------------------------------------------------
`tests/test_authz_coverage.py` enumerates `pkgutil.iter_modules(app.api.
routes.__path__)` and reads each entry's top-level `router` attribute — for
a PACKAGE that means THIS module. Aggregating every slice's router here
keeps the transport routes inside the structural-coverage net (a package
without a top-level `router` would silently escape the CI check — the exact
unclassified-route failure mode ADR-0024 exists to prevent). `excise.py`
(G4.6/WO-91) is the slice this paragraph named while it was still missing;
it, and every future one, includes itself HERE, not in `app/api/router.py`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.transport import (
    admin,
    claims,
    customers,
    excise,
    fuel,
    overcharges,
    rebates,
    recovery,
    savings,
)

router = APIRouter()
router.include_router(claims.router)
router.include_router(admin.router)
router.include_router(customers.router)
router.include_router(fuel.router)
router.include_router(recovery.router)
router.include_router(overcharges.router)
router.include_router(rebates.router)
router.include_router(savings.router)
router.include_router(excise.router)
