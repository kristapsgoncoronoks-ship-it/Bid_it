"""G3.2 slice 6 — the Moeve (ex-Cepsa) fuel-card parser: the per-line-IVA
VAT-inclusive money model (each line reverse-calculated at ITS OWN printed
IVA rate at the harvested 6-dp internal precision, `BA_fleet_fuel.md`
§5.1), the fail-closed rate/payment refusals, the cash-at-pump
provenance-only flag, and the deliberate absence of seller-entity
detection. Pure-function tests (no DB) — see
`test_g3_2_moeve_statement_ingest.py` for the DB-touching orchestration
tests. Mirrors `test_g3_2_tfc_fuel_card_parser.py`'s structure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.dkv import DKVParser
from app.services.transport.parsers.e100 import E100Parser
from app.services.transport.parsers.eurowag import EurowagParser
from app.services.transport.parsers.moeve import MoeveParser
from app.services.transport.parsers.q8 import Q8Parser
from app.services.transport.parsers.tfc import TFCParser
from tests.factories.transport import (
    synthetic_dkv_statement,
    synthetic_e100_statement,
    synthetic_eurowag_statement,
    synthetic_moeve_statement,
    synthetic_q8_statement,
    synthetic_tfc_statement,
)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _row(**overrides: object) -> dict[str, object]:
    """A well-formed single Moeve EcoBlue 21% transfer row, overridable
    per test."""
    base: dict[str, object] = {
        "txn_date": "2026-06-10",
        "txn_time": "11:05",
        "vehicle_ref": "Fleet-M-001",
        "station": "Zaragoza Ring Station",
        "country": "ES",
        "product": "ECOBLUE",
        "qty": "100.000",
        "currency": "EUR",
        "gross_local": "100.00",
        "iva_rate": "21",
        "payment": "transfer",
        "invoice_ref": "MOEVE-2026-000321",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# The registry — all six networks co-exist
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_six_networks_in_order():
    # Membership + relative order, not exact-list equality — the WO-62/WO-63
    # registry tests' own future-proof style (an exact list would re-encode
    # "no further network exists" and break on every subsequent G3.2 slice;
    # it did exactly that when WO-65 registered DKV fourth, when WO-67
    # registered TFC fifth, when WO-68 registered Moeve sixth, and again
    # when WO-69 registered BP seventh — the LAST §5.1 network).
    # The CURRENT exact seven-parser list is asserted by
    # `test_g3_2_bp_fuel_card_parser.py::
    # test_g3_2_registry_ships_seven_networks_in_order`.
    names = [p.network for p in fuel_card_parser.parsers()]
    assert (
        names.index("Eurowag")
        < names.index("E100")
        < names.index("Q8")
        < names.index("DKV")
        < names.index("TFC")
        < names.index("Moeve")
    )


def test_g3_2_select_dispatches_all_six_networks_without_crossing():
    moeve_content = synthetic_moeve_statement(seed=1)
    tfc_content = synthetic_tfc_statement(seed=1)
    dkv_content = synthetic_dkv_statement(seed=1)
    q8_content = synthetic_q8_statement(seed=1)
    e100_content = synthetic_e100_statement(seed=1)
    eurowag_content = synthetic_eurowag_statement(seed=1)

    assert fuel_card_parser.select("moeve.csv", _bytes(moeve_content)).network == "Moeve"
    assert fuel_card_parser.select("tfc.csv", _bytes(tfc_content)).network == "TFC"
    assert fuel_card_parser.select("dkv.csv", _bytes(dkv_content)).network == "DKV"
    assert fuel_card_parser.select("q8.csv", _bytes(q8_content)).network == "Q8"
    assert fuel_card_parser.select("e100.csv", _bytes(e100_content)).network == "E100"
    assert fuel_card_parser.select("eurowag.csv", _bytes(eurowag_content)).network == "Eurowag"


def test_g3_2_moeve_handles_never_matches_other_networks_files_and_vice_versa():
    moeve_content = synthetic_moeve_statement(seed=2)
    tfc_content = synthetic_tfc_statement(seed=2)
    dkv_content = synthetic_dkv_statement(seed=2)
    q8_content = synthetic_q8_statement(seed=2)
    e100_content = synthetic_e100_statement(seed=2)
    eurowag_content = synthetic_eurowag_statement(seed=2)

    assert not MoeveParser().handles("x.csv", _bytes(tfc_content))
    assert not MoeveParser().handles("x.csv", _bytes(dkv_content))
    assert not MoeveParser().handles("x.csv", _bytes(q8_content))
    assert not MoeveParser().handles("x.csv", _bytes(e100_content))
    assert not MoeveParser().handles("x.csv", _bytes(eurowag_content))
    assert not TFCParser().handles("x.csv", _bytes(moeve_content))
    assert not DKVParser().handles("x.csv", _bytes(moeve_content))
    assert not Q8Parser().handles("x.csv", _bytes(moeve_content))
    assert not E100Parser().handles("x.csv", _bytes(moeve_content))
    assert not EurowagParser().handles("x.csv", _bytes(moeve_content))


def test_g3_2_run_dispatches_moeve_and_returns_a_parsed_statement():
    content = synthetic_moeve_statement(seed=3)
    result = fuel_card_parser.run("moeve.csv", _bytes(content))
    assert result.network == "Moeve"
    assert len(result.lines) == 3
    assert result.entities == []


# --------------------------------------------------------------------------- #
# MoeveParser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_moeve_handles_a_well_formed_statement():
    content = synthetic_moeve_statement(seed=4)
    assert MoeveParser().handles("moeve.csv", _bytes(content))


def test_g3_2_moeve_does_not_handle_a_file_missing_the_marker():
    content = synthetic_moeve_statement(seed=4).replace("MOEVE STATEMENT", "SOMETHING ELSE")
    assert not MoeveParser().handles("moeve.csv", _bytes(content))


def test_g3_2_moeve_does_not_handle_undecodable_bytes():
    assert not MoeveParser().handles("moeve.csv", b"\xff\xfe\x00\x01")


def test_g3_2_moeve_does_not_match_on_a_partial_incidental_mention():
    """The marker must be the exact first line — text merely containing the
    substring "MOEVE" elsewhere in the file must not be picked up."""
    body = "some other statement\nMOEVE mentioned in passing\ntxn_date,foo\n"
    assert not MoeveParser().handles("moeve.csv", _bytes(body))


# --------------------------------------------------------------------------- #
# MoeveParser.parse() — the per-line-IVA VAT-inclusive 6-dp money model
# --------------------------------------------------------------------------- #


def test_g3_2_moeve_parse_reverse_calculates_at_the_printed_iva_rate_with_6dp():
    """The load-bearing correctness property: a VAT-inclusive 100.00 gross
    at the printed 21% IVA reverse-calculates to net 82.644628 (EXACTLY
    the harvested 6-dp internal precision — never more digits), with
    vat = gross − net EXACT so `net + vat == gross` holds identically and
    the implied rate stays within rule 9's ±0.5pp window."""
    content = synthetic_moeve_statement(rows=[_row()], seed=5)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]

    assert line.net_local == Decimal("82.644628")
    assert line.vat_local == Decimal("17.355372")
    assert line.gross_local == Decimal("100.00")
    assert line.net_local + line.vat_local == line.gross_local
    # Exactly 6 dp — the harvested internal precision, not an unrounded tail.
    assert line.net_local.as_tuple().exponent == -6
    # Rule-9 coherence by construction: implied rate within 0.5pp of 21.
    implied_pct = line.vat_local / line.net_local * Decimal(100)
    assert abs(implied_pct - Decimal(21)) <= Decimal("0.5")
    assert line.country == "ES"
    assert line.currency == "EUR"
    assert line.vehicle_ref == "Fleet-M-001"
    assert line.station == "Zaragoza Ring Station"
    assert line.product == "ECOBLUE"
    assert str(line.qty) == "100.000"
    assert line.invoice_ref == "MOEVE-2026-000321"
    assert line.txn_time == "11:05"
    assert line.provenance_note is not None
    assert "iva_rate=21" in line.provenance_note
    assert "6-dp" in line.provenance_note


