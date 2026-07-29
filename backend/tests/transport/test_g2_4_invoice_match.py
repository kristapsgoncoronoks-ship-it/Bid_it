"""G2.4 — note→invoice resolution (`app.services.transport.invoice_match`),
`BA_fleet_fuel.md` C3/C4, R2/R16. See that module's docstring for the exact
resolution order and the documented interpretation of the two underspecified
heuristics.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.services.transport import invoice_match
from tests.transport.conftest import enable_transport, make_entity, make_org


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


@pytest.mark.asyncio
async def test_g2_4_heuristic1_prefix_match_resolves_uniquely(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="INV-000123-STATEMENT",
    )
    assert matched is not None
    assert matched.invoice_id == inv.id
    assert matched.invoice_number == "INV-000123"


@pytest.mark.asyncio
async def test_g2_4_heuristic2_stem_contained_resolves_uniquely(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv1 = await _make_invoice(db_session, org, vendor, number="INV-000123")
    await _make_invoice(db_session, org, vendor, number="INV-000456")
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="STMT-INV-000123-PAGE2",
    )
    assert matched is not None
    assert matched.invoice_id == inv1.id


@pytest.mark.asyncio
async def test_g2_4_override_never_displaces_a_successful_heuristic_match(db_session):
    """C4 verbatim: an override only REDUCES UNMATCHED; it never displaces a
    successful heuristic match — even if an admin mistakenly points an
    override at the wrong invoice for the same note key."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    correct = await _make_invoice(db_session, org, vendor, number="INV-000123")
    wrong = await _make_invoice(db_session, org, vendor, number="INV-999999")
    await db_session.commit()

    await invoice_match.set_note_override(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="INV-000123-STATEMENT",
        target_invoice_id=wrong.id,
    )
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="INV-000123-STATEMENT",
    )
    assert matched is not None
    assert matched.invoice_id == correct.id


@pytest.mark.asyncio
async def test_g2_4_override_resolves_when_both_heuristics_are_ambiguous_or_fail(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    target = await _make_invoice(db_session, org, vendor, number="INV-000001")
    await _make_invoice(db_session, org, vendor, number="INV-000002")
    await db_session.commit()

    note = "CARD-STATEMENT-XYZ"  # matches neither heuristic against either invoice
    await invoice_match.set_note_override(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref=note,
        target_invoice_id=target.id,
    )
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref=note,
    )
    assert matched is not None
    assert matched.invoice_id == target.id


@pytest.mark.asyncio
async def test_g2_4_sole_registered_fallback_resolves_with_no_note_match_and_no_override(
    db_session,
):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    only = await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="totally-unrelated-note",
    )
    assert matched is not None
    assert matched.invoice_id == only.id


@pytest.mark.asyncio
async def test_g2_4_unmatched_when_multiple_candidates_and_no_match_and_no_override(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    await _make_invoice(db_session, org, vendor, number="INV-000001")
    await _make_invoice(db_session, org, vendor, number="INV-000002")
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="totally-unrelated-note",
    )
    assert matched is None


@pytest.mark.asyncio
async def test_g2_4_unmatched_when_supplier_has_no_registered_vendor(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="NoSuchVendor",
        refund_country="LV",
        invoice_ref="INV-000001",
    )
    assert matched is None


