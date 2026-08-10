"""WO-95 — the fee FREEZE at submission (G2.9, R13, `BA_fleet_fuel.md` C10).

C10, verbatim: *"On first entry to a locking state: freeze `vat_eur` **and**
`vat_local` computed over **exactly the locked `claim_set`** … Freeze `fee_pct`,
`fee_min`, `fee_eur`."* R13's acceptance line is the invariant: *"Change the
customer fee % after submission ⇒ the claim's fee is unchanged."*

Every claim here reaches its state through the REAL service — `lock.submit_claim`
runs the whole gate stack, freezes the lines, resolves the rate and stamps the
fee. Nothing writes a fee column by hand.

The fixture helpers come from `test_wo93_client_status.py` (the WO-94 precedent
for reusing them): one documented, resolved line per claim, a period that has
ended, and an activated customer — everything the gates ahead of the fee gate
need, so a refusal in these tests is always the fee's own.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import claim_lines, fee, lock
from tests.transport.conftest import (
    FIXTURE_FEE_MIN,
    FIXTURE_FEE_PCT,
    enable_transport,
    make_entity,
    make_org,
)
from tests.transport.test_wo93_client_status import (
    PERIOD_ENDED,
    _clean_claim,
    _make_claim,
)


async def _org_with_entity(db_session, *, fee_rate: bool = True):
    """`test_wo93_client_status._org_with_entity`, with the fee-rate seeding
    made explicit so a test can turn it off and exercise the refusal."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id, fee_rate=fee_rate)
    entity = await make_entity(db_session, org.id)
    from tests.transport.conftest import activate_entity

    await activate_entity(db_session, org.id, entity.id, "LV")
    await db_session.commit()
    return org.id, entity.id


async def _submit(db_session, org_id: str, entity_id: str, **kw) -> VatRefundClaim:
    claim, txn = await _clean_claim(db_session, org_id, entity_id, **kw)
    supplier = kw.get("supplier", "Q8")
    number = kw.get("number", "INV-0001")
    result = await lock.submit_claim(
        db_session,
        org_id,
        claim_id=claim.id,
        invoices=[(supplier, number, txn.id)],
        today=PERIOD_ENDED,
    )
    await db_session.commit()
    return result


def _meta(event: AuditEvent) -> dict:
    return json.loads(event.meta) if isinstance(event.meta, str) else event.meta


# --------------------------------------------------------------------------- #
# 1. The freeze itself, against hand-computed Decimals
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_submission_freezes_the_fee_from_the_frozen_base(db_session):
    """C10. The claim's ONE line carries €420.00 of VAT, so the frozen base is
    €420.00; the fixture standard rate is 10% / €25.00 minimum, so 10% of
    €420.00 = €42.00 clears the minimum and is what is charged."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _submit(db_session, org_id, entity_id)

    assert claim.vat_eur == Decimal("420.00")
    assert claim.fee_pct == FIXTURE_FEE_PCT == Decimal("10.00")
    assert claim.fee_min == FIXTURE_FEE_MIN == Decimal("25.00")
    assert claim.fee_eur == Decimal("42.00")


@pytest.mark.asyncio
async def test_wo95_submission_freezes_the_minimum_when_it_bites(db_session):
    """The other branch of C11's `max()` on a REAL claim. The base cannot simply
    be made small — R8's Art. 17 gate refuses a quarterly LV claim below €400
    long before the fee is reached — so the RATE is what makes the minimum bite:
    2% of €420.00 = €8.40, below the €25.00 standard minimum, so €25.00 is
    frozen and the audit trail records the basis as `minimum`."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(
        db_session,
        org_id,
        entity_id=entity_id,
        fee_pct=Decimal("2.00"),
        fee_min=Decimal("25.00"),
    )
    await db_session.commit()
    claim = await _submit(db_session, org_id, entity_id)

    assert claim.vat_eur == Decimal("420.00")
    assert claim.fee_eur == Decimal("25.00")

    events = await db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "transport.claim_submit"
        )
    )
    assert _meta(list(events)[0])["fee_basis"] == "minimum"


