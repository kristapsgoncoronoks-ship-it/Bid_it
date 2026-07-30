"""G2.6 slice 1 — the hard period-end gate (R7) + the 30-Sep deadline risk
scanner (R9), `BA_fleet_fuel.md` A4/A5. See `app.services.transport.
deadline`'s module docstring for why period-end is a hard gate but the
deadline scanner is deliberately NOT one.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.transport import deadline


@pytest.mark.parametrize(
    "ref_period,expected",
    [
        ("2026-Q1", date(2026, 3, 31)),
        ("2026-Q2", date(2026, 6, 30)),
        ("2026-Q3", date(2026, 9, 30)),
        ("2026-Q4", date(2026, 12, 31)),
        ("2026-YEAR", date(2026, 12, 31)),
    ],
)
def test_g2_6_period_end_date_matches_the_period_shape(ref_period, expected):
    assert deadline.period_end_date(ref_period) == expected


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 6, 29), False),  # before period end
        (date(2026, 6, 30), False),  # ON the period-end day itself — not yet ended
        (date(2026, 7, 1), True),  # the day after — ended
    ],
)
def test_g2_6_period_ended_is_strictly_after_period_end_date(today, expected):
    assert deadline.period_ended("2026-Q2", today=today) is expected


@pytest.mark.parametrize(
    "ref_period,expected_deadline",
    [
        ("2026-Q1", date(2027, 9, 30)),
        ("2026-Q4", date(2027, 9, 30)),
        ("2026-YEAR", date(2027, 9, 30)),
        ("2025-Q4", date(2026, 9, 30)),
    ],
)
def test_g2_6_filing_deadline_is_30_sep_of_year_plus_1(ref_period, expected_deadline):
    assert deadline.filing_deadline(ref_period) == expected_deadline


def test_g2_6_deadline_status_at_risk_window_is_60_days():
    dl = deadline.filing_deadline("2025-Q4")  # 2026-09-30
    assert dl == date(2026, 9, 30)

    # 61 days out — still ok.
    assert deadline.deadline_status("2025-Q4", today=date(2026, 7, 31)) == "ok"
    # Exactly 60 days out (2026-08-01) — at_risk (inclusive boundary).
    assert deadline.deadline_status("2025-Q4", today=date(2026, 8, 1)) == "at_risk"
    # On the deadline day itself — still at_risk, not overdue.
    assert deadline.deadline_status("2025-Q4", today=date(2026, 9, 30)) == "at_risk"
    # The day after — overdue.
    assert deadline.deadline_status("2025-Q4", today=date(2026, 10, 1)) == "overdue"


def test_g2_6_deadline_status_defaults_to_todays_real_date():
    # Just a smoke test that the no-args path doesn't blow up and returns a
    # recognised status.
    assert deadline.deadline_status("2020-Q1") == "overdue"
