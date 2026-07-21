"""Defence-in-depth: the ORM tenant guard scopes even queries that forget to
filter by org_id."""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.tenant import reset_current_org, set_current_org
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.vendor import Vendor


async def _seed_two_tenants(db):
    a = Organization(name="Tenant A")
    b = Organization(name="Tenant B")
    db.add_all([a, b])
    await db.flush()
    va = Vendor(org_id=a.id, name="VendorA")
    vb = Vendor(org_id=b.id, name="VendorB")
    db.add_all([va, vb])
    await db.flush()
    db.add(Invoice(org_id=a.id, vendor_id=va.id, invoice_number="A-1", issue_date=date(2026, 1, 1)))
    db.add(Invoice(org_id=b.id, vendor_id=vb.id, invoice_number="B-1", issue_date=date(2026, 1, 1)))
    await db.commit()
    return a, b


@pytest.mark.asyncio
async def test_unfiltered_select_is_tenant_scoped(db_session):
    a, b = await _seed_two_tenants(db_session)

    # Unscoped context sees everything (bootstrap / operator path).
    assert len((await db_session.scalars(select(Invoice))).all()) == 2

    # Scoped to A: an UNFILTERED select returns only A's rows.
    token = set_current_org(a.id)
    try:
        invs = (await db_session.scalars(select(Invoice))).all()
        assert {i.invoice_number for i in invs} == {"A-1"}
        vendors = (await db_session.scalars(select(Vendor))).all()
        assert {v.name for v in vendors} == {"VendorA"}
    finally:
        reset_current_org(token)

    # Scope restored to unscoped.
    assert len((await db_session.scalars(select(Invoice))).all()) == 2


@pytest.mark.asyncio
async def test_scope_blocks_cross_tenant_fetch_by_id(db_session):
    a, b = await _seed_two_tenants(db_session)
    b_invoice = (
        await db_session.scalars(select(Invoice).where(Invoice.invoice_number == "B-1"))
    ).one()

    # As tenant A, B's invoice is invisible even fetched by primary key.
    token = set_current_org(a.id)
    try:
        found = (
            await db_session.scalars(select(Invoice).where(Invoice.id == b_invoice.id))
        ).first()
        assert found is None
    finally:
        reset_current_org(token)


# --------------------------------------------------------------------------------
# End-to-end WARRANTY over HTTP: a fresh registrant (no invitation) can never see
# or touch another company's data, and re-registering a company name never takes
# over an existing company.
# --------------------------------------------------------------------------------


async def _register(client, org, email):
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "Owner", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"], r.json()["organization"]["id"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _http_inv(n):
    return {
        "vendor_name": "SecretVendor",
        "invoice_number": f"A-{n}",
        "issue_date": "2026-07-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "100",
                "tax_rate": "0",
            }
        ],
    }


@pytest.mark.asyncio
async def test_fresh_registrant_sees_no_other_company_data(client):
    # Company A creates data across several surfaces.
    a_tok, a_org = await _register(client, "Alpha Ltd", "a-owner@alpha.io")
    a_invoice_id = (
        await client.post("/api/v1/invoices", json=_http_inv(1), headers=_h(a_tok))
    ).json()["id"]
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(a_tok))
    a_report_id = (
        await client.post(
            "/api/v1/expenses",
            json={
                "title": "A trip",
                "items": [
                    {
                        "spend_date": "2026-05-01",
                        "category": "meals",
                        "description": "Dinner",
                        "amount": "50.00",
                        "vat_amount": "0",
                    }
                ],
            },
            headers=_h(a_tok),
        )
    ).json()["id"]
    assert (await client.get("/api/v1/invoices", headers=_h(a_tok))).json()["total"] == 1

    # Company B registers fresh — NO invitation.
    b_tok, b_org = await _register(client, "Beta Ltd", "b-owner@beta.io")
    assert b_org != a_org

    # B's lists are empty; none of A's rows leak.
    assert (await client.get("/api/v1/invoices", headers=_h(b_tok))).json()["total"] == 0
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(b_tok))
    assert (await client.get("/api/v1/expenses", headers=_h(b_tok))).json()["total"] == 0

    # B's team + audit show only B.
    members = (await client.get("/api/v1/team/members", headers=_h(b_tok))).json()
    assert [m["email"] for m in members] == ["b-owner@beta.io"]
    audit = (await client.get("/api/v1/audit", headers=_h(b_tok))).json()
    assert all(e["actor_email"] in (None, "b-owner@beta.io") for e in audit["items"])

    # B's analytics reflect zero spend (not A's €100).
    summ = (await client.get("/api/v1/analytics/summary", headers=_h(b_tok))).json()
    assert float(summ["total_spend"]) == 0.0

    # Direct-ID access to A's resources is a 404 for B (opaque, not a 403 leak).
    assert (
        await client.get(f"/api/v1/invoices/{a_invoice_id}", headers=_h(b_tok))
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/expenses/{a_report_id}", headers=_h(b_tok))
    ).status_code == 404
    # B cannot modify A's resources either; A's data is untouched.
    assert (
        await client.delete(f"/api/v1/invoices/{a_invoice_id}", headers=_h(b_tok))
    ).status_code == 404
    assert (await client.get("/api/v1/invoices", headers=_h(a_tok))).json()["total"] == 1