def test_g3_2_moeve_parse_applies_the_10_pct_gasoleo_rate():
    """The harvested gasoleo rate: 660.00 gross at 10% → net 600, vat 60 —
    exact, at the 6-dp internal representation."""
    rows = [_row(product="GASOLEO", gross_local="660.00", iva_rate="10")]
    content = synthetic_moeve_statement(rows=rows, seed=6)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    (line,) = parsed.lines
    assert line.net_local == Decimal("600")
    assert line.vat_local == Decimal("60")
    assert line.net_local.as_tuple().exponent == -6
    assert "iva_rate=10" in (line.provenance_note or "")


def test_g3_2_moeve_parse_mixes_10_and_21_in_one_statement():
    """The property no prior VAT-inclusive fixture exercised: the IVA rate
    genuinely varies LINE-BY-LINE within one statement — each line is
    split at its own printed rate, never a statement-wide constant."""
    rows = [
        _row(product="GASOLEO", gross_local="660.00", iva_rate="10"),
        _row(txn_date="2026-06-12", gross_local="121.00", iva_rate="21"),
    ]
    content = synthetic_moeve_statement(rows=rows, seed=7)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    gasoleo, ecoblue = parsed.lines
    assert gasoleo.net_local == Decimal("600")
    assert gasoleo.vat_local == Decimal("60")
    assert ecoblue.net_local == Decimal("100")
    assert ecoblue.vat_local == Decimal("21")


