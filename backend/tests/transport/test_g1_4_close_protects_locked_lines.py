"""G1.4 — locked lines are protected from a re-close (`BA_fleet_fuel.md` R30,
ADR-P3). See `app.services.transport.close`'s module docstring ("G1.4 ... IS
ALREADY STRUCTURALLY TRUE HERE, NOT A SEPARATE MECHANISM") for the two
independent layers this file proves:

1. `run_close` never touches a non-`draft` claim's lines at all (its own
   query filter, inherited from `build_claim_lines`'s own `draft`-only
   refusal) — proven in `tests/transport/test_g1_3_close.py::
   test_g1_3_run_close_ignores_non_draft_claims` already; this file adds the
   claim-lines-survive-untouched assertion specifically.
2. `VatClaimedInvoice`'s `RESTRICT` FK into `fuel_transactions` (WO-51) means
   a locked transaction cannot be deleted by ANY code path, even a
   hypothetical future one — the database itself refuses it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services.transport import claim as claim_svc
from app.services.transport import close, fuel_ingest, lock
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides):
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(db_session, org, entity, *, invoice_ref="INV-0001", line_seq=1):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period="2026-05",
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
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
        invoice_ref=invoice_ref,
    )


@pytest.mark.asyncio
async def test_g1_4_run_close_never_touches_a_submitted_claims_lines(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()

    # Build the lines once while still draft, then lock the invoice via
    # submit_claim (G2.2) — the claim leaves `draft`.
    await close.run_close(db_session, org.id, "2026-05")
    await db_session.commit()
    pre_lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(pre_lines) == 1

    await lock.submit_claim(
        db_session, org.id, claim_id=claim.id, invoices=[("Q8", "INV-0001", txn.id)]
    )
    await db_session.commit()

    # A re-close for the same period must not touch the now-submitted
    # claim's lines at all.
    result = await close.run_close(db_session, org.id, "2026-05")
    assert result["claims_processed"] == 0

    post_lines = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(post_lines) == 1
    assert post_lines[0].id == pre_lines[0].id, "the exact same line row must survive untouched"


@pytest.mark.asyncio
async def test_g1_4_a_locked_transaction_cannot_be_deleted_the_database_refuses_it(db_session):
    """The second, independent layer: `VatClaimedInvoice`'s RESTRICT FK into
    `fuel_transactions` (WO-51) means even a hypothetical future re-close
    that tried to delete a locked transaction row would be refused by the
    database itself — proven directly against the raw ORM delete, bypassing
    every service function (the lost-race-style backstop, per this
    codebase's standing test convention)."""
    # The test engine (tests/conftest.py's `_db` fixture) does not attach the
    # app's production `PRAGMA foreign_keys=ON` connect-listener (it's
    # registered on the app's OWN engine object, app/core/database.py), so
    # SQLite's ON DELETE actions — including RESTRICT — are otherwise inert
    # in this suite. This fixture uses a StaticPool (one physical connection
    # for the whole test), so setting the pragma once on this session's
    # connection makes the real `fk_vat_claimed_invoices_fuel_transaction`
    # RESTRICT action fire for the rest of this test.
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    txn = await _make_txn(db_session, org, entity)
    await db_session.commit()

    txn_id = txn.id  # captured BEFORE the rollback below expires it
    await lock.submit_claim(
        db_session, org.id, claim_id=claim.id, invoices=[("Q8", "INV-0001", txn_id)]
    )
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await db_session.execute(delete(FuelTransaction).where(FuelTransaction.id == txn_id))
        await db_session.flush()
    await db_session.rollback()

    # The transaction row is still there — the delete was refused, not
    # silently swallowed.
    survivor = await db_session.scalar(select(FuelTransaction).where(FuelTransaction.id == txn_id))
    assert survivor is not None
