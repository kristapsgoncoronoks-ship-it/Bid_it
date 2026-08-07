"""WO-88 — a fuel line may never assert both "no rate was available" and a EUR
figure (master-context §4.15/§4.14; `BA_fleet_fuel.md` R56).

Two layers are asserted here, and the second one is the point of the order:

1. `fuel_ingest._require_fx_provenance` refuses the inconsistent combination at
   the ONE service that writes `fuel_transactions`, fail CLOSED, with the
   tree's existing `fx_rate_unavailable` code — and writes nothing.
2. `ck_fuel_transactions_fx_provenance` / `ck_vat_off_invoice_rebates_fx_provenance`
   refuse the SAME combination in the database, so a script, a fixture, a
   future service or a hand-rolled INSERT that never comes through the gate
   cannot create the row either.

WO-87's `savings._require_eur_basis` stays exactly as it was — the third layer,
and the only one that can explain to an operator why a whole comparison was
refused rather than silently shrunk. It is re-asserted here (defence in depth
proven in one file), never weakened.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.models.audit import AuditEvent
from app.models.fx import FxSource
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.off_invoice_rebate import VatOffInvoiceRebate
from app.services import audit
from app.services.transport import fuel_ingest, rebate, savings
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

# No module-level `pytest.mark.asyncio`: `pytest.ini` sets `asyncio_mode = auto`,
# so the async tests below are collected without one and the sync tests are not
# mis-marked (the WO-87 convention, kept).

PERIOD = "2026-05"
TXN_DATE = date(2026, 5, 14)

_SEQ = {"n": 0}


async def _org_entity(db_session, *, seed: int | None = None):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=seed)
    await db_session.commit()
    return org.id, entity.id


def _kwargs(**overrides):
    """The argument tuple of one diesel line, EUR unless a test says otherwise."""
    _SEQ["n"] += 1
    net = Decimal("1400.00")
    base = dict(
        supplier="Q8",
        period=PERIOD,
        line_seq=_SEQ["n"],
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=TXN_DATE,
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("1000.000"),
        currency="EUR",
        net_local=net,
        vat_local=Decimal("294.00"),
        gross_local=Decimal("1694.00"),
        net_eur=net,
        vat_eur=Decimal("294.00"),
    )
    base.update(overrides)
    return base


def _detached(**overrides) -> FuelTransaction:
    """A `FuelTransaction` built in memory and NEVER added to a session — the
    only way this shape still exists after WO-88 (see the module docstring of
    the WO-88 work order, "Documented interpretations")."""
    row = FuelTransaction(
        org_id="org", entity_id="entity", product_group="Diesel", **_kwargs(**overrides)
    )
    row.net_eur_eff = row.net_eur
    return row


async def _count(db_session, org_id: str) -> int:
    return (
        await db_session.scalar(
            select(func.count())
            .select_from(FuelTransaction)
            .where(FuelTransaction.org_id == org_id)
        )
    ) or 0


# --------------------------------------------------------------------------- #
# Layer 1 — the service gate refuses, and writes nothing
# --------------------------------------------------------------------------- #


def test_wo88_the_unknown_literal_matches_the_platform_enum():
    """`fuel_ingest.FX_SOURCE_UNKNOWN` is a LITERAL, not an import (ADR-0023
    rule 2 — a transport service may only import `app.models.transport` /
    `app.models.base`; `savings.py` and `rebate.py` state their own the same
    way). A test file has no such restriction, so the two are pinned together
    here: a drift would silently stop the gate recognising the one value that
    means "this euro was never converted"."""
    assert fuel_ingest.FX_SOURCE_UNKNOWN == FxSource.unknown.value
    assert fuel_ingest.CURRENCY_EUR == FxSource.eur.value.upper()


async def test_wo88_ingest_refuses_an_unknown_fx_source_carrying_a_eur_figure(db_session):
    """The defect this order exists to close: `fx_source='unknown'` means "no
    rate was available" (`app/models/fx.py`), `net_eur` is NOT NULL, and until
    WO-88 the writer stored both assertions in one row."""
    org_id, entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org_id, entity_id=entity_id, **_kwargs(fx_source="unknown")
        )

    assert exc.value.code == "fx_rate_unavailable"
    assert exc.value.status == 422
    assert await _count(db_session, org_id) == 0


async def test_wo88_ingest_refuses_a_foreign_currency_with_no_recorded_conversion(db_session):
    """§4.14 — *"it never labels a foreign amount EUR"*. A PLN line with no
    provenance at all is a foreign amount wearing a euro figure."""
    org_id, entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org_id, entity_id=entity_id, **_kwargs(currency="PLN", fx_source=None)
        )

    assert exc.value.code == "fx_rate_unavailable"
    assert "PLN" in str(exc.value)
    assert await _count(db_session, org_id) == 0


async def test_wo88_a_refused_ingestion_writes_no_audit_event(db_session):
    """§4.16's mirror image: nothing happened, so nothing is recorded as having
    happened. The gate runs before `db.add` and before the flush."""
    org_id, entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError):
        await fuel_ingest.ingest_transaction(
            db_session, org_id, entity_id=entity_id, **_kwargs(fx_source="unknown")
        )
    await db_session.rollback()

    events = (await db_session.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id))).all()
    # The action string is read off the enum, not retyped — an assertion over a
    # name that does not exist would pass while proving nothing.
    assert [e for e in events if e.action == audit.A.FUEL_TRANSACTION_INGEST.value] == []


async def test_wo88_the_gate_runs_before_any_database_read(db_session):
    """Ordering is part of the contract: the provenance check is pure, so an
    argument tuple that can never become a row is refused before the entity
    lookup — no query, no side effect, and the error names the real problem."""
    org_id, _entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session,
            org_id,
            entity_id="00000000-0000-0000-0000-000000000000",
            **_kwargs(fx_source="unknown"),
        )
    assert exc.value.code == "fx_rate_unavailable"  # not entity_not_found


async def test_wo88_the_module_entitlement_still_wins(db_session):
    """The entitlement gate stays FIRST: a caller without the transport module
    learns that, not the FX detail of a row it may not write at all."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id, country="LV")
    await db_session.commit()

    with pytest.raises(Exception) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org.id, entity_id=entity.id, **_kwargs(fx_source="unknown")
        )
    assert getattr(exc.value, "code", None) == "module_not_enabled"


