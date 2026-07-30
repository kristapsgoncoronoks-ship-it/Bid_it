"""G3.2 slice 1 — the fuel-card parser registry + the Eurowag parser (R20,
closing what G3.1/WO-61 explicitly left open). Pure-function tests (no DB) —
see `test_g3_2_statement_ingest.py` for the DB-touching orchestration tests.
"""

from __future__ import annotations

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.eurowag import EurowagParser
from tests.factories.transport import synthetic_eurowag_statement, synthetic_vat_id


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_the_eurowag_parser_by_default():
    names = [p.network for p in fuel_card_parser.parsers()]
    assert "Eurowag" in names


def test_g3_2_select_raises_when_no_parser_recognizes_the_file():
    """Fail-closed network detection — never guess."""
    with pytest.raises(ValueError, match="No registered fuel-card parser"):
        fuel_card_parser.select("mystery.csv", _bytes("not a recognised statement at all"))


def test_g3_2_select_does_not_match_on_a_partial_marker():
    """The marker must be the exact first line — text merely CONTAINING the
    word "EUROWAG" elsewhere in the file must not be picked up."""
    body = "some other statement\nEUROWAG mentioned in passing\ntxn_date,foo\n"
    with pytest.raises(ValueError):
        fuel_card_parser.select("statement.csv", _bytes(body))


def test_g3_2_run_dispatches_to_the_matching_parser_and_returns_a_parsed_statement():
    content = synthetic_eurowag_statement(seed=1)
    result = fuel_card_parser.run("eurowag.csv", _bytes(content))
    assert result.network == "Eurowag"
    assert len(result.lines) == 1
    assert len(result.entities) == 1


def test_g3_2_register_first_gives_a_parser_priority():
    class _AlwaysHandles(fuel_card_parser.FuelCardParser):
        network = "Fake"

        def handles(self, filename, content):
            return True

        def parse(self, filename, content):
            return fuel_card_parser.ParsedStatement(network=self.network)

    before = fuel_card_parser.parsers()
    try:
        fuel_card_parser.register(_AlwaysHandles(), first=True)
        selected = fuel_card_parser.select("anything.csv", b"irrelevant")
        assert selected.network == "Fake"
    finally:
        fuel_card_parser._PARSERS[:] = before


# --------------------------------------------------------------------------- #
# EurowagParser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_eurowag_handles_a_well_formed_statement():
    content = synthetic_eurowag_statement(seed=2)
    assert EurowagParser().handles("eurowag.csv", _bytes(content))


def test_g3_2_eurowag_does_not_handle_a_file_missing_the_marker():
    content = synthetic_eurowag_statement(seed=2).replace("EUROWAG STATEMENT", "SOMETHING ELSE")
    assert not EurowagParser().handles("eurowag.csv", _bytes(content))


def test_g3_2_eurowag_does_not_handle_undecodable_bytes():
    assert not EurowagParser().handles("eurowag.csv", b"\xff\xfe\x00\x01")


# --------------------------------------------------------------------------- #
# EurowagParser.parse() — line extraction
# --------------------------------------------------------------------------- #


def test_g3_2_parse_extracts_every_field_of_a_line():
    content = synthetic_eurowag_statement(
        rows=[
            {
                "txn_date": "2026-06-15",
                "txn_time": "09:30",
                "vehicle_ref": "Fleet-A-001",
                "station": "Brussels Hub",
                "country": "be",
                "product": "DIESEL",
                "qty": "120.500",
                "currency": "eur",
                "net_local": "110.00",
                "vat_local": "23.10",
                "gross_local": "133.10",
                "invoice_ref": "EW-2026-000123",
            }
        ],
        seed=3,
    )
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]
    assert line.line_seq == 1
    assert line.country == "BE"  # normalised uppercase
    assert line.currency == "EUR"
    assert line.vehicle_ref == "Fleet-A-001"
    assert line.station == "Brussels Hub"
    assert line.product == "DIESEL"
    assert str(line.qty) == "120.500"
    assert str(line.net_local) == "110.00"
    assert str(line.vat_local) == "23.10"
    assert str(line.gross_local) == "133.10"
    assert line.invoice_ref == "EW-2026-000123"
    assert line.txn_time == "09:30"


def test_g3_2_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "BE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "10.00",
            "vat_local": "2.10",
            "gross_local": "12.10",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "BE",
            "product": "AdBlue",
            "qty": "5",
            "currency": "EUR",
            "net_local": "5.00",
            "vat_local": "1.05",
            "gross_local": "6.05",
            "invoice_ref": "",
        },
    ]
    content = synthetic_eurowag_statement(rows=rows, seed=4)
    first = EurowagParser().parse("eurowag.csv", _bytes(content))
    second = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert [l.line_seq for l in first.lines] == [1, 2]
    assert [l.line_seq for l in second.lines] == [1, 2]  # deterministic re-parse


def test_g3_2_parse_raises_on_a_missing_required_column():
    content = synthetic_eurowag_statement(seed=5).replace("net_local,", "")
    with pytest.raises(ValueError, match="missing required column"):
        EurowagParser().parse("eurowag.csv", _bytes(content))


