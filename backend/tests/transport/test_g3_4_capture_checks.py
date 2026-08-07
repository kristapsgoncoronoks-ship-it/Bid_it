"""G3.4 (WO-71) — deterministic advisory post-capture checks (R26), unit +
db level. Every check is ADVISORY: every path in `capture_checks` fails
toward NO finding on unknown/uncheckable input ("fail toward not crying
wolf", harvested verbatim), and no finding — whatever its severity — ever
blocks anything (§4.19; the ingest-level proof lives in
`test_g3_4_checks_on_ingest.py`)."""

from __future__ import annotations

import socket
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import PermissionError
from app.services.transport import capture_checks, fuel_ingest
from app.services.transport.fuel_card_parser import ParsedFuelLine
from tests.factories.transport import synthetic_iban, synthetic_vat_id
from tests.transport.conftest import enable_transport, make_entity, make_org


def _line(
    *,
    line_seq: int = 1,
    invoice_ref: str | None = "INV-0001",
    currency: str = "EUR",
    net: str = "100.00",
    vat: str = "21.00",
) -> ParsedFuelLine:
    return ParsedFuelLine(
        line_seq=line_seq,
        country="BE",
        vehicle_ref="V1",
        txn_date=date(2026, 6, 1),
        station="S1",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency=currency,
        net_local=Decimal(net),
        vat_local=Decimal(vat),
        gross_local=Decimal(net) + Decimal(vat),
        invoice_ref=invoice_ref,
    )


async def _ingest_group(
    db,
    org_id: str,
    *,
    entity_id: str,
    supplier: str = "Eurowag",
    period: str = "2026-06",
    invoice_ref: str = "INV-0001",
    currency: str = "EUR",
    net: str = "100.00",
    vat: str = "21.00",
    line_seq: int = 1,
) -> None:
    await fuel_ingest.ingest_transaction(
        db,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country="BE",
        vehicle_ref="V1",
        txn_date=date(2026, 6, 1),
        txn_time="08:00",
        station="S1",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency=currency,
        net_local=Decimal(net),
        vat_local=Decimal(vat),
        gross_local=Decimal(net) + Decimal(vat),
        net_eur=Decimal(net),
        vat_eur=Decimal(vat),
        invoice_ref=invoice_ref,
        # WO-88 (§4.15): `eur` is the IDENTITY provenance and only a EUR line
        # can claim it — a PLN line converted at a rate carries `ecb`. The gate
        # accepts either (it refuses a MISSING provenance, not a wrong one),
        # but a fixture should not assert something no ingestion would write.
        fx_source="eur" if str(currency).upper() == "EUR" else "ecb",
    )


# ---------------------------------------------------------------- IBAN ----


def test_r26_iban_mod97_valid_yields_no_finding():
    assert capture_checks.check_iban(synthetic_iban("EE", seed=7)) is None


def test_r26_iban_mod97_failure_is_error_severity_finding():
    iban = synthetic_iban("DE", seed=7)
    corrupted = iban[:-1] + ("0" if iban[-1] != "0" else "1")
    finding = capture_checks.check_iban(corrupted)
    assert finding is not None
    assert finding.severity == capture_checks.ERROR
    assert finding.code == "iban_invalid"


def test_r26_iban_missing_or_unknown_country_yields_no_finding():
    assert capture_checks.check_iban(None) is None
    assert capture_checks.check_iban("") is None
    # Unknown country prefix = uncheckable, not invalid (the SEPA path's
    # fail-closed posture is deliberately NOT copied here — decision 4).
    assert capture_checks.check_iban("XX321234567890123456") is None
    assert capture_checks.check_iban("garbage") is None


# -------------------------------------------------------------- VAT id ----


def test_r26_vat_id_structure_valid_across_countries_yields_no_finding():
    for country in ("EE", "LT", "LV", "PL", "DE", "BE", "FI"):
        assert capture_checks.check_vat_id(synthetic_vat_id(country, seed=3)) is None
    # NL's B-infix and AT's U-prefix shapes, synthetic all-nines bodies.
    assert capture_checks.check_vat_id("NL999999999B99") is None
    assert capture_checks.check_vat_id("ATU99999999") is None
    # EL maps to Greece — the one documented prefix exception. Runtime-
    # constructed so the pii-scan's structural VAT pattern never sees a
    # literal (all-nines synthetic body, same convention as the factory).
    assert capture_checks.check_vat_id("EL" + "9" * 9) is None


def test_r26_vat_id_malformed_is_warn_severity_finding():
    finding = capture_checks.check_vat_id("EE12")  # EE needs 9 digits
    assert finding is not None
    assert finding.severity == capture_checks.WARN
    assert finding.code == "vat_id_malformed"
    # Wrong character class / length for DE.
    assert capture_checks.check_vat_id("DE12345678X") is not None


