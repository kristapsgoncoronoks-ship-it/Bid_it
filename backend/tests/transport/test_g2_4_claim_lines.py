"""G2.4 — live claim-line construction (`app.services.transport.claim_lines.
build_claim_lines`), `BA_fleet_fuel.md` C1, R2. See that module's docstring
for why lines are MATERIALIZED (unlike Fleet Fuel's own live-derived
`invoice_lines()`) and why only `frozen_at IS NULL` rows are ever touched.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.invoice import Invoice
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.models.vendor import Vendor
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _make_claim(db_session, org, entity, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_vendor(db_session, org, *, name: str = "Q8", country: str | None = "LV") -> Vendor:
    vendor = Vendor(org_id=org.id, name=name, country=country)
    db_session.add(vendor)
    await db_session.flush()
    return vendor


async def _make_invoice(db_session, org, vendor, *, number: str) -> Invoice:
    inv = Invoice(
        org_id=org.id,
        vendor_id=vendor.id,
        invoice_number=number,
        issue_date=date(2026, 5, 1),
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("21.00"),
        total=Decimal("121.00"),
    )
    db_session.add(inv)
    await db_session.flush()
    return inv


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    supplier: str = "Q8",
    invoice_ref: str | None = "INV-0001",
    line_seq: int = 1,
    period: str = "2026-05",
    country: str = "LV",
    product: str = "DIESEL",
    net_eur: Decimal = Decimal("100.00"),
    vat_eur: Decimal = Decimal("21.00"),
) -> FuelTransaction:
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 15),
        station="Demo Station Riga",
        product=product,
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
    )


@pytest.mark.asyncio
async def test_g2_4_groups_transactions_by_invoice_and_product_group(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    # Two diesel lines on the same invoice (sole-registered fallback resolves
    # both, since there is exactly one registered invoice for this vendor).
    await _make_txn(
        db_session,
        org,
        entity,
        line_seq=1,
        product="DIESEL",
        net_eur=Decimal("100.00"),
        vat_eur=Decimal("21.00"),
    )
    await _make_txn(
        db_session,
        org,
        entity,
        line_seq=2,
        product="DIESEL",
        net_eur=Decimal("50.00"),
        vat_eur=Decimal("10.50"),
    )
    # One AdBlue line — a DIFFERENT product_group, so a separate claim line.
    await _make_txn(
        db_session,
        org,
        entity,
        line_seq=3,
        product="ADBLUE",
        net_eur=Decimal("20.00"),
        vat_eur=Decimal("4.20"),
    )
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    by_group = {line.product_group: line for line in lines}
    assert set(by_group) == {"Diesel", "AdBlue"}
    diesel = by_group["Diesel"]
    assert diesel.invoice_ref == "INV-0001"
    assert diesel.invoice_id == inv.id
    assert diesel.net_eur == Decimal("150.00")
    assert diesel.vat_eur == Decimal("31.50")
    adblue = by_group["AdBlue"]
    assert adblue.net_eur == Decimal("20.00")
    assert adblue.vat_eur == Decimal("4.20")
    assert all(line.frozen_at is None for line in lines)


@pytest.mark.asyncio
async def test_g2_4_unmatched_transaction_produces_an_unmatched_line_never_an_aggregate(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # No vendor registered at all for "Q8" — every transaction is UNMATCHED.
    await _make_txn(db_session, org, entity, invoice_ref="INV-9999", line_seq=1)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert len(lines) == 1
    assert lines[0].invoice_ref == "UNMATCHED"
    assert lines[0].invoice_id is None


@pytest.mark.asyncio
async def test_g2_4_refuses_a_non_draft_claim(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    claim.status = "submitted"
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert exc.value.code == "claim_not_draft"
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_g2_4_is_rebuildable_without_duplicating_unfrozen_lines(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity, line_seq=1)
    await db_session.commit()

    first = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    second = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    assert len(first) == 1
    assert len(second) == 1
    rows = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    assert len(rows) == 1, "rebuilding must replace, never accumulate, unfrozen lines"


@pytest.mark.asyncio
async def test_g2_4_never_touches_a_frozen_line(db_session):
    """Belt-and-braces proof (module docstring): even though no freeze
    service exists yet, `build_claim_lines` only ever DELETEs+re-INSERTs rows
    where `frozen_at IS NULL` — simulate a hypothetical frozen line and prove
    it survives a rebuild untouched."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    from datetime import UTC, datetime

    frozen = VatRefundClaimLine(
        org_id=org.id,
        claim_id=claim.id,
        invoice_ref="FROZEN-INV-0001",
        product_group="Diesel",
        net_eur=Decimal("999.00"),
        vat_eur=Decimal("199.00"),
        frozen_at=datetime.now(UTC),
    )
    db_session.add(frozen)
    await db_session.commit()

    await _make_txn(db_session, org, entity, line_seq=1)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    all_rows = (
        await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    ).all()
    frozen_rows = [r for r in all_rows if r.frozen_at is not None]
    unfrozen_rows = [r for r in all_rows if r.frozen_at is None]
    assert len(frozen_rows) == 1
    assert frozen_rows[0].invoice_ref == "FROZEN-INV-0001"
    assert frozen_rows[0].net_eur == Decimal("999.00")
    assert len(unfrozen_rows) == len(lines)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ref_period,in_scope_period,out_of_scope_period",
    [
        ("2026-Q1", "2026-03", "2026-04"),
        ("2026-Q2", "2026-04", "2026-03"),
        ("2026-Q4", "2026-12", "2027-01"),
        ("2026-YEAR", "2026-01", "2025-12"),
        ("2026-YEAR", "2026-12", "2027-01"),
    ],
)
async def test_g2_4_period_scoping_includes_and_excludes_the_right_months(
    db_session, ref_period, in_scope_period, out_of_scope_period
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity, ref_period=ref_period)
    await db_session.commit()
    await _make_txn(
        db_session, org, entity, line_seq=1, period=in_scope_period, invoice_ref="INV-IN"
    )
    await _make_txn(
        db_session, org, entity, line_seq=2, period=out_of_scope_period, invoice_ref="INV-OUT"
    )
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert len(lines) == 1  # only the in-scope transaction contributed


