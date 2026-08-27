"""WO-T — the claim lifecycle's last edge: the refund actually landing.

For its whole first arc this product could file a claim and record a member
state's answer, and then the trail went cold. Nothing wrote `submitted_date`,
nothing wrote `paid_date`, and no transition existed past `approved`. The
visible consequence was that `recovery.median_days_to_refund` — implemented
since WO-81, against the harvested definition — reported `null` in every
workspace forever: the booked-cash north star could not close its own loop.

These tests pin the two writers that fix it and the refusals that keep the edge
honest:

- `lock.submit_claim` stamps `submitted_date` at the transition (not from the
  caller — a back-dated filing is not a fact this surface asserts);
- `decision.record_payment` is the ONLY `approved -> paid` writer, requires a
  positive amount, refuses a payment dated before its approval, and cannot be
  run twice because the first one leaves the claim `paid`;
- the median goes from `null` to a HAND-COMPUTED figure over seeded intervals,
  which is the whole point of the work order;
- the audit event carries the variance against the approved base, because the
  amount that arrived and the amount that was approved are separate facts.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import claim as claim_svc
from app.services.transport import decision, recovery
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import (
    activate_entity,
    enable_transport,
    make_entity,
    make_org,
)

pytestmark = pytest.mark.asyncio


async def _approved_claim(
    db_session,
    org,
    entity,
    *,
    ref_period: str = "2026-Q2",
    vat: Decimal = Decimal("360.00"),
    submitted: date | None = None,
    approved: date | None = None,
) -> VatRefundClaim:
    """A claim in the post-decision shape, built directly — the submit gate
    chain and the decision transition are each proven in their own suites; this
    one tests what happens after both."""
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period=ref_period
    )
    db_session.add(
        VatRefundClaimLine(
            org_id=org.id,
            claim_id=claim.id,
            invoice_ref=f"INV-{ref_period}",
            product_group="DIESEL",
            goods_code="1",
            net_eur=Decimal("1000.00"),
            vat_eur=vat,
            net_local=Decimal("1000.00"),
            vat_local=vat,
            currency="EUR",
            frozen_at=datetime.now(UTC),
        )
    )
    claim.status = "approved"
    claim.vat_eur = vat
    claim.vat_local = vat
    claim.currency = "EUR"
    claim.submitted_date = submitted
    claim.approved_date = approved
    claim.decision_date = approved
    await db_session.commit()
    return claim


# --------------------------------------------------------------------------- #
# The edge itself
# --------------------------------------------------------------------------- #


async def test_an_approved_claim_can_be_recorded_as_paid(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(
        db_session, org, entity, submitted=date(2026, 5, 1), approved=date(2026, 6, 1)
    )

    out = await decision.record_payment(
        db_session,
        org.id,
        claim.id,
        paid_amount=Decimal("360.00"),
        paid_date=date(2026, 7, 15),
    )
    await db_session.commit()

    assert out.status == "paid"
    assert out.paid_date == date(2026, 7, 15)
    assert out.paid_amount == Decimal("360.00")
    # The decision's own stamps are untouched — payment answers the approval,
    # it does not restate it.
    assert out.approved_date == date(2026, 6, 1)
    assert out.submitted_date == date(2026, 5, 1)


async def test_the_paid_date_defaults_to_today_but_the_amount_never_defaults(db_session):
    """Two different kinds of fact. "When did you record this" has a sensible
    default; "how much money arrived" does not, and inventing one would assert
    that the refund matched the approved base."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(db_session, org, entity)

    out = await decision.record_payment(db_session, org.id, claim.id, paid_amount=Decimal("100.00"))
    await db_session.commit()
    assert out.paid_date == date.today()

    # …and the signature has no default for the amount: omitting it is a
    # TypeError at the call site, not a silently derived figure.
    with pytest.raises(TypeError):
        await decision.record_payment(db_session, org.id, claim.id)  # type: ignore[call-arg]