@pytest.mark.asyncio
async def test_wo95_a_per_client_rate_overrides_the_standard_on_a_real_claim(db_session):
    """R40's *"a per-client fee overrides it"*, proven end to end rather than in
    the resolver alone: 25% of €420.00 = €105.00, not the standard's €42.00."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(
        db_session,
        org_id,
        entity_id=entity_id,
        fee_pct=Decimal("25.00"),
        fee_min=Decimal("0.00"),
    )
    await db_session.commit()

    claim = await _submit(db_session, org_id, entity_id)
    assert (claim.fee_pct, claim.fee_min, claim.fee_eur) == (
        Decimal("25.00"),
        Decimal("0.00"),
        Decimal("105.00"),
    )


@pytest.mark.asyncio
async def test_wo95_a_per_country_rate_overrides_the_client_default(db_session):
    """C11's most specific rung, on a real LV claim: 5% of €420.00 = €21.00,
    which is below the client default's answer and above nothing — the point is
    that the LV row won."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(
        db_session, org_id, entity_id=entity_id, fee_pct=Decimal("25.00"), fee_min=Decimal("0.00")
    )
    await fee.set_rate(
        db_session,
        org_id,
        entity_id=entity_id,
        country="LV",
        fee_pct=Decimal("5.00"),
        fee_min=Decimal("0.00"),
    )
    await db_session.commit()

    claim = await _submit(db_session, org_id, entity_id)
    assert (claim.fee_pct, claim.fee_eur) == (Decimal("5.00"), Decimal("21.00"))


# --------------------------------------------------------------------------- #
# 2. The fail-closed refusal — and that nothing is written
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_submission_refuses_when_no_rate_is_configured_and_writes_nothing(db_session):
    """The governing constraint, end to end. The claim passes every statutory
    gate and is refused on the fee alone — and because the rate resolves BEFORE
    the freeze, the rollback leaves the claim exactly as it was: still `draft`,
    no frozen base, no frozen lines, no locks, no submit audit event.

    "Nothing written" is asserted column by column and row by row, not inferred
    from the exception."""
    org_id, entity_id = await _org_with_entity(db_session, fee_rate=False)
    claim, txn = await _clean_claim(db_session, org_id, entity_id)
    # Read the ids BEFORE the refusal: the rollback expires every ORM object in
    # the session, so a later `claim.id` would be a lazy load on a dead state.
    claim_id, txn_id = claim.id, txn.id

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org_id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn_id)],
            today=PERIOD_ENDED,
        )
    assert exc.value.code == "fee_rate_not_configured"
    await db_session.rollback()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim_id))
    assert fresh.status == "draft"
    assert fresh.status_code is None
    assert fresh.vat_eur is None
    assert (fresh.fee_pct, fresh.fee_min, fresh.fee_eur) == (None, None, None)

    locks = await db_session.scalar(
        select(func.count())
        .select_from(VatClaimedInvoice)
        .where(VatClaimedInvoice.claim_id == claim_id)
    )
    assert locks == 0
    frozen = await db_session.scalar(
        select(func.count())
        .select_from(VatRefundClaimLine)
        .where(
            VatRefundClaimLine.claim_id == claim_id,
            VatRefundClaimLine.frozen_at.is_not(None),
        )
    )
    assert frozen == 0
    events = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.org_id == org_id, AuditEvent.action == "transport.claim_submit")
    )
    assert events == 0


@pytest.mark.asyncio
async def test_wo95_the_same_claim_submits_once_a_rate_is_configured(db_session):
    """The positive control for the refusal above: nothing else was wrong with
    that claim. Configuring the standard rate and re-submitting the SAME claim
    succeeds — so the refusal was the fee gate and not a fixture defect."""
    org_id, entity_id = await _org_with_entity(db_session, fee_rate=False)
    claim, txn = await _clean_claim(db_session, org_id, entity_id)
    claim_id, txn_id = claim.id, txn.id

    with pytest.raises(AppError):
        await lock.submit_claim(
            db_session,
            org_id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn_id)],
            today=PERIOD_ENDED,
        )
    await db_session.rollback()

    await fee.set_rate(db_session, org_id, fee_pct=Decimal("10.00"), fee_min=Decimal("0.00"))
    await db_session.commit()
    result = await lock.submit_claim(
        db_session,
        org_id,
        claim_id=claim_id,
        invoices=[("Q8", "INV-0001", txn_id)],
        today=PERIOD_ENDED,
    )
    await db_session.commit()
    assert result.status == "submitted"
    assert result.fee_eur == Decimal("42.00")


