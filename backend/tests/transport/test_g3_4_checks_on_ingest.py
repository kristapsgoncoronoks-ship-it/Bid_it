"""G3.4 (WO-71) — the R26 checks wired through the REAL registration path
(`statement_ingest.ingest_statement`). The §4.19 heart lives here: an
``error``-SEVERITY advisory finding never blocks registration — rows are
written WITH the warning present. Ordering extends WO-70's stable
contract: parser → review → post-capture → drift."""

from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.models.transport.extraction_baseline import FuelExtractionBaseline
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.services.transport import statement_ingest
from tests.factories.transport import synthetic_eurowag_statement, synthetic_vat_id
from tests.transport.conftest import enable_transport, make_entity, make_org


def _ew_row(**overrides) -> dict[str, object]:
    base: dict[str, object] = {
        "txn_date": "2026-06-01",
        "txn_time": "08:00",
        "vehicle_ref": "V1",
        "station": "S1",
        "country": "BE",
        "product": "DIESEL",
        "qty": "100.000",
        "currency": "EUR",
        "net_local": "100.00",
        "vat_local": "21.00",
        "gross_local": "121.00",
        "invoice_ref": "INV-000001",
    }
    base.update(overrides)
    return base


def _content(rows, footer_lines) -> bytes:
    return synthetic_eurowag_statement(rows=rows, footer_lines=footer_lines, seed=5).encode("utf-8")


_CLEAN_FOOTER = [
    f"Pārdevējs / Verkoper: Nine Baltic Freight UAB, Nine Street 1, "
    f"PVN reg. Nr.: {synthetic_vat_id('LT', seed=11)}"
]


@pytest.mark.asyncio
async def test_r26_error_severity_finding_never_blocks_registration(db_session):
    """Entity B ingests entity A's already-registered invoice: B's result
    carries the `post-capture check (error)` warning AND B's rows are
    written — registration is NOT refused (§4.19, R26 verbatim)."""
    org = await make_org(db_session)
    entity_a = await make_entity(db_session, org.id)
    entity_b = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    content = _content([_ew_row()], _CLEAN_FOOTER)

    first = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity_a.id,
        period="2026-06",
        filename="ew.txt",
        content=content,
    )
    assert len(first.lines) == 1
    assert not any("post-capture check (error)" in w for w in first.warnings)

    second = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity_b.id,
        period="2026-06",
        filename="ew.txt",
        content=content,
    )
    dup = [w for w in second.warnings if "post-capture check (error)" in w]
    assert dup and "already registered under entity" in dup[0]
    assert entity_a.id in dup[0]
    assert len(second.lines) == 1  # rows WRITTEN — advisory never blocks


@pytest.mark.asyncio
async def test_r26_malformed_seller_vat_warns_and_still_registers_and_learns(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    # A genuine seller line whose VAT body is runtime-mangled (EE needs 9
    # digits; 'EE12' is captured by the deliberately-permissive parser
    # regex, then flagged structurally here).
    footer = ["Pārdevējs / Verkoper: Nine Baltic Freight UAB, Nine Street 1, PVN reg. Nr.: EE12"]
    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="ew.txt",
        content=_content([_ew_row()], footer),
    )
    warn = [w for w in result.warnings if "post-capture check (warn)" in w]
    assert warn and "EE12" in warn[0]
    assert len(result.lines) == 1
    # learn_registration still ran — advisory means advisory.
    learned = (
        await db_session.execute(
            select(SupplierVatRegistration).where(
                SupplierVatRegistration.org_id == org.id,
                SupplierVatRegistration.vat_number == "EE12",
            )
        )
    ).scalar_one()
    assert learned.source == "capture"


@pytest.mark.asyncio
async def test_r26_clean_statement_adds_no_post_capture_warnings(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    result = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="ew.txt",
        content=_content([_ew_row()], _CLEAN_FOOTER),
    )
    assert not any("post-capture check" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_warnings_ordering_parser_review_postcapture_drift(db_session):
    """WO-70's ordering contract extended end-to-end: parser → review →
    post-capture → drift, all four present in one result."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    rows = [
        _ew_row(),
        # VAT 50% of net on a BE line — capture_review rule-9 coherence WARN
        # (never a block; 50 <= 100 so the vat<=net ERROR rule stays quiet).
        _ew_row(
            txn_date="2026-06-02",
            vehicle_ref="V2",
            invoice_ref="INV-000002",
            vat_local="50.00",
            gross_local="150.00",
        ),
    ]
    footer = [
        # Parser warning: seller label present, no recognised legal form.
        "Pārdevējs / Verkoper: Somebody Without A Legal Form",
        # Post-capture warn: genuine seller line, mangled VAT body.
        "Pārdevējs / Verkoper: Nine Baltic Freight UAB, Nine Street 1, PVN reg. Nr.: EE12",
    ]
    content = _content(rows, footer)

    first = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="ew.txt",
        content=content,
    )
    assert len(first.lines) == 2

    # Doctor the recorded baseline so the re-ingest of the SAME bytes
    # reports drift (the advisory flag — WO-70).
    await db_session.execute(
        update(FuelExtractionBaseline)
        .where(FuelExtractionBaseline.org_id == org.id)
        .values(line_count=FuelExtractionBaseline.line_count + 1)
    )

    second = await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="ew.txt",
        content=content,
    )
    w = second.warnings
    idx_parser = next(i for i, s in enumerate(w) if "no recognised legal form" in s)
    idx_review = next(i for i, s in enumerate(w) if s.startswith("capture review:"))
    idx_check = next(i for i, s in enumerate(w) if s.startswith("post-capture check"))
    idx_drift = next(i for i, s in enumerate(w) if s.startswith("extraction drift:"))
    assert idx_parser < idx_review < idx_check < idx_drift
    # And the drifted re-ingest still wrote nothing new / blocked nothing.
    assert not any("blocked" in s for s in w)
