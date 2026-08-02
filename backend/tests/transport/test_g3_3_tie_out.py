"""G3.3 (WO-66) — the engine tie-out service
(`app.services.transport.tie_out`): expectation CRUD (audited, validated,
tenant-opaque) and `check_period`'s Decimal metric comparisons — lines
EXACT always, gross_local within the typed [0.02, 0.05] tolerance,
net_eur/gross_eur within 0.05, diesel litres counting ONLY the Diesel
product group, and `gross_local` never summed across currencies (§4.14).
See `test_g3_3_close_halts_on_tie_out.py` for the close-halting half of
R25."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError, NotFoundError, ValidationError
from app.models.audit import AuditEvent
from app.models.transport.tie_out import FuelTieOutExpectation
from app.services.transport import fuel_ingest, tie_out
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_txn(db_session, org, entity, *, line_seq=1, **overrides):
    kwargs = dict(
        supplier="DKV",
        period="2026-06",
        country="SE",
        vehicle_ref="Fleet-D-001",
        txn_date=date(2026, 6, 10),
        station="Stockholm South Hub",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="SEK",
        net_local=Decimal("1000.00"),
        vat_local=Decimal("250.00"),
        gross_local=Decimal("1250.00"),
        net_eur=Decimal("90.00"),
        vat_eur=Decimal("22.50"),
        invoice_ref="DKV-2026-000123",
    )
    kwargs.update(overrides)
    return await fuel_ingest.ingest_transaction(
        db_session, org.id, entity_id=entity.id, line_seq=line_seq, **kwargs
    )


async def _audit_events(db_session, org_id, action):
    return (
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.org_id == org_id, AuditEvent.action == action)
        )
    ).all()


def _meta(event):
    return json.loads(event.meta) if isinstance(event.meta, str) else event.meta


# --------------------------------------------------------------------------- #
# set_expectation / remove_expectation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_3_set_expectation_creates_and_audits(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    row = await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="DKV",
        period="2026-06",
        currency="sek",  # normalized to upper
        expected_lines=2,
        expected_gross_local=Decimal("2500.005"),  # q2 HALF_UP -> 2500.01
        expected_net_eur=Decimal("180.00"),
    )
    await db_session.commit()

    assert row.currency == "SEK"
    assert row.expected_gross_local == Decimal("2500.01")
    assert row.gross_local_tolerance == Decimal("0.02")  # the strict default

    events = await _audit_events(db_session, org.id, "transport.tieout_expectation_set")
    assert len(events) == 1
    meta = _meta(events[0])
    assert meta["old"] is None
    assert meta["new"]["expected_lines"] == 2
    assert meta["new"]["expected_gross_local"] == "2500.01"


@pytest.mark.asyncio
async def test_g3_3_set_expectation_is_an_upsert_with_old_new_audit(db_session):
    """A typo is corrected by retyping — the row is replaced, the audit
    trail keeps old->new."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    common = dict(entity_id=entity.id, supplier="DKV", period="2026-06", currency="SEK")
    await tie_out.set_expectation(db_session, org.id, expected_lines=2, **common)
    await db_session.commit()
    await tie_out.set_expectation(db_session, org.id, expected_lines=3, **common)
    await db_session.commit()

    rows = (
        await db_session.scalars(
            select(FuelTieOutExpectation).where(FuelTieOutExpectation.org_id == org.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].expected_lines == 3

    events = await _audit_events(db_session, org.id, "transport.tieout_expectation_set")
    assert len(events) == 2
    retype = _meta(events[-1])
    assert retype["old"]["expected_lines"] == 2
    assert retype["new"]["expected_lines"] == 3


@pytest.mark.asyncio
async def test_g3_3_set_expectation_validation_refusals(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    common = dict(entity_id=entity.id, supplier="DKV", currency="SEK")

    with pytest.raises(ValidationError) as exc:
        await tie_out.set_expectation(
            db_session, org.id, period="2026-13", expected_lines=1, **common
        )
    assert exc.value.code == "invalid_period"

    with pytest.raises(ValidationError) as exc:
        await tie_out.set_expectation(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="DKV",
            period="2026-06",
            currency="SEKK",
            expected_lines=1,
        )
    assert exc.value.code == "invalid_currency"

    with pytest.raises(ValidationError) as exc:
        await tie_out.set_expectation(
            db_session, org.id, period="2026-06", expected_lines=-1, **common
        )
    assert exc.value.code == "invalid_expected_lines"

    # The harvested tolerance band [0.02, 0.05] — both edges hold, both
    # sides beyond refuse.
    for bad in (Decimal("0.01"), Decimal("0.06")):
        with pytest.raises(ValidationError) as exc:
            await tie_out.set_expectation(
                db_session,
                org.id,
                period="2026-06",
                expected_lines=1,
                expected_gross_local=Decimal("100.00"),
                gross_local_tolerance=bad,
                **common,
            )
        assert exc.value.code == "invalid_gross_tolerance"
    row = await tie_out.set_expectation(
        db_session,
        org.id,
        period="2026-06",
        expected_lines=1,
        expected_gross_local=Decimal("100.00"),
        gross_local_tolerance=Decimal("0.05"),
        **common,
    )
    assert row.gross_local_tolerance == Decimal("0.05")


@pytest.mark.asyncio
async def test_g3_3_cross_tenant_entity_is_opaque_not_found(db_session):
    """§4.4: tenant B's entity id through tenant A's call — 404-shaped,
    never a 403 that confirms the id exists."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id)
    await db_session.commit()

    with pytest.raises(NotFoundError) as exc:
        await tie_out.set_expectation(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            supplier="DKV",
            period="2026-06",
            currency="SEK",
            expected_lines=1,
        )
    assert exc.value.code == "entity_not_found"


@pytest.mark.asyncio
async def test_g3_3_remove_expectation_deletes_and_audits(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    common = dict(entity_id=entity.id, supplier="DKV", period="2026-06", currency="SEK")
    await tie_out.set_expectation(db_session, org.id, expected_lines=2, **common)
    await db_session.commit()

    await tie_out.remove_expectation(db_session, org.id, **common)
    await db_session.commit()

    rows = (
        await db_session.scalars(
            select(FuelTieOutExpectation).where(FuelTieOutExpectation.org_id == org.id)
        )
    ).all()
    assert rows == []
    events = await _audit_events(db_session, org.id, "transport.tieout_expectation_remove")
    assert len(events) == 1
    meta = _meta(events[0])
    assert meta["old"]["expected_lines"] == 2
    assert meta["new"] is None

    # Removing again — or removing a foreign org's key — is the SAME
    # opaque absence.
    with pytest.raises(NotFoundError) as exc:
        await tie_out.remove_expectation(db_session, org.id, **common)
    assert exc.value.code == "tieout_expectation_not_found"


@pytest.mark.asyncio
async def test_g3_3_module_off_tie_out_is_inert(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    for call in (
        tie_out.set_expectation(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="DKV",
            period="2026-06",
            currency="SEK",
            expected_lines=1,
        ),
        tie_out.remove_expectation(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="DKV",
            period="2026-06",
            currency="SEK",
        ),
        tie_out.check_period(db_session, org.id, "2026-06"),
    ):
        with pytest.raises(AppError) as exc:
            await call
        assert exc.value.code == "module_not_enabled"
        assert exc.value.status == 403


# --------------------------------------------------------------------------- #
# check_period — the metric math
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_3_check_period_exact_match_passes_every_metric(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)
    await _make_txn(db_session, org, entity, line_seq=2, txn_date=date(2026, 6, 12))
    await db_session.commit()

    await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="DKV",
        period="2026-06",
        currency="SEK",
        expected_lines=2,
        expected_gross_local=Decimal("2500.00"),
        expected_net_eur=Decimal("180.00"),
        expected_gross_eur=Decimal("225.00"),
        expected_diesel_litres=Decimal("200.000"),
    )
    await db_session.commit()

    results = await tie_out.check_period(db_session, org.id, "2026-06")
    assert len(results) == 1
    assert results[0].ok is True
    assert {c.metric for c in results[0].checks} == {
        "lines",
        "gross_local",
        "net_eur",
        "gross_eur",
        "diesel_litres",
    }


@pytest.mark.asyncio
async def test_g3_3_lines_off_by_one_fails_even_when_money_matches(db_session):
    """R25: `lines` tolerance is 0 — EXACT, always."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)
    await db_session.commit()

    await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="DKV",
        period="2026-06",
        currency="SEK",
        expected_lines=2,  # off by one
        expected_gross_local=Decimal("1250.00"),  # money matches exactly
    )
    await db_session.commit()

    results = await tie_out.check_period(db_session, org.id, "2026-06")
    assert results[0].ok is False
    failed = {c.metric for c in results[0].failures}
    assert failed == {"lines"}


@pytest.mark.asyncio
async def test_g3_3_gross_local_tolerance_edges(db_session):
    """Default tolerance 0.02: a 0.02 diff passes, 0.03 fails; a typed
    0.05 tolerance accepts 0.05 and refuses 0.06."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)  # gross_local 1250.00
    await db_session.commit()

    common = dict(
        entity_id=entity.id, supplier="DKV", period="2026-06", currency="SEK", expected_lines=1
    )

    for expected, tol, ok in (
        (Decimal("1250.02"), Decimal("0.02"), True),
        (Decimal("1250.03"), Decimal("0.02"), False),
        (Decimal("1250.05"), Decimal("0.05"), True),
        (Decimal("1250.06"), Decimal("0.05"), False),
    ):
        await tie_out.set_expectation(
            db_session,
            org.id,
            expected_gross_local=expected,
            gross_local_tolerance=tol,
            **common,
        )
        await db_session.commit()
        results = await tie_out.check_period(db_session, org.id, "2026-06")
        gross = next(c for c in results[0].checks if c.metric == "gross_local")
        assert gross.ok is ok, f"expected {expected} tol {tol} -> ok={ok}"


@pytest.mark.asyncio
async def test_g3_3_eur_metric_tolerance_is_five_cents(db_session):
    """net_eur/gross_eur: 0.05 passes, 0.06 fails (harvested, fixed)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)  # net_eur 90.00, gross_eur 112.50
    await db_session.commit()

    common = dict(
        entity_id=entity.id, supplier="DKV", period="2026-06", currency="SEK", expected_lines=1
    )
    for expected_net, ok in ((Decimal("90.05"), True), (Decimal("90.06"), False)):
        await tie_out.set_expectation(db_session, org.id, expected_net_eur=expected_net, **common)
        await db_session.commit()
        results = await tie_out.check_period(db_session, org.id, "2026-06")
        net = next(c for c in results[0].checks if c.metric == "net_eur")
        assert net.ok is ok

    for expected_gross, ok in ((Decimal("112.55"), True), (Decimal("112.56"), False)):
        await tie_out.set_expectation(
            db_session, org.id, expected_net_eur=None, expected_gross_eur=expected_gross, **common
        )
        await db_session.commit()
        results = await tie_out.check_period(db_session, org.id, "2026-06")
        gross = next(c for c in results[0].checks if c.metric == "gross_eur")
        assert gross.ok is ok


@pytest.mark.asyncio
async def test_g3_3_diesel_litres_counts_only_the_diesel_product_group(db_session):
    """A parking-services line's qty must NOT count toward diesel litres."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)  # DIESEL, 100 L
    await _make_txn(
        db_session,
        org,
        entity,
        line_seq=2,
        product="PARKING FEE",  # -> Parking group
        qty=Decimal("1.000"),
        net_local=Decimal("50.00"),
        vat_local=Decimal("12.50"),
        gross_local=Decimal("62.50"),
        net_eur=Decimal("4.50"),
        vat_eur=Decimal("1.13"),
    )
    await db_session.commit()

    await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="DKV",
        period="2026-06",
        currency="SEK",
        expected_lines=2,
        expected_diesel_litres=Decimal("100.000"),  # NOT 101
    )
    await db_session.commit()

    results = await tie_out.check_period(db_session, org.id, "2026-06")
    litres = next(c for c in results[0].checks if c.metric == "diesel_litres")
    assert litres.ok is True
    assert litres.actual == Decimal("100.000")


@pytest.mark.asyncio
async def test_g3_3_check_period_isolates_currencies(db_session):
    """§4.14: a SEK expectation ties out ONLY the SEK lines — an EUR line
    for the same (entity, supplier, period) is neither counted nor summed
    into gross_local."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)  # SEK 1250.00
    await _make_txn(
        db_session,
        org,
        entity,
        line_seq=2,
        country="DE",
        currency="EUR",
        net_local=Decimal("200.00"),
        vat_local=Decimal("38.00"),
        gross_local=Decimal("238.00"),
        net_eur=Decimal("200.00"),
        vat_eur=Decimal("38.00"),
    )
    await db_session.commit()

    await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="DKV",
        period="2026-06",
        currency="SEK",
        expected_lines=1,  # only the SEK line
        expected_gross_local=Decimal("1250.00"),  # only the SEK gross
    )
    await db_session.commit()

    results = await tie_out.check_period(db_session, org.id, "2026-06")
    assert results[0].ok is True


@pytest.mark.asyncio
async def test_g3_3_untyped_metrics_are_not_checked(db_session):
    """Only figures typed from the PDF tie out: an expectation carrying
    ONLY the line count ignores a wildly different money aggregate."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await _make_txn(db_session, org, entity, line_seq=1)
    await db_session.commit()

    await tie_out.set_expectation(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="DKV",
        period="2026-06",
        currency="SEK",
        expected_lines=1,
    )
    await db_session.commit()

    results = await tie_out.check_period(db_session, org.id, "2026-06")
    assert results[0].ok is True
    assert [c.metric for c in results[0].checks] == ["lines"]


@pytest.mark.asyncio
async def test_g3_3_check_period_never_sees_a_foreign_orgs_expectation(db_session):
    """Tenancy overlap discipline (§8): org B types an expectation for the
    same supplier/period — org A's check_period returns nothing."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id)
    await db_session.commit()

    await tie_out.set_expectation(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        supplier="DKV",
        period="2026-06",
        currency="SEK",
        expected_lines=99,
    )
    await db_session.commit()

    assert await tie_out.check_period(db_session, org_a.id, "2026-06") == []
