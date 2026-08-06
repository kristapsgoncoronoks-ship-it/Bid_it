"""G3.5 (WO-72) — the receipt-control grid (`BA_fleet_fuel.md` §3.J):
expectation = cadence × activity; cross-control statuses; overrides
(waived/note) survive re-runs; and the §4.19 advisory proof — a `missing`
slot / a waived slot never changes a claim submission's outcome."""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.receipt_control import VatReceiptControl
from app.models.vendor import Vendor
from app.services import extraction
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest, lock, receipt_control
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import activate_entity, enable_transport, make_entity, make_org

PERIOD = "2026-05"


async def _make_vendor(db_session, org, *, name: str, country: str | None = "LV") -> Vendor:
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
        subtotal=Decimal("2000.00"),
        tax_amount=Decimal("420.00"),
        total=Decimal("2420.00"),
    )
    db_session.add(inv)
    await db_session.flush()
    return inv


async def _attach_document(db_session, org, invoice, *, filename="stmt-0001.pdf") -> None:
    run = await extraction.record(
        db_session,
        org.id,
        filename=filename,
        sha256="a" * 64,
        method="pdf-text",
        status="parsed",
    )
    await extraction.link_to_invoice(db_session, org.id, run.id, invoice.id)
    await db_session.commit()


async def _make_txn(
    db_session,
    org,
    entity,
    *,
    supplier: str,
    invoice_ref: str | None,
    txn_date: date,
    country: str = "LV",
    line_seq: int = 1,
    period: str = PERIOD,
    net_eur: Decimal = Decimal("100.00"),
    vat_eur: Decimal = Decimal("21.00"),
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=txn_date,
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
    )


async def _grid(db_session, org) -> dict[tuple[str, str, str], VatReceiptControl]:
    rows = await receipt_control.list_controls(db_session, org.id, PERIOD)
    return {(r.supplier, r.slot, r.country): r for r in rows}


# --------------------------------------------------------------------------- #
# Cross-control statuses
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_5_monthly_slot_with_resolved_documented_invoice_is_received_doc(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org, name="BP")
    inv = await _make_invoice(db_session, org, vendor, number="BP-2026-05-001")
    await db_session.commit()
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="BP",
        invoice_ref="BP-2026-05-001",
        txn_date=date(2026, 5, 10),
    )
    await db_session.commit()
    await _attach_document(db_session, org, inv)

    summary = await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    assert summary["received_doc"] == 1

    grid = await _grid(db_session, org)
    row = grid[("BP", "M", "")]
    assert row.status == "received_doc"
    assert row.txn_count == 1


@pytest.mark.asyncio
async def test_g3_5_resolved_but_undocumented_invoice_is_received_no_doc(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    vendor = await _make_vendor(db_session, org, name="BP")
    await _make_invoice(db_session, org, vendor, number="BP-2026-05-001")
    await db_session.commit()
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="BP",
        invoice_ref="BP-2026-05-001",
        txn_date=date(2026, 5, 10),
    )
    await db_session.commit()

    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()

    grid = await _grid(db_session, org)
    assert grid[("BP", "M", "")].status == "received_no_doc"


@pytest.mark.asyncio
async def test_g3_5_activity_with_no_registered_invoice_is_missing(db_session):
    """§3.J item 3 verbatim: 'MISSING (activity but no invoice registered
    → chase the supplier)'."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    # No vendor, no invoice — nothing can cover this activity.
    await _make_txn(
        db_session, org, entity, supplier="BP", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()

    summary = await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    assert summary["missing"] == 1

    grid = await _grid(db_session, org)
    assert grid[("BP", "M", "")].status == "missing"


# --------------------------------------------------------------------------- #
# Cadence × activity — the slot shapes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_5_semi_monthly_boundary_day_15_is_h1_day_16_is_h2(db_session):
    """The both-sides pair for the half-month split: day 15 lands H1,
    day 16 lands H2 (the documented 1-15/16-end interpretation)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="E100",
        invoice_ref=None,
        txn_date=date(2026, 5, 15),
        line_seq=1,
    )
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="E100",
        invoice_ref=None,
        txn_date=date(2026, 5, 16),
        line_seq=2,
    )
    await db_session.commit()

    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()

    grid = await _grid(db_session, org)
    assert grid[("E100", "H1", "")].txn_count == 1
    assert grid[("E100", "H2", "")].txn_count == 1
    assert grid[("E100", "H1", "")].status == "missing"
    assert grid[("E100", "H2", "")].status == "missing"