async def test_a_short_payment_is_recorded_as_what_arrived(db_session):
    """The case the whole design is for: the member state pays less than it
    approved. The claim records what landed; the approved base is untouched, so
    both figures survive and the difference is visible."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(db_session, org, entity, vat=Decimal("360.00"))

    out = await decision.record_payment(db_session, org.id, claim.id, paid_amount=Decimal("312.40"))
    await db_session.commit()

    assert out.paid_amount == Decimal("312.40")
    assert out.vat_eur == Decimal("360.00"), "the approved base must not be rewritten"


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["draft", "submitted", "rejected", "withdrawn"])
async def test_only_an_approved_claim_can_be_paid(db_session, status):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(db_session, org, entity, ref_period="2026-Q3")
    claim.status = status
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await decision.record_payment(db_session, org.id, claim.id, paid_amount=Decimal("10.00"))
    assert exc.value.code == "claim_not_approved"

    await db_session.refresh(claim)
    assert claim.paid_date is None and claim.paid_amount is None


async def test_a_claim_cannot_be_paid_twice(db_session):
    """No dedicated interlock is load-bearing here: the first payment leaves the
    claim `paid`, which is not `approved`. The distinct code exists only to say
    something more useful than "this one is 'paid'"."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(db_session, org, entity)

    await decision.record_payment(db_session, org.id, claim.id, paid_amount=Decimal("360.00"))
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await decision.record_payment(db_session, org.id, claim.id, paid_amount=Decimal("360.00"))
    assert exc.value.code == "claim_already_paid"

    await db_session.refresh(claim)
    assert claim.paid_amount == Decimal("360.00"), "the second attempt must change nothing"


@pytest.mark.parametrize("amount", [Decimal("0.00"), Decimal("-1.00")])
async def test_a_recorded_refund_is_a_positive_amount(db_session, amount):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(db_session, org, entity)

    with pytest.raises(AppError) as exc:
        await decision.record_payment(db_session, org.id, claim.id, paid_amount=amount)
    assert exc.value.code == "paid_amount_not_positive"


async def test_a_refund_cannot_predate_its_own_approval(db_session):
    """Not pedantry: a paid_date before the approval would produce a negative
    days-to-refund and quietly poison the median this work order exists to
    make computable."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(db_session, org, entity, approved=date(2026, 6, 1))

    with pytest.raises(AppError) as exc:
        await decision.record_payment(
            db_session,
            org.id,
            claim.id,
            paid_amount=Decimal("360.00"),
            paid_date=date(2026, 5, 30),
        )
    assert exc.value.code == "paid_date_before_approval"


# --------------------------------------------------------------------------- #
# The measure that was waiting on all of this
# --------------------------------------------------------------------------- #


async def test_the_median_days_to_refund_goes_from_null_to_a_hand_computed_figure(db_session):
    """The point of the work order, asserted end to end.

    Three claims with deliberately chosen intervals: 30, 45 and 60 days. The
    median of an odd sample is its middle value, so the expected answer is 45.0
    — computed by hand here rather than by re-implementing the measure.
    """
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    before = await recovery.recovery_dashboard(db_session, org.id, 2026)
    assert before.median_days_to_refund is None, "nothing paid yet — the honest null"

    intervals = [(date(2026, 1, 10), 30), (date(2026, 2, 10), 45), (date(2026, 3, 10), 60)]
    for i, (submitted, days) in enumerate(intervals):
        claim = await _approved_claim(
            db_session,
            org,
            entity,
            ref_period=f"2026-Q{i + 1}",
            submitted=submitted,
            approved=submitted,
        )
        await decision.record_payment(
            db_session,
            org.id,
            claim.id,
            paid_amount=Decimal("360.00"),
            paid_date=submitted + timedelta(days=days),
        )
    await db_session.commit()

    after = await recovery.recovery_dashboard(db_session, org.id, 2026)
    assert after.median_days_to_refund == Decimal("45.0")
    assert after.days_to_refund_sample == 3


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


async def test_the_payment_audits_its_variance_against_the_approved_base(db_session):
    """The approved base and the amount that arrived are separate facts. A
    reader should see the difference without recomputing it from two screens."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _approved_claim(
        db_session,
        org,
        entity,
        vat=Decimal("360.00"),
        submitted=date(2026, 5, 1),
        approved=date(2026, 6, 1),
    )

    await decision.record_payment(
        db_session,
        org.id,
        claim.id,
        paid_amount=Decimal("312.40"),
        paid_date=date(2026, 6, 30),
    )
    await db_session.commit()

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.claim_paid",
            )
        )
    ).all()
    assert len(events) == 1
    meta = json.loads(events[0].meta or "{}")
    assert meta["old"]["status"] == "approved"
    assert meta["new"]["status"] == "paid"
    assert meta["approved_vat_eur"] == "360.00"
    assert meta["variance_eur"] == "-47.60"
    assert meta["days_to_refund"] == 60


