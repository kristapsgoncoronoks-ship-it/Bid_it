"""G3.3 (WO-66) — the capture review gate's PURE rule engine
(`app.services.transport.capture_review`): the nine-rule verdict lattice,
the batch tie-out on Decimals, and the harvested commit-gate formula
`can_commit = (errors == 0) and (tie is None or tie.ok)` (R25 /
`BA_fleet_fuel.md` §2.1a). See `test_g3_3_capture_gate_ingest.py` for the
gate wired through the REAL registration path."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.transport import capture_review
from app.services.transport.capture_review import (
    ERROR,
    TIE_TOLERANCE,
    VAT_RATES,
    WARN,
    CapturedLine,
    check_line,
    review_statement,
    validate_batch,
)
from app.services.transport.fuel_card_parser import ParsedFuelLine, ParsedStatement


def _line(**overrides) -> CapturedLine:
    """A fully-clean line (BE, 21% VAT) — every test overrides ONE aspect."""
    base = dict(
        invoice_no="INV-000001",
        date="2026-06-01",
        country="BE",
        net="100.00",
        vat="21.00",
        line_seq=1,
    )
    base.update(overrides)
    return CapturedLine(**base)


def _codes(findings, severity=None):
    return [f.code for f in findings if severity is None or f.severity == severity]


# --------------------------------------------------------------------------- #
# The nine rules, in order
# --------------------------------------------------------------------------- #


def test_g3_3_clean_line_yields_no_findings():
    assert check_line(_line()) == []


def test_g3_3_rule1_missing_invoice_number_is_an_error():
    for empty in ("", "   "):
        findings = check_line(_line(invoice_no=empty))
        assert _codes(findings, ERROR) == ["invoice_number_missing"]


def test_g3_3_rule2_bad_date_shape_warns_and_empty_passes():
    findings = check_line(_line(date="01/06/2026"))
    assert _codes(findings, WARN) == ["date_format"]
    assert _codes(findings, ERROR) == []
    assert check_line(_line(date="")) == []  # empty passes — harvested


def test_g3_3_rule3_unknown_country_warns():
    findings = check_line(_line(country="ZA"))
    assert _codes(findings, WARN) == ["country_unknown"]
    assert _codes(findings, ERROR) == []


def test_g3_3_rule4_unparseable_amount_errors_and_early_returns():
    """The early return is observable: a line whose net is unparseable AND
    whose vat is negative reports ONLY the parse error — rules 5-9 need
    the parsed numbers and never run."""
    findings = check_line(_line(net="not-a-number", vat="-5.00"))
    assert _codes(findings) == ["amount_unparseable"]
    # NaN/Infinity are lexically valid Decimal text but not numbers money
    # may carry.
    for weird in ("NaN", "Infinity", "-Infinity"):
        assert _codes(check_line(_line(vat=weird))) == ["amount_unparseable"]


def test_g3_3_rule5_non_positive_net_is_an_error():
    for net in ("0", "0.00", "-10.00"):
        findings = check_line(_line(net=net, vat="0"))
        assert "net_not_positive" in _codes(findings, ERROR)


def test_g3_3_rule6_negative_vat_is_an_error():
    findings = check_line(_line(vat="-0.01"))
    assert "vat_negative" in _codes(findings, ERROR)


def test_g3_3_rule7_vat_exceeding_net_is_an_error():
    findings = check_line(_line(net="100.00", vat="123.00"))
    assert "vat_exceeds_net" in _codes(findings, ERROR)
    # vat == net (a 100% implied rate) is NOT over the line — rule 7 is
    # strictly "vat <= net".
    ok = check_line(_line(net="100.00", vat="100.00"))
    assert "vat_exceeds_net" not in _codes(ok)


def test_g3_3_rule8_implausibly_large_net_warns():
    findings = check_line(_line(net="5000000.01", vat="1050000.00"))
    assert "net_implausibly_large" in _codes(findings, WARN)
    ok = check_line(_line(net="5000000", vat="1050000.00"))
    assert "net_implausibly_large" not in _codes(ok)


def test_g3_3_rule9_incoherent_vat_rate_warns():
    # 21% in PL — not within 0.5pp of either known PL rate (23 or 8).
    findings = check_line(_line(country="PL", net="100.00", vat="21.00"))
    assert _codes(findings, WARN) == ["vat_rate_incoherent"]
    assert _codes(findings, ERROR) == []


def test_g3_3_rule9_harvested_dual_entries_all_cohere():
    """The four harvested business-case dual entries (§2.1a): PL diesel
    8%, ES gasoleo 10%, EE 22 & 20, FI 24 & 25.5 — each coheres."""
    cases = [
        ("PL", "8.00"),
        ("PL", "23.00"),
        ("ES", "10.00"),
        ("ES", "21.00"),
        ("EE", "20.00"),
        ("EE", "22.00"),
        ("FI", "24.00"),
        ("FI", "25.50"),
    ]
    for country, vat in cases:
        findings = check_line(_line(country=country, net="100.00", vat=vat))
        assert findings == [], f"{country} @ {vat}% must cohere"


def test_g3_3_rule9_half_pp_boundary():
    """±0.5pp inclusive: BE (21%) with an implied 21.5% passes; 21.51%
    warns."""
    assert check_line(_line(net="100.00", vat="21.50")) == []
    findings = check_line(_line(net="100.00", vat="21.51"))
    assert _codes(findings, WARN) == ["vat_rate_incoherent"]


def test_g3_3_rule9_zero_vat_is_deliberately_skipped():
    """Harvested verbatim: `vat == 0` is SKIPPED (domestic/exempt is
    validated elsewhere) — a zero-VAT line in a known country is clean."""
    assert check_line(_line(vat="0.00")) == []


def test_g3_3_rule9_unknown_country_is_skipped():
    """A country outside the 23-country table yields the rule-3 warn but
    NO rule-9 finding — coherence needs a known rate to cohere with."""
    findings = check_line(_line(country="ZA", net="100.00", vat="15.00"))
    assert _codes(findings) == ["country_unknown"]


def test_g3_3_vat_rates_table_shape():
    """23 countries (harvested count); rule 3's set IS the table's keys."""
    assert len(VAT_RATES) == 23
    assert capture_review.COUNTRIES == frozenset(VAT_RATES)


# --------------------------------------------------------------------------- #
# The batch verdict + the tie-out
# --------------------------------------------------------------------------- #


def test_g3_3_warnings_never_block():
    """A batch carrying ONLY warns commits — the lattice's whole point."""
    review = validate_batch(
        [
            _line(country="ZA"),  # rule 3 warn
            _line(date="junk", line_seq=2),  # rule 2 warn
            _line(country="PL", vat="21.00", line_seq=3),  # rule 9 warn
        ]
    )
    assert review.errors == 0
    assert review.warnings == 3
    assert review.can_commit is True


