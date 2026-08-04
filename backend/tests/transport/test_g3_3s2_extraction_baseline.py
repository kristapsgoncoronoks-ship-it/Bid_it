"""G3.3 slice 2 (WO-70) — the anti-drift extraction baseline service
(`app.services.transport.extraction_baseline`): the harvested sentence
"`extraction_baseline` records a confirmed extraction as known-good;
`regression_check` flags a drift when a re-extraction moves net or vat by
more than 0.02" (`BA_fleet_fuel.md` §2.1a), proven strictly-greater-than,
per currency, advisory, audited, tenant-opaque.

SIMULATING A PARSER CHANGE: the parsers are deterministic, so the same
bytes always re-parse to the same figures — a test cannot "change the
parser" without monkeypatching the registry. Instead these tests TAMPER
THE STORED BASELINE ROW, which is observationally identical: the baseline
then holds what an OLDER parser recorded while the current parser produces
different figures. The compare path cannot tell the difference (it sees
two aggregates), so this exercises exactly the drift branch.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.audit import AuditEvent
from app.models.transport.extraction_baseline import FuelExtractionBaseline
from app.services import fx
from app.services.transport import extraction_baseline, statement_ingest
from tests.factories.transport import synthetic_eurowag_statement, synthetic_q8_statement
from tests.transport.conftest import enable_transport, make_entity, make_org

# --------------------------------------------------------------------------- #
# Fixture material — deterministic figures so the recorded totals are exact.
# --------------------------------------------------------------------------- #


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


def _q8_row(country: str, currency: str, net: str, vat: str, gross: str, ref: str):
    return {
        "txn_date": "2026-06-12",
        "txn_time": "07:45",
        "vehicle_ref": "V1",
        "station": "S1",
        "country": country,
        "product": "DIESEL",
        "qty": "100.00",
        "currency": currency,
        "net_local": net,
        "vat_local": vat,
        "gross_local": gross,
        "invoice_ref": ref,
    }


def _q8_content() -> bytes:
    """Two currencies in one statement (the WO-64 Q8 property): EUR + PLN."""
    rows = [
        _q8_row("EE", "EUR", "100.00", "20.00", "120.00", "INV-000010"),
        _q8_row("PL", "PLN", "430.00", "98.90", "528.90", "INV-000011"),
    ]
    return synthetic_q8_statement(rows=rows, seed=1).encode("utf-8")


async def _ingest(db_session, org, entity, content: bytes, filename: str = "s.csv"):
    return await statement_ingest.ingest_statement(
        db_session,
        org.id,
        entity_id=entity.id,
        period="2026-06",
        filename=filename,
        content=content,
    )


async def _registered_org(db_session, content: bytes | None = None):
    """An org with the module on, one entity, and (optionally) one
    registered statement — the 'confirmed extraction' precondition."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    if content is not None:
        await _ingest(db_session, org, entity, content)
        await db_session.commit()
    return org, entity


async def _baseline_rows(db_session, org_id: str):
    return (
        await db_session.scalars(
            select(FuelExtractionBaseline).where(FuelExtractionBaseline.org_id == org_id)
        )
    ).all()


async def _audit_events(db_session, org_id, action):
    return (
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.org_id == org_id, AuditEvent.action == action)
        )
    ).all()


def _meta(event):
    return json.loads(event.meta) if isinstance(event.meta, str) else event.meta


# --------------------------------------------------------------------------- #
# regression_check — the explicit re-extraction caller
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_3s2_unbaselined_statement_is_not_checked(db_session):
    """Fail-OPEN by absence: a digest that never registered is simply not
    checked — `baselined=False`, zero findings, no error."""
    org, _ = await _registered_org(db_session)  # module on, nothing ingested
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=_ew_content()
    )
    assert result.baselined is False
    assert result.findings == ()
    assert result.drifted is False


@pytest.mark.asyncio
async def test_g3_3s2_identical_reextraction_is_clean(db_session):
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    assert result.baselined is True
    assert result.findings == ()
    assert result.network == "Eurowag"


