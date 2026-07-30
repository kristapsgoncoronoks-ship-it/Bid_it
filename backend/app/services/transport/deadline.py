"""The hard period-end gate + the 30-Sep filing-deadline risk scanner
(G2.6 slice; R7/R9; `BA_fleet_fuel.md` A4/A5).

**This is the single highest-risk rule in the entire M3 plan.** CJEU
C-294/11 *Elsacom*: missing the 30-September-of-year+1 filing deadline
PERMANENTLY forfeits the refund right — `ARCH_plan.md`'s own risk register
scores this L-1 at 6 (the maximum), and M3's north-star KPI is literally
"deadline misses = 0."

WHY `period_ended` IS A HARD SUBMISSION GATE, BUT `deadline_status` IS NOT
------------------------------------------------------------------------------
R7 ("a claim cannot be filed before the period has fully closed") is a real
LEGAL prerequisite — Art. 5 Dir. 2008/9/EC constructs the refund period as a
closed accounting window; filing mid-period is invalid on its face, so
`lock.submit_claim` refuses it (`code="period_not_ended"`).

R9 ("surfaced with a 60-day risk window") is DELIBERATELY informational, not
a submission gate: this software refusing to even ATTEMPT a late filing
would be strictly worse than trying — the refunding tax authority, not this
codebase, is what actually enforces the time-bar, and a claim already past
its own deadline might still be worth submitting (interest/appeal
considerations are Directive Arts. 19-22/26-27 territory, G4.9, future
work). `deadline_status`/`filing_deadline` exist as pure functions any
future consumer (a dashboard, G4.3; a worklist; the checklist, G2.10) can
call — this order does not wire them into `submit_claim`'s pass/fail path.
"""

from __future__ import annotations

from datetime import date

# A4 (`vat_refund.approaching_deadlines(within_days=60)`) — inside this many
# days of 30-Sep-year+1, a claim is "deadline risk".
DEADLINE_RISK_DAYS = 60

# (month, day) of the LAST day of each quarter — Art. 16's period shapes are
# always a calendar quarter or the calendar year, so every quarter ends on a
# fixed, non-leap-year-sensitive date (Mar/Jun/Sep/Dec all end on the same
# day number every year).
_QUARTER_END: dict[str, tuple[int, int]] = {
    "1": (3, 31),
    "2": (6, 30),
    "3": (9, 30),
    "4": (12, 31),
}


def period_end_date(ref_period: str) -> date:
    """The last calendar day of `ref_period` — `"YYYY-Qn"` ends at the
    quarter's last day, `"YYYY-YEAR"` ends 31 December. Trusts the shape is
    already valid (enforced by `claim.validate_ref_period` + the DB CHECK
    constraint at claim creation), matching `claim_lines.period_months`'s
    own no-re-validation convention."""
    year_s, tail = ref_period.split("-", 1)
    year = int(year_s)
    if tail == "YEAR":
        return date(year, 12, 31)
    month, day = _QUARTER_END[tail[1]]
    return date(year, month, day)


def period_ended(ref_period: str, *, today: date | None = None) -> bool:
    """R7 — `today > period_end_date(ref_period)`. The period-end DAY itself
    does not yet count as "ended" (a claim cannot be filed ON its own last
    day either, matching Fleet Fuel's own strict `>` comparison, not `>=`)."""
    today = today if today is not None else date.today()
    return today > period_end_date(ref_period)


def filing_deadline(ref_period: str) -> date:
    """Art. 15: 30 September of the year FOLLOWING the refund period's OWN
    year — a fatal time-bar (CJEU C-294/11 *Elsacom*), the same date
    regardless of whether the period is a quarter or the full year (a Q1
    claim and a full-YEAR claim for the same year share one deadline)."""
    year_s, _tail = ref_period.split("-", 1)
    return date(int(year_s) + 1, 9, 30)


def deadline_status(ref_period: str, *, today: date | None = None) -> str:
    """`"ok"` / `"at_risk"` / `"overdue"` — informational only, see module
    docstring for why this is never wired into a submission gate. `"at_risk"`
    is inclusive of the boundary day (exactly `DEADLINE_RISK_DAYS` days out
    counts as at-risk, not merely fewer)."""
    today = today if today is not None else date.today()
    deadline = filing_deadline(ref_period)
    if today > deadline:
        return "overdue"
    if (deadline - today).days <= DEADLINE_RISK_DAYS:
        return "at_risk"
    return "ok"