def test_g3_2_parse_raises_on_an_unparseable_decimal_and_writes_nothing_partial():
    """A single malformed field aborts the WHOLE statement — never a
    silently-dropped row (see the parser module's docstring)."""
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "BE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "not-a-number",
            "vat_local": "2.10",
            "gross_local": "12.10",
            "invoice_ref": "",
        }
    ]
    content = synthetic_eurowag_statement(rows=rows, seed=6)
    with pytest.raises(ValueError, match="invalid decimal"):
        EurowagParser().parse("eurowag.csv", _bytes(content))


def test_g3_2_parse_raises_on_an_invalid_country_code():
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
            "net_local": "10.00",
            "vat_local": "2.10",
            "gross_local": "12.10",
            "invoice_ref": "",
        }
    ]
    content = synthetic_eurowag_statement(rows=rows, seed=7)
    with pytest.raises(ValueError, match="invalid country code"):
        EurowagParser().parse("eurowag.csv", _bytes(content))


def test_g3_2_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_eurowag_statement(seed=8)
    # Strip the one data row, keeping marker + header + separator + footer.
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        EurowagParser().parse("eurowag.csv", _bytes(stripped))


# --------------------------------------------------------------------------- #
# Seller-entity anchoring (R20/B1) — the product thesis
# --------------------------------------------------------------------------- #


def test_g3_2_parse_reads_the_be_bvba_seller_not_the_czech_factoring_entity():
    """R20's own worked example, through the PUBLIC `parse()` path: a BE
    Eurowag invoice yields the BE entity; a Czech factoring-entity
    disclosure elsewhere in the document (NOT using the seller label) must
    never be picked up. VAT ids are built via `synthetic_vat_id` (never a
    hand-written literal) so no structural-VAT-shaped string appears in
    this file's own source text — `scripts/pii_scan.py` scans FILE TEXT,
    and a value computed at test-run time is invisible to it, exactly like
    every other transport test's use of this factory."""
    be_vat = synthetic_vat_id("BE", seed=20)
    cz_vat = synthetic_vat_id("CZ", seed=21)
    footer = [
        f"Pārdevējs / Verkoper: Eurowag Belgium BVBA, 1 Industrieweg, Brussels, "
        f"PVN reg. Nr.: {be_vat}",
        "",
        f"Receivables under this agreement are ceded to W.A.G. Issuing Services, "
        f"a.s., Prague, VAT {cz_vat} for factoring purposes.",
    ]
    content = synthetic_eurowag_statement(footer_lines=footer, seed=10)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))

    assert len(parsed.entities) == 1
    assert parsed.entities[0].country == "BE"
    assert parsed.entities[0].vat_number == be_vat
    assert "Eurowag Belgium BVBA" in parsed.entities[0].entity_name
    assert not any(cz_vat in e.vat_number for e in parsed.entities)
    assert not any("W.A.G. Issuing Services" in e.entity_name for e in parsed.entities)


def test_g3_2_parse_supports_multiple_country_footers_in_one_statement():
    be_vat = synthetic_vat_id("BE", seed=22)
    lt_vat = synthetic_vat_id("LT", seed=23)
    footer = [
        f"Pārdevējs / Verkoper: Eurowag Belgium BVBA, Brussels, PVN reg. Nr.: {be_vat}",
        f"Pārdevējs / Verkoper: Eurowag Baltic UAB, Vilnius, PVN reg. Nr.: {lt_vat}",
    ]
    content = synthetic_eurowag_statement(footer_lines=footer, seed=11)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert sorted(e.country for e in parsed.entities) == ["BE", "LT"]


def test_g3_2_parse_warns_and_detects_nothing_when_footer_has_no_seller_line():
    content = synthetic_eurowag_statement(footer_lines=["Thank you for your business."], seed=9)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert len(parsed.lines) == 1  # transaction lines still ingest fine
    assert parsed.entities == []
    assert any("no per-country seller entity detected" in w for w in parsed.warnings)


def test_g3_2_parse_warns_when_label_present_but_no_legal_form_recognised():
    footer = ["Pārdevējs / Verkoper: Some Random Corp, Nowhere"]
    content = synthetic_eurowag_statement(footer_lines=footer, seed=12)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert parsed.entities == []
    assert any("no recognised legal form" in w for w in parsed.warnings)


def test_g3_2_parse_warns_when_label_present_but_no_vat_found():
    footer = ["Pārdevējs / Verkoper: Eurowag Belgium BVBA, Brussels"]
    content = synthetic_eurowag_statement(footer_lines=footer, seed=13)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert parsed.entities == []
    assert any("no VAT registration number" in w for w in parsed.warnings)


def test_g3_2_parse_maps_the_greek_el_vat_prefix_exception_to_gr():
    el_vat = synthetic_vat_id("EL", seed=24)
    footer = [f"Pārdevējs / Verkoper: Eurowag Hellas AB, Athens, PVN reg. Nr.: {el_vat}"]
    content = synthetic_eurowag_statement(footer_lines=footer, seed=14)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert len(parsed.entities) == 1
    assert parsed.entities[0].country == "GR"


def test_g3_2_parse_warns_and_detects_nothing_for_an_unresolvable_vat_prefix():
    footer = ["Pārdevējs / Verkoper: Eurowag Weird SE, Nowhere, PVN reg. Nr.: 9INVALID12"]
    content = synthetic_eurowag_statement(footer_lines=footer, seed=15)
    parsed = EurowagParser().parse("eurowag.csv", _bytes(content))
    assert parsed.entities == []
    assert any("could not determine a country" in w for w in parsed.warnings)
