"""The claim status lifecycle 1A->5 (G2.7; `BA_fleet_fuel.md` §3.D, R12/R17).

Two layers, per the harvested design: a coarse ENGINE status that drives
locks/fees (`VatRefundClaim.status`: draft/submitted/approved/paid/
withdrawn/rejected — unchanged by this module) and a controllable WORKFLOW
CODE (`VatRefundClaim.status_code`, a nullable string column shipped by
WO-49 but never read or written until this order).

`AUTO_CODES` (1A/1B/1C/1E) are SYSTEM-DERIVED and never user-settable (D1,
R17) — `derive_stage` is the read-only preview a future caller (a status
dashboard, the eventual G2.10 checklist consumer) uses to know which one
currently applies to a `draft` claim; `set_status_code` is the ONLY writer
of `status_code`, and refuses to ever accept an `AUTO_CODES` value.

WHY `set_status_code` NEVER TOUCHES `claim.status` (THE ENGINE COLUMN)
------------------------------------------------------------------------
D2 describes an `ENGINE_OF` mapping from each manual code to a coarse
engine state (e.g. `"3A"` (money received) -> `paid`). Building that
mapping FOR REAL would mean this order also builds the "money received"/
"decision received"/"rejected" transitions — which collide with G2.9's fee
freezing (`compute_fee`, `record_payment`, `payout_to`), explicitly
decision-gated (no established customer/fee-rate concept to build against,
`docs/DECISIONS-NEEDED.md` §10). Rather than invent a fee-free stand-in for
those transitions, this order deliberately narrows `set_status_code` to
managing ONLY the workflow-code LABEL (+ `action_deadline`, R12) — the
engine-state transitions stay explicit future work, entangled with G2.9.
This is the same fail-toward-blocking discipline `is_synthetic()`'s own
docstring states: refusing to guess is safer than a wrong engine-state
transition that silently bypasses a fee invariant nobody has built yet.

WHY `derive_stage`'S "NON-PERIOD CHECKLIST ITEMS" NOW CALL
`checklist.submission_checklist` (G2.10 SLICE 1, WO-60)
------------------------------------------------------------------------
D3's "all non-period checklist items pass?" describes `submission_
checklist()` — G2.10, the adjustable checklist as data. WO-59 shipped a
documented two-check PROXY for this (an unresolved line + a missing
document) with an explicit note that G2.10 would REPLACE it, not sit
alongside it. This module now does exactly that: every item
`checklist.submission_checklist` returns except `"period_ended"` is a
"non-period item"; any of them failing -> `"1A"`. The proxy's own two
checks are still IN there (`unresolved_refs`/`receipt_control`,
`documents_attached`), now alongside the two adjustable `customer`-scope
DATA rules (`customer_data`, `bank_account`) — see `checklist.py`'s own
module docstring for why only those two of the six harvested defaults are
evaluable today.

WHY THE "CAVEAT" (1C) SIGNAL IS A DOCUMENTED INTERPRETATION
------------------------------------------------------------
D3 says only "verdict has a caveat (not READY*)" without defining what
produces one — the harvested checklist engine that would normally emit
that verdict is G2.10. This module's reading: a caveat exists when the
claim would need an explicit override to submit cleanly — either it is
currently below the Art. 17 minimum (`minimum.below_minimum`, previewed
via `freeze.preview_vat_base`, exactly the same preview `lock.submit_claim`
uses for its own R8 gate) or it carries an active receipt-control waiver
(`waiver.waived_suppliers` non-empty — a waiver is, by definition, an
accepted compromise: "we know this money is unclaimable", not a
full-strength pass).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionError, ValidationError
from app.models.transport.vat_claim import VatRefundClaim
from app.services import audit, modules
from app.services.transport.checklist import submission_checklist
from app.services.transport.deadline import period_ended
from app.services.transport.freeze import preview_vat_base
from app.services.transport.minimum import below_minimum
from app.services.transport.waiver import waived_suppliers

# D1/R17 — system-derived, never user-settable via `set_status_code`.
AUTO_CODES = ("1A", "1B", "1C", "1E")

# D2's manual codes, EXCLUDING "2" (Submit) — that transition only ever
# happens through `lock.submit_claim`'s gated procedure (freeze + lock +
# status flip), never as a bare label write through this module. See the
# module docstring for why the ENGINE_OF engine-state mapping itself is
# deferred; this module stamps the LABEL only.
MANUAL_CODES = ("2A", "2B", "3", "3A", "3B", "3C", "3D", "4", "4A", "5")

# Mirrors `lock._LOCK_HOLDING_STATUSES` exactly (R17 "before the claim is
# submitted" means "not yet in a locking engine state") — imported, not
# re-derived, so the two can never drift.
_LOCKED_STATUSES = ("submitted", "approved", "paid")


async def _get_claim(db: AsyncSession, org_id: str, claim_id: str) -> VatRefundClaim:
    """Org-scoped lookup — a cross-tenant claim id is indistinguishable from
    an unknown one (master-context §4.4: opaque 404, never 403)."""
    claim = await db.scalar(
        select(VatRefundClaim).where(VatRefundClaim.id == claim_id, VatRefundClaim.org_id == org_id)
    )
    if claim is None:
        raise NotFoundError("Claim not found", code="claim_not_found")
    return claim


async def derive_stage(
    db: AsyncSession, org_id: str, claim_id: str, *, today: date | None = None
) -> str:
    """D3, verbatim order — read-only, writes nothing. Refuses (409,
    `code="claim_not_draft"`) on a non-`draft` claim: this preview only
    means something BEFORE submission (once locked, the workflow moves
    into `set_status_code`'s manual-code space instead).

    Order (D3 literal): any non-period item failing -> `"1A"` (checked
    FIRST, even if the period also hasn't ended — the harvested order,
    preserved even though checking period-end first might seem more
    natural); else period not ended -> `"1B"`; else a caveat is present
    (see module docstring) -> `"1C"`; else `"1E"`. "Non-period items" are
    every `checklist.submission_checklist` item except `"period_ended"`
    (G2.10 slice 1, WO-60 — see module docstring).

    `today` is a test seam (defaults to `date.today()` inside `deadline.
    period_ended`), mirroring `lock.submit_claim`'s own `today` parameter —
    so period-end can be asserted deterministically without patching the
    clock.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    claim = await _get_claim(db, org_id, claim_id)
    if claim.status != "draft":
        raise ConflictError(
            f"Claim is '{claim.status}', not 'draft' — no stage to derive",
            code="claim_not_draft",
        )

    items = await submission_checklist(db, org_id, claim_id, today=today)
    non_period = [i for i in items if i.key != "period_ended"]
    if any(not i.ok for i in non_period):
        return "1A"

    if not period_ended(claim.ref_period, today=today):
        return "1B"

    vat_eur, vat_local, _currency = await preview_vat_base(db, org_id, claim_id)
    if below_minimum(
        country=claim.refund_country,
        ref_period=claim.ref_period,
        vat_eur=vat_eur,
        vat_local=vat_local,
    ):
        return "1C"
    if await waived_suppliers(db, org_id, claim_id):
        return "1C"

    return "1E"


async def list_status_codes(
    db: AsyncSession, org_id: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The status-code VOCABULARY `(AUTO_CODES, MANUAL_CODES)` (WO-77 — the
    route-facing read). This codebase deliberately has NO label mapping:
    Fleet Fuel's `STATUS_LABELS` was never harvested as data (master-context
    §10 — rules survive as specification, bytes are never copied), so the
    two code tuples ARE the vocabulary a caller can offer. Module-gated even
    though the tuples are static: an un-entitled org must stay byte-
    identical to before the vertical existed (ADR-P3 rule 3 — the same
    reason every transport surface 403s `module_not_enabled`)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")
    return AUTO_CODES, MANUAL_CODES


async def set_status_code(
    db: AsyncSession,
    org_id: str,
    claim_id: str,
    code: str,
    *,
    action_deadline: date | None = None,
) -> VatRefundClaim:
    """R17 — refuses `AUTO_CODES` (`code="status_code_system_controlled"`,
    "is system-controlled — it follows the checklist automatically") and
    refuses any `MANUAL_CODES` value while the claim is still `draft`
    (`code="claim_not_submitted"`, "can't set '<code>' before the claim is
    submitted" — D4's own wording). On an already-locked claim, stamps the
    workflow `status_code` label and, when given, `action_deadline` (R12 —
    a SOFT reminder: never auto-reject, never touches `claim.status`, never
    blocks any other action). See module docstring for why the coarse
    engine `status` column is never touched here.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    if code in AUTO_CODES:
        raise ConflictError(
            f"'{code}' is system-controlled — it follows the checklist automatically.",
            code="status_code_system_controlled",
        )
    if code not in MANUAL_CODES:
        raise ValidationError(f"Unknown status code '{code}'", code="unknown_status_code")

    claim = await _get_claim(db, org_id, claim_id)
    if claim.status not in _LOCKED_STATUSES:
        raise ConflictError(
            f"Can't set '{code}' before the claim is submitted", code="claim_not_submitted"
        )

    old_code = claim.status_code
    claim.status_code = code
    if action_deadline is not None:
        claim.action_deadline = action_deadline
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_STATUS_CODE_SET,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta={
            "old_status_code": old_code,
            "new_status_code": code,
            "action_deadline": action_deadline.isoformat() if action_deadline else None,
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return claim
