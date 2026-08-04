"""G3.3 slice 2 (WO-70) — the anti-drift baseline wired through the REAL
registration path (`statement_ingest.ingest_statement`): record on the
FIRST fully successful registration only (a capture-gate-blocked or
FX-failed statement leaves no baseline), compare on a re-seen digest, and
the drift flag is ADVISORY — the harvested verb is "flags", never a
block (§4.19 semantics; contrast the same section's two R25 regimes,
which block registration and halt the close).

The drifted-reingest test doubles as the slice's motivation proof:
`ingest_transaction` is insert-or-no-op on the natural key, so a drifted
re-parse changes NOTHING in `fuel_transactions` — the warning is the only
visibility that the parser no longer reproduces the registered figures.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.models.audit import AuditEvent
from app.models.transport.extraction_baseline import FuelExtractionBaseline
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import fx
from app.services.transport import statement_ingest
from tests.factories.transport import synthetic_eurowag_statement, synthetic_q8_statement
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


def _ew_content(rows=None) -> bytes:
    rows = rows or [
        _ew_row(),
        _ew_row(txn_date="2026-06-02", vehicle_ref="V2", invoice_ref="INV-000002"),
    ]
    return synthetic_eurowag_statement(
        rows=rows, footer_lines=["No seller line here."], seed=1
    ).encode("utf-8")


async def _ingest(db_session, org, entity, content: bytes):
    return await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename="s.csv",
        content=content,
    )


async def _org_entity(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    return org, entity


async def _baseline_rows(db_session, org_id: str):
    return (
        await db_session.scalars(
            select(FuelExtractionBaseline).where(FuelExtractionBaseline.org_id == org_id)
        )
    ).all()


async def _set_events(db_session, org_id):
    return (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org_id,
                AuditEvent.action == "transport.extraction_baseline_set",
            )
        )
    ).all()


@pytest.mark.asyncio
async def test_g3_3s2_first_successful_ingest_records_the_baseline(db_session):
    """The confirm-time record: one row per currency, q2-exact totals of
    the parsed LOCAL figures, one audited SET event with old=None."""
    org, entity = await _org_entity(db_session)
    await _ingest(db_session, org, entity, _ew_content())
    await db_session.commit()

    (row,) = await _baseline_rows(db_session, org.id)
    assert row.currency == "EUR"
    assert row.network == "Eurowag"
    assert row.filename == "s.csv"
    assert row.line_count == 2
    assert Decimal(row.net_total) == Decimal("200.00")
    assert Decimal(row.vat_total) == Decimal("42.00")
    assert len(row.statement_sha256) == 64

    (event,) = await _set_events(db_session, org.id)
    import json

    meta = json.loads(event.meta) if isinstance(event.meta, str) else event.meta
    assert meta["old"] is None
    assert meta["new"]["EUR"] == {"lines": 2, "net": "200.00", "vat": "42.00"}


@pytest.mark.asyncio
async def test_g3_3s2_capture_blocked_statement_leaves_no_baseline(db_session):
    """Only a CONFIRMED extraction is known-good — a statement the
    capture review gate refuses records nothing."""
    org, entity = await _org_entity(db_session)
    bad = _ew_content(rows=[_ew_row(net_local="100.00", vat_local="123.00", gross_local="223.00")])
    with pytest.raises(ValidationError) as exc_info:
        await _ingest(db_session, org, entity, bad)
    assert exc_info.value.code == "capture_review_blocked"
    assert await _baseline_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_3s2_fx_failed_statement_leaves_no_baseline(db_session):
    """Same for an FX failure in phase 1 — the two-phase guarantee's
    'zero rows written' extends to the baseline."""
    org, entity = await _org_entity(db_session)
    rows = [
        {
            "txn_date": "2026-06-12",
            "txn_time": "07:45",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "ZA",
            "product": "DIESEL",
            "qty": "100.00",
            # ZAR is deliberately outside the test fixture's bundled ECB/
            # fallback currency set — guaranteed zero cached rate.
            "currency": "ZAR",
            "net_local": "100.00",
            "vat_local": "15.00",
            "gross_local": "115.00",
            "invoice_ref": "INV-000009",
        }
    ]
    content = synthetic_q8_statement(rows=rows, seed=1).encode("utf-8")
    with pytest.raises(ValidationError) as exc_info:
        await _ingest(db_session, org, entity, content)
    assert exc_info.value.code == "fx_rate_unavailable"
    assert await _baseline_rows(db_session, org.id) == []


@pytest.mark.asyncio
async def test_g3_3s2_reingest_of_identical_bytes_is_clean(db_session):
    """A legitimate retry: no drift warning, no second baseline, no
    second SET audit event."""
    org, entity = await _org_entity(db_session)
    content = _ew_content()
    await _ingest(db_session, org, entity, content)
    await db_session.commit()

    result = await _ingest(db_session, org, entity, content)
    await db_session.commit()

    assert not [w for w in result.warnings if w.startswith("extraction drift:")]
    assert len(await _baseline_rows(db_session, org.id)) == 1
    assert len(await _set_events(db_session, org.id)) == 1


@pytest.mark.asyncio
async def test_g3_3s2_drifted_reingest_warns_but_never_blocks(db_session):
    """The §4.19 proof AND the motivation in one test: with the baseline
    holding an older parse's figures, the re-ingest SUCCEEDS (advisory —
    'flags', never a gate), carries the drift warning, and the stored
    `fuel_transactions` figures are untouched (insert-or-no-op made the
    divergence otherwise invisible)."""
    org, entity = await _org_entity(db_session)
    content = _ew_content()
    await _ingest(db_session, org, entity, content)
    await db_session.commit()

    # Simulate "recorded by an older parser": the baseline's net differs
    # from what the current (deterministic) parser produces by 0.03.
    (baseline,) = await _baseline_rows(db_session, org.id)
    baseline.net_total = Decimal(baseline.net_total) - Decimal("0.03")
    await db_session.commit()

    before = {
        (t.line_seq): (Decimal(t.net_local), Decimal(t.vat_local))
        for t in (
            await db_session.scalars(
                select(FuelTransaction).where(FuelTransaction.org_id == org.id)
            )
        ).all()
    }

    result = await _ingest(db_session, org, entity, content)  # no raise
    await db_session.commit()

    drift = [w for w in result.warnings if w.startswith("extraction drift:")]
    assert len(drift) == 1
    assert "EUR net moved from 199.97 to 200.00" in drift[0]

    after_rows = (
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org.id))
    ).all()
    after = {(t.line_seq): (Decimal(t.net_local), Decimal(t.vat_local)) for t in after_rows}
    assert after == before  # nothing rewritten, nothing added

    # And the baseline itself was NOT silently self-adjusted (§4.19:
    # nothing self-adjusts; only an explicit rebaseline may).
    (baseline,) = await _baseline_rows(db_session, org.id)
    assert Decimal(baseline.net_total) == Decimal("199.97")


@pytest.mark.asyncio
async def test_g3_3s2_multi_currency_ingest_records_one_row_per_currency(db_session):
    org, entity = await _org_entity(db_session)
    await fx.load_rates(db_session, [(date(2026, 6, 12), "PLN", Decimal("4.30"))])
    rows = [
        {
            "txn_date": "2026-06-12",
            "txn_time": "07:45",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "EE",
            "product": "DIESEL",
            "qty": "100.00",
            "currency": "EUR",
            "net_local": "100.00",
            "vat_local": "20.00",
            "gross_local": "120.00",
            "invoice_ref": "INV-000010",
        },
        {
            "txn_date": "2026-06-12",
            "txn_time": "08:45",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "PL",
            "product": "DIESEL",
            "qty": "100.00",
            "currency": "PLN",
            "net_local": "430.00",
            "vat_local": "98.90",
            "gross_local": "528.90",
            "invoice_ref": "INV-000011",
        },
    ]
    content = synthetic_q8_statement(rows=rows, seed=1).encode("utf-8")
    await _ingest(db_session, org, entity, content)
    await db_session.commit()

    by_currency = {r.currency: r for r in await _baseline_rows(db_session, org.id)}
    assert set(by_currency) == {"EUR", "PLN"}
    assert Decimal(by_currency["EUR"].net_total) == Decimal("100.00")
    assert Decimal(by_currency["PLN"].net_total) == Decimal("430.00")
    assert by_currency["EUR"].line_count == 1
    assert by_currency["PLN"].line_count == 1
    # ONE audit event for the statement (its natural grain), not one per row.
    assert len(await _set_events(db_session, org.id)) == 1


@pytest.mark.asyncio
async def test_g3_3s2_drift_warnings_come_after_review_warnings(db_session):
    """Stable ordering: parser warnings, then capture-review warns, then
    drift warns — the review surface reads chronologically."""
    org, entity = await _org_entity(db_session)
    # Country "XX" is outside the 23-country set — a rule-3 WARN that
    # registers fine (warnings never block).
    content = _ew_content(rows=[_ew_row(country="XX")])
    await _ingest(db_session, org, entity, content)
    await db_session.commit()

    (baseline,) = await _baseline_rows(db_session, org.id)
    baseline.vat_total = Decimal(baseline.vat_total) + Decimal("0.05")
    await db_session.commit()

    result = await _ingest(db_session, org, entity, content)
    review_idx = [i for i, w in enumerate(result.warnings) if w.startswith("capture review:")]
    drift_idx = [i for i, w in enumerate(result.warnings) if w.startswith("extraction drift:")]
    assert review_idx and drift_idx
    assert max(review_idx) < min(drift_idx)
