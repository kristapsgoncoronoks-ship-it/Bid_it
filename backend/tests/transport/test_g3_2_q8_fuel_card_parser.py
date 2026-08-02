"""G3.2 slice 3 — the Q8/Kuwait Petroleum fuel-card parser: the list-price
money model (same shape as Eurowag's, NOT E100's reverse-calc), the
per-line country/currency proof (a SINGLE statement spanning two
countries), and the deliberate absence of seller-entity detection. Pure-
function tests (no DB) — see `test_g3_2_q8_statement_ingest.py` for the
DB-touching orchestration tests. Mirrors `test_g3_2_fuel_card_parser.py` /
`test_g3_2_e100_fuel_card_parser.py`'s structure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.transport import fuel_card_parser
from app.services.transport.parsers.e100 import E100Parser
from app.services.transport.parsers.eurowag import EurowagParser
from app.services.transport.parsers.q8 import Q8Parser
from tests.factories.transport import (
    synthetic_e100_statement,
    synthetic_eurowag_statement,
    synthetic_q8_statement,
)


def _bytes(text: str) -> bytes:
    return text.encode("utf-8")


# --------------------------------------------------------------------------- #
# The registry — all three networks co-exist
# --------------------------------------------------------------------------- #


def test_g3_2_registry_ships_eurowag_e100_and_q8():
    # Membership + relative order, not exact-list equality — the WO-62/WO-63
    # registry tests' own future-proof style (an exact list would re-encode
    # "no further network exists" and break on every subsequent G3.2 slice;
    # it did exactly that when WO-65 registered DKV fourth). The CURRENT
    # exact four-parser list is asserted by
    # `test_g3_2_dkv_fuel_card_parser.py::test_g3_2_registry_ships_four_networks_in_order`.
    names = [p.network for p in fuel_card_parser.parsers()]
    assert names.index("Eurowag") < names.index("E100") < names.index("Q8")


def test_g3_2_select_dispatches_all_three_networks_without_crossing():
    q8_content = synthetic_q8_statement(seed=1)
    e100_content = synthetic_e100_statement(seed=1)
    eurowag_content = synthetic_eurowag_statement(seed=1)

    assert fuel_card_parser.select("q8.csv", _bytes(q8_content)).network == "Q8"
    assert fuel_card_parser.select("e100.csv", _bytes(e100_content)).network == "E100"
    assert fuel_card_parser.select("eurowag.csv", _bytes(eurowag_content)).network == "Eurowag"


def test_g3_2_q8_handles_never_matches_eurowag_or_e100_files_and_vice_versa():
    q8_content = synthetic_q8_statement(seed=2)
    e100_content = synthetic_e100_statement(seed=2)
    eurowag_content = synthetic_eurowag_statement(seed=2)

    assert not Q8Parser().handles("x.csv", _bytes(e100_content))
    assert not Q8Parser().handles("x.csv", _bytes(eurowag_content))
    assert not E100Parser().handles("x.csv", _bytes(q8_content))
    assert not EurowagParser().handles("x.csv", _bytes(q8_content))


def test_g3_2_run_dispatches_q8_and_returns_a_parsed_statement():
    content = synthetic_q8_statement(seed=3)
    result = fuel_card_parser.run("q8.csv", _bytes(content))
    assert result.network == "Q8"
    assert len(result.lines) == 2
    assert result.entities == []


# --------------------------------------------------------------------------- #
# Q8Parser.handles()
# --------------------------------------------------------------------------- #


def test_g3_2_q8_handles_a_well_formed_statement():
    content = synthetic_q8_statement(seed=4)
    assert Q8Parser().handles("q8.csv", _bytes(content))


def test_g3_2_q8_does_not_handle_a_file_missing_the_marker():
    content = synthetic_q8_statement(seed=4).replace("Q8 STATEMENT", "SOMETHING ELSE")
    assert not Q8Parser().handles("q8.csv", _bytes(content))


def test_g3_2_q8_does_not_handle_undecodable_bytes():
    assert not Q8Parser().handles("q8.csv", b"\xff\xfe\x00\x01")


def test_g3_2_q8_does_not_match_on_a_partial_incidental_mention():
    """The marker must be the exact first line — text merely containing the
    substring "Q8" elsewhere in the file must not be picked up."""
    body = "some other statement\nQ8 mentioned in passing\ntxn_date,foo\n"
    assert not Q8Parser().handles("q8.csv", _bytes(body))


# --------------------------------------------------------------------------- #
# Q8Parser.parse() — the list-price money model + per-line country/currency
# --------------------------------------------------------------------------- #


def test_g3_2_q8_parse_reads_independently_given_net_vat_gross_like_eurowag():
    """The load-bearing correctness property distinguishing Q8 from E100:
    Q8 supplies net/vat/gross INDEPENDENTLY (list-price invoicing) — no
    reverse-calculation, figures pass through unchanged."""
    rows = [
        {
            "txn_date": "2026-06-15",
            "txn_time": "09:30",
            "vehicle_ref": "Fleet-A-001",
            "station": "Tallinn Hub",
            "country": "ee",
            "product": "DIESEL",
            "qty": "100.000",
            "currency": "eur",
            "net_local": "100.00",
            "vat_local": "20.00",
            "gross_local": "120.00",
            "invoice_ref": "Q8-2026-000123",
        }
    ]
    content = synthetic_q8_statement(rows=rows, seed=5)
    parsed = Q8Parser().parse("q8.csv", _bytes(content))
    assert len(parsed.lines) == 1
    line = parsed.lines[0]

    assert line.net_local == Decimal("100.00")
    assert line.vat_local == Decimal("20.00")
    assert line.gross_local == Decimal("120.00")
    assert line.country == "EE"
    assert line.currency == "EUR"
    assert line.vehicle_ref == "Fleet-A-001"
    assert line.station == "Tallinn Hub"
    assert line.product == "DIESEL"
    assert str(line.qty) == "100.000"
    assert line.invoice_ref == "Q8-2026-000123"
    assert line.txn_time == "09:30"


def test_g3_2_q8_parse_carries_more_than_one_country_and_currency_in_one_statement():
    """Q8's own network-table quirk, taken literally: "per-line country +
    currency" — a SINGLE statement legitimately spans more than one country/
    currency. Neither Eurowag's nor E100's fixtures ever exercised this."""
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "Tallinn Hub",
            "country": "EE",
            "product": "DIESEL",
            "qty": "50",
            "currency": "EUR",
            "net_local": "50.00",
            "vat_local": "10.00",
            "gross_local": "60.00",
            "invoice_ref": "Q8-EE-0001",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "Warsaw Hub",
            "country": "PL",
            "product": "DIESEL",
            "qty": "60",
            "currency": "PLN",
            "net_local": "300.00",
            "vat_local": "69.00",
            "gross_local": "369.00",
            "invoice_ref": "Q8-PL-0001",
        },
    ]
    content = synthetic_q8_statement(rows=rows, seed=6)
    parsed = Q8Parser().parse("q8.csv", _bytes(content))
    assert len(parsed.lines) == 2
    assert {line.country for line in parsed.lines} == {"EE", "PL"}
    assert {line.currency for line in parsed.lines} == {"EUR", "PLN"}


def test_g3_2_q8_parse_line_seq_is_1_based_position_and_stable_on_reparse():
    content = synthetic_q8_statement(seed=7)
    first = Q8Parser().parse("q8.csv", _bytes(content))
    second = Q8Parser().parse("q8.csv", _bytes(content))
    assert [line.line_seq for line in first.lines] == [1, 2]
    assert [line.line_seq for line in second.lines] == [1, 2]


def test_g3_2_q8_parse_raises_on_a_missing_required_column():
    content = synthetic_q8_statement(seed=8).replace("net_local,", "")
    with pytest.raises(ValueError, match="missing required column"):
        Q8Parser().parse("q8.csv", _bytes(content))


def test_g3_2_q8_parse_raises_on_an_unparseable_net_local_decimal():
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "EE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "not-a-number",
            "vat_local": "2.00",
            "gross_local": "12.00",
            "invoice_ref": "",
        }
    ]
    content = synthetic_q8_statement(rows=rows, seed=9)
    with pytest.raises(ValueError, match="invalid decimal"):
        Q8Parser().parse("q8.csv", _bytes(content))


def test_g3_2_q8_parse_raises_on_an_invalid_country_code():
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
            "vat_local": "2.00",
            "gross_local": "12.00",
            "invoice_ref": "",
        }
    ]
    content = synthetic_q8_statement(rows=rows, seed=10)
    with pytest.raises(ValueError, match="invalid country code"):
        Q8Parser().parse("q8.csv", _bytes(content))


def test_g3_2_q8_parse_raises_when_the_statement_carries_no_rows():
    content = synthetic_q8_statement(seed=11)
    lines = content.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("txn_date,"))
    sep_idx = next(i for i, line in enumerate(lines) if line.strip() == "---")
    stripped = "\n".join(lines[: header_idx + 1] + lines[sep_idx:])
    with pytest.raises(ValueError, match="no transaction rows"):
        Q8Parser().parse("q8.csv", _bytes(stripped))


def test_g3_2_q8_malformed_row_aborts_the_whole_statement():
    """A malformed row must abort the WHOLE statement — never a silently
    dropped row (mirrors `eurowag.py`/`e100.py`'s own discipline)."""
    rows = [
        {
            "txn_date": "2026-06-01",
            "txn_time": "",
            "vehicle_ref": "V1",
            "station": "S1",
            "country": "EE",
            "product": "DIESEL",
            "qty": "10",
            "currency": "EUR",
            "net_local": "10.00",
            "vat_local": "2.00",
            "gross_local": "12.00",
            "invoice_ref": "",
        },
        {
            "txn_date": "2026-06-02",
            "txn_time": "",
            "vehicle_ref": "V2",
            "station": "S2",
            "country": "PL",
            "product": "DIESEL",
            "qty": "5",
            "currency": "PLN",
            "net_local": "bad-value",  # malformed
            "vat_local": "2.00",
            "gross_local": "12.00",
            "invoice_ref": "",
        },
    ]
    content = synthetic_q8_statement(rows=rows, seed=12)
    with pytest.raises(ValueError, match="invalid decimal"):
        Q8Parser().parse("q8.csv", _bytes(content))


# --------------------------------------------------------------------------- #
# Deliberate absence of seller-entity detection
# --------------------------------------------------------------------------- #


def test_g3_2_q8_parse_never_attempts_entity_detection():
    """Unlike Eurowag/E100, Q8 has no R20 worked example — `entities` is
    unconditionally empty and the warning explicitly says detection was
    never attempted (distinct wording from "none found in this document")."""
    content = synthetic_q8_statement(seed=13)
    parsed = Q8Parser().parse("q8.csv", _bytes(content))
    assert parsed.entities == []
    assert len(parsed.warnings) == 1
    assert "not implemented for this network" in parsed.warnings[0]
    assert "no R20 worked marker" in parsed.warnings[0]


def test_g3_2_q8_parse_entities_stay_empty_even_with_kp_looking_text_in_the_file():
    """A hand-rolled 'Kuwait Petroleum'-shaped line elsewhere in the file
    must never be picked up as an entity — Q8Parser attempts no such scan
    at all (see module docstring)."""
    content = synthetic_q8_statement(seed=14) + "Kuwait Petroleum International, VAT: EE912345678\n"
    parsed = Q8Parser().parse("q8.csv", _bytes(content))
    assert parsed.entities == []
