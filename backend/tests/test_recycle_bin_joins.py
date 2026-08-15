"""Does the hiding guard reach a JOIN whose aggregates are over the CHILD table?

The step-1 suite proved a column-only `select(Invoice.id)` is covered. This is a
genuinely different shape and the one the spend figures actually use:

    select(func.sum(LineItem.amount)).select_from(LineItem)
        .join(Invoice, Invoice.id == LineItem.invoice_id)

`LineItem` is in neither TENANT_MODELS nor SOFT_DELETE_MODELS. Nothing in the
columns clause mentions `Invoice` at all — it appears only in an explicit ON
clause. If `with_loader_criteria` does not reach there, every category total,
budget actual, benchmark figure and analytics number in the product silently
includes deleted spend for 30 days — which is precisely the silent-wrong-numbers
failure the mechanism was built to prevent, arriving through the one query shape
nobody probed.

This test exists to answer that question with a fact rather than an assumption.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core import tenant
from app.models.invoice import Invoice, LineItem


async def _invoice(auth_client, number: str, amount: str) -> str:
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Fictional Fuels OU",
            "invoice_number": number,
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Diesel",
                    "quantity": "1",
                    "unit_price": amount,
                    "amount": amount,
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_a_binned_invoices_LINE_ITEMS_are_excluded_from_a_joined_aggregate(
    auth_client, db_session
):
    live = await _invoice(auth_client, "INV-JOIN-LIVE", "100.00")
    binned = await _invoice(auth_client, "INV-JOIN-BINNED", "500.00")

    with tenant.include_deleted():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == binned))
    inv.deleted_at = datetime.now(UTC)
    await db_session.commit()

    total = await db_session.scalar(
        select(func.sum(LineItem.amount))
        .select_from(LineItem)
        .join(Invoice, Invoice.id == LineItem.invoice_id)
    )

    assert live  # the live one is what should remain in the figure
    assert float(total or 0) == 100.0, (
        "a joined aggregate over LINE ITEMS counted a binned invoice's spend — "
        "the guard does not reach an explicit-ON join, so every category total, "
        f"budget and benchmark figure is wrong for 30 days (got {total})"
    )


@pytest.mark.asyncio
async def test_the_same_join_written_as_a_two_entity_select_is_also_covered(
    auth_client, db_session
):
    """The other common spelling — both entities in the columns clause."""
    await _invoice(auth_client, "INV-JOIN2-LIVE", "100.00")
    binned = await _invoice(auth_client, "INV-JOIN2-BINNED", "500.00")

    with tenant.include_deleted():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == binned))
    inv.deleted_at = datetime.now(UTC)
    await db_session.commit()

    rows = list(
        await db_session.execute(
            select(Invoice.invoice_number, LineItem.amount).join(
                LineItem, LineItem.invoice_id == Invoice.id
            )
        )
    )

    assert [r[0] for r in rows] == ["INV-JOIN2-LIVE"], (
        f"a two-entity join returned a binned invoice's rows: {rows}"
    )