@pytest.mark.asyncio
async def test_g3_3s2_net_drift_flags_strictly_beyond_two_cents(db_session):
    """The harvested threshold verbatim: a move of exactly 0.02 is NOT
    drift ("more than 0.02"); 0.03 is."""
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)
    (row,) = await _baseline_rows(db_session, org.id)
    parsed_net = Decimal(row.net_total)

    # Exactly 0.02 away — inside "more than", no flag.
    row.net_total = parsed_net - Decimal("0.02")
    await db_session.commit()
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    assert result.baselined is True and result.findings == ()

    # 0.03 away — flags, naming the metric and both figures.
    row.net_total = parsed_net - Decimal("0.03")
    await db_session.commit()
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    assert result.drifted is True
    (finding,) = result.findings
    assert finding.metric == "net"
    assert finding.currency == "EUR"
    assert finding.baseline == parsed_net - Decimal("0.03")
    assert finding.actual == parsed_net
    assert "more than 0.02" in finding.message


@pytest.mark.asyncio
async def test_g3_3s2_vat_drift_flags(db_session):
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)
    (row,) = await _baseline_rows(db_session, org.id)
    row.vat_total = Decimal(row.vat_total) + Decimal("0.05")
    await db_session.commit()

    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    (finding,) = result.findings
    assert finding.metric == "vat"


@pytest.mark.asyncio
async def test_g3_3s2_line_count_change_flags_at_any_difference(db_session):
    """`lines` compares EXACTLY (the documented interpretation): a
    split/merged line can drift compensatingly with equal totals."""
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)
    (row,) = await _baseline_rows(db_session, org.id)
    row.line_count = row.line_count + 1
    await db_session.commit()

    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    (finding,) = result.findings
    assert finding.metric == "lines"
    assert finding.baseline == 3 and finding.actual == 2


@pytest.mark.asyncio
async def test_g3_3s2_multi_currency_drift_is_isolated_per_currency(db_session):
    """A PLN-row tamper never flags the EUR row (§4.14: per-currency
    aggregates, never a cross-currency sum)."""
    content = _q8_content()
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await fx.load_rates(db_session, [(date(2026, 6, 12), "PLN", Decimal("4.30"))])
    await _ingest(db_session, org, entity, content)
    await db_session.commit()

    rows = await _baseline_rows(db_session, org.id)
    assert {r.currency for r in rows} == {"EUR", "PLN"}
    pln = next(r for r in rows if r.currency == "PLN")
    pln.net_total = Decimal(pln.net_total) + Decimal("1.00")
    await db_session.commit()

    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    (finding,) = result.findings
    assert finding.currency == "PLN"
    assert finding.metric == "net"


@pytest.mark.asyncio
async def test_g3_3s2_currency_set_change_flags_in_both_directions(db_session):
    """The per-currency row SET is part of what was known-good: a currency
    on one side only flags as metric 'currency'."""
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)

    # Baseline carries a currency the re-extraction no longer produces.
    (row,) = await _baseline_rows(db_session, org.id)
    db_session.add(
        FuelExtractionBaseline(
            org_id=org.id,
            statement_sha256=row.statement_sha256,
            currency="SEK",
            network=row.network,
            filename=row.filename,
            line_count=1,
            net_total=Decimal("10.00"),
            vat_total=Decimal("2.50"),
        )
    )
    await db_session.commit()

    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    (finding,) = result.findings
    assert finding.metric == "currency"
    assert finding.currency == "SEK"
    assert "absent from the re-extraction" in finding.message

    # And the mirror: drop the EUR baseline row so the re-extraction's
    # currency is missing from the baseline.
    await db_session.delete(row)
    await db_session.commit()
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    by_metric = {(f.metric, f.currency) for f in result.findings}
    assert ("currency", "EUR") in by_metric


@pytest.mark.asyncio
async def test_g3_3s2_unrecognized_bytes_refused(db_session):
    org, _ = await _registered_org(db_session)
    with pytest.raises(ValidationError) as exc_info:
        await extraction_baseline.regression_check(
            db_session, org.id, filename="x.csv", content=b"not a statement"
        )
    assert exc_info.value.code == "unrecognized_fuel_card_statement"


@pytest.mark.asyncio
async def test_g3_3s2_module_off_refuses_every_entrypoint(db_session):
    org = await make_org(db_session)  # transport NOT enabled
    for call in (
        extraction_baseline.regression_check(
            db_session, org.id, filename="s.csv", content=_ew_content()
        ),
        extraction_baseline.rebaseline(db_session, org.id, filename="s.csv", content=_ew_content()),
        extraction_baseline.remove_baseline(db_session, org.id, statement_sha256="0" * 64),
    ):
        with pytest.raises(PermissionError) as exc_info:
            await call
        assert exc_info.value.code == "module_not_enabled"