async def test_the_real_submit_transition_stamps_the_filing_date(db_session):
    """The median's first leg, proven through the REAL gated submit rather than
    by reading the source: a claim that comes out of `submit_claim` carries the
    day it was filed. And no caller can assert a different one — the signature
    offers no such parameter, which is why a back-dated filing is impossible
    rather than merely discouraged."""
    import inspect

    from app.services.transport import fuel_ingest, lock

    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await activate_entity(db_session, org.id, entity.id, "LV")
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
    )
    txn = await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period="2026-05",
        line_seq=1,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(seed=1),
        txn_date=date(2026, 5, 15),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
        invoice_ref="INV-0001",
    )
    await db_session.commit()

    out = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        # The fixture VAT is below the Art. 17 threshold; this test is about the
        # date stamp, not R8.
        override_minimum=True,
    )
    await db_session.commit()

    assert out.status == "submitted"
    assert out.submitted_date == date.today()
    assert "submitted_date" not in str(inspect.signature(lock.submit_claim))


# --------------------------------------------------------------------------- #
# Over HTTP
# --------------------------------------------------------------------------- #


async def _http_org(client):
    import uuid as _uuid

    suffix = _uuid.uuid4().hex[:8]
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": f"WO-T Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wot.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def test_the_payment_route_records_the_refund_and_shows_it_on_the_claim(client, db_session):
    """The route is a thin controller: it must persist (a `flush` would not) and
    return the claim carrying its new dates — `ClaimOut` has advertised
    `paid_date`/`paid_amount` since WO-76 and, until now, never had a value to
    put in them."""
    from app.models.organization import Organization
    from app.services import modules

    headers, org_id = await _http_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    org = await db_session.get(Organization, org_id)
    entity = await make_entity(db_session, org_id)
    claim = await _approved_claim(
        db_session,
        org,
        entity,
        submitted=date(2026, 5, 1),
        approved=date(2026, 6, 1),
    )

    r = await client.post(
        f"/api/v1/transport/claims/{claim.id}/payment",
        headers=headers,
        json={"paid_amount": "312.40", "paid_date": "2026-06-30"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paid"
    assert body["paid_date"] == "2026-06-30"
    # §4.9 — the amount crosses the wire as an exact string, not a float.
    assert body["paid_amount"] == "312.40"

    # It persisted: a fresh read through the API, not the session that wrote it.
    again = await client.get(f"/api/v1/transport/claims/{claim.id}", headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["paid_amount"] == "312.40"


async def test_the_payment_route_refuses_an_unapproved_claim_with_the_service_code(
    client, db_session
):
    from app.models.organization import Organization
    from app.services import modules

    headers, org_id = await _http_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    org = await db_session.get(Organization, org_id)
    entity = await make_entity(db_session, org_id)
    claim = await _approved_claim(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()

    r = await client.post(
        f"/api/v1/transport/claims/{claim.id}/payment",
        headers=headers,
        json={"paid_amount": "100.00"},
    )
    assert r.status_code == 409, r.text
    # The route maps nothing: this vocabulary is the service's own (§4.20).
    assert r.json()["code"] == "claim_not_approved"
