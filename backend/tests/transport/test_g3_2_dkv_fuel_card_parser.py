"""G3.2 slice 4 — the DKV Euro Service fuel-card parser: the supplier-
stated-EUR money model ("trusts the supplier's per-line EUR and pro-rates",
`BA_fleet_fuel.md` §5.1), the SEK/EUR mix in one statement, and the
deliberate absence of seller-entity detection. Pure-function tests (no DB)
— see `test_g3_2_dkv_statement_ingest.py` for the DB-touching orchestration
tests. Mirrors `test_g3_2_q8_fuel_card_parser.py`'s structure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.dkv import DKVParser
from app.services.transport.parsers.e100 import E100Parser
from app.services.transport.parsers.eurowag import EurowagParser
from app.services.transport.parsers.q8 import Q8Parser
from tests.factories.transport import (
    synthetic_dkv_statement,
    synthetic_e100_statement,
    synthetic_eurowag_statement,
    synthetic_q8_statement,
)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _row(**overrides: object) -> dict[str, object]:
    """A well-formed single DKV SEK row, overridable per test."""
    base: dict[str, object] = {
        "txn_date": "2026-06-10",
        "txn_time": "08:00",
        "vehicle_ref": "Fleet-D-001",
        "station": "Stockholm South Hub",
        "country": "SE",
        "product": "DIESEL",
        "qty": "100.000",
        "currency": "SEK",
        "net_local": "1000.00",
        "vat_local": "250.00",
        "gross_local": "1250.00",
        "net_eur": "90.00",
        "invoice_ref": "DKV-2026-000123",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# The registry — all four networks co-exist
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_four_networks_in_order():
    names = [p.network for p in fuel_card_parser.parsers()]
    assert names == ["Eurowag", "E100", "Q8", "DKV"]


def test_g3_2_select_dispatches_all_four_networks_without_crossing():
    dkv_content = synthetic_dkv_statement(seed=1)
    q8_content = synthetic_q8_statement(seed=1)
    e100_content = synthetic_e100_statement(seed=1)
    eurowag_content = synthetic_eurowag_statement(seed=1)

    assert fuel_card_parser.select("dkv.csv", _bytes(dkv_content)).network == "DKV"
    assert fuel_card_parser.select("q8.csv", _bytes(q8_content)).network == "Q8"
    assert fuel_card_parser.select("e100.csv", _bytes(e100_content)).network == "E100"
    assert fuel_card_parser.select("eurowag.csv", _bytes(eurowag_content)).network == "Eurowag"


def test_g3_2_dkv_handles_never_matches_other_networks_files_and_vice_versa():
    dkv_content = synthetic_dkv_statement(seed=2)
    q8_content = synthetic_q8_statement(seed=2)
    e100_content = synthetic_e100_statement(seed=2)
    eurowag_content = synthetic_eurowag_statement(seed=2)

    assert not DKVParser().handles("x.csv", _bytes(q8_content))
    assert not DKVParser().handles("x.csv", _bytes(e100_content))
    assert not DKVParser().handles("x.csv", _bytes(eurowag_content))
    assert not Q8Parser().handles("x.csv", _bytes(dkv_content))
    assert not E100Parser().handles("x.csv", _bytes(dkv_content))
    assert not EurowagParser().handles("x.csv", _bytes(dkv_content))


def test_g3_2_run_dispatches_dkv_and_returns_a_parsed_statement():
    content = synthetic_dkv_statement(seed=3)
    result = fuel_card_parser.run("dkv.csv", _bytes(content))
    assert result.network == "DKV"
    assert len(result.lines) == 2
    assert result.entities == []


# --------------------------------------------------------------------------- #
# DKVParser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_dkv_handles_a_well_formed_statement():
    content = synthetic_dkv_statement(seed=4)
    assert DKVParser().handles("dkv.csv", _bytes(content))


def test_g3_2_dkv_does_not_handle_a_file_missing_the_marker():
    content = synthetic_dkv_statement(seed=4).replace("DKV STATEMENT", "SOMETHING ELSE")
    assert not DKVParser().handles("dkv.csv", _bytes(content))


def test_g3_2_dkv_does_not_handle_undecodable_bytes():
    assert not DKVParser().handles("dkv.csv", b"\xff\xfe\x00\x01")


def test_g3_2_dkv_does_not_match_on_a_partial_incidental_mention():
    """The marker must be the exact first line — text merely containing the
    substring "DKV" elsewhere in the file must not be picked up."""
    body = "some other statement\nDKV mentioned in passing\ntxn_date,foo\n"
    assert not DKVParser().handles("dkv.csv", _bytes(body))


# --------------------------------------------------------------------------- #
# DKVParser.parse() — the supplier-stated-EUR money model
# --------------------------------------------------------------------------- #


def test_g3_2_dkv_parse_reads_given_figures_and_carries_stated_net_eur():
    """The load-bearing correctness property distinguishing DKV from every
    prior network: the statement's own per-line EUR figure rides along AS
    PRINTED (`stated_net_eur`) — nothing is recomputed here, and the
    on-invoice 1.30 SEK/L discount / 5.63% service fee are already inside
    the given figures, never re-derived (`BA_fleet_fuel.md` §4.2)."""
    content = synthetic_dkv_statement(rows=[_row()], seed=5)
    parsed = DKVParser().parse("dkv.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]

    assert line.net_local == Decimal("1000.00")
    assert line.vat_local == Decimal("250.00")
    assert line.gross_local == Decimal("1250.00")
    assert line.stated_net_eur == Decimal("90.00")
    assert line.country == "SE"
    assert line.currency == "SEK"
    assert line.vehicle_ref == "Fleet-D-001"
    assert line.station == "Stockholm South Hub"
    assert line.product == "DIESEL"
    assert str(line.qty) == "100.000"
    assert line.invoice_ref == "DKV-2026-000123"
    assert line.txn_time == "08:00"
    assert line.provenance_note is not None
    assert "supplier-stated per-line EUR" in line.provenance_note


def test_g3_2_dkv_parse_spans_sek_and_eur_in_one_statement():
    """DKV's own network-table currency mix (SEK/EUR) — the default fixture
    carries one row of each, in one file."""
    content = synthetic_dkv_statement(seed=6)
    parsed = DKVParser().parse("dkv.csv", _bytes(content))
    assert len(parsed.lines) == 2
    assert {line.currency for line in parsed.lines} == {"SEK", "EUR"}
    assert {line.country for line in parsed.lines} == {"SE", "DE"}


def test_g3_2_dkv_parse_eur_row_carries_no_stated_figure():
    """An EUR row has nothing to state — `stated_net_eur` stays None so the
    ingestion identity branch (shared with every other network) applies."""
    content = synthetic_dkv_statement(seed=7)
    parsed = DKVParser().parse("dkv.csv", _bytes(content))
    eur_line = next(line for line in parsed.lines if line.currency == "EUR")
    sek_line = next(line for line in parsed.lines if line.currency == "SEK")
    assert eur_line.stated_net_eur is None
    assert eur_line.provenance_note is None
    assert sek_line.stated_net_eur is not None


def test_g3_2_dkv_parse_eur_row_with_mismatched_net_eur_aborts_the_statement():
    """Two disagreeing figures for the same EUR amount is a malformed row —
    refuse the whole statement, never silently pick one."""
    rows = [
        _row(
            country="DE",
            currency="EUR",
            net_local="100.00",
            vat_local="19.00",
            gross_local="119.00",
            net_eur="99.00",  # disagrees with net_local
        )
    ]
    content = synthetic_dkv_statement(rows=rows, seed=8)
    with pytest.raises(ValueError, match="must equal net_local"):
        DKVParser().parse("dkv.csv", _bytes(content))


def test_g3_2_dkv_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    content = synthetic_dkv_statement(seed=9)
    first = DKVParser().parse("dkv.csv", _bytes(content))
    second = DKVParser().parse("dkv.csv", _bytes(content))
    assert [line.line_seq for line in first.lines] == [1, 2]
    assert [line.line_seq for line in second.lines] == [1, 2]


def test_g3_2_dkv_parse_raises_on_a_missing_required_column():
    content = synthetic_dkv_statement(seed=10).replace("net_eur,", "")
    with pytest.raises(ValueError, match="missing required column"):
        DKVParser().parse("dkv.csv", _bytes(content))


def test_g3_2_dkv_parse_raises_on_an_unparseable_net_eur_decimal():
    content = synthetic_dkv_statement(rows=[_row(net_eur="not-a-number")], seed=11)
    with pytest.raises(ValueError, match="invalid decimal"):
        DKVParser().parse("dkv.csv", _bytes(content))


def test_g3_2_dkv_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_dkv_statement(seed=12)
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        DKVParser().parse("dkv.csv", _bytes(stripped))


def test_g3_2_dkv_malformed_row_aborts_the_whole_statement():
    """A malformed row must abort the WHOLE statement — never a silently
    dropped row (mirrors `eurowag.py`/`e100.py`/`q8.py`'s discipline)."""
    rows = [
        _row(),
        _row(txn_date="2026-06-11", vehicle_ref="Fleet-D-002", net_local="bad-value"),
    ]
    content = synthetic_dkv_statement(rows=rows, seed=13)
    with pytest.raises(ValueError, match="invalid decimal"):
        DKVParser().parse("dkv.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# Deliberate absence of seller-entity detection
# --------------------------------------------------------------------------- #


def test_g3_2_dkv_parse_never_attempts_entity_detection():
    """Like Q8 (and unlike Eurowag/E100), DKV has no R20 worked example —
    `entities` is unconditionally empty and the warning explicitly says
    detection was never attempted (distinct wording from "none found in
    this document")."""
    content = synthetic_dkv_statement(seed=14)
    parsed = DKVParser().parse("dkv.csv", _bytes(content))
    assert parsed.entities == []
    assert len(parsed.warnings) == 1
    assert "not implemented for this network" in parsed.warnings[0]
    assert "no R20 worked marker" in parsed.warnings[0]


def test_g3_2_dkv_parse_entities_stay_empty_even_with_dkv_looking_text_in_the_file():
    """A hand-rolled 'DKV Euro Service'-shaped line elsewhere in the file
    must never be picked up as an entity — DKVParser attempts no such scan
    at all (see module docstring). The VAT id is a fabricated all-nines
    synthetic (pii-allowlisted, never a real registration)."""
    content = synthetic_dkv_statement(seed=15) + "DKV Euro Service GmbH, VAT: SE999999999901\n"
    parsed = DKVParser().parse("dkv.csv", _bytes(content))
    assert parsed.entities == []
