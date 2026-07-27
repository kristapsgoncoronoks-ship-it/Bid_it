"""Shared CSV/Excel formula-injection helper (board R1) — pure unit tests."""

from __future__ import annotations

from decimal import Decimal

from app.core import csv_safety


def test_every_trigger_character_gets_a_leading_quote():
    for trigger in csv_safety.FORMULA_TRIGGERS:
        out = csv_safety.sanitize_cell(f"{trigger}EVIL")
        assert out == "'" + trigger + "EVIL"
        assert out.startswith("'" + trigger)


def test_none_becomes_empty_string():
    assert csv_safety.sanitize_cell(None) == ""


def test_ordinary_string_is_untouched():
    assert csv_safety.sanitize_cell("Acme Logistics") == "Acme Logistics"
    assert csv_safety.sanitize_cell("A-1 Logistics") == "A-1 Logistics"  # '-' not at index 0


def test_string_starting_with_trigger_mid_word_is_still_neutralised():
    # A real-world payload doesn't have to look obviously malicious.
    assert csv_safety.sanitize_cell('=HYPERLINK("http://evil","click")').startswith("'=")
    assert csv_safety.sanitize_cell("@SUM(A1:A9)").startswith("'@")


def test_non_str_values_round_trip_via_str():
    assert csv_safety.sanitize_cell(42) == "42"
    assert csv_safety.sanitize_cell(Decimal("100.00")) == "100.00"
    # A negative Decimal legitimately starts with '-' — the caller decides
    # whether a numeric cell should ever reach this function at all (it
    # should not, for a money column), but the helper itself is a pure
    # string-prefix transform with no special-casing for "looks numeric".
    assert csv_safety.sanitize_cell(Decimal("-12.50")) == "'-12.50"
