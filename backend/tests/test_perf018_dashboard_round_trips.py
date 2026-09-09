"""PERF-018 (audit 2026-09-05, remedied 2026-09-09) — the composed dashboard
stops paying for figures it never renders.

CONC-001 measured the dashboard as the one endpoint whose concurrent p95 sits
far above a queue's, and traced the cost to the request's own Python work
rather than to the connection pool. This is the round-trip half of that
remedy, and what it found is narrower and worse than "too many statements":

* With the `issuing` module ON — the state of any workspace that raises sales
  invoices — the request ran the canonical receivables report **twice**: once
  for the receivables card, and once more inside `cash_position.summary`,
  which the dashboard called for four of its twenty-odd figures. Four
  statements against `issued_invoices`, executed and reduced, then discarded.
* Of `summary()`'s remaining work the dashboard rendered nothing: the AR aging
  bands, the payable count / scheduled / in-run / other-currency list, and the
  whole bank-reconciliation roll-up, which is two more statements against
  `bank_lines`.
* The perf harness never saw any of it, because `issuing` is default-off and
  the harness only enabled `transport`: every run since PERF-003 measured a
  dashboard with its receivables card skipped. Same blind spot as PERF-004,
  where the harness seeded no payable state.

So the tests below hold the SHAPE (how many statements, how many times the AR
read runs) and the NUMBERS (the narrow read agrees with the full roll-up, field
by field, on the PERF-002 dataset that covers every lifecycle, credit notes,
partial payments, several currencies). A shape claim without the numbers beside
it would let the next optimisation quietly change what the card says.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.services import cash_position, issued_reports
from tests.test_perf002_dashboard_aggregates_in_sql import (
    TODAY,
    _org,
    _seed_ap,
    _seed_ar,
)


def _statement_recorder(db_session):
    """(listen, remove, statements) over the session's own engine."""
    engine = db_session.bind
    sync_engine = getattr(engine, "sync_engine", engine)
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    return sync_engine, _record, statements


async def _full_dashboard_org(auth_client, db_session) -> str:
    """A workspace in the shape the finding is about: the owner (every
    permission), the two default-off modules the dashboard branches on, and
    real payables and receivables."""
    org_id = await _org(db_session)
    for key in ("issuing", "expenses"):
        r = await auth_client.put(f"/api/v1/modules/{key}", json={"enabled": True})
        assert r.status_code == 200, r.text
    await _seed_ap(db_session, org_id)
    await _seed_ar(db_session, org_id)
    return org_id


@pytest.mark.asyncio
async def test_the_receivables_report_is_read_once_per_dashboard_request(
    auth_client, db_session, monkeypatch
):
    """The defect itself: with `issuing` on, the canonical AR read ran twice —
    once for the receivables card, once inside the cash roll-up. It is now one
    read shared by both cards, and the aging bands it never renders are not
    queried at all."""
    await _full_dashboard_org(auth_client, db_session)
    calls: list[bool] = []
    real = issued_reports.receivables_scalars

    async def spy(db, org_id, today=None, *, with_aging=True):
        calls.append(with_aging)
        return await real(db, org_id, today, with_aging=with_aging)

    monkeypatch.setattr(issued_reports, "receivables_scalars", spy)
    r = await auth_client.get("/api/v1/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receivables"] is not None and body["cash"] is not None
    assert calls == [False], f"the AR report ran {len(calls)} time(s): {calls}"


@pytest.mark.asyncio
async def test_the_dashboard_never_reads_the_reconciliation_or_the_aging_bands(
    auth_client, db_session
):
    """Two whole reads the composed endpoint used to make and never show: the
    bank-reconciliation roll-up (`bank_lines`) and the AR aging bands."""
    await _full_dashboard_org(auth_client, db_session)
    sync_engine, recorder, statements = _statement_recorder(db_session)
    event.listen(sync_engine, "before_cursor_execute", recorder)
    try:
        r = await auth_client.get("/api/v1/dashboard")
    finally:
        event.remove(sync_engine, "before_cursor_execute", recorder)
    assert r.status_code == 200, r.text
    assert not [s for s in statements if "bank_lines" in s], statements
    # The band query is the only one that GROUPs BY a CASE. The first version of
    # this line looked for the label `GROUP BY band`, which SQLAlchemy never
    # emits — it renders the CASE expression itself — so the assertion could not
    # fail and the guard named for the aging bands guarded nothing.
    grouped_by_case = [s for s in statements if "GROUP BY CASE" in s.upper()]
    assert not grouped_by_case, grouped_by_case


@pytest.mark.asyncio
async def test_the_full_dashboard_shape_is_a_ratchet(auth_client, db_session):
    """The round-trip budget for the request in its LARGEST shape — every
    permission, both default-off modules on, both sides populated. The bound
    only ever moves down; it was 24 before this batch. `test_dashboard.py`'s
    own ratchet covers the same request for loader options."""
    await _full_dashboard_org(auth_client, db_session)
    sync_engine, recorder, statements = _statement_recorder(db_session)
    event.listen(sync_engine, "before_cursor_execute", recorder)
    try:
        r = await auth_client.get("/api/v1/dashboard")
    finally:
        event.remove(sync_engine, "before_cursor_execute", recorder)
    assert r.status_code == 200, r.text
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert 0 < len(selects) <= 16, [s[:70] for s in selects]


