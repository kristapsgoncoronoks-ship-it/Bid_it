"""G1.2 — `fuel_transactions`: the typed transaction model + idempotent
ingestion. `BA_fleet_fuel.md` section 4.2 (the canonical schema) + section 8.1
items 4-6 (no duplicated positional schema; split `note`; a real natural
key). See `docs/plan/plan-a/wo/WO-50-G1.2.md` for the R29/R30 citation
correction — this order builds from section 4.2 / 8.1, not the literal
R29/R30 rows (which are about engine read-only ownership and the separate
claims store, not the transaction schema).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.fuel_transaction import FuelTransaction
from app.services.transport import fuel_ingest
from tests.transport.conftest import enable_transport, make_entity, make_org

_BASE_KWARGS = dict(
    supplier="Q8",
    period="2026-06",
    country="lv",
    vehicle_ref="Fleet-A-001",
    txn_date="2026-06-15",
    station="Demo Station Riga",
    product="DIESEL",
    qty=Decimal("450.500"),
    currency="EUR",
    net_local=Decimal("620.00"),
    vat_local=Decimal("130.20"),
    gross_local=Decimal("750.20"),
    net_eur=Decimal("620.00"),
    vat_eur=Decimal("130.20"),
)


def _kwargs(**overrides):
    from datetime import date

    kw = dict(_BASE_KWARGS)
    kw.update(overrides)
    kw["txn_date"] = (
        date.fromisoformat(kw["txn_date"]) if isinstance(kw["txn_date"], str) else kw["txn_date"]
    )
    return kw


@pytest.mark.asyncio
async def test_g1_2_ingest_transaction_upserts_never_duplicates(db_session):
    """Calling `ingest_transaction` twice with the SAME natural key
    `(org, entity, supplier, period, line_seq)` returns the SAME row and
    creates no second one — exactly one AuditEvent, mirroring WO-49's R1
    acceptance test."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    first = await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=1, **_kwargs()
    )
    await db_session.commit()

    second = await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=1, **_kwargs()
    )
    await db_session.commit()

    assert first.id == second.id
    rows = (
        await db_session.scalars(
            select(FuelTransaction).where(
                FuelTransaction.org_id == org.id,
                FuelTransaction.entity_id == entity.id,
                FuelTransaction.supplier == "Q8",
                FuelTransaction.period == "2026-06",
                FuelTransaction.line_seq == 1,
            )
        )
    ).all()
    assert len(rows) == 1

    creates = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.fuel_transaction_ingest",
            )
        )
    ).all()
    assert len(creates) == 1


@pytest.mark.asyncio
async def test_g1_2_different_line_seq_or_period_are_distinct_rows(db_session):
    """Changing ANY one of the five natural-key fields is a different row —
    the constraint must not be over-broad."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    line1 = await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=1, **_kwargs()
    )
    line2 = await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=2, **_kwargs()
    )
    other_period = await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=1, **_kwargs(period="2026-07")
    )
    other_supplier = await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=1, **_kwargs(supplier="BP")
    )
    assert len({line1.id, line2.id, other_period.id, other_supplier.id}) == 4


@pytest.mark.asyncio
async def test_g1_2_natural_key_uniqueness_constraint_rejects_a_raw_duplicate_insert(db_session):
    """The DB constraint itself (`uq_fuel_transactions_natural_key`) is the
    backstop under a lost race — a bare second INSERT with the identical key,
    bypassing the service's find-first lookup, must be refused by the
    database."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    row_kwargs = _kwargs()
    row_kwargs["product_group"] = "Diesel"
    row_kwargs["net_eur_eff"] = row_kwargs["net_eur"]

    db_session.add(FuelTransaction(org_id=org.id, entity_id=entity.id, line_seq=1, **row_kwargs))
    await db_session.commit()

    db_session.add(FuelTransaction(org_id=org.id, entity_id=entity.id, line_seq=1, **row_kwargs))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_g1_2_qty_is_never_quantized_to_cents(db_session):
    """`qty` survives ingestion unrounded (it is the €/L denominator); the
    monetary columns passed alongside it ARE quantized to cents ROUND_HALF_UP."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    txn = await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        line_seq=1,
        **_kwargs(qty=Decimal("123.4567"), net_local=Decimal("12.345"), net_eur=Decimal("12.345")),
    )
    assert txn.qty == Decimal("123.4567")
    assert txn.net_local == Decimal("12.35")  # q2 ROUND_HALF_UP
    assert txn.net_eur == Decimal("12.35")
    assert txn.net_eur_eff == Decimal("12.35")  # defaults to net_eur, also quantized


@pytest.mark.asyncio
async def test_g1_2_unknown_entity_is_a_404_not_found_never_a_500(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    with pytest.raises(AppError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org.id, entity_id=str(uuid.uuid4()), line_seq=1, **_kwargs()
        )
    assert exc.value.code == "entity_not_found"
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_g1_2_cross_tenant_entity_is_opaque_not_found_never_leaks_across_orgs(db_session):
    """Tenancy invariant (master context §4.4): a cross-tenant fetch by id is
    an opaque 404, never a 403 or a successful cross-tenant row. Org B's
    entity id is a perfectly real id — just not org A's."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org_a.id, entity_id=entity_b.id, line_seq=1, **_kwargs()
        )
    assert exc.value.code == "entity_not_found"
    assert exc.value.status == 404

    rows = (
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_a.id))
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_g1_2_transport_disabled_by_default_ingest_is_inert(db_session):
    """A fresh org has `transport` disabled by default; `ingest_transaction`
    must refuse BEFORE any row is queried or written — proven by getting a
    403 module_not_enabled rather than a 404 entity_not_found for a
    deliberately nonexistent entity id (mirrors WO-49's
    test_transport_service_is_inert_when_the_module_is_off)."""
    org = await make_org(db_session)  # transport NOT enabled

    with pytest.raises(AppError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org.id, entity_id=str(uuid.uuid4()), line_seq=1, **_kwargs()
        )
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403

    rows = (await db_session.scalars(select(FuelTransaction))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_g1_2_transport_off_for_one_org_does_not_leak_from_another_orgs_activation(
    db_session,
):
    """Entitlement is per-org, not global — activating transport for org B
    must not make org A's ingest calls succeed."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org_a.id, entity_id=entity_a.id, line_seq=1, **_kwargs()
        )
    assert exc.value.code == "module_not_enabled"


@pytest.mark.asyncio
async def test_g1_2_product_group_ck_rejects_an_out_of_set_value_at_the_database_level(db_session):
    """Defense-in-depth: a raw INSERT with an out-of-set product_group must be
    refused by the database, not only by derive_product_group()."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    row_kwargs = _kwargs()
    row_kwargs["product_group"] = "Petrol"  # not one of the 7
    row_kwargs["net_eur_eff"] = row_kwargs["net_eur"]

    db_session.add(FuelTransaction(org_id=org.id, entity_id=entity.id, line_seq=1, **row_kwargs))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
