"""G3.2 slice 2 — the E100 fuel-card parser (R20's second worked example):
the reverse VAT-inclusive-gross money model + anchor-to-marker entity
detection. Pure-function tests (no DB) — see
`test_g3_2_e100_statement_ingest.py` for the DB-touching orchestration
tests. Mirrors `test_g3_2_fuel_card_parser.py`'s structure for the Eurowag
parser (WO-62).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.e100 import E100Parser
from app.services.transport.parsers.eurowag import EurowagParser
from tests.factories.transport import (
    synthetic_e100_statement,
    synthetic_eurowag_statement,
    synthetic_vat_id,
)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


# --------------------------------------------------------------------------- #
# The registry — both networks co-exist
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_both_eurowag_and_e100():
    names = [p.network for p in fuel_card_parser.parsers()]
    assert "Eurowag" in names
    assert "E100" in names


def test_g3_2_select_dispatches_e100_and_eurowag_files_without_crossing():
    e100_content = synthetic_e100_statement(seed=1)
    eurowag_content = synthetic_eurowag_statement(seed=1)

    e100_selected = fuel_card_parser.select("e100.csv", _bytes(e100_content))
    eurowag_selected = fuel_card_parser.select("eurowag.csv", _bytes(eurowag_content))

    assert e100_selected.network == "E100"
    assert eurowag_selected.network == "Eurowag"


def test_g3_2_e100_handles_never_matches_a_eurowag_file_and_vice_versa():
    e100_content = synthetic_e100_statement(seed=2)
    eurowag_content = synthetic_eurowag_statement(seed=2)

    assert not E100Parser().handles("x.csv", _bytes(eurowag_content))
    assert not EurowagParser().handles("x.csv", _bytes(e100_content))


def test_g3_2_run_dispatches_e100_and_returns_a_parsed_statement():
    content = synthetic_e100_statement(seed=3)
    result = fuel_card_parser.run("e100.csv", _bytes(content))
    assert result.network == "E100"
    assert len(result.lines) == 1
    assert len(result.entities) == 1


# --------------------------------------------------------------------------- #
# E100Parser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_e100_handles_a_well_formed_statement():
    content = synthetic_e100_statement(seed=4)
    assert E100Parser().handles("e100.csv", _bytes(content))


def test_g3_2_e100_does_not_handle_a_file_missing_the_marker():
    content = synthetic_e100_statement(seed=4).replace("E100 STATEMENT", "SOMETHING ELSE")
    assert not E100Parser().handles("e100.csv", _bytes(content))


def test_g3_2_e100_does_not_handle_undecodable_bytes():
    assert not E100Parser().handles("e100.csv", b"\xff\xfe\x00\x01")


def test_g3_2_e100_does_not_match_on_a_partial_incidental_mention():
    """The marker must be the exact first line — text merely containing the
    substring "E100" elsewhere in the file must not be picked up."""
    body = "some other statement\nE100 mentioned in passing\ntxn_date,foo\n"
    assert not E100Parser().handles("e100.csv", _bytes(body))


# --------------------------------------------------------------------------- #
# E100Parser.parse() — the reverse VAT-inclusive-gross money model
# --------------------------------------------------------------------------- #


def test_g3_2_e100_parse_reverse_calculates_net_and_vat_from_gross_and_rate():
    """The load-bearing correctness property this order exists to prove:
    net_local + vat_local == gross_local exactly (pre-rounding Decimal
    arithmetic), matching the hand-computed reverse figures."""
    rows = [
        {
            "txn_date": "2026-06-15",
            "txn_time": "09:30",
            "vehicle_ref": "Fleet-A-001",
            "station": "Warsaw Hub",
            "country": "pl",
            "product": "DIESEL",
            "qty": "100.000",
            "currency": "eur",
            "gross_local": "121.00",
            "vat_rate": "21.00",
            "invoice_ref": "E1-2026-000123",
        }
    ]
    content = synthetic_e100_statement(rows=rows, seed=5)
    parsed = E100Parser().parse("e100.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]

    expected_net = Decimal("121.00") / (Decimal(1) + Decimal("21.00") / Decimal(100))
    expected_vat = Decimal("121.00") - expected_net

    assert line.net_local == expected_net
    assert line.vat_local == expected_vat
    assert line.net_local + line.vat_local == line.gross_local
    assert line.country == "PL"
    assert line.currency == "EUR"
    assert line.vehicle_ref == "Fleet-A-001"
    assert line.station == "Warsaw Hub"
    assert line.product == "DIESEL"
    assert str(line.qty) == "100.000"
    assert line.invoice_ref == "E1-2026-000123"
    assert line.txn_time == "09:30"
    assert line.provenance_note is not None
    assert "gross_local=121.00" in line.provenance_note
    assert "vat_rate=21.00" in line.provenance_note


def test_g3_2_e100_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "21",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "PL",
            "product": "AdBlue",
            "qty": "5",
            "currency": "EUR",
            "gross_local": "6.05",
            "vat_rate": "21",
            "invoice_ref": "",
        },
    ]
    content = synthetic_e100_statement(rows=rows, seed=6)
    first = E100Parser().parse("e100.csv", _bytes(content))
    second = E100Parser().parse("e100.csv", _bytes(content))
    assert [l.line_seq for l in first.lines] == [1, 2]
    assert [l.line_seq for l in second.lines] == [1, 2]


def test_g3_2_e100_parse_raises_on_a_missing_required_column():
    content = synthetic_e100_statement(seed=7).replace("gross_local,", "")
    with pytest.raises(ValueError, match="missing required column"):
        E100Parser().parse("e100.csv", _bytes(content))


def test_g3_2_e100_parse_raises_on_an_unparseable_gross_decimal():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "not-a-number",
            "vat_rate": "21",
            "invoice_ref": "",
        }
    ]
    content = synthetic_e100_statement(rows=rows, seed=8)
    with pytest.raises(ValueError, match="invalid decimal"):
        E100Parser().parse("e100.csv", _bytes(content))


def test_g3_2_e100_parse_raises_on_an_unparseable_vat_rate():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "not-a-number",
            "invoice_ref": "",
        }
    ]
    content = synthetic_e100_statement(rows=rows, seed=9)
    with pytest.raises(ValueError, match="invalid decimal"):
        E100Parser().parse("e100.csv", _bytes(content))


def test_g3_2_e100_parse_raises_when_vat_rate_is_above_100():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "150",
            "invoice_ref": "",
        }
    ]
    content = synthetic_e100_statement(rows=rows, seed=10)
    with pytest.raises(ValueError, match="out of range"):
        E100Parser().parse("e100.csv", _bytes(content))


def test_g3_2_e100_parse_raises_when_vat_rate_is_negative():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "-5",
            "invoice_ref": "",
        }
    ]
    content = synthetic_e100_statement(rows=rows, seed=11)
    with pytest.raises(ValueError, match="out of range"):
        E100Parser().parse("e100.csv", _bytes(content))


def test_g3_2_e100_parse_raises_on_an_invalid_country_code():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "XX1",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "21",
            "invoice_ref": "",
        }
    ]
    content = synthetic_e100_statement(rows=rows, seed=12)
    with pytest.raises(ValueError, match="invalid country code"):
        E100Parser().parse("e100.csv", _bytes(content))


def test_g3_2_e100_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_e100_statement(seed=13)
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        E100Parser().parse("e100.csv", _bytes(stripped))


def test_g3_2_e100_malformed_row_aborts_the_whole_statement():
    """A malformed row must abort the WHOLE statement — never a silently
    dropped row (mirrors `eurowag.py`'s own discipline)."""
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "PL",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "gross_local": "12.10",
            "vat_rate": "21",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "PL",
            "product": "AdBlue",
            "qty": "5",
            "currency": "EUR",
            "gross_local": "6.05",
            "vat_rate": "150",  # malformed — out of range
            "invoice_ref": "",
        },
    ]
    content = synthetic_e100_statement(rows=rows, seed=14)
    with pytest.raises(ValueError, match="out of range"):
        E100Parser().parse("e100.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# Seller-entity anchoring (R20's second worked example) — the product thesis
# --------------------------------------------------------------------------- #


def test_g3_2_e100_parse_reads_the_marker_entity_not_the_buyer_annexe_vat():
    """R20's second worked example, verbatim: a statement whose footer
    carries BOTH a genuine "E100 International Trade" marker line AND an
    unrelated buyer-VAT annexe line (no marker) must detect ONLY the
    marker's entity."""
    pl_vat = synthetic_vat_id("PL", seed=20)
    lv_vat = synthetic_vat_id("LV", seed=21)
    footer = [
        f"E100 International Trade sp. z o.o., VAT reg. No.: {pl_vat}",
        "",
        f"Client VAT reference (repeats on every annexe page): {lv_vat}",
    ]
    content = synthetic_e100_statement(footer_lines=footer, seed=15)
    parsed = E100Parser().parse("e100.csv", _bytes(content))

    assert len(parsed.entities) == 1
    assert parsed.entities[0].country == "PL"
    assert parsed.entities[0].vat_number == pl_vat
    assert "E100 International Trade" in parsed.entities[0].entity_name
    assert not any(lv_vat in e.vat_number for e in parsed.entities)


def test_g3_2_e100_parse_warns_and_still_ingests_when_no_marker_at_all():
    content = synthetic_e100_statement(footer_lines=["Thank you for your business."], seed=16)
    parsed = E100Parser().parse("e100.csv", _bytes(content))
    assert len(parsed.lines) == 1  # transaction lines still ingest fine
    assert parsed.entities == []
    assert any("no per-country seller entity detected" in w for w in parsed.warnings)


def test_g3_2_e100_parse_warns_when_marker_present_but_no_vat_found():
    footer = ["E100 International Trade sp. z o.o., Warsaw"]
    content = synthetic_e100_statement(footer_lines=footer, seed=17)
    parsed = E100Parser().parse("e100.csv", _bytes(content))
    assert parsed.entities == []
    assert any("no VAT registration number" in w for w in parsed.warnings)


def test_g3_2_e100_parse_maps_the_greek_el_vat_prefix_exception_to_gr():
    el_vat = synthetic_vat_id("EL", seed=22)
    footer = [f"E100 International Trade Hellas, VAT reg. No.: {el_vat}"]
    content = synthetic_e100_statement(footer_lines=footer, seed=18)
    parsed = E100Parser().parse("e100.csv", _bytes(content))
    assert len(parsed.entities) == 1
    assert parsed.entities[0].country == "GR"


def test_g3_2_e100_parse_warns_and_detects_nothing_for_an_unresolvable_vat_prefix():
    footer = ["E100 International Trade Weird, VAT reg. No.: 9INVALID12"]
    content = synthetic_e100_statement(footer_lines=footer, seed=19)
    parsed = E100Parser().parse("e100.csv", _bytes(content))
    assert parsed.entities == []
    assert any("could not determine a country" in w for w in parsed.warnings)
