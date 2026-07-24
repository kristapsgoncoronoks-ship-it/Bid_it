"""Foundation guard: every tenant-scoped model MUST be registered in the ORM
tenant guard (`TENANT_MODELS`).

This is the single most important isolation invariant (see
docs/architecture/security-boundaries.md). The defence-in-depth guard only
scopes SELECTs for models it knows about; a new table with an `org_id` that is
NOT registered would silently bypass the guard — a cross-tenant leak waiting to
happen. This test fails the build the moment such a model is introduced, so the
omission is caught in code review, not in production.
"""

from __future__ import annotations

import app.models  # noqa: F401 — importing populates the mapper registry
from app.core.tenant import TENANT_MODELS
from app.models.base import Base

# Child tables reached only via an already-scoped parent legitimately have no
# org_id (line items, issued lines). They are covered by their parent's scope
# and are intentionally NOT in TENANT_MODELS. (expense_items USED to be such a
# child; Slice 2b denormalised org_id onto it, so it is now registered.)
_TENANT_COLUMN = "org_id"


def _models_with_org_id() -> set[str]:
    return {
        mapper.class_.__name__
        for mapper in Base.registry.mappers
        if _TENANT_COLUMN in mapper.columns.keys()
    }


def _registered() -> set[str]:
    return {model.__name__ for model in TENANT_MODELS}


def test_every_org_scoped_model_is_registered():
    """Any model carrying `org_id` must be in TENANT_MODELS, or the ORM guard
    cannot scope it and cross-tenant reads become possible."""
    missing = _models_with_org_id() - _registered()
    assert not missing, (
        "These models have an `org_id` but are NOT registered in "
        "app.core.tenant.TENANT_MODELS, so the tenant guard will not scope them "
        f"(potential cross-tenant leak): {sorted(missing)}. "
        "Add them to TENANT_MODELS."
    )


def test_registered_models_actually_have_org_id():
    """Guard against stale registrations: everything in TENANT_MODELS must carry
    an `org_id` column (otherwise the loader criteria would be malformed)."""
    orphaned = _registered() - _models_with_org_id()
    assert not orphaned, (
        "These models are registered in TENANT_MODELS but have no `org_id` "
        f"column: {sorted(orphaned)}. Remove them or add the column."
    )


def test_no_duplicate_registrations():
    """TENANT_MODELS should list each model exactly once."""
    names = [m.__name__ for m in TENANT_MODELS]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"Duplicate entries in TENANT_MODELS: {sorted(dupes)}"