# --------------------------------------------------------------------------- #
# The other side of the gate — every legal combination still lands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("currency", "fx_source"),
    [("EUR", "eur"), ("PLN", "ecb"), ("PLN", "stated"), ("SEK", "ecb")],
)
async def test_wo88_the_legal_combinations_are_accepted(db_session, currency, fx_source):
    """EUR is the identity, a converted line carries `ecb`, a supplier-stated
    line carries `stated` — the three provenances `statement_ingest` actually
    produces (plus one more currency, to prove the rule is not PLN-shaped)."""
    org_id, entity_id = await _org_entity(db_session)

    row = await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        **_kwargs(currency=currency, fx_source=fx_source),
    )
    await db_session.commit()

    assert row.fx_source == fx_source
    assert row.currency == currency
    assert row.net_eur == Decimal("1400.00")
    assert await _count(db_session, org_id) == 1


async def test_wo88_a_eur_line_with_no_provenance_is_still_accepted(db_session):
    """The deliberate carve-out, asserted so it can never be tightened by
    accident: EUR involves no rate, so a NULL `fx_source` asserts nothing
    false. It is what keeps every EUR line the tree has ever written valid —
    and `savings._require_eur_basis` makes the same carve-out."""
    org_id, entity_id = await _org_entity(db_session)

    row = await fuel_ingest.ingest_transaction(
        db_session, org_id, entity_id=entity_id, **_kwargs(currency="EUR", fx_source=None)
    )
    await db_session.commit()

    assert row.fx_source is None
    assert await _count(db_session, org_id) == 1