@pytest.mark.asyncio
async def test_g3_5_semi_monthly_half_with_no_activity_is_no_activity_never_missing(db_session):
    """§3.J item 2 verbatim: 'No activity → NO ACTIVITY, no invoice
    expected — OK.' The acceptance line: a supplier with no activity in a
    slot is never chased."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(
        db_session, org, entity, supplier="E100", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()

    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()

    grid = await _grid(db_session, org)
    assert grid[("E100", "H1", "")].status == "missing"
    h2 = grid[("E100", "H2", "")]
    assert h2.status == "no_activity"
    assert h2.txn_count == 0


@pytest.mark.asyncio
async def test_g3_5_monthly_per_country_only_countries_with_activity_get_a_row(db_session):
    """Q8 (monthly-per-country, §3.J item 1): FR resolves, DE has nothing
    registered, and an inactive country (PL) has NO row at all."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # Vendor with NO declared country — its invoices register for any
    # refund country (invoice_match's documented interpretation).
    vendor = await _make_vendor(db_session, org, name="Q8", country=None)
    await _make_invoice(db_session, org, vendor, number="Q8-FR-2026-05-01")
    await db_session.commit()
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="Q8",
        invoice_ref="Q8-FR-2026-05-01",
        txn_date=date(2026, 5, 5),
        country="FR",
        line_seq=1,
    )
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="Q8",
        invoice_ref=None,
        txn_date=date(2026, 5, 6),
        country="DE",
        line_seq=2,
    )
    await db_session.commit()

    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()

    grid = await _grid(db_session, org)
    assert grid[("Q8", "M", "FR")].status == "received_no_doc"
    assert grid[("Q8", "M", "DE")].status == "missing"
    assert ("Q8", "M", "PL") not in grid
    assert len([k for k in grid if k[0] == "Q8"]) == 2


@pytest.mark.asyncio
async def test_g3_5_supplier_without_a_cadence_produces_no_rows_and_is_counted(db_session):
    """Opt-in by absence (the R61/WO-66 discipline): an ungoverned
    supplier behaves exactly as before — no expectations, only a summary
    count."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(
        db_session, org, entity, supplier="NOCADENCE", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()

    summary = await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    assert summary["rows"] == 0
    assert summary["unmanaged_suppliers"] == ["NOCADENCE"]
    assert await _grid(db_session, org) == {}


# --------------------------------------------------------------------------- #
# Overrides survive re-runs (§3.J item 4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_5_overrides_survive_rerun_unchanged_and_after_registration(db_session):
    """§3.J item 4 verbatim: 'manual overrides (waived / note) survive
    re-runs.' Two re-runs — one with nothing changed, one after the
    invoice finally registers (status recomputes, overrides untouched)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="TFC",
        invoice_ref="TFC-2026-05-777",
        txn_date=date(2026, 5, 10),
    )
    await db_session.commit()

    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    row = (await _grid(db_session, org))[("TFC", "M", "")]
    assert row.status == "missing"

    updated = await receipt_control.set_control_override(
        db_session, org.id, row.id, waived=True, note="supplier confirms nothing was issued"
    )
    await db_session.commit()
    assert updated.waived is True

    # Re-run 1 — nothing changed underneath.
    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    row1 = (await _grid(db_session, org))[("TFC", "M", "")]
    assert row1.id == row.id, "the re-run must upsert the SAME row, never a duplicate"
    assert row1.waived is True
    assert row1.note == "supplier confirms nothing was issued"
    assert row1.status == "missing"

    # Re-run 2 — the invoice finally registers: status recomputes,
    # overrides pass through untouched.
    vendor = await _make_vendor(db_session, org, name="TFC")
    await _make_invoice(db_session, org, vendor, number="TFC-2026-05-777")
    await db_session.commit()
    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    row2 = (await _grid(db_session, org))[("TFC", "M", "")]
    assert row2.id == row.id
    assert row2.status == "received_no_doc"
    assert row2.waived is True
    assert row2.note == "supplier confirms nothing was issued"