@pytest.mark.asyncio
async def test_g2_4_scoped_by_entity_and_country_too(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    other_entity = await make_entity(db_session, org.id, seed=2)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    # Right entity, right country.
    await _make_txn(db_session, org, entity, line_seq=1, country="LV", invoice_ref="INV-IN")
    # Wrong country — excluded even for the right entity.
    await _make_txn(db_session, org, entity, line_seq=2, country="EE", invoice_ref="INV-EE")
    # Right country, wrong entity — excluded.
    await _make_txn(
        db_session, org, other_entity, line_seq=1, country="LV", invoice_ref="INV-OTHER"
    )
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_g2_4_cross_tenant_claim_is_opaque_not_found_never_leaks_across_orgs(db_session):
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id)
    claim_b = await _make_claim(db_session, org_b, entity_b)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await claim_lines.build_claim_lines(db_session, org_a.id, claim_b.id)
    assert exc.value.code == "claim_not_found"
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_g2_4_module_off_is_inert(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)

    # Build the claim under transport ENABLED (get_or_create_claim itself
    # gates on it — a claim cannot even exist with the module off), then
    # disable and prove build_claim_lines refuses before any query.
    await enable_transport(db_session, org.id)
    real_claim = await _make_claim(db_session, org, entity)
    await db_session.commit()

    from app.services import modules

    await modules.set_enabled(db_session, org.id, "transport", False)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await claim_lines.build_claim_lines(db_session, org.id, real_claim.id)
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_g2_4_audit_event_fires_once_per_build_call(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity, line_seq=1)
    await db_session.commit()

    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.claim_lines_build",
            )
        )
    ).all()
    assert len(events) == 2, "each rebuild call audits once — not insert-or-no-op"
