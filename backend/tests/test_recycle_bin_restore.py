"""Deleting an invoice stops destroying it, and there is a way back.

Step 2 of docs/design/deletion-and-archive.md. Step 1 shipped the hiding while
nothing set `deleted_at`, deliberately inert. This is where delete starts using
it — so the first thing these tests establish is that the row SURVIVES, because
"soft delete" that still destroys the row is a rename, not a feature.

The build order was swapped so this lands before the consent gate: the gate
exists to permit deleting things past draft, and permitting that before anything
could bring one back would make a paid invoice destroyable with no recovery.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import tenant
from app.models.invoice import Invoice
from app.services import invoices as invoice_service


async def _invoice(client, number: str, vendor: str = "Fictional Fuels OU") -> str:
    r = await client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": vendor,
            "invoice_number": number,
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Diesel",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "amount": "100.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _row(db_session, invoice_id: str) -> Invoice | None:
    """The raw row, bin or no bin."""
    with tenant.include_deleted():
        return await db_session.scalar(select(Invoice).where(Invoice.id == invoice_id))


# --------------------------------------------------------------------------- #
# The row survives
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deleting_an_invoice_bins_it_instead_of_destroying_it(auth_client, db_session):
    """THE claim of this step. If `_bin` were still `db.delete`, everything else
    here would still look right from the outside — the invoice would vanish from
    the list either way — and only this test would notice the difference."""
    invoice_id = await _invoice(auth_client, "INV-SOFT-1")

    assert (await auth_client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204

    row = await _row(db_session, invoice_id)
    assert row is not None, "the delete destroyed the row — this is not a recycle bin"
    assert row.deleted_at is not None
    assert row.deleted_by == "owner@acme.io", "the bin does not record who did it"
    # And it is gone from the client's world, per step 1.
    assert (await auth_client.get(f"/api/v1/invoices/{invoice_id}")).status_code == 404


@pytest.mark.asyncio
async def test_bulk_delete_bins_too(auth_client, db_session):
    """The two delete paths must not diverge — one destroying and one binning
    would be the worst of both."""
    a = await _invoice(auth_client, "INV-SOFT-B1")
    b = await _invoice(auth_client, "INV-SOFT-B2")

    r = await auth_client.post(
        "/api/v1/invoices/bulk-delete",
        json={"invoice_ids": [a, b], "selection": "explicit", "agreed_count": 2},
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 2

    for invoice_id in (a, b):
        row = await _row(db_session, invoice_id)
        assert row is not None and row.deleted_at is not None
        assert row.deleted_by == "owner@acme.io"


@pytest.mark.asyncio
async def test_deleting_something_already_in_the_bin_is_an_opaque_404(auth_client):
    """It is invisible to the delete path for exactly the reason a foreign
    tenant's invoice is — one rule, not a second special case."""
    invoice_id = await _invoice(auth_client, "INV-SOFT-TWICE")
    assert (await auth_client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204
    assert (await auth_client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 404


# --------------------------------------------------------------------------- #
# The bin listing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_trash_route_is_not_swallowed_by_the_invoice_id_route(auth_client):
    """`/invoices/trash` sits under `/invoices/{invoice_id}` in the same router.
    Declared in the wrong order, FastAPI reads "trash" as an id and answers a
    confident 404 — a failure that looks exactly like an empty bin."""
    r = await auth_client.get("/api/v1/invoices/trash")

    assert r.status_code == 200, "the path matched /{invoice_id} instead of the bin"
    assert "retention_days" in r.json(), "matched some other route"


@pytest.mark.asyncio
async def test_the_bin_lists_what_was_deleted_with_time_remaining(auth_client):
    live = await _invoice(auth_client, "INV-BIN-LIVE")
    binned = await _invoice(auth_client, "INV-BIN-GONE")
    await auth_client.delete(f"/api/v1/invoices/{binned}")

    body = (await auth_client.get("/api/v1/invoices/trash")).json()

    assert body["total"] == 1
    assert body["retention_days"] == 30
    (item,) = body["items"]
    assert item["invoice_id"] == binned
    assert item["invoice_number"] == "INV-BIN-GONE"
    assert item["vendor_name"] == "Fictional Fuels OU"
    assert item["deleted_by"] == "owner@acme.io"
    assert item["days_left"] == 30, "a just-deleted record should have the full window"
    assert live not in {i["invoice_id"] for i in body["items"]}, "a LIVE invoice is in the bin"


@pytest.mark.asyncio
async def test_days_left_floors_at_zero_rather_than_going_negative(auth_client, db_session):
    """A record the purge has not collected yet is still restorable. Showing
    "-4 days" would read as already gone, and the operator would not try."""
    invoice_id = await _invoice(auth_client, "INV-BIN-OLD")
    await auth_client.delete(f"/api/v1/invoices/{invoice_id}")

    row = await _row(db_session, invoice_id)
    row.deleted_at = datetime.now(UTC) - timedelta(days=34)
    await db_session.commit()

    body = (await auth_client.get("/api/v1/invoices/trash")).json()

    assert body["items"][0]["days_left"] == 0


# --------------------------------------------------------------------------- #
# Getting it back
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_restore_puts_the_invoice_back_in_the_books(auth_client):
    invoice_id = await _invoice(auth_client, "INV-RESTORE-1")
    await auth_client.delete(f"/api/v1/invoices/{invoice_id}")

    r = await auth_client.post(f"/api/v1/invoices/{invoice_id}/restore")

    assert r.status_code == 200, r.text
    assert r.json()["invoice_number"] == "INV-RESTORE-1"
    # Visible again by id, back in the list, and out of the bin.
    assert (await auth_client.get(f"/api/v1/invoices/{invoice_id}")).status_code == 200
    listed = (await auth_client.get("/api/v1/invoices")).json()
    assert invoice_id in {i["id"] for i in listed["items"]}
    assert (await auth_client.get("/api/v1/invoices/trash")).json()["total"] == 0


@pytest.mark.asyncio
async def test_restoring_something_that_was_never_deleted_is_a_404(auth_client):
    invoice_id = await _invoice(auth_client, "INV-RESTORE-LIVE")

    assert (await auth_client.post(f"/api/v1/invoices/{invoice_id}/restore")).status_code == 404


@pytest.mark.asyncio
async def test_restore_refuses_when_a_live_invoice_already_uses_the_number(auth_client):
    """The expensive sequence: delete INV-001 by mistake, re-key it as a new
    invoice, then restore the original — and the organisation holds two live
    copies of one supplier bill, which is how a bill gets paid twice."""
    original = await _invoice(auth_client, "INV-DOUBLE")
    await auth_client.delete(f"/api/v1/invoices/{original}")
    await _invoice(auth_client, "INV-DOUBLE")  # re-keyed by hand

    r = await auth_client.post(f"/api/v1/invoices/{original}/restore")

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "invoice_number_in_use"
    # And it is still in the bin — a refused restore must not consume the record.
    assert (await auth_client.get("/api/v1/invoices/trash")).json()["total"] == 1


@pytest.mark.asyncio
async def test_another_BINNED_invoice_with_the_same_number_does_not_block_a_restore(auth_client):
    """Pins the deliberate choice to run the clash check OUTSIDE
    `include_deleted()`. Two records sitting in the bin sharing a number are not
    a problem until one of them comes back; checking against binned rows too
    would make the bin progressively harder to escape the more it held."""
    first = await _invoice(auth_client, "INV-BOTH-BINNED")
    second = await _invoice(auth_client, "INV-BOTH-BINNED")
    await auth_client.delete(f"/api/v1/invoices/{first}")
    await auth_client.delete(f"/api/v1/invoices/{second}")

    r = await auth_client.post(f"/api/v1/invoices/{first}/restore")

    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Who may do what
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_finance_manager_may_delete_and_see_the_bin_but_not_restore(role_client):
    """The owner's decision, enforced: restore is admin/owner only, narrower than
    delete. A finance manager needs to SEE what they deleted — that is how the
    mistake is discovered — but putting a record back into the books is somebody
    else's call.

    This grant works by OMISSION (INVOICE_RESTORE is absent from the role's
    explicit permission set), which is exactly the kind of thing that is silently
    undone by a later edit, so it is asserted rather than assumed."""
    fm = await role_client("finance_manager")
    invoice_id = await _invoice(fm, "INV-ROLE-FM")

    assert (await fm.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204
    assert (await fm.get("/api/v1/invoices/trash")).status_code == 200
    assert (await fm.post(f"/api/v1/invoices/{invoice_id}/restore")).status_code == 403


@pytest.mark.asyncio
async def test_an_administrator_may_restore(role_client):
    admin = await role_client("admin")
    invoice_id = await _invoice(admin, "INV-ROLE-ADMIN")
    await admin.delete(f"/api/v1/invoices/{invoice_id}")

    assert (await admin.post(f"/api/v1/invoices/{invoice_id}/restore")).status_code == 200


@pytest.mark.asyncio
async def test_an_employee_may_neither_delete_nor_browse_the_bin(role_client):
    """The bin holds records somebody removed; it is not a second, unguarded copy
    of the invoice list for roles that cannot delete."""
    employee = await role_client("user")

    assert (await employee.get("/api/v1/invoices/trash")).status_code == 403


@pytest.mark.asyncio
async def test_one_org_cannot_see_or_restore_another_orgs_binned_invoice(auth_client, role_client):
    """The bin opts out of the deleted-row guard. It must NOT also opt out of
    tenancy — an `include_deleted()` block that forgot its org filter would be a
    cross-tenant leak of precisely the records clients believe they deleted."""
    mine = await _invoice(auth_client, "INV-TENANT-MINE")
    await auth_client.delete(f"/api/v1/invoices/{mine}")

    other = await role_client("owner")

    assert (await other.get("/api/v1/invoices/trash")).json()["total"] == 0
    assert (await other.post(f"/api/v1/invoices/{mine}/restore")).status_code == 404


# --------------------------------------------------------------------------- #
# The trail
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_both_the_delete_and_the_restore_are_audited(auth_client):
    """§4.16 — every mutating operation is on the record. A restore especially:
    it is the step that puts a number back into the books, and "who decided
    that?" is the question an auditor asks."""
    invoice_id = await _invoice(auth_client, "INV-AUDIT-BIN")
    await auth_client.delete(f"/api/v1/invoices/{invoice_id}")
    await auth_client.post(f"/api/v1/invoices/{invoice_id}/restore")

    entries = (await auth_client.get("/api/v1/audit?limit=100")).json()
    actions = [e["action"] for e in entries["items"]]

    assert "invoice.delete" in actions
    assert "invoice.restore" in actions


def test_the_retention_window_is_stated_in_exactly_one_place():
    """The UI reads `retention_days` off the response rather than hardcoding 30,
    so the promise made on screen cannot drift from the one the purge keeps."""
    assert invoice_service.BIN_RETENTION_DAYS == 30
