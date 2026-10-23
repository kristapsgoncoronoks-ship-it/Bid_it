"""IMP-09: bounded, tenant-safe supplier context on the invoice worklist.

The supplier label is the current authorized vendor-master name, not a frozen
document identity. The list keeps its invoice population when a supplier cannot
be joined; the detail contract and financial values remain unchanged.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event, text, update

from app.api.routes.invoices import list_invoices
from app.core import tenant
from app.core.identity import Principal
from app.models.invoice import Invoice, InvoiceStatus, WorkflowState
from app.models.user import UserRole
from app.models.vendor import VENDOR_PROVISIONAL, Vendor


async def _create(client, number: str, supplier: str, *, headers=None) -> dict:
    response = await client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "vendor_name": supplier,
            "invoice_number": number,
            "issue_date": "2026-09-01",
            "due_date": "2026-10-01",
            "currency": "USD",
            "supplier_registration_number": "SYNTHETIC-DOCUMENT-REG",
            "line_items": [
                {
                    "description": "Synthetic service",
                    "quantity": "1",
                    "unit_price": "100",
                    "tax_rate": "21",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _seed(client, db_session, n: int = 24):
    me = (await client.get("/api/v1/auth/me")).json()
    org_id = me["organization"]["id"]
    vendors, invoices = [], []
    for i in range(n):
        vendor = Vendor(id=str(uuid4()), org_id=org_id, name=f"Synthetic supplier {i:02d}")
        invoice = Invoice(
            id=str(uuid4()),
            org_id=org_id,
            vendor_id=vendor.id,
            invoice_number=f"CTX-{i:02d}",
            issue_date=date(2026, 9, 1) + timedelta(days=i),
            due_date=date(2026, 10, 1) + timedelta(days=i),
            currency="USD",
            status=InvoiceStatus.pending,
            workflow_state={
                21: WorkflowState.submitted,
                22: WorkflowState.partially_approved,
                23: WorkflowState.approved,
            }.get(i, WorkflowState.draft),
            validation_status="flagged" if i == 23 else "passed",
            subtotal=Decimal("100.00"),
            tax_amount=Decimal("21.00"),
            total=Decimal("121.00"),
        )
        vendors.append(vendor)
        invoices.append(invoice)
    db_session.add_all(vendors)
    await db_session.flush()
    db_session.add_all(invoices)
    await db_session.commit()
    return org_id, vendors, invoices


@pytest.mark.asyncio
async def test_list_names_current_supplier_without_changing_detail_or_document_values(
    auth_client, db_session
):
    created = await _create(auth_client, "CONTEXT-1", "Synthetic original supplier")
    listed = await auth_client.get("/api/v1/invoices")
    assert listed.status_code == 200, listed.text
    (row,) = listed.json()["items"]
    assert row["supplier_name"] == "Synthetic original supplier"
    assert (row["due_date"], row["currency"], row["subtotal"], row["tax_amount"], row["total"]) == (
        "2026-10-01",
        "USD",
        "100.00",
        "21.00",
        "121.00",
    )
    assert row["supplier_registration_number"] == "SYNTHETIC-DOCUMENT-REG"
    # The additive projection belongs to the list only; existing detail clients
    # continue receiving vendor_name, with no new supplier_name property.
    detail = (await auth_client.get(f"/api/v1/invoices/{created['id']}")).json()
    assert detail["vendor_name"] == "Synthetic original supplier"
    assert "supplier_name" not in detail

    # Provisional suppliers are still meaningful invoice context. The list must
    # neither hide historical invoices nor invent a frozen document-name claim.
    await db_session.execute(
        update(Vendor)
        .where(Vendor.id == created["vendor_id"])
        .values(name="Synthetic renamed supplier", status=VENDOR_PROVISIONAL)
    )
    await db_session.commit()
    renamed = (await auth_client.get("/api/v1/invoices")).json()["items"][0]
    assert renamed["supplier_name"] == "Synthetic renamed supplier"
    assert renamed["id"] == created["id"]
    assert renamed["total"] == row["total"]
    assert renamed["supplier_registration_number"] == row["supplier_registration_number"]


@pytest.mark.asyncio
async def test_list_projection_preserves_default_pagination_filters_and_soft_delete(
    auth_client, db_session
):
    _, vendors, invoices = await _seed(auth_client, db_session)
    page_one = (await auth_client.get("/api/v1/invoices")).json()
    assert (page_one["total"], page_one["page"], page_one["page_size"]) == (24, 1, 20)
    assert [r["invoice_number"] for r in page_one["items"]] == [
        f"CTX-{i:02d}" for i in range(23, 3, -1)
    ]
    assert [r["supplier_name"] for r in page_one["items"]] == [
        f"Synthetic supplier {i:02d}" for i in range(23, 3, -1)
    ]
    page_two = (await auth_client.get("/api/v1/invoices?page=2")).json()
    assert [r["invoice_number"] for r in page_two["items"]] == [
        "CTX-03",
        "CTX-02",
        "CTX-01",
        "CTX-00",
    ]

    filtered = await auth_client.get(
        "/api/v1/invoices",
        params={
            "q": "CTX-2",
            "status": "pending",
            "workflow_state": "in_approval",
            "start": "2026-09-22",
            "end": "2026-09-23",
            "validation_status": "passed",
        },
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 2
    assert [
        (r["supplier_name"], r["due_date"], r["workflow_state"]) for r in filtered.json()["items"]
    ] == [
        ("Synthetic supplier 22", "2026-10-23", "partially_approved"),
        ("Synthetic supplier 21", "2026-10-22", "submitted"),
    ]
    vendor_only = (
        await auth_client.get("/api/v1/invoices", params={"vendor_id": vendors[0].id})
    ).json()
    assert vendor_only["total"] == 1
    assert vendor_only["items"][0]["supplier_name"] == "Synthetic supplier 00"
    invoices[0].deleted_at = datetime.now(UTC)
    await db_session.commit()
    hidden = (await auth_client.get("/api/v1/invoices", params={"vendor_id": vendors[0].id})).json()
    assert hidden["total"] == 0 and hidden["items"] == []


@pytest.mark.asyncio
async def test_unavailable_supplier_keeps_invoice_without_disclosing_foreign_master(
    auth_client, db_session
):
    own_a = await _create(auth_client, "OWN-CROSS", "Synthetic own supplier")
    own_b = await _create(auth_client, "OWN-ORPHAN", "Synthetic own supplier")
    me = (await auth_client.get("/api/v1/auth/me")).json()
    registration = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Synthetic foreign workspace",
            "name": "Foreign owner",
            "email": "foreign-projection@example.com",
            "password": "supersecret",
        },
    )
    assert registration.status_code == 201, registration.text
    foreign_headers = {"Authorization": f"Bearer {registration.json()['token']['access_token']}"}
    foreign = await _create(
        auth_client, "FOREIGN-ONLY", "Confidential foreign supplier", headers=foreign_headers
    )

    # Production's composite FK refuses both links. Corrupt only this fresh
    # in-memory test database to exercise read-side defence independently.
    await db_session.commit()
    await db_session.execute(text("PRAGMA foreign_keys=OFF"))
    assert await db_session.scalar(text("PRAGMA foreign_keys")) == 0
    try:
        await db_session.execute(
            update(Invoice).where(Invoice.id == own_a["id"]).values(vendor_id=foreign["vendor_id"])
        )
        await db_session.execute(
            update(Invoice).where(Invoice.id == own_b["id"]).values(vendor_id=str(uuid4()))
        )
        await db_session.commit()
    finally:
        await db_session.execute(text("PRAGMA foreign_keys=ON"))
        assert await db_session.scalar(text("PRAGMA foreign_keys")) == 1
        await db_session.commit()

    response = await auth_client.get("/api/v1/invoices")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert {r["id"] for r in response.json()["items"]} == {own_a["id"], own_b["id"]}
    assert all(r["supplier_name"] is None for r in response.json()["items"])
    assert "Confidential foreign supplier" not in response.text
    authorized_foreign = (await auth_client.get("/api/v1/invoices", headers=foreign_headers)).json()
    assert authorized_foreign["total"] == 1
    assert authorized_foreign["items"][0]["supplier_name"] == "Confidential foreign supplier"

    # Isolate route-level scoping from middleware/ORM tenant context. Even with
    # that second layer absent, its explicit join must not expose the name.
    principal = Principal(
        id=me["user"]["id"],
        org_id=me["organization"]["id"],
        email=me["user"]["email"],
        name="Owner",
        role=UserRole.owner,
        is_expense_approver=True,
        is_platform_admin=False,
        is_active=True,
        email_verified=False,
        iban=None,
        bic=None,
    )
    scope_token = tenant.set_current_org(None)
    try:
        direct = await list_invoices(
            current=principal,
            db=db_session,
            vendor_id=None,
            status_=None,
            start=None,
            end=None,
            q=None,
            validation_status=None,
            workflow_state=None,
            page=1,
            page_size=20,
        )
    finally:
        tenant.reset_current_org(scope_token)
    assert direct.total == 2
    assert all(r.supplier_name is None for r in direct.items)


class _InvoiceQueryCounter:
    def __init__(self, db_session):
        engine = db_session.bind
        self.engine = getattr(engine, "sync_engine", engine)
        self.statements = []

    def _count(self, conn, cursor, statement, parameters, context, executemany):
        if re.search(r"\b(?:FROM|JOIN)\s+(?:invoices|vendors)\b", statement, re.IGNORECASE):
            self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._count)


@pytest.mark.asyncio
async def test_supplier_context_queries_stay_flat_at_twenty_distinct_suppliers(
    auth_client, db_session
):
    await _seed(auth_client, db_session)
    counts = []
    for page_size in (1, 20):
        with _InvoiceQueryCounter(db_session) as counter:
            response = await auth_client.get("/api/v1/invoices", params={"page_size": page_size})
            assert response.status_code == 200, response.text
            rows = response.json()["items"]
            assert len(rows) == page_size
            assert all(r["supplier_name"] for r in rows)
        counts.append(len(counter.statements))
    assert counts == [2, 2], f"Invoice/supplier reads grew with the page: {counts}"


@pytest.mark.asyncio
async def test_invoice_read_only_permission_still_gets_supplier_context(role_client, db_session):
    reader = await role_client("user_free")
    await _seed(reader, db_session, n=1)
    response = await reader.get("/api/v1/invoices")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["supplier_name"] == "Synthetic supplier 00"