def test_g3_3_any_error_blocks():
    review = validate_batch([_line(), _line(vat="123.00", line_seq=2)])
    assert review.errors == 1
    assert review.can_commit is False


def test_g3_3_tie_none_means_no_tie_constraint():
    review = validate_batch([_line()], coversheet_total=None)
    assert review.tie is None
    assert review.can_commit is True


def test_g3_3_batch_tie_out_two_cents_allowed_three_blocked():
    """R25's own acceptance line: €0.02 off ⇒ allowed; €0.03 off ⇒
    blocked. Lines sum to 121.00 (net 100 + vat 21)."""
    ok = validate_batch([_line()], coversheet_total=Decimal("121.02"))
    assert ok.tie is not None and ok.tie.ok is True
    assert ok.tie.diff == Decimal("0.02")
    assert ok.can_commit is True

    blocked = validate_batch([_line()], coversheet_total=Decimal("121.03"))
    assert blocked.tie is not None and blocked.tie.ok is False
    assert blocked.tie.diff == Decimal("0.03")
    assert blocked.can_commit is False


def test_g3_3_tie_boundary_never_flips_on_float_noise():
    """§2.1a verbatim: the comparison is made on Decimals so a diff
    sitting exactly on the 2-cent boundary never flips. These addends are
    the classic binary-float hazard (0.1 + 0.2 + 0.3 != 0.6 in float):
    a float implementation would compute diff ≈ 0.020000000000000018 and
    wrongly BLOCK; the Decimal one computes exactly 0.02 and allows."""
    lines = [
        _line(net="0.10", vat="0.00", line_seq=1),
        _line(net="0.20", vat="0.00", line_seq=2),
        _line(net="0.30", vat="0.00", line_seq=3),
    ]
    assert 0.1 + 0.2 + 0.3 != 0.6  # noqa: PLR0133 - the binary-float hazard is real
    review = validate_batch(lines, coversheet_total=Decimal("0.58"))
    assert review.tie is not None
    assert review.tie.computed_total == Decimal("0.60")
    assert review.tie.diff == Decimal("0.02")
    assert review.tie.ok is True
    assert review.can_commit is True
    assert TIE_TOLERANCE == Decimal("0.02")


def test_g3_3_tie_failure_and_line_errors_are_both_reported():
    """The tie is still computed over the parseable remainder when a line
    fails rule 4 — the review surface shows BOTH problems at once."""
    review = validate_batch(
        [_line(), _line(net="garbage", line_seq=2)], coversheet_total=Decimal("500.00")
    )
    assert review.errors == 1
    assert review.tie is not None and review.tie.ok is False
    assert review.can_commit is False


# --------------------------------------------------------------------------- #
# The ParsedStatement adapter
# --------------------------------------------------------------------------- #


def _parsed_line(**overrides) -> ParsedFuelLine:
    base = dict(
        line_seq=1,
        country="BE",
        vehicle_ref="Fleet-A-001",
        txn_date=date(2026, 6, 1),
        station="Demo Fuel Hub",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=Decimal("100.00"),
        vat_local=Decimal("21.00"),
        gross_local=Decimal("121.00"),
        invoice_ref="INV-000001",
    )
    base.update(overrides)
    return ParsedFuelLine(**base)


def test_g3_3_review_statement_maps_typed_lines_losslessly():
    parsed = ParsedStatement(network="Eurowag", lines=[_parsed_line()])
    review = review_statement(parsed, coversheet_total=Decimal("121.00"))
    assert review.findings == ()
    assert review.tie is not None and review.tie.ok is True
    assert review.can_commit is True


def test_g3_3_review_statement_missing_invoice_ref_is_the_rule1_error():
    parsed = ParsedStatement(network="Eurowag", lines=[_parsed_line(invoice_ref=None)])
    review = review_statement(parsed)
    assert _codes(review.findings, ERROR) == ["invoice_number_missing"]
    assert review.can_commit is False


def test_g3_3_review_statement_vat_over_net_blocks():
    parsed = ParsedStatement(network="Eurowag", lines=[_parsed_line(vat_local=Decimal("123.00"))])
    review = review_statement(parsed)
    assert "vat_exceeds_net" in _codes(review.findings, ERROR)
    assert review.can_commit is False