@pytest.mark.asyncio
async def test_the_narrow_cash_read_equals_the_full_roll_up(auth_client, db_session):
    """The numbers, not just the shape: `net_figures` is `summary()`'s four
    published figures and nothing else — same currency pick, same AR
    outstanding, same AP outstanding in that currency, same net."""
    org_id = await _org(db_session)
    await _seed_ap(db_session, org_id)
    await _seed_ar(db_session, org_id)
    full = await cash_position.summary(db_session, org_id, TODAY)
    narrow = await cash_position.net_figures(db_session, org_id, TODAY)
    assert narrow == {
        "currency": full["currency"],
        "receivables_outstanding": full["receivables"]["outstanding"],
        "payables_outstanding": full["payables"]["outstanding"],
        "net_position": full["net_position"],
    }
    # The dataset is the one that matters: a real payable figure, not zero.
    assert narrow["payables_outstanding"] == Decimal("525.00")
    assert narrow["net_position"] != Decimal("0.00")


@pytest.mark.asyncio
async def test_the_narrow_cash_read_accepts_the_receivables_the_caller_already_has(
    auth_client, db_session
):
    """The mechanism that removes the second read: handing in the AR scalars
    must not change a figure, and must not read them again."""
    org_id = await _org(db_session)
    await _seed_ap(db_session, org_id)
    await _seed_ar(db_session, org_id)
    rep = await issued_reports.receivables_scalars(db_session, org_id, TODAY, with_aging=False)

    sync_engine, recorder, statements = _statement_recorder(db_session)
    event.listen(sync_engine, "before_cursor_execute", recorder)
    try:
        passed_in = await cash_position.net_figures(db_session, org_id, TODAY, receivables=rep)
    finally:
        event.remove(sync_engine, "before_cursor_execute", recorder)
    assert passed_in == await cash_position.net_figures(db_session, org_id, TODAY)
    assert not [s for s in statements if "issued_invoices" in s], statements
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


@pytest.mark.asyncio
async def test_bands_not_asked_for_are_none_and_never_an_empty_list(auth_client, db_session):
    """`aging=None` means NOT COMPUTED. Returning `[]` would let a caller that
    forgot which it asked for render a screen of confident zeros — the FE-004
    failure mode, one layer down."""
    org_id = await _org(db_session)
    await _seed_ar(db_session, org_id)
    with_bands = await issued_reports.receivables_scalars(db_session, org_id, TODAY)
    without = await issued_reports.receivables_scalars(db_session, org_id, TODAY, with_aging=False)
    assert with_bands.aging is not None and any(b.count for b in with_bands.aging)
    assert without.aging is None
    # Everything else is identical — the flag drops a query, not a figure.
    assert (without.currency, without.total_outstanding, without.overdue_outstanding) == (
        with_bands.currency,
        with_bands.total_outstanding,
        with_bands.overdue_outstanding,
    )
    assert without.avg_days_to_pay == with_bands.avg_days_to_pay


@pytest.mark.asyncio
async def test_the_dashboard_cards_still_say_what_the_canonical_reports_say(
    auth_client, db_session
):
    """End to end: after the composition changed, both cards still carry the
    canonical services' own numbers."""
    org_id = await _full_dashboard_org(auth_client, db_session)
    r = await auth_client.get("/api/v1/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    # The route's own `as_of`, not a second `date.today()`: a run crossing UTC
    # midnight would otherwise compare two different report bases.
    today = date.fromisoformat(body["as_of"])
    rep = await issued_reports.receivables_scalars(db_session, org_id, today)
    pos = await cash_position.summary(db_session, org_id, today)
    assert body["receivables"]["currency"] == rep.currency
    assert Decimal(body["receivables"]["outstanding"]) == rep.total_outstanding
    assert Decimal(body["receivables"]["overdue"]) == rep.overdue_outstanding
    assert body["receivables"]["avg_days_to_pay"] == rep.avg_days_to_pay
    assert body["cash"]["currency"] == pos["currency"]
    assert Decimal(body["cash"]["receivables_outstanding"]) == pos["receivables"]["outstanding"]
    assert Decimal(body["cash"]["payables_outstanding"]) == pos["payables"]["outstanding"]
    assert Decimal(body["cash"]["net_position"]) == pos["net_position"]


@pytest.mark.asyncio
async def test_a_workspace_without_the_issuing_module_still_gets_its_cash_card(
    auth_client, db_session
):
    """The cash card is gated on REPORT_READ, the receivables card additionally
    on the issuing module — so the AR read still has to happen for the first
    when the second is off. The one read serves whichever card survived."""
    org_id = await _org(db_session)
    await _seed_ap(db_session, org_id)
    await _seed_ar(db_session, org_id)
    r = await auth_client.get("/api/v1/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receivables"] is None, "issuing is default-off"
    assert body["cash"] is not None
    pos = await cash_position.summary(db_session, org_id, date.fromisoformat(body["as_of"]))
    assert Decimal(body["cash"]["net_position"]) == pos["net_position"]