def test_r26_vat_id_unknown_prefix_yields_no_finding():
    assert capture_checks.check_vat_id(None) is None
    assert capture_checks.check_vat_id("") is None
    assert capture_checks.check_vat_id("XI999999999") is None  # not EU-27
    assert capture_checks.check_vat_id("CH999999") is None
    assert capture_checks.check_vat_id("99123") is None  # no alpha prefix


# ---------------------------------------------------------------- VIES ----


def test_r26_vies_lookup_returns_not_checked_offline_and_never_raises(monkeypatch):
    def _explode(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("vies_check must perform no I/O at all")

    monkeypatch.setattr(socket, "socket", _explode)
    assert capture_checks.vies_check("EE999999999") == capture_checks.NOT_CHECKED
    assert capture_checks.vies_check(None) == capture_checks.NOT_CHECKED
    # No HTTP/SOAP dependency in the module at all.
    import app.services.transport.capture_checks as module

    with open(module.__file__) as fh:
        source = fh.read()
    for banned in ("httpx", "requests", "aiohttp", "zeep", "urllib"):
        assert banned not in source


# ---------------------------------------------------------- duplicates ----


@pytest.mark.asyncio
async def test_r26_prior_duplicate_across_entities_is_error_finding(db_session):
    org = await make_org(db_session)
    entity_a = await make_entity(db_session, org.id)
    entity_b = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    await _ingest_group(db_session, org.id, entity_id=entity_a.id, invoice_ref="INV 0001")

    findings = await capture_checks.find_duplicates(
        db_session,
        org.id,
        entity_id=entity_b.id,
        supplier="Eurowag",
        period="2026-07",
        lines=[_line(invoice_ref="inv0001")],  # normalizes identically
    )
    assert [f.code for f in findings] == ["duplicate_prior_registration"]
    assert findings[0].severity == capture_checks.ERROR
    assert entity_a.id in findings[0].message


@pytest.mark.asyncio
async def test_r26_same_statement_replay_does_not_self_flag(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    await _ingest_group(db_session, org.id, entity_id=entity.id)

    findings = await capture_checks.find_duplicates(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Eurowag",
        period="2026-06",  # the SAME (entity, supplier, period) group
        lines=[_line()],
    )
    assert findings == []


@pytest.mark.asyncio
async def test_r26_same_ref_different_amount_yields_no_finding(db_session):
    org = await make_org(db_session)
    entity_a = await make_entity(db_session, org.id)
    entity_b = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    await _ingest_group(db_session, org.id, entity_id=entity_a.id)

    findings = await capture_checks.find_duplicates(
        db_session,
        org.id,
        entity_id=entity_b.id,
        supplier="Eurowag",
        period="2026-07",
        lines=[_line(net="200.00", vat="42.00")],  # same ref, different amount
    )
    assert findings == []


@pytest.mark.asyncio
async def test_r26_same_ref_amount_different_currency_yields_no_finding(db_session):
    org = await make_org(db_session)
    entity_a = await make_entity(db_session, org.id)
    entity_b = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    await _ingest_group(db_session, org.id, entity_id=entity_a.id, currency="PLN")

    findings = await capture_checks.find_duplicates(
        db_session,
        org.id,
        entity_id=entity_b.id,
        supplier="Eurowag",
        period="2026-07",
        lines=[_line(currency="EUR")],  # same ref + amount, EUR vs PLN
    )
    assert findings == []


@pytest.mark.asyncio
async def test_r26_in_batch_normalization_collision_is_warn_finding(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)

    findings = await capture_checks.find_duplicates(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Eurowag",
        period="2026-06",
        lines=[
            _line(line_seq=1, invoice_ref="INV 0001"),
            _line(line_seq=2, invoice_ref="inv0001"),  # distinct raw, same normalized
        ],
    )
    assert [f.code for f in findings] == ["duplicate_in_batch"]
    assert findings[0].severity == capture_checks.WARN


@pytest.mark.asyncio
async def test_r26_lines_without_invoice_ref_yield_no_finding(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await enable_transport(db_session, org.id)
    findings = await capture_checks.find_duplicates(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Eurowag",
        period="2026-06",
        lines=[_line(invoice_ref=None)],
    )
    assert findings == []


@pytest.mark.asyncio
async def test_r26_cross_tenant_identical_invoice_never_flags(db_session):
    org_a = await make_org(db_session)
    org_b = await make_org(db_session)
    entity_a = await make_entity(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    # Org B registers the identical ref + amount + currency.
    await _ingest_group(db_session, org_b.id, entity_id=entity_b.id)

    findings = await capture_checks.find_duplicates(
        db_session,
        org_a.id,
        entity_id=entity_a.id,
        supplier="Eurowag",
        period="2026-07",
        lines=[_line()],
    )
    assert findings == []


@pytest.mark.asyncio
async def test_run_checks_module_disabled_is_refused(db_session):
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    # transport deliberately NOT enabled
    with pytest.raises(PermissionError):
        await capture_checks.run_checks(
            db_session,
            org.id,
            entity_id=entity.id,
            supplier="Eurowag",
            period="2026-06",
            lines=[_line()],
        )