@pytest.mark.asyncio
async def test_g2_4_already_synthetic_invoice_ref_short_circuits_to_unmatched(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    await _make_invoice(db_session, org, vendor, number="INPUT-000001")
    await db_session.commit()

    for ref in ("INPUT:pending", "ALL:LV", "UNMATCHED", None):
        matched = await invoice_match.resolve_invoice_ref(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="Q8",
            refund_country="LV",
            invoice_ref=ref,
        )
        assert matched is None, f"{ref!r} should short-circuit to UNMATCHED"


@pytest.mark.asyncio
async def test_g2_4_vendor_country_mismatch_yields_no_registered_invoices(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    vendor = await _make_vendor(db_session, org, country="LV")
    await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    rows = await invoice_match.registered_invoices(
        db_session, org.id, supplier="Q8", refund_country="EE"
    )
    assert rows == []


@pytest.mark.asyncio
async def test_g2_4_vendor_with_no_country_set_is_not_excluded(db_session):
    """Documented interpretation (module docstring): a vendor that has not
    declared a country is not excluded by the refund-country filter — this
    codebase does not yet model per-country legal-entity registrations."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    vendor = await _make_vendor(db_session, org, country=None)
    await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    rows = await invoice_match.registered_invoices(
        db_session, org.id, supplier="Q8", refund_country="EE"
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_g2_4_set_note_override_refuses_a_synthetic_note(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await invoice_match.set_note_override(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="Q8",
            refund_country="LV",
            invoice_ref="UNMATCHED",
            target_invoice_id=inv.id,
        )
    assert exc.value.code == "override_note_is_synthetic"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_g2_4_set_note_override_refuses_an_unregistered_target(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await invoice_match.set_note_override(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="Q8",
            refund_country="LV",
            invoice_ref="some-note",
            target_invoice_id=str(uuid.uuid4()),
        )
    assert exc.value.code == "override_target_not_registered"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_g2_4_set_note_override_is_idempotent_on_key_updates_the_target(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    first = await _make_invoice(db_session, org, vendor, number="INV-000001")
    second = await _make_invoice(db_session, org, vendor, number="INV-000002")
    await db_session.commit()

    from app.models.transport.note_override import VatNoteInvoiceOverride

    await invoice_match.set_note_override(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="some-note",
        target_invoice_id=first.id,
    )
    await db_session.commit()
    await invoice_match.set_note_override(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="some-note",
        target_invoice_id=second.id,
    )
    await db_session.commit()

    rows = (
        await db_session.scalars(
            select(VatNoteInvoiceOverride).where(VatNoteInvoiceOverride.org_id == org.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].target_invoice_id == second.id


@pytest.mark.asyncio
async def test_g2_4_deregistering_the_target_invoice_stops_the_override_resolving(db_session):
    """R16's acceptance line, verbatim: "De-register the target ⇒ the
    override silently stops resolving; the line reverts to UNMATCHED."."""
    # The test engine (tests/conftest.py's `_db` fixture) does not attach the
    # app's production `PRAGMA foreign_keys=ON` connect-listener
    # (app/core/database.py — it's registered on the app's OWN engine object),
    # so SQLite's ON DELETE actions are otherwise inert in this suite. This
    # fixture uses a StaticPool (one physical connection for the whole test),
    # so setting the pragma once on this session's connection makes the real
    # `fk_vat_note_invoice_overrides_target` SET NULL action fire for the rest
    # of this test — proving the actual DB constraint, not simulating it.
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    target = await _make_invoice(db_session, org, vendor, number="INV-000001")
    await db_session.commit()

    note = "CARD-STATEMENT-XYZ"  # matches neither heuristic
    await invoice_match.set_note_override(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref=note,
        target_invoice_id=target.id,
    )
    await db_session.commit()

    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref=note,
    )
    assert matched is not None
    assert matched.invoice_id == target.id

    # De-register: delete the target invoice — the DB-level ON DELETE CASCADE
    # fires on `fk_vat_note_invoice_overrides_target`, deleting the override
    # ROW itself (see the model docstring "WHY CASCADE..." — a composite-FK
    # SET NULL is not viable here since org_id is NOT NULL). This session
    # runs with `expire_on_commit=False` (this codebase's standard session
    # config), so `expire_all()` simulates what a fresh request's session
    # sees naturally, proving the DB constraint itself, not an ORM cache.
    org_id, entity_id = org.id, entity.id  # captured BEFORE expire_all() below
    fresh_target = await db_session.scalar(select(Invoice).where(Invoice.id == target.id))
    await db_session.delete(fresh_target)
    await db_session.commit()
    db_session.expire_all()

    from app.models.transport.note_override import VatNoteInvoiceOverride

    override_row = await db_session.scalar(
        select(VatNoteInvoiceOverride).where(VatNoteInvoiceOverride.org_id == org_id)
    )
    assert override_row is None, "the override row must be gone once its target is de-registered"

    # Falls through to sole-registered fallback — there are now ZERO
    # registered invoices for this vendor, so it reverts to UNMATCHED.
    matched_after = await invoice_match.resolve_invoice_ref(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref=note,
    )
    assert matched_after is None


@pytest.mark.asyncio
async def test_g2_4_module_off_resolve_invoice_ref_is_inert(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await invoice_match.resolve_invoice_ref(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="Q8",
            refund_country="LV",
            invoice_ref="INV-000123",
        )
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_g2_4_module_off_set_note_override_is_inert(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org)
    inv = await _make_invoice(db_session, org, vendor, number="INV-000123")
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await invoice_match.set_note_override(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="Q8",
            refund_country="LV",
            invoice_ref="some-note",
            target_invoice_id=inv.id,
        )
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_g2_4_cross_tenant_vendors_never_leak_into_another_orgs_resolution(db_session):
    """Overlapping-looking data across two orgs — same vendor name, same
    invoice number — must never resolve across the tenant boundary."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    entity_a = await make_entity(db_session, org_a.id)

    vendor_b = await _make_vendor(db_session, org_b)
    await _make_invoice(db_session, org_b, vendor_b, number="INV-000123")
    await db_session.commit()

    # Org A has no vendor named "Q8" of its own — org B's identically-named
    # vendor/invoice must not resolve for org A.
    matched = await invoice_match.resolve_invoice_ref(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        supplier="Q8",
        refund_country="LV",
        invoice_ref="INV-000123",
    )
    assert matched is None
