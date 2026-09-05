"""PERF-002/003 (audit 2026-09-05) — the dashboard's three reductions are done
in SQL, and they agree with the Python rules they replaced.

`ap_aging.summarize(worklist())`, `cash_position._ap_summary` and
`issued_reports.receivables` each hydrated every relevant row and reduced it
in Python for a handful of scalars, on the single most-requested endpoint. The
perf harness reported them flat because it seeded no payable state and no
issued invoice (PERF-004); once it did, two of them grew past their ceilings.

The Python implementations are kept — `worklist`/`summarize` for the worklist
screen, `receivables()` for the AR report — and used HERE as the oracle: each
new SQL function must equal the old Python on a dataset that covers every
branch of `ap_status.status_of`, `issued_status.ar_status_of` and
`outstanding_of` (lifecycles, credit notes, credited totals, partials, every
aging band, several currencies). Finally, the dashboard's query count must be
the same at 12 rows as at 120 — the property the whole change exists for.

One BEHAVIOUR CHANGE is asserted rather than hidden: cash position's payables
amounts are now in the report currency only, with the others named — the old
code summed PLN and EUR into one figure labelled with the AR currency.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app.models.base import new_uuid
from app.models.invoice import Invoice, InvoiceStatus, WorkflowState
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.models.vendor import Vendor
from app.services import ap_aging, cash_position, issued_reports

TODAY = date(2026, 9, 5)


async def _org(db_session) -> str:
    return await db_session.scalar(select(Organization.id).limit(1))


def _ap(
    org_id,
    vendor_id,
    n,
    *,
    due,
    total="100.00",
    paid="0.00",
    currency="EUR",
    state=WorkflowState.approved,
    run=None,
):
    return Invoice(
        id=new_uuid(),
        org_id=org_id,
        vendor_id=vendor_id,
        invoice_number=f"AP-{n:03d}",
        issue_date=TODAY - timedelta(days=60),
        due_date=due,
        currency=currency,
        subtotal=Decimal(total),
        tax_amount=Decimal("0"),
        total=Decimal(total),
        status=InvoiceStatus.pending,
        workflow_state=state,
        amount_paid=Decimal(paid),
        payment_run_id=run,
    )


async def _seed_ap(db_session, org_id, *, reps: int = 1) -> None:
    vendor = Vendor(org_id=org_id, name=f"Perf Vendor {reps}")
    db_session.add(vendor)
    await db_session.flush()
    n = 0
    for _ in range(reps):
        rows = [
            # EUR: overdue partial, due-soon, later, fully paid but still approved
            # with a due date in the window (status PAID → not overdue; bucket is
            # date-based → due_soon with outstanding 0), no due date, zero total.
            _ap(org_id, vendor.id, n + 1, due=TODAY - timedelta(days=12), paid="40.00"),
            _ap(org_id, vendor.id, n + 2, due=TODAY + timedelta(days=3)),
            _ap(org_id, vendor.id, n + 3, due=TODAY + timedelta(days=40)),
            _ap(org_id, vendor.id, n + 4, due=TODAY + timedelta(days=2), paid="100.00"),
            _ap(org_id, vendor.id, n + 5, due=None, paid="10.00"),
            _ap(org_id, vendor.id, n + 6, due=TODAY - timedelta(days=5), total="0.00"),
            # scheduled + in a run, overdue; partially paid state, due today
            _ap(
                org_id,
                vendor.id,
                n + 7,
                due=TODAY - timedelta(days=1),
                state=WorkflowState.scheduled_for_payment,
                run=new_uuid(),
            ),
            _ap(
                org_id,
                vendor.id,
                n + 8,
                due=TODAY,
                paid="25.00",
                state=WorkflowState.partially_paid,
            ),
            # PLN: two overdue, one due soon — a second currency competing for the pick
            _ap(
                org_id,
                vendor.id,
                n + 9,
                due=TODAY - timedelta(days=45),
                currency="PLN",
                total="800.00",
            ),
            _ap(
                org_id,
                vendor.id,
                n + 10,
                due=TODAY - timedelta(days=100),
                currency="PLN",
                total="250.00",
            ),
            _ap(
                org_id,
                vendor.id,
                n + 11,
                due=TODAY + timedelta(days=7),
                currency="PLN",
                total="99.99",
            ),
            # USD due soon; and two rows the payable filter must exclude
            _ap(org_id, vendor.id, n + 12, due=TODAY + timedelta(days=1), currency="USD"),
            _ap(
                org_id, vendor.id, n + 13, due=TODAY - timedelta(days=9), state=WorkflowState.draft
            ),
            _ap(
                org_id,
                vendor.id,
                n + 14,
                due=TODAY - timedelta(days=9),
                state=WorkflowState.paid,
                paid="100.00",
            ),
        ]
        db_session.add_all(rows)
        n += len(rows)
    await db_session.commit()


def _ar(
    org_id,
    n,
    *,
    due,
    total="100.00",
    paid="0.00",
    credited="0.00",
    lifecycle="issued",
    doc_type="invoice",
    currency="EUR",
    paid_date=None,
    voided=False,
    issue_days_ago=30,
):
    inv = IssuedInvoice(
        id=new_uuid(),
        org_id=org_id,
        number=f"AR-{n:03d}-{new_uuid()[:6]}",  # unique per seed pass (org, number) is UNIQUE
        issue_date=TODAY - timedelta(days=issue_days_ago),
        due_date=due,
        currency=currency,
        buyer_name="Site Crew OU",
        seller_json=json.dumps({"legal_name": "Haulage Co"}),
        subtotal=Decimal(total),
        tax_total=Decimal("0"),
        total=Decimal(total),
        amount_paid=Decimal(paid),
        credited_total=Decimal(credited),
        lifecycle=lifecycle,
        doc_type=doc_type,
        paid_date=paid_date,
    )
    if voided:
        from datetime import UTC, datetime

        inv.voided_at = datetime(2026, 9, 1, tzinfo=UTC)
    return inv


async def _seed_ar(db_session, org_id, *, reps: int = 1) -> None:
    n = 0
    for _ in range(reps):
        rows = [
            _ar(org_id, n + 1, due=TODAY + timedelta(days=10)),  # open, Current
            _ar(org_id, n + 2, due=TODAY, paid="30.00"),  # partial, due today → Current
            _ar(
                org_id, n + 3, due=TODAY - timedelta(days=10), paid="30.00"
            ),  # overdue partial, 1–30
            _ar(org_id, n + 4, due=TODAY - timedelta(days=45)),  # overdue, 31–60
            _ar(org_id, n + 5, due=TODAY - timedelta(days=75)),  # overdue, 61–90
            _ar(org_id, n + 6, due=TODAY - timedelta(days=120), total="1000.00"),  # overdue, 90+
            _ar(
                org_id,
                n + 7,
                due=TODAY - timedelta(days=30),
                paid="100.00",
                paid_date=TODAY - timedelta(days=12),
                issue_days_ago=40,
            ),  # PAID, DSO 28
            _ar(
                org_id,
                n + 8,
                due=TODAY - timedelta(days=5),
                paid="100.00",
                paid_date=TODAY - timedelta(days=1),
                issue_days_ago=20,
            ),  # PAID, DSO 19
            _ar(org_id, n + 9, due=TODAY - timedelta(days=20), credited="40.00"),  # overdue, eff 60
            _ar(org_id, n + 10, due=TODAY - timedelta(days=20), credited="100.00"),  # CREDITED
            _ar(
                org_id, n + 11, due=TODAY - timedelta(days=20), credited="100.00", paid="20.00"
            ),  # over-credited, settled → PAID (no paid_date)
            _ar(
                org_id, n + 12, due=TODAY - timedelta(days=50), lifecycle="disputed"
            ),  # DISPUTED: outstanding counts, not overdue
            _ar(
                org_id, n + 13, due=TODAY - timedelta(days=50), lifecycle="written_off"
            ),  # owes nothing
            _ar(
                org_id, n + 14, due=TODAY - timedelta(days=50), lifecycle="cancelled"
            ),  # not reportable
            _ar(
                org_id, n + 15, due=TODAY - timedelta(days=50), lifecycle="draft"
            ),  # not reportable
            _ar(org_id, n + 16, due=TODAY - timedelta(days=50), voided=True),  # not reportable
            _ar(
                org_id,
                n + 17,
                due=TODAY - timedelta(days=50),
                doc_type="credit_note",
                total="40.00",
            ),  # never a receivable
            _ar(org_id, n + 18, due=None, paid="10.00"),  # partial, no due date → no aging
            _ar(
                org_id, n + 19, due=TODAY - timedelta(days=3), currency="USD", total="999.00"
            ),  # other currency
        ]
        db_session.add_all(rows)
        n += len(rows)
    await db_session.commit()


@pytest.mark.asyncio
async def test_due_summary_in_sql_equals_summarize_of_the_worklist(auth_client, db_session):
    org_id = await _org(db_session)
    await _seed_ap(db_session, org_id)
    via_sql = await ap_aging.due_summary(db_session, org_id, TODAY)
    via_python = ap_aging.summarize(await ap_aging.worklist(db_session, org_id, TODAY))
    assert via_sql == via_python
    # The dataset exercised what it claims to: several currencies, both bands.
    assert via_python.overdue_count >= 4 and via_python.due_soon_count >= 4
    assert via_python.other_currencies, "no competing currency — the pick was not tested"


@pytest.mark.asyncio
async def test_receivables_scalars_in_sql_equal_the_python_report(auth_client, db_session):
    org_id = await _org(db_session)
    await _seed_ar(db_session, org_id)
    scalars = await issued_reports.receivables_scalars(db_session, org_id, today=TODAY)
    report = await issued_reports.receivables(db_session, org_id, None, None, None, today=TODAY)
    assert scalars.currency == report.currency == "EUR"
    assert scalars.total_outstanding == report.total_outstanding
    assert scalars.overdue_outstanding == report.overdue_outstanding
    assert scalars.avg_days_to_pay == report.avg_days_to_pay
    assert scalars.aging == report.aging
    # And the dataset really reached every band and both DSO rows.
    assert [b.count for b in report.aging] == [2, 1, 1, 1, 2] or all(
        b.count > 0 for b in report.aging
    )
    assert report.avg_days_to_pay == 23.5


@pytest.mark.asyncio
async def test_cash_position_payables_are_in_the_report_currency_with_the_rest_named(
    auth_client, db_session
):
    """The stated behaviour change: PLN and USD payables are no longer added into
    an EUR figure; they are named."""
    org_id = await _org(db_session)
    await _seed_ap(db_session, org_id)
    await _seed_ar(db_session, org_id)
    pos = await cash_position.summary(db_session, org_id, TODAY)
    assert pos["currency"] == "EUR"
    ap = pos["payables"]
    # EUR open payables: 60 (partial) + 100 + 100 + 0 (paid) + 90 + 0 + 100 (scheduled) + 75 = 525
    assert ap["outstanding"] == Decimal("525.00")
    # EUR overdue: 60 (12 days) + 0 (zero total, not > 0 → excluded by outstanding) + 100 (scheduled, 1 day) = 160
    assert ap["overdue"] == Decimal("160.00")
    assert ap["other_currencies"] == ["PLN", "USD"]
    assert ap["count"] == 12  # counts span every currency; draft/paid excluded
    assert ap["scheduled"] == 1 and ap["in_run"] == 1
    # And the AR half matches the report's own figures.
    rep = await issued_reports.receivables(db_session, org_id, None, None, None, today=TODAY)
    assert pos["receivables"]["outstanding"] == rep.total_outstanding
    assert pos["net_position"] == rep.total_outstanding - Decimal("525.00")


@pytest.mark.asyncio
async def test_dashboard_query_count_does_not_grow_with_the_data(auth_client, db_session):
    """The property the change exists for: GET /dashboard issues the same number
    of SQL statements over 14 payables + 19 receivables as over ten times that."""
    org_id = await _org(db_session)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    engine = db_session.bind
    sync_engine = getattr(engine, "sync_engine", engine)
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        await _seed_ap(db_session, org_id, reps=1)
        await _seed_ar(db_session, org_id, reps=1)
        statements.clear()
        r1 = await auth_client.get("/api/v1/dashboard")
        assert r1.status_code == 200, r1.text
        small = len(statements)

        await _seed_ap(db_session, org_id, reps=9)
        await _seed_ar(db_session, org_id, reps=9)
        statements.clear()
        r2 = await auth_client.get("/api/v1/dashboard")
        assert r2.status_code == 200, r2.text
        large = len(statements)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert small == large, f"dashboard issued {small} statements at 1× and {large} at 10× the rows"
    assert r2.json()["payables"]["overdue_count"] > r1.json()["payables"]["overdue_count"]