@pytest.mark.asyncio
async def test_same_company_name_registration_does_not_take_over(client):
    # The user's exact scenario: two accounts, DIFFERENT emails, SAME company name.
    # Isolation is keyed on org_id (a fresh UUID per registration), never the name,
    # so the second account sees none of the first's REPORTS or data.
    a_tok, a_org = await _register(client, "Acme Corp", "first@acme-a.io")
    await client.post("/api/v1/invoices", json=_http_inv(1), headers=_h(a_tok))
    await client.post("/api/v1/invoices", json=_http_inv(2), headers=_h(a_tok))
    a_inv_id = (await client.post("/api/v1/invoices", json=_http_inv(3), headers=_h(a_tok))).json()[
        "id"
    ]
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(a_tok))
    a_report_id = (
        await client.post(
            "/api/v1/expenses",
            json={
                "title": "A report",
                "items": [
                    {
                        "spend_date": "2026-05-01",
                        "category": "meals",
                        "description": "Lunch",
                        "amount": "80.00",
                        "vat_amount": "0",
                    }
                ],
            },
            headers=_h(a_tok),
        )
    ).json()["id"]

    # Registering the SAME name (different email) creates a NEW, isolated company.
    dup_tok, dup_org = await _register(client, "Acme Corp", "impostor@acme-b.io")
    assert dup_org != a_org

    # The second "Acme Corp" sees ZERO of the first's invoices, expenses, analytics.
    assert (await client.get("/api/v1/invoices", headers=_h(dup_tok))).json()["total"] == 0
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(dup_tok))
    assert (await client.get("/api/v1/expenses", headers=_h(dup_tok))).json()["total"] == 0
    assert (
        float(
            (await client.get("/api/v1/analytics/summary", headers=_h(dup_tok))).json()[
                "total_spend"
            ]
        )
        == 0.0
    )
    # …and cannot reach the first company's rows even by their exact ids.
    assert (
        await client.get(f"/api/v1/invoices/{a_inv_id}", headers=_h(dup_tok))
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/expenses/{a_report_id}", headers=_h(dup_tok))
    ).status_code == 404

    # A still owns all of its data.
    assert (await client.get("/api/v1/invoices", headers=_h(a_tok))).json()["total"] == 3


@pytest.mark.asyncio
async def test_joining_a_company_requires_an_invitation(client):
    a_tok, a_org = await _register(client, "Gamma Ltd", "g-owner@gamma.io")
    token = (
        await client.post(
            "/api/v1/team/invites",
            json={"email": "staff@gamma.io", "role": "user"},
            headers=_h(a_tok),
        )
    ).json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "name": "Staff", "password": "supersecret"},
    )
    assert acc.json()["organization"]["id"] == a_org  # invited → joins A
    _, other_org = await _register(client, "Gamma Ltd", "outsider@gamma-x.io")
    assert other_org != a_org  # plain register → new company