@pytest.mark.asyncio
async def test_wo95_the_fee_gate_runs_after_every_statutory_gate(db_session):
    """Gate ORDER, not just gate presence. An org with no fee rate AND a period
    that has not ended reports `period_not_ended` — the legal problem — never
    the commercial one. An operator hears about the filing before the billing.
    """
    org_id, entity_id = await _org_with_entity(db_session, fee_rate=False)
    claim, txn = await _clean_claim(
        db_session, org_id, entity_id, ref_period="2026-Q4", month="2026-11"
    )
    claim_id, txn_id = claim.id, txn.id

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org_id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn_id)],
            today=PERIOD_ENDED,
        )
    assert exc.value.code == "period_not_ended"
    await db_session.rollback()


@pytest.mark.asyncio
async def test_wo95_the_fee_gate_runs_after_the_document_gate(db_session):
    """The same ordering against R10, the LAST statutory gate before this one:
    a claim whose line has no vaulted document reports the missing document, not
    the missing rate."""
    org_id, entity_id = await _org_with_entity(db_session, fee_rate=False)
    claim = await _make_claim(db_session, org_id, entity_id)
    from tests.transport.test_wo93_client_status import _make_invoice, _make_txn, _make_vendor

    vendor = await _make_vendor(db_session, org_id, name="Q8")
    await _make_invoice(db_session, org_id, vendor, number="INV-0001")
    await db_session.commit()
    txn = await _make_txn(db_session, org_id, entity_id, supplier="Q8", invoice_ref="INV-0001")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()
    claim_id, txn_id = claim.id, txn.id

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org_id,
            claim_id=claim_id,
            invoices=[("Q8", "INV-0001", txn_id)],
            today=PERIOD_ENDED,
        )
    assert exc.value.code == "invoice_document_missing"
    await db_session.rollback()


# --------------------------------------------------------------------------- #
# 3. R13 — a filed claim is never re-rated
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_changing_the_rate_after_submission_never_re_rates_the_claim(db_session):
    """R13's acceptance line, verbatim: *"Change the customer fee % after
    submission ⇒ the claim's fee is unchanged."* C10: *"% / minimum changes only
    affect un-submitted declarations."*"""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _submit(db_session, org_id, entity_id)
    frozen = (claim.fee_pct, claim.fee_min, claim.fee_eur)
    assert frozen == (Decimal("10.00"), Decimal("25.00"), Decimal("42.00"))

    await fee.set_rate(db_session, org_id, fee_pct=Decimal("40.00"), fee_min=Decimal("999.00"))
    await fee.set_rate(
        db_session, org_id, entity_id=entity_id, fee_pct=Decimal("35.00"), fee_min=Decimal("0.00")
    )
    await db_session.commit()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert (fresh.fee_pct, fresh.fee_min, fresh.fee_eur) == frozen


@pytest.mark.asyncio
async def test_wo95_removing_every_rate_after_submission_leaves_the_claim_billable(db_session):
    """The stronger form of the same property: deleting the configuration a
    claim was filed under changes nothing about it. The claim carries its own
    copy, and this is what makes removing a rate a safe administrative act."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _submit(db_session, org_id, entity_id)

    assert await fee.remove_rate(db_session, org_id) is True
    await db_session.commit()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.fee_eur == Decimal("42.00")


@pytest.mark.asyncio
async def test_wo95_freeze_fee_refuses_an_already_frozen_claim(db_session):
    """`fee_already_frozen`. C10 freezes *"on FIRST entry to a locking state"*;
    a second stamp would silently re-rate a filed claim. Unreachable through the
    lifecycle (`submit_claim` accepts only a `draft` claim), so this is the
    structural backstop for a future transition that forgets."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _submit(db_session, org_id, entity_id)

    rate = fee.FeeRate(fee_pct=Decimal("50.00"), fee_min=Decimal("0.00"), scope="org")
    with pytest.raises(AppError) as exc:
        fee.freeze_fee(claim, rate)
    assert exc.value.code == "fee_already_frozen"
    assert claim.fee_pct == Decimal("10.00")


