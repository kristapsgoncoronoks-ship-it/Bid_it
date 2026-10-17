"""Canonical AR/display decisions survive SQL projection and cent boundaries."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import product

import pytest
from sqlalchemy import select

from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.services import issued_status as status


@pytest.mark.asyncio
async def test_sql_selection_matches_canonical_status_for_cent_and_lifecycle_edges(
    auth_client, db_session
):
    org = await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))
    today = date(2026, 10, 16)
    stamp = datetime(2026, 10, 16, tzinfo=UTC)
    scenarios = []
    for total, paid, credited, due, delivery in product(
        ("0.00", "0.03", "10.00"),
        ("0.00", "0.01", "10.00"),
        ("0.00", "0.02", "10.00"),
        (None, today - timedelta(days=1), today + timedelta(days=1)),
        ("none", "sent", "viewed"),
    ):
        scenarios.append(
            dict(
                total=Decimal(total),
                amount_paid=Decimal(paid),
                credited_total=Decimal(credited),
                due_date=due,
                sent_at=stamp if delivery == "sent" else None,
                viewed_at=stamp if delivery == "viewed" else None,
            )
        )
    for lifecycle in ("draft", "approved", "cancelled", "written_off", "disputed"):
        scenarios.append(
            dict(
                lifecycle=lifecycle,
                total=Decimal("10"),
                amount_paid=Decimal("10"),
                due_date=today - timedelta(days=1),
            )
        )
    scenarios.append(dict(doc_type="credit_note", total=Decimal("10"), amount_paid=Decimal("0")))
    scenarios.append(dict(voided_at=stamp, total=Decimal("10"), amount_paid=Decimal("10")))
    rows = []
    for index, values in enumerate(scenarios):
        row = IssuedInvoice(
            org_id=org,
            number=f"SYNTH-PARITY-{index}",
            buyer_name="Synthetic buyer",
            seller_json="{}",
            issue_date=today,
            **values,
        )
        db_session.add(row)
        rows.append(row)
    await db_session.commit()
    # Reload Numeric values at their persisted precision, as every consumer does.
    db_session.expire_all()
    records = list(
        await db_session.scalars(select(IssuedInvoice).where(IssuedInvoice.org_id == org))
    )
    expected = {
        row.id: (status.status_of(row, today), status.ar_status_of(row, today)) for row in records
    }
    actual = {
        identifier: (display, ar)
        for identifier, display, ar in await db_session.execute(
            select(
                IssuedInvoice.id, status.sql_status(today), status.sql_status(today, delivery=False)
            ).where(IssuedInvoice.org_id == org)
        )
    }
    assert actual == expected
    assert {value[0] for value in actual.values()} >= {
        "paid",
        "open",
        "partial",
        "overdue",
        "credited",
        "credit_note",
        "void",
        "viewed",
        "sent",
        "draft",
        "approved",
        "disputed",
        "written_off",
    }
