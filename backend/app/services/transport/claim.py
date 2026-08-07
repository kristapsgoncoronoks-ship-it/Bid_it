"""The VAT refund claim aggregate root — grain construction only (R1).

WO-49 (the M3 opener) ships exactly the claim-grain invariant and nothing of
the submission machinery: no lock acquisition (R4/R5), no checklist/period-end/
minimum-amount/deadline gates (R6-R10), no fee freezing (R13), no status
derivation (R17). Those are ADR-P3's G2.2/G2.6/G2.7/G2.9 — separate, future
work orders. This module gives them a stable aggregate-root constructor to
build on: `get_or_create_claim` is idempotent on the grain key so a future
gate can always "get me the claim for this key" without worrying about
duplicate creation, and every future mutation of a claim reuses THIS lookup
rather than re-deriving it.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit, issuer, modules

# Art. 16 Dir. 2008/9/EC: a period is a calendar quarter or a calendar year
# (shorter periods only ever occur as the remainder of a year, which is still
# filed under the YEAR bucket in this model — R6 "annual = mop-up" is a future
# gate, not this order's concern). "YYYY-Q1".."YYYY-Q4" or "YYYY-YEAR".
_PERIOD_RE = re.compile(r"^(?P<year>\d{4})-(?:Q(?P<q>[1-4])|YEAR)$")


def validate_ref_period(ref_period: str) -> None:
    """Refuse a malformed period at the service boundary (schemas catch shape;
    this is the business-rule form of that same check, per the master-context
    DoD "validation at the schema boundary AND the invariant in the service").
    Raises `ValidationError` (422, code=invalid_period); never silently
    accepts a value the DB CHECK constraint would also have to catch."""
    if not _PERIOD_RE.match(ref_period):
        raise ValidationError(
            f"'{ref_period}' is not a valid claim period — use YYYY-Q1..YYYY-Q4 or YYYY-YEAR",
            code="invalid_period",
        )


async def list_claims(
    db: AsyncSession, org_id: str, *, year: int | None = None
) -> list[VatRefundClaim]:
    """Every claim in the org, newest first (WO-76 — the first route-facing
    read accessor). Module-gated exactly like every other transport service
    entry point (defense-in-depth: the route layer's structural permission
    check is the caller-facing gate, but the transport rule since WO-49 is
    that NO service entry point trusts its caller to have checked the
    entitlement — fails CLOSED so an un-entitled org stays byte-identical
    to before the vertical existed, ADR-P3 rule 3).

    `year` (WO-81, additive — the default is byte-identical to WO-76's
    behaviour) narrows to one REFUND year, matched on the claim's own
    `ref_period` prefix: `"YYYY-Qn"`/`"YYYY-YEAR"` always begins with the
    four-digit year (`_PERIOD_RE`), so a `LIKE 'YYYY-%'` is exact, not a
    heuristic. It lives HERE rather than in the caller because the
    cash-recovery dashboard (G4.3/R38) is per-year and R38's own acceptance
    line forbids forking the claims query: one claim-listing query, one
    filter, every consumer.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    where = [VatRefundClaim.org_id == org_id]
    if year is not None:
        where.append(VatRefundClaim.ref_period.like(f"{year}-%"))

    return list(
        await db.scalars(
            select(VatRefundClaim)
            .where(*where)
            .order_by(VatRefundClaim.created_at.desc(), VatRefundClaim.id)
        )
    )


async def get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """Org-scoped by-id read (WO-76). A cross-tenant claim id is
    indistinguishable from an unknown one (master-context §4.4: opaque 404,
    never 403). Module gate first, same rationale as `list_claims`."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await db.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    if claim is None:
        raise NotFoundError("Claim not found", code="claim_not_found")
    return claim


async def get_or_create_claim(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    refund_country: str,
    ref_period: str,
) -> VatRefundClaim:
    """R1 — the claim grain `(org, entity, refund_country, ref_period)`.
    Idempotent: calling this twice with the same key returns the SAME row,
    never a duplicate (the `uq_vat_refund_claims_grain` constraint is the
    backstop if two callers ever race — see `tests/transport/
    test_r1_claim_grain.py::test_r1_grain_uniqueness_constraint_rejects_a_raw_duplicate_insert`).

    Gated on the `transport` module entitlement FIRST, before any query, so an
    org that has not activated the vertical is byte-identical to before this
    module existed (ADR-P3 rule 3) — this is the one reachable entry point
    into the transport tables today; the (future) API routes will carry their
    own router-level gate the same way `partners`/`recurring`/`issued` already
    do, at which point this check stays as defense-in-depth, not the only one.

    Uses `modules.is_enabled` (a plain bool check) rather than `modules.
    require_enabled` deliberately: the latter raises `fastapi.HTTPException`
    directly, which is the right shape for a ROUTE-layer `_guard` (every
    existing caller of `require_enabled` is one) but wrong for a SERVICE,
    which must signal failure with `app.core.errors.AppError` — the single
    wire-contract-consistent error vocabulary (`{"detail","code"}`), never a
    web-framework exception (master-context §"services raise AppError").
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    refund_country = refund_country.upper()
    validate_ref_period(ref_period)

    # Reads the AP/AR core through its OWN service (issuer.get_by_id), never a
    # raw cross-domain join (ADR-P3 rule 2 / VAT_HARVEST E.2).
    entity = await issuer.get_by_id(db, org_id, entity_id)
    if entity is None:
        raise NotFoundError("Entity not found", code="entity_not_found")

    existing = await db.scalar(
        select(VatRefundClaim).where(
            VatRefundClaim.org_id == org_id,
            VatRefundClaim.entity_id == entity_id,
            VatRefundClaim.refund_country == refund_country,
            VatRefundClaim.ref_period == ref_period,
        )
    )
    if existing is not None:
        return existing

    claim = VatRefundClaim(
        org_id=org_id,
        entity_id=entity_id,
        refund_country=refund_country,
        ref_period=ref_period,
    )
    db.add(claim)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_CLAIM_CREATE,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta={"entity_id": entity_id, "refund_country": refund_country, "ref_period": ref_period},
        org_id=org_id,  # explicit — this service is also callable from a job/
        # test with no ambient request-scoped org context (audit.record's
        # `org_id or get_current_org()` fallback would otherwise silently no-op).
    )
    return claim
