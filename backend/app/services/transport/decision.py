"""The member state's answer to a filed claim — §13's decision transition (WO-L).

Owner-decided 2026-08-08 (docs/DECISIONS-NEEDED.md §13): *"figures do not
change after a claim is submitted; they change only on partial rejection,
when some invoices in a claim are rejected."* Until this module, nothing in
the tree could move a claim past `submitted` — `approved`/`rejected` existed
in the status vocabulary (and in `lock._LOCK_HOLDING_STATUSES`) with no
writer. This is that writer, and the ONLY one:

- **approved** — the full claim stood: `status=approved`, `decision_date` +
  `approved_date` stamped, every frozen figure untouched.
- **rejected** — the whole claim refused: `status=rejected`, frozen figures
  STAND (fee.py's R5 reading: nothing in the harvest clears a frozen figure
  on an adverse outcome; the fee dispute is commercial, not accounting).
- **partial** — some invoices rejected: the named frozen lines get
  `rejected_at`, the claim's `vat_eur`/`vat_local` shrink to the surviving
  base, and the fee is recomputed on the CHANGED BASE at the FROZEN rate —
  `fee.compute_fee(new_base, claim.fee_pct, claim.fee_min)`, exactly the
  seam fee.py documented ("Only the fee BASE changes; the frozen
  rate/minimum are never re-derived"). The claim then stands `approved` for
  the remainder.

**WO-T added a FOURTH transition to this module: `approved -> paid`.** It
lives here rather than in a module of its own because the property that makes
this lifecycle auditable is that every post-submission edge is written in one
place; scattering the last one would be the drift the WO-82 edge-set pin exists
to prevent. It is a different KIND of event, though, and the difference drives
its rules: a decision is the member state's answer, while payment is money
landing in a bank account. So `paid_amount` is REQUIRED and never derived — the
refund that arrived is a bank fact, and defaulting it to the approved base
would quietly assert that they matched when the whole reason to record it is
that they sometimes do not.

Refusals are total: an outcome outside the three, a decision on anything but
a `submitted` claim, unknown invoice refs, or a "partial" that rejects every
line (that is a full rejection wearing the wrong name) — all fail CLOSED
with their own codes. Payment refuses just as hard: anything but an `approved`
claim (which makes double-payment structurally impossible, since the first
payment leaves it `paid`), a non-positive amount, or a payment dated before the
approval it answers. Locks are NOT touched: an approved/rejected claim
keeps its one-invoice-one-submission locks per R5 (`lock.py`'s
`_LOCK_HOLDING_STATUSES` already counts `approved`; a rejected claim's locks
are released only through the existing `withdraw` path — deliberately NOT
invented here, the harvest draws no such edge).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.core.money import q2
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services import audit as audit_svc
from app.services.transport import claim as claim_svc
from app.services.transport import fee, queries

OUTCOMES = ("approved", "rejected", "partial")


async def record_decision(
    db: AsyncSession,
    org_id: str,
    claim_id: str,
    *,
    outcome: str,
    rejected_refs: list[str] | None = None,
    decision_date: date | None = None,
) -> VatRefundClaim:
    claim = await claim_svc.get_claim(db, org_id, claim_id)

    if outcome not in OUTCOMES:
        raise ValidationError(
            f"'{outcome}' is not a decision outcome — use one of: " + ", ".join(OUTCOMES),
            code="invalid_decision_outcome",
        )
    if claim.status != "submitted":
        raise ConflictError(
            f"A '{claim.status}' claim is not awaiting a decision — only a submitted one is",
            code="claim_not_awaiting_decision",
        )
    if outcome != "partial" and rejected_refs:
        raise ValidationError(
            "Rejected invoice refs belong to a 'partial' outcome only",
            code="rejected_refs_not_applicable",
        )

    when = decision_date or date.today()
    old = {
        "status": claim.status,
        "vat_eur": str(claim.vat_eur),
        "vat_local": None if claim.vat_local is None else str(claim.vat_local),
        "fee_eur": None if claim.fee_eur is None else str(claim.fee_eur),
    }
    meta: dict = {"outcome": outcome, "decision_date": when.isoformat(), "old": old}

    if outcome == "approved":
        claim.status = "approved"
        claim.approved_date = when
    elif outcome == "rejected":
        claim.status = "rejected"
    else:
        meta["rejected_refs"] = sorted(rejected_refs or [])
        await _apply_partial(db, org_id, claim, rejected_refs or [], when)
        claim.status = "approved"
        claim.approved_date = when

    claim.decision_date = when
    await db.flush()
    meta["new"] = {
        "status": claim.status,
        "vat_eur": str(claim.vat_eur),
        "vat_local": None if claim.vat_local is None else str(claim.vat_local),
        "fee_eur": None if claim.fee_eur is None else str(claim.fee_eur),
    }
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_CLAIM_DECISION,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta=meta,
        org_id=org_id,
    )
    return claim


async def record_payment(
    db: AsyncSession,
    org_id: str,
    claim_id: str,
    *,
    paid_amount: Decimal,
    paid_date: date | None = None,
) -> VatRefundClaim:
    """The refund landed: `approved -> paid`, with the amount and the date.

    Closes the loop `recovery.median_days_to_refund` has been waiting on since
    WO-81 — that measure reads `submitted_date` (stamped by `lock.submit_claim`
    since WO-T) and `paid_date` (stamped here), and reported `null` forever
    because neither had a writer.

    Fails CLOSED on every edge that is not `approved -> paid`. Double-payment is
    refused by that rule alone — the first payment leaves the claim `paid`,
    which is not `approved` — so the status IS the interlock and no separate
    guard is load-bearing. The explicit `claim_already_paid` branch below exists
    only to say something more useful than "this one is 'paid'".
    """
    claim = await claim_svc.get_claim(db, org_id, claim_id)

    if claim.status == "paid":
        raise ConflictError("This claim is already recorded as paid", code="claim_already_paid")
    if claim.status != "approved":
        raise ConflictError(
            f"A refund can only be recorded against an approved claim — this one is "
            f"'{claim.status}'",
            code="claim_not_approved",
        )

    amount = q2(paid_amount)
    if amount <= Decimal("0"):
        raise ValidationError(
            "A recorded refund is a positive amount", code="paid_amount_not_positive"
        )

    when = paid_date or date.today()
    if claim.approved_date is not None and when < claim.approved_date:
        raise ValidationError(
            f"A refund cannot be dated {when.isoformat()}, before the approval on "
            f"{claim.approved_date.isoformat()}",
            code="paid_date_before_approval",
        )

    old = {
        "status": claim.status,
        "paid_date": None if claim.paid_date is None else claim.paid_date.isoformat(),
        "paid_amount": None if claim.paid_amount is None else str(claim.paid_amount),
    }

    claim.status = "paid"
    claim.paid_date = when
    claim.paid_amount = amount
    await db.flush()

    # The variance is audited, not asserted anywhere on the claim: the approved
    # base and the amount that arrived are separate facts, and a reader deserves
    # to see both without recomputing the difference from two screens.
    approved_base = None if claim.vat_eur is None else q2(Decimal(claim.vat_eur))
    await audit_svc.record(
        db,
        audit_svc.A.TRANSPORT_CLAIM_PAID,
        target_type="vat_refund_claim",
        target_id=claim.id,
        meta={
            "old": old,
            "new": {
                "status": claim.status,
                "paid_date": when.isoformat(),
                "paid_amount": str(amount),
            },
            "approved_vat_eur": None if approved_base is None else str(approved_base),
            "variance_eur": None if approved_base is None else str(amount - approved_base),
            "days_to_refund": (
                None if claim.submitted_date is None else (when - claim.submitted_date).days
            ),
        },
        org_id=org_id,
    )
    return claim


async def _apply_partial(
    db: AsyncSession,
    org_id: str,
    claim: VatRefundClaim,
    rejected_refs: list[str],
    when: date,
) -> None:
    refs = sorted({r.strip() for r in rejected_refs if r and r.strip()})
    if not refs:
        raise ValidationError(
            "A partial rejection names the rejected invoice refs — none were given",
            code="rejected_refs_required",
        )

    frozen: list[VatRefundClaimLine] = list(
        await db.scalars(queries.vat_claim_lines(org_id, claim.id, frozen=True))
    )
    by_ref: dict[str, list[VatRefundClaimLine]] = {}
    for line in frozen:
        by_ref.setdefault(line.invoice_ref, []).append(line)

    unknown = [r for r in refs if r not in by_ref]
    if unknown:
        raise ValidationError(
            "These refs are not frozen lines of this claim: " + ", ".join(unknown),
            code="rejected_refs_unknown",
        )

    surviving = [line for line in frozen if line.invoice_ref not in refs]
    if not surviving:
        raise ValidationError(
            "This rejects every invoice on the claim — record a full 'rejected' "
            "decision instead of a partial one",
            code="partial_rejects_everything",
        )

    stamp = datetime.now(UTC)
    for r in refs:
        for line in by_ref[r]:
            line.rejected_at = stamp

    # The changed base at the FROZEN rate — fee.py's documented seam, verbatim.
    new_base = q2(sum((Decimal(line.vat_eur) for line in surviving), Decimal("0")))
    claim.vat_eur = new_base
    locals_ = [line.vat_local for line in surviving]
    claim.vat_local = (
        q2(sum((Decimal(v) for v in locals_ if v is not None), Decimal("0")))
        if all(v is not None for v in locals_)
        else None
    )
    if claim.fee_pct is not None and claim.fee_min is not None:
        new_fee, _basis = fee.compute_fee(new_base, Decimal(claim.fee_pct), Decimal(claim.fee_min))
        claim.fee_eur = new_fee
