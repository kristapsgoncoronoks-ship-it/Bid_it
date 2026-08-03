"""G3.2 slice 5 — the TFC by Moya fuel-card parser: the DERIVED-NET
hub-only discount money model (litres x LIST price minus the
station-classified per-litre tier at a flat 21% VAT, `BA_fleet_fuel.md`
§5.1), the fail-closed tier/currency/price refusals, and the deliberate
absence of seller-entity detection. Pure-function tests (no DB) — see
`test_g3_2_tfc_statement_ingest.py` for the DB-touching orchestration
tests. Mirrors `test_g3_2_dkv_fuel_card_parser.py`'s structure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.dkv import DKVParser
from app.services.transport.parsers.e100 import E100Parser
from app.services.transport.parsers.eurowag import EurowagParser
from app.services.transport.parsers.q8 import Q8Parser
from app.services.transport.parsers.tfc import TFCParser
from tests.factories.transport import (
    synthetic_dkv_statement,
    synthetic_e100_statement,
    synthetic_eurowag_statement,
    synthetic_q8_statement,
    synthetic_tfc_statement,
)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _row(**overrides: object) -> dict[str, object]:
    """A well-formed single TFC hub row, overridable per test."""
    base: dict[str, object] = {
        "txn_date": "2026-06-10",
        "txn_time": "09:15",
        "vehicle_ref": "Fleet-T-001",
        "station": "Antwerp Ring Hub",
        "station_class": "hub",
        "country": "BE",
        "product": "DIESEL",
        "qty": "100.000",
        "currency": "EUR",
        "list_price_eur_l": "1.500",
        "invoice_ref": "TFC-2026-000123",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# The registry — all five networks co-exist
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_five_networks_in_order():
    names = [p.network for p in fuel_card_parser.parsers()]
    assert names == ["Eurowag", "E100", "Q8", "DKV", "TFC"]


def test_g3_2_select_dispatches_all_five_networks_without_crossing():
    tfc_content = synthetic_tfc_statement(seed=1)
    dkv_content = synthetic_dkv_statement(seed=1)
    q8_content = synthetic_q8_statement(seed=1)
    e100_content = synthetic_e100_statement(seed=1)
    eurowag_content = synthetic_eurowag_statement(seed=1)

    assert fuel_card_parser.select("tfc.csv", _bytes(tfc_content)).network == "TFC"
    assert fuel_card_parser.select("dkv.csv", _bytes(dkv_content)).network == "DKV"
    assert fuel_card_parser.select("q8.csv", _bytes(q8_content)).network == "Q8"
    assert fuel_card_parser.select("e100.csv", _bytes(e100_content)).network == "E100"
    assert fuel_card_parser.select("eurowag.csv", _bytes(eurowag_content)).network == "Eurowag"


def test_g3_2_tfc_handles_never_matches_other_networks_files_and_vice_versa():
    tfc_content = synthetic_tfc_statement(seed=2)
    dkv_content = synthetic_dkv_statement(seed=2)
    q8_content = synthetic_q8_statement(seed=2)
    e100_content = synthetic_e100_statement(seed=2)
    eurowag_content = synthetic_eurowag_statement(seed=2)

    assert not TFCParser().handles("x.csv", _bytes(dkv_content))
    assert not TFCParser().handles("x.csv", _bytes(q8_content))
    assert not TFCParser().handles("x.csv", _bytes(e100_content))
    assert not TFCParser().handles("x.csv", _bytes(eurowag_content))
    assert not DKVParser().handles("x.csv", _bytes(tfc_content))
    assert not Q8Parser().handles("x.csv", _bytes(tfc_content))
    assert not E100Parser().handles("x.csv", _bytes(tfc_content))
    assert not EurowagParser().handles("x.csv", _bytes(tfc_content))


def test_g3_2_run_dispatches_tfc_and_returns_a_parsed_statement():
    content = synthetic_tfc_statement(seed=3)
    result = fuel_card_parser.run("tfc.csv", _bytes(content))
    assert result.network == "TFC"
    assert len(result.lines) == 3
    assert result.entities == []


# --------------------------------------------------------------------------- #
# TFCParser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_tfc_handles_a_well_formed_statement():
    content = synthetic_tfc_statement(seed=4)
    assert TFCParser().handles("tfc.csv", _bytes(content))


def test_g3_2_tfc_does_not_handle_a_file_missing_the_marker():
    content = synthetic_tfc_statement(seed=4).replace("TFC STATEMENT", "SOMETHING ELSE")
    assert not TFCParser().handles("tfc.csv", _bytes(content))


def test_g3_2_tfc_does_not_handle_undecodable_bytes():
    assert not TFCParser().handles("tfc.csv", b"\xff\xfe\x00\x01")


def test_g3_2_tfc_does_not_match_on_a_partial_incidental_mention():
    """The marker must be the exact first line — text merely containing the
    substring "TFC" elsewhere in the file must not be picked up."""
    body = "some other statement\nTFC mentioned in passing\ntxn_date,foo\n"
    assert not TFCParser().handles("tfc.csv", _bytes(body))


# --------------------------------------------------------------------------- #
# TFCParser.parse() — the derived-net hub-only discount money model
# --------------------------------------------------------------------------- #


def test_g3_2_tfc_parse_derives_hub_discounted_net_at_flat_21_pct():
    """The load-bearing correctness property distinguishing TFC from every
    prior network: the invoiced figures are DERIVED — 100 L at a 1.500
    EUR/L LIST price fills at 1.295 (the −0.205/L hub tier), so
    net = 129.5, vat = 27.195 (flat 21%), gross = 156.695, all exact
    unrounded Decimals with `net + vat == gross` holding pre-rounding."""
    content = synthetic_tfc_statement(rows=[_row()], seed=5)
    parsed = TFCParser().parse("tfc.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]

    assert line.net_local == Decimal("129.5")
    assert line.vat_local == Decimal("27.195")
    assert line.gross_local == Decimal("156.695")
    assert line.net_local + line.vat_local == line.gross_local
    # The flat rate holds exactly on the derived pair.
    assert line.vat_local / line.net_local == Decimal("0.21")
    assert line.country == "BE"
    assert line.currency == "EUR"
    assert line.vehicle_ref == "Fleet-T-001"
    assert line.station == "Antwerp Ring Hub"
    assert line.product == "DIESEL"
    assert str(line.qty) == "100.000"
    assert line.invoice_ref == "TFC-2026-000123"
    assert line.txn_time == "09:15"
    assert line.provenance_note is not None
    assert "hub" in line.provenance_note
    assert "0.205" in line.provenance_note


def test_g3_2_tfc_parse_applies_the_meer_tier():
    """The Meer hub's own harvested tier is −0.19/L, not the generic
    −0.205/L: 200 L x (1.490 − 0.19) = 260 net, 54.6 vat, 314.6 gross."""
    rows = [
        _row(
            station="Meer Border Hub",
            station_class="meer",
            qty="200.000",
            list_price_eur_l="1.490",
        )
    ]
    content = synthetic_tfc_statement(rows=rows, seed=6)
    parsed = TFCParser().parse("tfc.csv", _bytes(content))
    (line,) = parsed.lines
    assert line.net_local == Decimal("260")
    assert line.vat_local == Decimal("54.6")
    assert line.gross_local == Decimal("314.6")
    assert "meer" in (line.provenance_note or "")
    assert "0.19" in (line.provenance_note or "")


def test_g3_2_tfc_parse_leaves_third_party_stations_undiscounted():
    """Third-party stations bill at LIST — net = qty x list price exactly,
    no tier subtracted (the harvested "third-party stations
    undiscounted")."""
    rows = [
        _row(
            station="Utrecht Third-Party Station",
            station_class="third_party",
            country="NL",
            qty="150.000",
            list_price_eur_l="1.600",
        )
    ]
    content = synthetic_tfc_statement(rows=rows, seed=7)
    parsed = TFCParser().parse("tfc.csv", _bytes(content))
    (line,) = parsed.lines
    assert line.net_local == Decimal("240")
    assert line.vat_local == Decimal("50.4")
    assert line.gross_local == Decimal("290.4")


def test_g3_2_tfc_parse_default_fixture_spans_all_three_tiers():
    content = synthetic_tfc_statement(seed=8)
    parsed = TFCParser().parse("tfc.csv", _bytes(content))
    assert len(parsed.lines) == 3
    assert {line.country for line in parsed.lines} == {"BE", "NL"}
    assert all(line.currency == "EUR" for line in parsed.lines)
    # Every derived pair carries the flat rate exactly.
    for line in parsed.lines:
        assert line.vat_local / line.net_local == Decimal("0.21")
        assert line.net_local + line.vat_local == line.gross_local


def test_g3_2_tfc_parse_unknown_station_class_aborts_the_statement():
    """A tier is never guessed — an unknown classification refuses the
    whole statement (fail-closed, see the parser's module docstring)."""
    rows = [_row(station_class="franchise")]
    content = synthetic_tfc_statement(rows=rows, seed=9)
    with pytest.raises(ValueError, match="unknown station_class"):
        TFCParser().parse("tfc.csv", _bytes(content))


def test_g3_2_tfc_parse_non_eur_row_aborts_the_statement():
    """The tier constants are EUR/L — applying them to a PLN list price
    would be arithmetically wrong, and §5.1 gives TFC as an EUR network."""
    rows = [_row(currency="PLN")]
    content = synthetic_tfc_statement(rows=rows, seed=10)
    with pytest.raises(ValueError, match="EUR network"):
        TFCParser().parse("tfc.csv", _bytes(content))


def test_g3_2_tfc_parse_non_positive_discounted_price_aborts_the_statement():
    """A hub list price at (or below) the 0.205 tier is a mis-keyed price —
    refused with the actual defect named, both-sides: 0.205 blocked,
    0.206 (unit price 0.001) parses."""
    blocked = synthetic_tfc_statement(rows=[_row(list_price_eur_l="0.205")], seed=11)
    with pytest.raises(ValueError, match="not positive"):
        TFCParser().parse("tfc.csv", _bytes(blocked))

    allowed = synthetic_tfc_statement(rows=[_row(list_price_eur_l="0.206")], seed=11)
    parsed = TFCParser().parse("tfc.csv", _bytes(allowed))
    assert parsed.lines[0].net_local == Decimal("100.000") * Decimal("0.001")


def test_g3_2_tfc_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    content = synthetic_tfc_statement(seed=12)
    first = TFCParser().parse("tfc.csv", _bytes(content))
    second = TFCParser().parse("tfc.csv", _bytes(content))
    assert [line.line_seq for line in first.lines] == [1, 2, 3]
    assert [line.line_seq for line in second.lines] == [1, 2, 3]


def test_g3_2_tfc_parse_raises_on_a_missing_required_column():
    content = synthetic_tfc_statement(seed=13).replace("station_class,", "")
    with pytest.raises(ValueError, match="missing required column"):
        TFCParser().parse("tfc.csv", _bytes(content))


def test_g3_2_tfc_parse_raises_on_an_unparseable_list_price_decimal():
    content = synthetic_tfc_statement(rows=[_row(list_price_eur_l="not-a-number")], seed=14)
    with pytest.raises(ValueError, match="invalid decimal"):
        TFCParser().parse("tfc.csv", _bytes(content))


def test_g3_2_tfc_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_tfc_statement(seed=15)
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        TFCParser().parse("tfc.csv", _bytes(stripped))


def test_g3_2_tfc_malformed_row_aborts_the_whole_statement():
    """A malformed row must abort the WHOLE statement — never a silently
    dropped row (mirrors every prior network parser's discipline)."""
    rows = [
        _row(),
        _row(txn_date="2026-06-11", vehicle_ref="Fleet-T-002", qty="bad-value"),
    ]
    content = synthetic_tfc_statement(rows=rows, seed=16)
    with pytest.raises(ValueError, match="invalid decimal"):
        TFCParser().parse("tfc.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# Deliberate absence of seller-entity detection
# --------------------------------------------------------------------------- #


def test_g3_2_tfc_parse_never_attempts_entity_detection():
    """Like Q8/DKV (and unlike Eurowag/E100), TFC has no R20 worked example
    — `entities` is unconditionally empty and the warning explicitly says
    detection was never attempted (distinct wording from "none found in
    this document")."""
    content = synthetic_tfc_statement(seed=17)
    parsed = TFCParser().parse("tfc.csv", _bytes(content))
    assert parsed.entities == []
    assert len(parsed.warnings) == 1
    assert "not implemented for this network" in parsed.warnings[0]
    assert "no R20 worked marker" in parsed.warnings[0]


def test_g3_2_tfc_parse_entities_stay_empty_even_with_tfc_looking_text_in_the_file():
    """A hand-rolled 'TFC by Moya'-shaped line elsewhere in the file must
    never be picked up as an entity — TFCParser attempts no such scan at
    all (see module docstring). The VAT id is a fabricated all-nines
    synthetic (pii-allowlisted shape, never a real registration)."""
    content = synthetic_tfc_statement(seed=18) + "TFC by Moya UAB, VAT: LT999999999901\n"
    parsed = TFCParser().parse("tfc.csv", _bytes(content))
    assert parsed.entities == []
