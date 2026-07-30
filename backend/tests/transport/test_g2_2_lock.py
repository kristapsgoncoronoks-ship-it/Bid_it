"""G2.2 — `vat_claimed_invoices`: the one-invoice-one-submission lock table
(R4/R5). `BA_fleet_fuel.md` section 3.C5/C7 + section 4.3's schema fragment:
`UNIQUE(entity, refund_country, supplier, invoice_ref)` IS the lock, widened
with `org_id` per this codebase's standing tenant-scoping convention (see
`docs/plan/plan-a/wo/WO-51-G2.2.md`'s citation-check note). The real-Postgres
concurrency proof lives in `tests/test_transport_lock_concurrency.py` — this
file is the SQLite service-level suite.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaim
from app.services.transport import claim as claim_svc
from app.services.transport import fuel_ingest, lock
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

_LOCK_PATH = "app/services/transport/lock.py"


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    supplier: str = "Q8",
    invoice_ref: str = "INV-0001",
    line_seq: int = 1,
    period: str = "2026-06",
) -> FuelTransaction:
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 6, 15),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
        invoice_ref=invoice_ref,
    )


@pytest.mark.asyncio
async def test_g2_2_submit_claim_acquires_one_lock_row_per_invoice_and_flips_status(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    txn1 = await _make_txn(db_session, org, entity, invoice_ref="INV-0001", line_seq=1)
    txn2 = await _make_txn(db_session, org, entity, invoice_ref="INV-0002", line_seq=2)
    await db_session.commit()

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn1.id), ("Q8", "INV-0002", txn2.id)],
        override_minimum=True,  # fixture VAT (21+21 EUR) is below the Art. 17 test threshold; this test is about locking, not R8
    )
    await db_session.commit()

    assert result.status == "submitted"
    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "submitted"

    rows = (
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim.id)
        )
    ).all()
    assert len(rows) == 2
    for row in rows:
        assert row.entity_id == entity.id
        assert row.refund_country == "LV"

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id, AuditEvent.action == "transport.claim_submit"
            )
        )
    ).all()
    assert len(events) == 1


@pytest.mark.asyncio
async def test_g2_2_natural_key_uniqueness_constraint_rejects_a_raw_duplicate_insert(db_session):
    """The DB constraint itself (`uq_vat_claimed_invoices_lock`) is the
    backstop under a lost race — a bare second INSERT with the identical
    natural key, bypassing the service, must be refused by the database.
    Builds the claim/transaction via raw ORM adds (module NOT enabled) —
    mirrors `test_r1_grain_uniqueness_constraint_rejects_a_raw_duplicate_insert`'s
    own bypass-the-service pattern."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    claim = VatRefundClaim(
        org_id=org.id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
    )
    db_session.add(claim)
    await db_session.flush()
    txn = FuelTransaction(
        org_id=org.id,
        entity_id=entity.id,
        supplier="Q8",
        period="2026-06",
        line_seq=1,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 6, 15),
        station="Demo Station Riga",
        product="DIESEL",
        product_group="Diesel",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
        net_eur_eff=Decimal("100.00"),
        invoice_ref="INV-0001",
    )
    db_session.add(txn)
    await db_session.commit()

    db_session.add(
        VatClaimedInvoice(
            org_id=org.id,
            claim_id=claim.id,
            entity_id=entity.id,
            refund_country="LV",
            supplier="Q8",
            invoice_ref="INV-0001",
            fuel_transaction_id=txn.id,
        )
    )
    await db_session.commit()

    db_session.add(
        VatClaimedInvoice(
            org_id=org.id,
            claim_id=claim.id,
            entity_id=entity.id,
            refund_country="LV",
            supplier="Q8",
            invoice_ref="INV-0001",
            fuel_transaction_id=txn.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_g2_2_submit_claim_on_an_already_locked_invoice_raises_integrity_error_and_leaves_the_claim_status_unchanged(
    db_session,
):
    """Same-session proof: seed one lock row directly (a different claim
    already holds it), then call `submit_claim` with an overlapping invoice
    key; assert IntegrityError at flush and, after rollback(), THIS claim's
    status column reads back 'draft' from a fresh query — not just unchanged
    in memory."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    other_claim = await _make_claim(db_session, org, entity, ref_period="2026-Q1")
    my_claim = await _make_claim(db_session, org, entity, ref_period="2026-Q2")
    txn = await _make_txn(db_session, org, entity, invoice_ref="INV-0001")
    await db_session.commit()

    # Another claim already holds the lock for this exact invoice key.
    db_session.add(
        VatClaimedInvoice(
            org_id=org.id,
            claim_id=other_claim.id,
            entity_id=entity.id,
            refund_country="LV",
            supplier="Q8",
            invoice_ref="INV-0001",
            fuel_transaction_id=txn.id,
        )
    )
    await db_session.commit()
    my_claim_id = my_claim.id  # capture before the rollback expires the instance

    with pytest.raises(IntegrityError):
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=my_claim_id,
            invoices=[("Q8", "INV-0001", txn.id)],
            override_minimum=True,  # this test is about the lock race, not R8
        )
    await db_session.rollback()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == my_claim_id))
    assert fresh.status == "draft"


@pytest.mark.asyncio
async def test_g2_2_submit_claim_refuses_a_non_draft_claim(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    txn = await _make_txn(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session, org.id, claim_id=claim.id, invoices=[("Q8", "INV-0001", txn.id)]
        )
    assert exc.value.code == "claim_not_draft"
    assert exc.value.status == 409

    rows = (await db_session.scalars(select(VatClaimedInvoice))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_g2_2_submit_claim_refuses_an_empty_invoice_list(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(db_session, org.id, claim_id=claim.id, invoices=[])
    assert exc.value.code == "empty_claim_set"
    assert exc.value.status == 422

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.status == "draft"
    rows = (await db_session.scalars(select(VatClaimedInvoice))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_g2_2_withdraw_claim_deletes_every_lock_row_for_that_claim_and_sets_status_withdrawn(
    db_session,
):
    """Happy path; the SAME invoice key can then be locked by a DIFFERENT
    claim — R5's 'after withdraw, it can' acceptance line, verbatim."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity, ref_period="2026-Q1")
    txn = await _make_txn(db_session, org, entity, invoice_ref="INV-0001")
    await db_session.commit()

    await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        override_minimum=True,  # this test is about withdraw/re-lock, not R8
    )
    await db_session.commit()

    result = await lock.withdraw_claim(db_session, org.id, claim.id)
    await db_session.commit()

    assert result.status == "withdrawn"
    rows = (
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim.id)
        )
    ).all()
    assert rows == []

    withdraw_events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id, AuditEvent.action == "transport.claim_withdraw"
            )
        )
    ).all()
    assert len(withdraw_events) == 1

    # After withdraw, a DIFFERENT claim can lock the exact same invoice key.
    another_claim = await _make_claim(db_session, org, entity, ref_period="2026-Q2")
    await db_session.commit()
    result2 = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=another_claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        override_minimum=True,
    )
    await db_session.commit()
    assert result2.status == "submitted"