@pytest.mark.asyncio
async def test_wo95_freeze_fee_refuses_an_unfrozen_vat_base(db_session):
    """`vat_base_not_frozen`. A fee computed off an unfrozen base is exactly the
    drift R13 forbids — the base must already be the sum over exactly the locked
    claim set (C10), never a period total."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _make_claim(db_session, org_id, entity_id)
    await db_session.commit()
    assert claim.vat_eur is None

    rate = fee.FeeRate(fee_pct=Decimal("10.00"), fee_min=Decimal("0.00"), scope="org")
    with pytest.raises(AppError) as exc:
        fee.freeze_fee(claim, rate)
    assert exc.value.code == "vat_base_not_frozen"
    assert (claim.fee_pct, claim.fee_min, claim.fee_eur) == (None, None, None)


@pytest.mark.asyncio
async def test_wo95_a_withdrawn_claim_keeps_its_frozen_fee(db_session):
    """§3.D **D7** enumerates exactly what a withdrawal changes — the locks and
    `status_code`. Nothing in the harvest clears a frozen figure on withdrawal,
    and `withdraw_claim` correspondingly leaves `vat_eur` standing too; clearing
    the fee but not the base it came from would be a shape the spec never
    describes. The reading is pinned here rather than only argued in a
    docstring.

    It cannot go stale either: `submit_claim` accepts only a `draft` claim and a
    withdrawn one never returns to `draft` (WO-94), so the frozen fee can never
    be re-frozen or contradicted."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _submit(db_session, org_id, entity_id)
    frozen = (claim.vat_eur, claim.fee_pct, claim.fee_min, claim.fee_eur)

    await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "withdrawn"
    assert fresh.status_code is None  # D7's first half, WO-94 — unchanged here
    assert (fresh.vat_eur, fresh.fee_pct, fresh.fee_min, fresh.fee_eur) == frozen


# --------------------------------------------------------------------------- #
# 4. Audit
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_the_submit_audit_event_carries_the_fee_old_to_new(db_session):
    """§4.16 — old→new in the SAME transaction as the mutation. The freeze rides
    the existing `transport.claim_submit` event (WO-94's one-event-per-lifecycle-
    moment precedent) and additionally records the RUNG and the BASIS, neither of
    which can be reconstructed later from a rate table that has since changed."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _submit(db_session, org_id, entity_id)

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org_id, AuditEvent.action == "transport.claim_submit"
            )
        )
    )
    assert len(events) == 1
    meta = _meta(events[0])
    assert meta["old_fee_pct"] is None and meta["new_fee_pct"] == "10.00"
    assert meta["old_fee_min"] is None and meta["new_fee_min"] == "25.00"
    assert meta["old_fee_eur"] is None and meta["new_fee_eur"] == "42.00"
    assert meta["fee_basis"] == "percent"
    assert meta["fee_rate_scope"] == "org"
    # The pre-existing keys are untouched — this order ADDS to the meta shape.
    assert meta["invoice_count"] == 1
    assert meta["frozen_line_count"] == 1


@pytest.mark.asyncio
async def test_wo95_no_separate_fee_freeze_audit_action_exists(db_session):
    """The freeze deliberately has no action of its own. Asserted so a later
    order does not add one and leave two events describing one moment."""
    from app.services import audit as audit_svc

    actions = {v for k, v in vars(audit_svc.A).items() if not k.startswith("_")}
    assert "transport.fee_freeze" not in actions
    assert {"transport.fee_rate_set", "transport.fee_rate_remove"} <= actions


# --------------------------------------------------------------------------- #
# 5. Org scoping over the real submission path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_one_orgs_rate_never_prices_another_orgs_claim(db_session):
    """Overlapping data: both orgs run the identical fixture claim, and only the
    org that configured 30% pays 30%."""
    org_a, entity_a = await _org_with_entity(db_session)
    org_b, entity_b = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_b, fee_pct=Decimal("30.00"), fee_min=Decimal("0.00"))
    await db_session.commit()

    claim_a = await _submit(db_session, org_a, entity_a)
    claim_b = await _submit(db_session, org_b, entity_b)

    assert claim_a.fee_eur == Decimal("42.00")  # A's 10% standard
    assert claim_b.fee_eur == Decimal("126.00")  # B's 30% standard
