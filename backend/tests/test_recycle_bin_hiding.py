"""A binned record must disappear from EVERYTHING (recycle bin, step 1).

19 query sites across 11 modules read the invoice table. A bin that depends on
each of them remembering to exclude deleted rows is a bin that quietly makes the
numbers wrong — and that is worse than the problem it solves. Today a mistaken
delete is loud; a half-applied bin is silent, and the totals are wrong for 30
days before anyone notices.

So it is enforced the way tenancy already is: one criterion added to every
SELECT, which no query can forget. These tests exercise that claim through the
REAL read paths rather than trusting it, and one of them exists specifically to
find the mechanism's limit rather than assume it has none.

Nothing sets `deleted_at` in production yet — the delete routes move onto this in
a later step. These tests set it directly, which is exactly what a step-1 test
should do: prove the hiding before anything can hide.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core import tenant
from app.models.invoice import Invoice


async def _invoice(auth_client, number: str, total: str = "100.00") -> str:
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
                    "unit_price": total,
                    "amount": total,
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _bin_it(db_session, invoice_id: str) -> None:
    """Put a record in the bin the way a delete eventually will."""
    with tenant.include_deleted():
        inv = await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))
    inv.deleted_at = datetime.now(UTC)
    inv.deleted_by = "someone@test.io"
    await db_session.commit()


@pytest.mark.asyncio
async def test_a_binned_invoice_vanishes_from_the_list_and_by_id(auth_client, db_session):
    keep = await _invoice(auth_client, "INV-BIN-KEEP")
    drop = await _invoice(auth_client, "INV-BIN-DROP")
    await _bin_it(db_session, drop)

    listed = await auth_client.get("/api/v1/invoices")
    assert listed.status_code == 200, listed.text
    ids = {i["id"] for i in listed.json()["items"]}
    assert keep in ids
    assert drop not in ids
    assert listed.json()["total"] == 1, "the list total still counted a binned record"

    # And it is gone by id too — an opaque 404, the same as one that never existed.
    assert (await auth_client.get(f"/api/v1/invoices/{drop}")).status_code == 404


@pytest.mark.asyncio
async def test_a_binned_invoice_is_excluded_from_a_COLUMN_only_query(auth_client, db_session):
    """THE test that decides whether "one place enforcing" is a true claim.

    The guard works by adding loader criteria to entities in a statement. A query
    that selects a bare column — `select(Invoice.id)` — rather than the entity is
    the obvious way for the mechanism to have a hole, and this codebase issues
    exactly that shape in several places. If this fails, the guard is NOT
    sufficient on its own and the remaining sites need auditing by hand: better
    to learn that from a test than from a customer's totals.
    """
    live = await _invoice(auth_client, "INV-COL-LIVE")
    binned = await _invoice(auth_client, "INV-COL-BINNED")
    await _bin_it(db_session, binned)

    ids = set(await db_session.scalars(select(Invoice.id)))

    assert live in ids
    assert binned not in ids, (
        "a column-only select saw a binned invoice — the central guard does not "
        "cover this query shape, so every such site needs an explicit filter"
    )


@pytest.mark.asyncio
async def test_a_binned_invoice_is_excluded_from_an_AGGREGATE(auth_client, db_session):
    """Counts and sums are where a leak does the most damage, because a wrong
    number does not look wrong."""
    await _invoice(auth_client, "INV-AGG-1", total="100.00")
    binned = await _invoice(auth_client, "INV-AGG-2", total="500.00")
    await _bin_it(db_session, binned)

    count = await db_session.scalar(select(func.count()).select_from(Invoice))

    assert count == 1, "an aggregate counted a binned invoice"


@pytest.mark.asyncio
async def test_duplicate_detection_does_not_match_against_a_binned_invoice(auth_client, db_session):
    """A real downstream read path, not a synthetic query. Matching a new upload
    against a record the client has deleted would warn about a duplicate the
    operator cannot see or act on."""
    first = await _invoice(auth_client, "INV-DUP-9")
    await _bin_it(db_session, first)

    r = await auth_client.get("/api/v1/invoices/duplicate-candidates?invoice_number=INV-DUP-9")

    assert r.status_code == 200, r.text
    body = r.json()
    # BOTH buckets, not just `exact`: with no vendor_id supplied every match
    # lands in `cross_supplier`, so asserting on `exact` alone would pass even
    # with the bin switched off — a test that proves nothing.
    assert body["exact"] == [] and body["cross_supplier"] == [], (
        "duplicate detection matched a binned invoice"
    )


@pytest.mark.asyncio
async def test_include_deleted_is_the_deliberate_way_back_in(auth_client, db_session):
    """The Trash screen, restore and the purge are the only three things with any
    business seeing a binned record, and they have to ask."""
    binned = await _invoice(auth_client, "INV-VISIBLE-AGAIN")
    await _bin_it(db_session, binned)

    hidden = set(await db_session.scalars(select(Invoice.id)))
    assert binned not in hidden

    with tenant.include_deleted():
        shown = set(await db_session.scalars(select(Invoice.id)))
    assert binned in shown

    # And the opt-in is scoped: outside the block it is hidden again, so it
    # cannot be switched on once and left on.
    assert binned not in set(await db_session.scalars(select(Invoice.id)))


@pytest.mark.asyncio
async def test_the_bin_hides_from_a_platform_operator_too(auth_client, db_session):
    """A cross-tenant operator has no more business seeing a client's binned
    invoice than the client's own dashboard does, so the rule is applied whether
    or not a tenant context is set."""
    binned = await _invoice(auth_client, "INV-OPERATOR")
    await _bin_it(db_session, binned)

    token = tenant.set_current_org(None)  # unscoped, as a platform route runs
    try:
        ids = set(await db_session.scalars(select(Invoice.id)))
    finally:
        tenant.reset_current_org(token)

    assert binned not in ids


def test_every_model_with_deleted_at_is_registered_with_the_guard():
    """The registration guard. A model that grows a `deleted_at` column without
    being added to SOFT_DELETE_MODELS is soft-deletable but NOT hidden — the
    silent-wrong-numbers failure this whole mechanism exists to prevent, arriving
    by omission instead."""
    from app.models.base import Base

    with_column = {
        m.class_ for m in Base.registry.mappers if "deleted_at" in m.class_.__table__.columns
    }
    registered = set(tenant.SOFT_DELETE_MODELS)

    missing = with_column - registered
    assert not missing, (
        "models carry `deleted_at` but are not in tenant.SOFT_DELETE_MODELS, so "
        f"their binned rows are visible everywhere: {sorted(m.__name__ for m in missing)}"
    )
    stale = registered - with_column
    assert not stale, (
        "registered for soft-delete hiding but carry no `deleted_at` column: "
        f"{sorted(m.__name__ for m in stale)}"
    )