@pytest.mark.asyncio
async def test_g3_5_rerun_is_idempotent_no_duplicate_rows(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(
        db_session, org, entity, supplier="E100", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()

    first = await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    second = await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    assert first["rows"] == second["rows"] == 2  # H1 + H2

    count = await db_session.scalar(
        select(func.count())
        .select_from(VatReceiptControl)
        .where(VatReceiptControl.org_id == org.id)
    )
    assert count == 2


@pytest.mark.asyncio
async def test_g3_5_set_control_override_cross_tenant_is_an_opaque_404(db_session):
    """Master-context §4.4 — a cross-tenant id is indistinguishable from
    an unknown one: 404, never 403."""
    org_a = await make_org(db_session, name="Org A")
    await enable_transport(db_session, org_a.id)
    entity = await make_entity(db_session, org_a.id)
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_b.id)
    await db_session.commit()
    await _make_txn(
        db_session, org_a, entity, supplier="BP", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()
    await receipt_control.run_receipt_control(db_session, org_a.id, PERIOD)
    await db_session.commit()
    row = (await receipt_control.list_controls(db_session, org_a.id, PERIOD))[0]

    with pytest.raises(AppError) as exc:
        await receipt_control.set_control_override(db_session, org_b.id, row.id, waived=True)
    assert exc.value.code == "receipt_control_not_found"
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_g3_5_no_op_override_audits_nothing_and_none_leaves_columns_alone(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _make_txn(
        db_session, org, entity, supplier="BP", invoice_ref=None, txn_date=date(2026, 5, 10)
    )
    await db_session.commit()
    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    row = (await receipt_control.list_controls(db_session, org.id, PERIOD))[0]

    from app.models.audit import AuditEvent

    # waived=False is already the value; note=None means "leave unchanged".
    await receipt_control.set_control_override(db_session, org.id, row.id, waived=False, note=None)
    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.receipt_control_override",
            )
        )
    ).all()
    assert events == []


# --------------------------------------------------------------------------- #
# Advisory end-to-end (§4.19) + structural proofs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_5_missing_and_waived_slots_never_change_a_claim_submission(db_session):
    """The §4.19 proof: the SAME submission that succeeds without receipt-
    control rows succeeds identically with a `missing` row AND a waived
    slot in the claim's period — receipt control gates nothing (the
    blocking side stays with WO-58's waivers + the WO-60 checklist)."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # WO-73 (R44): raised past the activation gate — the WO-60 fixture precedent.
    await activate_entity(db_session, org.id, entity.id, "LV")
    vendor = await _make_vendor(db_session, org, name="Q8", country="LV")
    inv = await _make_invoice(db_session, org, vendor, number="INV-0001")
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2026-Q2"
    )
    await db_session.commit()

    txn = await _make_txn(
        db_session,
        org,
        entity,
        supplier="Q8",
        invoice_ref="INV-0001",
        txn_date=date(2026, 5, 10),
        line_seq=1,
        net_eur=Decimal("2000.00"),
        vat_eur=Decimal("420.00"),  # clears the Art. 17 €400 minimum (R8)
    )
    # A cadence-managed supplier whose invoice never arrived — OUTSIDE the
    # claim's refund country so the grid rows are pure receipt-control
    # findings, not claim-line inputs.
    await _make_txn(
        db_session,
        org,
        entity,
        supplier="E100",
        invoice_ref=None,
        txn_date=date(2026, 5, 12),
        country="EE",
        line_seq=2,
    )
    await db_session.commit()

    await receipt_control.run_receipt_control(db_session, org.id, PERIOD)
    await db_session.commit()
    grid = await _grid(db_session, org)
    assert grid[("E100", "H1", "")].status == "missing"
    await receipt_control.set_control_override(
        db_session, org.id, grid[("E100", "H1", "")].id, waived=True
    )
    await db_session.commit()

    await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    await db_session.commit()
    await _attach_document(db_session, org, inv)

    result = await lock.submit_claim(
        db_session,
        org.id,
        claim_id=claim.id,
        invoices=[("Q8", "INV-0001", txn.id)],
        today=date(2026, 7, 1),
    )
    assert result.status == "submitted"
    assert result.status_note is None or "Receipt-control waiver" not in result.status_note, (
        "a G3.5 slot waiver is NOT WO-58's claim-level waiver and must not be stamped"
    )


def test_g3_5_run_never_writes_the_override_columns_structurally():
    """The §3.J item 4 contract, grep-proven (the R5 structural-test
    pattern): `run_receipt_control` never assigns an EXISTING row's
    `waived`/`note`; `set_control_override` is their only writer."""
    run_src = inspect.getsource(receipt_control.run_receipt_control)
    assert "existing.status" in run_src  # the computed columns ARE written...
    assert "existing.waived" not in run_src  # ...the override columns NEVER
    assert "existing.note" not in run_src

    override_src = inspect.getsource(receipt_control.set_control_override)
    assert "row.waived = waived" in override_src
    assert "row.note = note" in override_src

    # No OTHER transport service writes these columns at all.
    import pathlib

    import app.services.transport as pkg

    pkg_dir = pathlib.Path(pkg.__file__).parent
    for path in sorted(pkg_dir.glob("*.py")):
        if path.name == "receipt_control.py":
            continue
        src = path.read_text()
        assert ".waived =" not in src, f"{path.name} must not write VatReceiptControl.waived"
