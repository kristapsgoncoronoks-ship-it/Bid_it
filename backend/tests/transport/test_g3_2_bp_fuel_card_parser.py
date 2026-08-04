"""G3.2 slice 7 — the BP/Aral (B2Mobility) fuel-card parser: the PLN
independently-given money model (figures read VERBATIM off the file —
Eurowag/Q8's shape; the network's quirks live elsewhere), the fail-closed
`line_type` refusal, the two documented decisions (ORS fee lines are
ordinary VAT-bearing lines; MPP split-payment is settlement-side advisory
only), and the deliberate absence of seller-entity detection. Pure-function
tests (no DB) — see `test_g3_2_bp_statement_ingest.py` for the DB-touching
orchestration tests (incl. the ECB conversion path this all-PLN network
defaults to). Mirrors `test_g3_2_moeve_fuel_card_parser.py`'s structure.
This is the LAST G3.2 network — the exact seven-parser registry list is
asserted here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.bp import BPParser
from app.services.transport.parsers.dkv import DKVParser
from app.services.transport.parsers.e100 import E100Parser
from app.services.transport.parsers.eurowag import EurowagParser
from app.services.transport.parsers.moeve import MoeveParser
from app.services.transport.parsers.q8 import Q8Parser
from app.services.transport.parsers.tfc import TFCParser
from tests.factories.transport import (
    synthetic_bp_statement,
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
    """A well-formed single BP PL/PLN 23% fuel row, overridable per test."""
    base: dict[str, object] = {
        "txn_date": "2026-06-04",
        "txn_time": "10:20",
        "vehicle_ref": "Fleet-P-001",
        "station": "Poznan A2 Services",
        "country": "PL",
        "product": "DIESEL",
        "qty": "200.000",
        "currency": "PLN",
        "net_local": "1000.00",
        "vat_local": "230.00",
        "gross_local": "1230.00",
        "line_type": "fuel",
        "invoice_ref": "BP-2026-000451",
    }
    base.update(overrides)
    return base


def _toll_and_fee_rows() -> list[dict[str, object]]:
    """A toll row (8% — the harvested PL reduced rate) and its ORS fee row
    (23%, net = 2.5% of the toll net — the harvested ratio shape)."""
    return [
        _row(
            product="A2 TOLL",
            qty="1",
            net_local="200.00",
            vat_local="16.00",
            gross_local="216.00",
            line_type="toll",
            invoice_ref="BP-2026-000452",
        ),
        _row(
            product="ORS FEE",
            qty="1",
            net_local="5.00",
            vat_local="1.15",
            gross_local="6.15",
            line_type="ors_fee",
            invoice_ref="BP-2026-000453",
        ),
    ]


# --------------------------------------------------------------------------- #
# The registry — all seven networks co-exist (G3.2 complete)
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_seven_networks_in_order():
    names = [p.network for p in fuel_card_parser.parsers()]
    assert names == ["Eurowag", "E100", "Q8", "DKV", "TFC", "Moeve", "BP"]


def test_g3_2_select_dispatches_all_seven_networks_without_crossing():
    bp_content = synthetic_bp_statement(seed=1)
    moeve_content = synthetic_moeve_statement(seed=1)
    tfc_content = synthetic_tfc_statement(seed=1)
    dkv_content = synthetic_dkv_statement(seed=1)
    q8_content = synthetic_q8_statement(seed=1)
    e100_content = synthetic_e100_statement(seed=1)
    eurowag_content = synthetic_eurowag_statement(seed=1)

    assert fuel_card_parser.select("bp.csv", _bytes(bp_content)).network == "BP"
    assert fuel_card_parser.select("moeve.csv", _bytes(moeve_content)).network == "Moeve"
    assert fuel_card_parser.select("tfc.csv", _bytes(tfc_content)).network == "TFC"
    assert fuel_card_parser.select("dkv.csv", _bytes(dkv_content)).network == "DKV"
    assert fuel_card_parser.select("q8.csv", _bytes(q8_content)).network == "Q8"
    assert fuel_card_parser.select("e100.csv", _bytes(e100_content)).network == "E100"
    assert fuel_card_parser.select("eurowag.csv", _bytes(eurowag_content)).network == "Eurowag"


def test_g3_2_bp_handles_never_matches_other_networks_files_and_vice_versa():
    bp_content = synthetic_bp_statement(seed=2)
    moeve_content = synthetic_moeve_statement(seed=2)
    tfc_content = synthetic_tfc_statement(seed=2)
    dkv_content = synthetic_dkv_statement(seed=2)
    q8_content = synthetic_q8_statement(seed=2)
    e100_content = synthetic_e100_statement(seed=2)
    eurowag_content = synthetic_eurowag_statement(seed=2)

    assert not BPParser().handles("x.csv", _bytes(moeve_content))
    assert not BPParser().handles("x.csv", _bytes(tfc_content))
    assert not BPParser().handles("x.csv", _bytes(dkv_content))
    assert not BPParser().handles("x.csv", _bytes(q8_content))
    assert not BPParser().handles("x.csv", _bytes(e100_content))
    assert not BPParser().handles("x.csv", _bytes(eurowag_content))
    assert not MoeveParser().handles("x.csv", _bytes(bp_content))
    assert not TFCParser().handles("x.csv", _bytes(bp_content))
    assert not DKVParser().handles("x.csv", _bytes(bp_content))
    assert not Q8Parser().handles("x.csv", _bytes(bp_content))
    assert not E100Parser().handles("x.csv", _bytes(bp_content))
    assert not EurowagParser().handles("x.csv", _bytes(bp_content))


def test_g3_2_run_dispatches_bp_and_returns_a_parsed_statement():
    content = synthetic_bp_statement(seed=3)
    result = fuel_card_parser.run("bp.csv", _bytes(content))
    assert result.network == "BP"
    assert len(result.lines) == 3
    assert result.entities == []


# --------------------------------------------------------------------------- #
# BPParser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_bp_handles_a_well_formed_statement():
    content = synthetic_bp_statement(seed=4)
    assert BPParser().handles("bp.csv", _bytes(content))


def test_g3_2_bp_does_not_handle_a_file_missing_the_marker():
    content = synthetic_bp_statement(seed=4).replace("BP ARAL STATEMENT", "SOMETHING ELSE")
    assert not BPParser().handles("bp.csv", _bytes(content))


def test_g3_2_bp_does_not_handle_undecodable_bytes():
    assert not BPParser().handles("bp.csv", b"\xff\xfe\x00\x01")


def test_g3_2_bp_does_not_match_on_a_partial_incidental_mention():
    """The marker must be the exact first line — text merely containing the
    substring "BP ARAL" elsewhere in the file must not be picked up."""
    body = "some other statement\nBP ARAL mentioned in passing\ntxn_date,foo\n"
    assert not BPParser().handles("bp.csv", _bytes(body))


# --------------------------------------------------------------------------- #
# BPParser.parse() — the independently-given PLN money model
# --------------------------------------------------------------------------- #


def test_g3_2_bp_parse_reads_printed_figures_verbatim():
    """The load-bearing correctness property: BP's figures are
    independently given — the parser reads net/vat/gross AS PRINTED with
    NO derivation, NO rounding, NO conversion (the PLN→EUR conversion is
    ingestion's dated-ECB-rate branch, never the parser's)."""
    content = synthetic_bp_statement(rows=[_row()], seed=5)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]

    assert line.net_local == Decimal("1000.00")
    assert line.vat_local == Decimal("230.00")
    assert line.gross_local == Decimal("1230.00")
    assert line.country == "PL"
    assert line.currency == "PLN"
    assert line.vehicle_ref == "Fleet-P-001"
    assert line.station == "Poznan A2 Services"
    assert line.product == "DIESEL"
    assert str(line.qty) == "200.000"
    assert line.invoice_ref == "BP-2026-000451"
    assert line.txn_time == "10:20"
    # The stated-EUR field stays unset — BP is NOT a DKV-style stated
    # network; ingestion must take the ECB branch, never the stated one.
    assert line.stated_net_eur is None
    assert line.provenance_note == "line_type=fuel"


def test_g3_2_bp_parse_default_fixture_carries_fuel_toll_and_ors_fee():
    content = synthetic_bp_statement(seed=6)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert len(parsed.lines) == 3
    assert {line.country for line in parsed.lines} == {"PL"}
    assert all(line.currency == "PLN" for line in parsed.lines)
    notes = [line.provenance_note for line in parsed.lines]
    assert notes == ["line_type=fuel", "line_type=toll", "line_type=ors_fee"]


def test_g3_2_bp_parse_line_type_is_case_insensitive():
    content = synthetic_bp_statement(rows=[_row(line_type="ORS_Fee")], seed=7)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert parsed.lines[0].provenance_note == "line_type=ors_fee"


def test_g3_2_bp_parse_unknown_line_type_aborts_the_statement():
    """A line kind is never guessed — it feeds the Art. 9 goods-code path
    (fuel=1 / tolls=4 / other=10); an unknown `line_type` refuses the
    whole statement (fail-closed, the same discipline as TFC's
    station_class and Moeve's payment channel)."""
    content = synthetic_bp_statement(rows=[_row(line_type="rebate")], seed=8)
    with pytest.raises(ValueError, match="unknown line_type"):
        BPParser().parse("bp.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# THE ORS-FEE-LINE DECISION — an ordinary VAT-bearing line + one advisory
# --------------------------------------------------------------------------- #


def test_g3_2_bp_parse_ors_fee_lines_surface_one_advisory_summary():
    """The documented ORS decision: fee lines parse as ordinary lines with
    their printed figures untouched, and the parse surfaces ONE advisory
    warning (count + per-currency ORS gross + toll gross) so a reviewer
    can eyeball the contracted ~2.5% ratio — which is NEVER verified or
    enforced at capture (G3.4 territory)."""
    content = synthetic_bp_statement(rows=[_row(), *_toll_and_fee_rows()], seed=9)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert len(parsed.lines) == 3

    fee_line = parsed.lines[2]
    assert fee_line.net_local == Decimal("5.00")
    assert fee_line.vat_local == Decimal("1.15")
    assert fee_line.gross_local == Decimal("6.15")

    ors_warnings = [w for w in parsed.warnings if "ORS fee line" in w]
    assert len(ors_warnings) == 1
    assert "1 ORS fee line(s)" in ors_warnings[0]
    assert "6.15 PLN" in ors_warnings[0]
    assert "216.00 PLN" in ors_warnings[0]
    assert "not verified at capture" in ors_warnings[0]


def test_g3_2_bp_parse_no_ors_lines_no_ors_warning():
    content = synthetic_bp_statement(rows=[_row()], seed=10)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert not any("ORS fee line" in w for w in parsed.warnings)


# --------------------------------------------------------------------------- #
# THE MPP DECISION — settlement-side advisory only, fail-open on absence
# --------------------------------------------------------------------------- #


def test_g3_2_bp_parse_mpp_annotation_present_surfaces_the_settlement_advisory():
    """The documented MPP decision: the statutory annotation yields ONE
    advisory warning carrying the per-currency VAT total the mechanism
    directs to the dedicated VAT account — settlement-side information
    only; no figure changes and nothing blocks."""
    content = synthetic_bp_statement(rows=[_row(), *_toll_and_fee_rows()], seed=11)
    parsed = BPParser().parse("bp.csv", _bytes(content))

    mpp_warnings = [w for w in parsed.warnings if "MPP" in w and "annotated" in w]
    assert len(mpp_warnings) == 1
    # Σ vat_local = 230.00 + 16.00 + 1.15 — per-currency, never a raw
    # cross-currency sum (master-context §4.14).
    assert "247.15 PLN" in mpp_warnings[0]
    assert "settlement-side only" in mpp_warnings[0]
    # Figures are byte-identical to what is printed — the annotation never
    # mutates a number.
    assert parsed.lines[0].net_local == Decimal("1000.00")
    assert parsed.lines[0].vat_local == Decimal("230.00")


def test_g3_2_bp_parse_missing_mpp_annotation_warns_but_parses():
    """Fail-OPEN by design: an absent settlement annotation corrupts no
    captured figure, so it warns (mandatory-network wording) and never
    blocks — contrast the fail-closed `line_type` refusal, where a
    present-but-unknown enumerated value is unreadable data."""
    content = synthetic_bp_statement(rows=[_row()], annotation_lines=[], seed=12)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert len(parsed.lines) == 1

    missing_warnings = [w for w in parsed.warnings if "no split-payment (MPP) annotation" in w]
    assert len(missing_warnings) == 1
    assert "mandatory" in missing_warnings[0]
    assert not any("annotated" in w and "MPP" in w for w in parsed.warnings)


def test_g3_2_bp_parse_mpp_scan_ignores_csv_cell_text():
    """The annotation scan covers only the non-CSV portion of the file — a
    station cell containing the statutory words must not satisfy it."""
    rows = [_row(station="MECHANIZM PODZIELONEJ PŁATNOŚCI Cafe")]
    content = synthetic_bp_statement(rows=rows, annotation_lines=[], seed=13)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert any("no split-payment (MPP) annotation" in w for w in parsed.warnings)


# --------------------------------------------------------------------------- #
# Structural refusals + determinism
# --------------------------------------------------------------------------- #


def test_g3_2_bp_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    content = synthetic_bp_statement(seed=14)
    first = BPParser().parse("bp.csv", _bytes(content))
    second = BPParser().parse("bp.csv", _bytes(content))
    assert [line.line_seq for line in first.lines] == [1, 2, 3]
    assert [line.line_seq for line in second.lines] == [1, 2, 3]


def test_g3_2_bp_parse_raises_on_a_missing_required_column():
    content = synthetic_bp_statement(seed=15).replace("line_type,", "")
    with pytest.raises(ValueError, match="missing required column"):
        BPParser().parse("bp.csv", _bytes(content))


def test_g3_2_bp_parse_raises_on_an_unparseable_net_decimal():
    content = synthetic_bp_statement(rows=[_row(net_local="not-a-number")], seed=16)
    with pytest.raises(ValueError, match="invalid decimal"):
        BPParser().parse("bp.csv", _bytes(content))


def test_g3_2_bp_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_bp_statement(seed=17)
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        BPParser().parse("bp.csv", _bytes(stripped))


def test_g3_2_bp_malformed_row_aborts_the_whole_statement():
    """A malformed row must abort the WHOLE statement — never a silently
    dropped row (mirrors every prior network parser's discipline)."""
    rows = [
        _row(),
        _row(txn_date="2026-06-05", vehicle_ref="Fleet-P-002", vat_local="bad-value"),
    ]
    content = synthetic_bp_statement(rows=rows, seed=18)
    with pytest.raises(ValueError, match="invalid decimal"):
        BPParser().parse("bp.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# Deliberate absence of seller-entity detection
# --------------------------------------------------------------------------- #


def test_g3_2_bp_parse_never_attempts_entity_detection():
    """Like Q8/DKV/TFC/Moeve (and unlike Eurowag/E100), BP has no R20
    worked example — `entities` is unconditionally empty and the warning
    explicitly says detection was never attempted (distinct wording from
    "none found in this document")."""
    content = synthetic_bp_statement(rows=[_row()], seed=19)
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert parsed.entities == []
    entity_warnings = [w for w in parsed.warnings if "not implemented for this network" in w]
    assert len(entity_warnings) == 1
    assert "no R20 worked marker" in entity_warnings[0]


def test_g3_2_bp_parse_entities_stay_empty_even_with_bp_looking_text_in_the_file():
    """A hand-rolled 'B2Mobility GmbH'-shaped line elsewhere in the file
    must never be picked up as an entity — BPParser attempts no such scan
    at all (see module docstring). The VAT id is a fabricated all-nines
    synthetic (pii-allowlisted shape, never a real registration)."""
    content = synthetic_bp_statement(seed=20) + "B2Mobility GmbH, VAT: PL9999999903\n"
    parsed = BPParser().parse("bp.csv", _bytes(content))
    assert parsed.entities == []