def test_g3_2_moeve_parse_net_local_is_quantized_to_6dp_and_identity_holds():
    """A non-terminating division (100.00 / 1.1 = 90.9090…) must land on
    EXACTLY 6 dp ROUND_HALF_UP (90.909091 — the seventh digit rounds up)
    with the identity preserved by the exact remainder."""
    rows = [_row(product="GASOLEO", gross_local="100.00", iva_rate="10")]
    content = synthetic_moeve_statement(rows=rows, seed=8)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    (line,) = parsed.lines
    assert line.net_local == Decimal("90.909091")
    assert line.net_local.as_tuple().exponent == -6
    assert line.vat_local == Decimal("9.090909")
    assert line.net_local + line.vat_local == Decimal("100.00")


def test_g3_2_moeve_parse_default_fixture_mixes_rates_and_channels():
    content = synthetic_moeve_statement(seed=9)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    assert len(parsed.lines) == 3
    assert {line.country for line in parsed.lines} == {"ES"}
    assert all(line.currency == "EUR" for line in parsed.lines)
    for line in parsed.lines:
        assert line.net_local + line.vat_local == line.gross_local


def test_g3_2_moeve_parse_iva_rate_out_of_range_aborts_the_statement():
    """A rate outside [0, 100] is never silently clamped (E100's own
    refusal, unchanged) — the whole statement is refused."""
    content = synthetic_moeve_statement(rows=[_row(iva_rate="101")], seed=10)
    with pytest.raises(ValueError, match="out of range"):
        MoeveParser().parse("moeve.csv", _bytes(content))


def test_g3_2_moeve_parse_unknown_payment_channel_aborts_the_statement():
    """A settlement channel is never guessed — an unknown `payment` value
    refuses the whole statement (fail-closed, see the parser's module
    docstring; the same discipline as TFC's station_class)."""
    content = synthetic_moeve_statement(rows=[_row(payment="voucher")], seed=11)
    with pytest.raises(ValueError, match="unknown payment channel"):
        MoeveParser().parse("moeve.csv", _bytes(content))