async def test_wo88_a_lower_case_eur_is_the_identity_too(db_session):
    """The gate compares case-insensitively (`savings` and `rebate` read the
    column the same way), so a caller that stores 'eur' is not refused for a
    conversion it never had to make."""
    org_id, entity_id = await _org_entity(db_session)

    row = await fuel_ingest.ingest_transaction(
        db_session, org_id, entity_id=entity_id, **_kwargs(currency="eur", fx_source=None)
    )
    await db_session.commit()
    assert row.id is not None


# --------------------------------------------------------------------------- #
# Layer 2 — THE DATABASE ITSELF REFUSES (the load-bearing test of this order)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("currency", "fx_source"),
    [("EUR", "unknown"), ("PLN", "unknown"), ("PLN", None), ("SEK", None)],
)
async def test_wo88_the_database_refuses_a_direct_insert_that_bypasses_the_service(
    db_session, currency, fx_source
):
    """`ck_fuel_transactions_fx_provenance`. The service gate protects the one
    path that exists today; this constraint protects every path that does not
    exist yet — a migration, a fixture, a repair script, a future writer."""
    org_id, entity_id = await _org_entity(db_session)

    db_session.add(
        FuelTransaction(
            org_id=org_id,
            entity_id=entity_id,
            product_group="Diesel",
            net_eur_eff=Decimal("1400.00"),
            **_kwargs(currency=currency, fx_source=fx_source),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo88_the_database_refuses_an_update_that_makes_a_stored_row_inconsistent(
    db_session,
):
    """A legal row tampered into an illegal one — the exact manoeuvre WO-87's
    route test used to perform to seed its fixture, now refused by storage."""
    org_id, entity_id = await _org_entity(db_session)
    row = await fuel_ingest.ingest_transaction(
        db_session, org_id, entity_id=entity_id, **_kwargs(currency="PLN", fx_source="ecb")
    )
    await db_session.commit()

    row.fx_source = "unknown"
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo88_the_database_still_accepts_every_legal_row(db_session):
    """A constraint that refused everything would pass every negative test
    above and be useless. Both legal shapes are inserted RAW, past the service,
    and stored."""
    org_id, entity_id = await _org_entity(db_session)

    for currency, fx_source in (("EUR", None), ("EUR", "eur"), ("PLN", "ecb"), ("PLN", "stated")):
        db_session.add(
            FuelTransaction(
                org_id=org_id,
                entity_id=entity_id,
                product_group="Diesel",
                net_eur_eff=Decimal("1400.00"),
                **_kwargs(currency=currency, fx_source=fx_source),
            )
        )
    await db_session.commit()
    assert await _count(db_session, org_id) == 4


# --------------------------------------------------------------------------- #
# The OTHER money-bearing transport table (WO-84's rebate registry)
# --------------------------------------------------------------------------- #


def _rebate_row(org_id: str, *, currency: str, fx_source: str | None, seq: int = 1):
    return VatOffInvoiceRebate(
        org_id=org_id,
        supplier="Q8",
        country="LV",
        period=PERIOD,
        source_ref=f"RB-{seq:04d}",
        source_party="Q8 Rebate Desk",
        rebate_date=TXN_DATE,
        currency=currency,
        amount_local=Decimal("215.00"),
        amount_eur=Decimal("215.00"),
        fx_source=fx_source,
    )


@pytest.mark.parametrize(
    ("currency", "fx_source"), [("EUR", "unknown"), ("PLN", "unknown"), ("PLN", None)]
)
async def test_wo88_the_rebate_table_refuses_the_same_inconsistency(
    db_session, currency, fx_source
):
    """WO-84 shipped `vat_off_invoice_rebates` with the FX quadruple and
    `amount_eur` NOT NULL — and no `fx_source` constraint at all. The same
    invariant, over this table's own EUR column."""
    org = await make_org(db_session)
    await db_session.commit()

    db_session.add(_rebate_row(org.id, currency=currency, fx_source=fx_source))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo88_the_rebate_table_refuses_a_free_text_fx_source(db_session):
    """The value-domain CHECK every other FX-bearing table has carried since
    WO-8 (`ck_expense_items_fx_source`, `ck_invoices_fx_source`,
    `ck_fuel_transactions_fx_source`) and this one never did."""
    org = await make_org(db_session)
    await db_session.commit()

    db_session.add(_rebate_row(org.id, currency="EUR", fx_source="banana"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo88_the_rebate_table_still_accepts_what_its_writer_produces(db_session):
    """`rebate._resolve_eur` emits exactly two provenances; both must survive
    the new constraints, or the order would have broken the writer it protects."""
    org = await make_org(db_session)
    await db_session.commit()

    db_session.add(_rebate_row(org.id, currency="EUR", fx_source=rebate.FX_SOURCE_EUR, seq=1))
    db_session.add(_rebate_row(org.id, currency="PLN", fx_source=rebate.FX_SOURCE_ECB, seq=2))
    await db_session.commit()

    stored = (await db_session.scalars(select(VatOffInvoiceRebate))).all()
    assert {r.fx_source for r in stored} == {"eur", "ecb"}


def test_wo88_the_rebate_writer_can_only_produce_eur_or_ecb():
    """The service half of the same finding, asserted rather than assumed: the
    rebate module names its two provenances as constants and has deliberately
    no `stated` branch (a rebate invoice states an amount, never a rate)."""
    assert (rebate.FX_SOURCE_EUR, rebate.FX_SOURCE_ECB) == ("eur", "ecb")
    assert not hasattr(rebate, "FX_SOURCE_STATED")


# --------------------------------------------------------------------------- #
# Defence in depth — WO-87's analysis boundary is UNCHANGED and still refuses
# --------------------------------------------------------------------------- #


def test_wo88_the_analysis_boundary_still_refuses_both_combinations():
    """WO-87's `savings._require_eur_basis` is not weakened by this order: it
    still refuses the same two combinations with the same code. After WO-88 the
    rows it guards against can no longer reach it THROUGH STORAGE, which is why
    they are built detached here — that is the fix working, not the guard
    becoming pointless (a future order that makes the EUR columns nullable, or
    a database restored from before the constraint, walks straight into it)."""
    for row in (_detached(fx_source="unknown"), _detached(currency="PLN", fx_source=None)):
        with pytest.raises(ValidationError) as exc:
            savings._require_eur_basis([row])
        assert exc.value.code == "fx_rate_unavailable"

    # …and the legal shapes still pass the analysis guard untouched.
    savings._require_eur_basis(
        [_detached(fx_source=None), _detached(currency="PLN", fx_source="ecb")]
    )


async def test_wo88_a_second_tenants_legal_rows_are_untouched_by_the_refusal(db_session):
    """The gate is per-row and org-agnostic. Tenant B holds an identical-looking
    legal line; tenant A's refused ingestion changes nothing about it."""
    org_a, entity_a = await _org_entity(db_session, seed=1)
    org_b, entity_b = await _org_entity(db_session, seed=2)

    await fuel_ingest.ingest_transaction(
        db_session, org_b, entity_id=entity_b, **_kwargs(currency="PLN", fx_source="ecb")
    )
    await db_session.commit()

    with pytest.raises(ValidationError):
        await fuel_ingest.ingest_transaction(
            db_session, org_a, entity_id=entity_a, **_kwargs(currency="PLN", fx_source=None)
        )
    await db_session.rollback()

    assert await _count(db_session, org_a) == 0
    assert await _count(db_session, org_b) == 1