# --------------------------------------------------------------------------- #
# rebaseline / remove_baseline — the audited human acts
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_3s2_rebaseline_replaces_figures_and_audits_old_new(db_session):
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)
    (row,) = await _baseline_rows(db_session, org.id)
    parsed_net = Decimal(row.net_total)
    row.net_total = parsed_net - Decimal("1.00")  # the stale known-good
    await db_session.commit()

    rows = await extraction_baseline.rebaseline(
        db_session, org.id, filename="s.csv", content=content
    )
    await db_session.commit()
    assert [Decimal(r.net_total) for r in rows] == [parsed_net]

    events = await _audit_events(db_session, org.id, "transport.extraction_baseline_set")
    assert len(events) == 2  # confirm-time record + the rebaseline
    meta = _meta(events[-1])
    assert meta["old"]["EUR"]["net"] == str(parsed_net - Decimal("1.00"))
    assert meta["new"]["EUR"]["net"] == str(parsed_net)

    # And the check is clean again.
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    assert result.findings == ()


@pytest.mark.asyncio
async def test_g3_3s2_rebaseline_unknown_digest_refused(db_session):
    """Registration is the only act that CREATES a baseline — rebaseline
    of a never-registered digest is refused, not silently recorded."""
    org, _ = await _registered_org(db_session)  # nothing ingested
    with pytest.raises(NotFoundError) as exc_info:
        await extraction_baseline.rebaseline(
            db_session, org.id, filename="s.csv", content=_ew_content()
        )
    assert exc_info.value.code == "baseline_not_found"


@pytest.mark.asyncio
async def test_g3_3s2_remove_baseline_audits_and_returns_digest_to_unchecked(db_session):
    content = _ew_content()
    org, _ = await _registered_org(db_session, content)
    (row,) = await _baseline_rows(db_session, org.id)
    sha = row.statement_sha256

    await extraction_baseline.remove_baseline(db_session, org.id, statement_sha256=sha)
    await db_session.commit()
    assert await _baseline_rows(db_session, org.id) == []

    events = await _audit_events(db_session, org.id, "transport.extraction_baseline_remove")
    assert len(events) == 1
    meta = _meta(events[0])
    assert meta["old"]["EUR"]["lines"] == 2
    assert meta["new"] is None

    # Fail-open by absence again — and a second remove is an opaque 404.
    result = await extraction_baseline.regression_check(
        db_session, org.id, filename="s.csv", content=content
    )
    assert result.baselined is False
    with pytest.raises(NotFoundError):
        await extraction_baseline.remove_baseline(db_session, org.id, statement_sha256=sha)


# --------------------------------------------------------------------------- #
# Cross-tenant opacity (§4.1/§4.4)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_g3_3s2_cross_tenant_baseline_is_invisible_and_independent(db_session):
    """Org A's baseline is invisible to org B (absence, not error); B
    ingesting the SAME bytes gets its OWN independent row set; A's tamper
    never flags B."""
    content = _ew_content()
    org_a, _ = await _registered_org(db_session, content)

    org_b = await make_org(db_session, name="Other Org")
    await enable_transport(db_session, org_b.id)
    entity_b = await make_entity(db_session, org_b.id, seed=2)

    # Invisible: B sees A's digest as never-baselined…
    result = await extraction_baseline.regression_check(
        db_session, org_b.id, filename="s.csv", content=content
    )
    assert result.baselined is False
    # …and B's rebaseline/remove against it are opaque 404s.
    with pytest.raises(NotFoundError):
        await extraction_baseline.rebaseline(
            db_session, org_b.id, filename="s.csv", content=content
        )
    (row_a,) = await _baseline_rows(db_session, org_a.id)
    with pytest.raises(NotFoundError):
        await extraction_baseline.remove_baseline(
            db_session, org_b.id, statement_sha256=row_a.statement_sha256
        )

    # Independent: B registers the same bytes and gets its own baseline.
    await _ingest(db_session, org_b, entity_b, content)
    await db_session.commit()
    (row_b,) = await _baseline_rows(db_session, org_b.id)
    assert row_b.statement_sha256 == row_a.statement_sha256
    assert row_b.id != row_a.id

    # A's tamper never flags B.
    row_a.net_total = Decimal(row_a.net_total) + Decimal("9.99")
    await db_session.commit()
    result = await extraction_baseline.regression_check(
        db_session, org_b.id, filename="s.csv", content=content
    )
    assert result.baselined is True and result.findings == ()
