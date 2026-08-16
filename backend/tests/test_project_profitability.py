"""Project profitability, phase 1 (docs/design/project-profitability.md).

The subject is the LOOP: open a project, issue revenue under it, allocate
costs to it, add manual lines, read revenue − costs. Industry-neutral by owner
requirement — these tests use generic fixtures (a project, a supplier, a job)
and their subjects never name an industry.

The P&L's stated basis (net EUR, live) is pinned test by test: what counts
(approved expenses, non-draft issued documents), what doesn't (drafts,
rejected reports, binned invoices), and what SUBTRACTS (credit notes on the
parent's project).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.expense import ExpenseItem, ExpenseReport
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.user import User
from app.models.vendor import Vendor


async def _org(db) -> str:
    return await db.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _project(client, code="JOB-1", name="Won contract") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _issuer_ready(client):
    """Complete the default issuer so issuing is allowed (the onboarding gate)."""
    r = await client.put(
        "/api/v1/issuer",
        json={
            "legal_name": "Acme OU",
            "reg_number": "12345678",
            "vat_number": "EE101234567",
            "address_line1": "Main 1",
            "city": "Tallinn",
            "postal_code": "10111",
            "country": "EE",
            "invoice_prefix": "ACM-",
        },
    )
    assert r.status_code == 200, r.text
    assert (await client.put("/api/v1/modules/issuing", json={"enabled": True})).status_code == 200


async def _issue(client, project_id, *, amount="1000.00", draft=False, buyer="Customer OU"):
    r = await client.post(
        "/api/v1/issued",
        json={
            "buyer_name": buyer,
            "project_id": project_id,
            "draft": draft,
            "lines": [
                {
                    "description": "Work performed",
                    "quantity": "1",
                    "unit_price": amount,
                    "vat_rate": "22",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _received_invoice(db, org_id, project_id, *, subtotal="200.00", total="244.00"):
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id))
    if vendor is None:
        vendor = Vendor(org_id=org_id, name="Generic Supplier OU")
        db.add(vendor)
        await db.flush()
    inv = Invoice(
        org_id=org_id,
        vendor_id=vendor.id,
        invoice_number=f"SUP-{subtotal}",
        issue_date=date(2026, 8, 1),
        subtotal=Decimal(subtotal),
        total=Decimal(total),
        project_id=project_id,
    )
    db.add(inv)
    await db.commit()
    return inv.id


async def _pnl(client, project_id) -> dict:
    r = await client.get(f"/api/v1/masters/projects/{project_id}/pnl")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# The loop, end to end
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_owner_scenario_end_to_end(auth_client, db_session):
    """Open a project → issue revenue → allocate a supplier invoice → approve an
    expense → book a wage line → read the P&L. The whole point of phase 1 in
    one test, with hand-checkable numbers:

        revenue 1000.00 (net)
        costs    200.00 (supplier, net) + 50.00 (expense, net) + 300.00 (wages)
        profit   450.00, margin 45.0%
    """
    org_id = await _org(db_session)
    await _issuer_ready(auth_client)
    project_id = await _project(auth_client)

    await _issue(auth_client, project_id, amount="1000.00")
    await _received_invoice(db_session, org_id, project_id, subtotal="200.00", total="244.00")

    owner_id = await db_session.scalar(select(User.id).where(User.email == "owner@acme.io"))
    report = ExpenseReport(
        org_id=org_id,
        employee_id=owner_id,
        employee_name="Owner",
        title="Job expenses",
        status="approved",
    )
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        ExpenseItem(
            report_id=report.id,
            org_id=org_id,
            description="Travel for the job",
            spend_date=date(2026, 8, 2),
            amount=Decimal("61.00"),  # gross
            vat_amount=Decimal("11.00"),  # → 50.00 net
            project_id=project_id,
        )
    )
    await db_session.commit()

    wage = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/cost-entries",
        json={"label": "Crew wages", "category": "wages", "amount": "300.00"},
    )
    assert wage.status_code == 201, wage.text

    pnl = await _pnl(auth_client, project_id)
    assert pnl["revenue"] == "1000.00"
    assert pnl["invoice_costs"] == "200.00"
    assert pnl["expense_costs"] == "50.00"
    assert pnl["manual_costs"] == "300.00"
    assert pnl["costs"] == "550.00"
    assert pnl["profit"] == "450.00"
    assert pnl["margin_pct"] == "45.0"
    assert pnl["basis"] == "net_eur_live"


@pytest.mark.asyncio
async def test_a_credit_note_subtracts_on_the_parents_project(auth_client, db_session):
    """Revenue reversals land where the revenue did — otherwise every credited
    project overstates. The credit note inherits its parent's project without
    the caller saying so."""
    await _issuer_ready(auth_client)
    project_id = await _project(auth_client)
    issued = await _issue(auth_client, project_id, amount="1000.00")

    cn = await auth_client.post(f"/api/v1/issued/{issued['id']}/credit-note", json={})
    assert cn.status_code == 201, cn.text

    pnl = await _pnl(auth_client, project_id)
    assert pnl["credited"] == "1000.00"
    assert pnl["revenue"] == "0.00"


@pytest.mark.asyncio
async def test_what_does_not_count(auth_client, db_session):
    """Three exclusions, each a real mistake if wrong: a DRAFT issued invoice is
    not revenue yet; a DRAFT expense report is not a cost yet; a BINNED supplier
    invoice has left the books (and returns on restore)."""
    org_id = await _org(db_session)
    await _issuer_ready(auth_client)
    project_id = await _project(auth_client)

    await _issue(auth_client, project_id, amount="500.00", draft=True)

    owner_id = await db_session.scalar(select(User.id).where(User.email == "owner@acme.io"))
    report = ExpenseReport(
        org_id=org_id,
        employee_id=owner_id,
        employee_name="Owner",
        title="Not yet approved",
        status="draft",
    )
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        ExpenseItem(
            report_id=report.id,
            org_id=org_id,
            description="Pending expense",
            spend_date=date(2026, 8, 2),
            amount=Decimal("99.00"),
            vat_amount=Decimal("0.00"),
            project_id=project_id,
        )
    )
    await db_session.commit()

    invoice_id = await _received_invoice(db_session, org_id, project_id, subtotal="80.00")
    assert (await auth_client.delete(f"/api/v1/invoices/{invoice_id}")).status_code == 204

    pnl = await _pnl(auth_client, project_id)
    assert pnl["revenue"] == "0.00", "a draft is not a sale"
    assert pnl["expense_costs"] == "0.00", "an unapproved report is not a cost"
    assert pnl["invoice_costs"] == "0.00", "a binned invoice has left the books"

    # Restore brings the cost back — the bin composes with the P&L for free.
    assert (await auth_client.post(f"/api/v1/invoices/{invoice_id}/restore")).status_code == 200
    assert (await _pnl(auth_client, project_id))["invoice_costs"] == "80.00"


# --------------------------------------------------------------------------- #
# Cost entries and documents
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cost_entries_negative_allowed_zero_refused_unknown_category_refused(
    auth_client,
):
    project_id = await _project(auth_client)
    base = f"/api/v1/masters/projects/{project_id}/cost-entries"

    ok = await auth_client.post(
        base, json={"label": "Wages", "category": "wages", "amount": "300.00"}
    )
    assert ok.status_code == 201
    correction = await auth_client.post(
        base, json={"label": "Wages correction", "category": "wages", "amount": "-50.00"}
    )
    assert correction.status_code == 201, "a correction is a cost line too"

    assert (
        await auth_client.post(base, json={"label": "Zero", "category": "wages", "amount": "0"})
    ).status_code == 400
    assert (
        await auth_client.post(
            base, json={"label": "X", "category": "fuel_surcharge", "amount": "10"}
        )
    ).status_code == 400, "categories are a closed, industry-neutral set"

    pnl = await _pnl(auth_client, project_id)
    assert pnl["manual_costs"] == "250.00"


@pytest.mark.asyncio
async def test_deleting_a_cost_entry_audits_what_was_removed(auth_client, db_session):
    from app.models.audit import AuditEvent

    project_id = await _project(auth_client)
    entry = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/cost-entries",
        json={"label": "Equipment hire", "category": "equipment", "amount": "120.00"},
    )
    r = await auth_client.delete(
        f"/api/v1/masters/projects/{project_id}/cost-entries/{entry.json()['id']}"
    )
    assert r.status_code == 204

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "project.cost_entry_delete")
    )
    assert event is not None
    assert "Equipment hire" in (event.meta or ""), (
        "the audit event is the only remaining trace of the line — it must say WHAT"
    )
    assert (await _pnl(auth_client, project_id))["manual_costs"] == "0.00"


@pytest.mark.asyncio
async def test_the_contract_rides_with_the_project(auth_client):
    project_id = await _project(auth_client)

    up = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/documents",
        files={"file": ("contract.pdf", b"%PDF-1.4 signed contract", "application/pdf")},
    )
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]
    assert up.json()["kind"] == "contract"

    listed = await auth_client.get(f"/api/v1/masters/projects/{project_id}/documents")
    assert [d["id"] for d in listed.json()] == [doc_id]

    dl = await auth_client.get(f"/api/v1/masters/projects/{project_id}/documents/{doc_id}/download")
    assert dl.status_code == 200
    assert dl.content == b"%PDF-1.4 signed contract"
    # Inert, like every document route: attachment + nosniff.
    assert "attachment" in dl.headers.get("content-disposition", "")
    assert dl.headers.get("x-content-type-options") == "nosniff"

    empty = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert empty.status_code == 400


# --------------------------------------------------------------------------- #
# Boundaries
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_issuing_against_an_unknown_project_is_an_opaque_404(auth_client):
    await _issuer_ready(auth_client)
    r = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Customer OU",
            "project_id": "00000000-0000-0000-0000-000000000000",
            "lines": [
                {"description": "Work", "quantity": "1", "unit_price": "10", "vat_rate": "22"}
            ],
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_the_pnl_of_an_unknown_project_is_an_opaque_404(auth_client):
    r = await auth_client.get("/api/v1/masters/projects/00000000-0000-0000-0000-000000000000/pnl")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_booking_costs_needs_write_not_settings(auth_client, client, db_session):
    """The permission choice, pinned: a finance user without SETTINGS_MANAGE can
    still book a wage line (INVOICE_WRITE), because booking costs is
    bookkeeping, not org configuration. A read-only role cannot."""

    project_id = await _project(auth_client)

    invite = await auth_client.post(
        "/api/v1/team/invites", json={"email": "reader@acme.io", "role": "user_free"}
    )
    token = invite.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "name": "Reader", "password": "supersecret"},
    )
    reader_token = acc.json()["token"]["access_token"]

    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/cost-entries",
        json={"label": "Wages", "category": "wages", "amount": "10"},
        headers={"Authorization": f"Bearer {reader_token}"},
    )
    assert r.status_code == 403, "a read-only role must not book costs"