@pytest.mark.asyncio
async def test_g2_2_withdraw_claim_refuses_a_claim_that_never_held_locks(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)  # still draft
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.withdraw_claim(db_session, org.id, claim.id)
    assert exc.value.code == "claim_not_locked"
    assert exc.value.status == 409

    rows = (await db_session.scalars(select(VatClaimedInvoice))).all()
    assert rows == []


def test_g2_2_withdraw_claim_is_the_only_function_that_deletes_a_lock_row():
    """Structural (grep-based) proof: exactly one function body in
    `app/services/transport/lock.py` issues a `DELETE` against
    `VatClaimedInvoice`, and it is `withdraw_claim`."""
    source = (_repo_root() / _LOCK_PATH).read_text(encoding="utf-8")
    matches = list(re.finditer(r"delete\(VatClaimedInvoice\)", source))
    assert len(matches) == 1, (
        f"expected exactly one delete(VatClaimedInvoice), found {len(matches)}"
    )

    # The single match must fall inside withdraw_claim's body, not submit_claim's.
    def_positions = [
        (m.start(), m.group(1))
        for m in re.finditer(r"^(?:async )?def (\w+)\(", source, re.MULTILINE)
    ]
    def_positions.sort()
    match_pos = matches[0].start()
    enclosing = None
    for pos, name in def_positions:
        if pos <= match_pos:
            enclosing = name
        else:
            break
    assert enclosing == "withdraw_claim"


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent


@pytest.mark.asyncio
async def test_g2_2_directly_flipping_claim_status_never_cascades_a_lock_release(db_session):
    """The withdraw-only-release proof: seed a submitted claim with locks,
    mutate `claim.status` on the ORM object directly to 'rejected' (simulating
    a hypothetical future transition that does NOT call `withdraw_claim`) and
    commit; assert the lock rows are still present afterward — nothing but
    `withdraw_claim` can make them disappear."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    txn = await _make_txn(db_session, org, entity, invoice_ref="INV-0001")
    await db_session.commit()

    await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        override_minimum=True,  # this test is about the withdraw-only-release invariant, not R8
    )
    await db_session.commit()

    # Simulate a hypothetical future transition that forgets to withdraw.
    claim.status = "rejected"
    await db_session.commit()

    rows = (
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim.id)
        )
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_g2_2_transport_disabled_by_default_is_inert(db_session):
    """A fresh org has `transport` disabled by default; both submit_claim and
    withdraw_claim must refuse BEFORE any row is queried or written — proven
    by getting a 403 module_not_enabled rather than a 404 claim_not_found for
    a deliberately nonexistent claim id."""
    org = await make_org(db_session)  # transport NOT enabled

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=str(uuid.uuid4()),
            invoices=[("Q8", "INV-1", str(uuid.uuid4()))],
        )
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403

    with pytest.raises(AppError) as exc2:
        await lock.withdraw_claim(db_session, org.id, str(uuid.uuid4()))
    assert exc2.value.code == "module_not_enabled"
    assert exc2.value.status == 403

    rows = (await db_session.scalars(select(VatClaimedInvoice))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_g2_2_unknown_claim_is_a_404_not_found_never_a_500(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org.id,
            claim_id=str(uuid.uuid4()),
            invoices=[("Q8", "INV-1", str(uuid.uuid4()))],
        )
    assert exc.value.code == "claim_not_found"
    assert exc.value.status == 404

    with pytest.raises(AppError) as exc2:
        await lock.withdraw_claim(db_session, org.id, str(uuid.uuid4()))
    assert exc2.value.code == "claim_not_found"
    assert exc2.value.status == 404


@pytest.mark.asyncio
async def test_g2_2_cross_tenant_claim_is_opaque_not_found_never_leaks_across_orgs(db_session):
    """Tenancy invariant (master context §4.4): a cross-tenant fetch by id is
    an opaque 404, never a 403 and never a cross-org lock. Org B's claim id
    is a perfectly real id — just not org A's."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id)
    claim_b = await _make_claim(db_session, org_b, entity_b)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await lock.submit_claim(
            db_session,
            org_a.id,
            claim_id=claim_b.id,
            invoices=[("Q8", "INV-1", str(uuid.uuid4()))],
        )
    assert exc.value.code == "claim_not_found"
    assert exc.value.status == 404

    with pytest.raises(AppError) as exc2:
        await lock.withdraw_claim(db_session, org_a.id, claim_b.id)
    assert exc2.value.code == "claim_not_found"
    assert exc2.value.status == 404

    rows = (await db_session.scalars(select(VatClaimedInvoice))).all()
    assert rows == []