def test_g3_2_moeve_parse_cash_at_pump_flags_provenance_never_figures():
    """The documented cash-at-pump decision: a flagged row's derived
    figures are byte-identical to its transfer twin's (the flag NEVER
    mutates a number); the flag rides `provenance_note`; and the parse
    surfaces ONE advisory summary warning (count + gross sum) — the
    settlement netting is information, not arithmetic."""
    transfer_rows = [_row()]
    cash_rows = [_row(payment="cash_at_pump")]
    parsed_transfer = MoeveParser().parse(
        "moeve.csv", _bytes(synthetic_moeve_statement(rows=transfer_rows, seed=12))
    )
    parsed_cash = MoeveParser().parse(
        "moeve.csv", _bytes(synthetic_moeve_statement(rows=cash_rows, seed=12))
    )
    (t_line,) = parsed_transfer.lines
    (c_line,) = parsed_cash.lines

    assert c_line.net_local == t_line.net_local
    assert c_line.vat_local == t_line.vat_local
    assert c_line.gross_local == t_line.gross_local
    assert "cash at pump" in (c_line.provenance_note or "")
    assert "nets against the bank transfer" in (c_line.provenance_note or "")
    assert "cash at pump" not in (t_line.provenance_note or "")

    cash_warnings = [w for w in parsed_cash.warnings if "cash-at-pump" in w]
    assert len(cash_warnings) == 1
    assert "1 cash-at-pump line(s)" in cash_warnings[0]
    assert "100.00" in cash_warnings[0]
    assert "settlement-side only" in cash_warnings[0]
    assert not any("cash-at-pump" in w for w in parsed_transfer.warnings)


def test_g3_2_moeve_parse_payment_channel_is_case_insensitive():
    content = synthetic_moeve_statement(rows=[_row(payment="Cash_At_Pump")], seed=13)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    assert "cash at pump" in (parsed.lines[0].provenance_note or "")


def test_g3_2_moeve_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    content = synthetic_moeve_statement(seed=14)
    first = MoeveParser().parse("moeve.csv", _bytes(content))
    second = MoeveParser().parse("moeve.csv", _bytes(content))
    assert [line.line_seq for line in first.lines] == [1, 2, 3]
    assert [line.line_seq for line in second.lines] == [1, 2, 3]


def test_g3_2_moeve_parse_raises_on_a_missing_required_column():
    content = synthetic_moeve_statement(seed=15).replace("iva_rate,", "")
    with pytest.raises(ValueError, match="missing required column"):
        MoeveParser().parse("moeve.csv", _bytes(content))


def test_g3_2_moeve_parse_raises_on_an_unparseable_gross_decimal():
    content = synthetic_moeve_statement(rows=[_row(gross_local="not-a-number")], seed=16)
    with pytest.raises(ValueError, match="invalid decimal"):
        MoeveParser().parse("moeve.csv", _bytes(content))


def test_g3_2_moeve_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_moeve_statement(seed=17)
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        MoeveParser().parse("moeve.csv", _bytes(stripped))


def test_g3_2_moeve_malformed_row_aborts_the_whole_statement():
    """A malformed row must abort the WHOLE statement — never a silently
    dropped row (mirrors every prior network parser's discipline)."""
    rows = [
        _row(),
        _row(txn_date="2026-06-11", vehicle_ref="Fleet-M-002", iva_rate="bad-value"),
    ]
    content = synthetic_moeve_statement(rows=rows, seed=18)
    with pytest.raises(ValueError, match="invalid decimal"):
        MoeveParser().parse("moeve.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# Deliberate absence of seller-entity detection
# --------------------------------------------------------------------------- #


def test_g3_2_moeve_parse_never_attempts_entity_detection():
    """Like Q8/DKV/TFC (and unlike Eurowag/E100), Moeve has no R20 worked
    example — `entities` is unconditionally empty and the warning
    explicitly says detection was never attempted (distinct wording from
    "none found in this document")."""
    content = synthetic_moeve_statement(rows=[_row()], seed=19)
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    assert parsed.entities == []
    assert len(parsed.warnings) == 1
    assert "not implemented for this network" in parsed.warnings[0]
    assert "no R20 worked marker" in parsed.warnings[0]


def test_g3_2_moeve_parse_entities_stay_empty_even_with_moeve_looking_text_in_the_file():
    """A hand-rolled 'Moeve Pro Services'-shaped line elsewhere in the file
    must never be picked up as an entity — MoeveParser attempts no such
    scan at all (see module docstring). The VAT id is a fabricated
    all-nines synthetic (pii-allowlisted shape, never a real
    registration)."""
    content = (
        synthetic_moeve_statement(seed=20) + "Moeve Pro Services S.A.U., VAT: ES999999999902\n"
    )
    parsed = MoeveParser().parse("moeve.csv", _bytes(content))
    assert parsed.entities == []
