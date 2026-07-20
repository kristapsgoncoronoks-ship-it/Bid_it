"""Recurring-invoice schedules + the idempotent generator."""
from datetime import date

import pytest

from app.services import recurring

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "email": "billing@invoiceiq.test",
}

TEMPLATE = {
    "buyer_name": "Globex SARL",
    "buyer_email": "ap@globex.example",
    "lines": [{"description": "Monthly retainer", "quantity": "1", "unit_price": "500.00", "vat_rate": "21"}],
}


async def _activate(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert (await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})).status_code == 200


def test_advance_month_end_clamps():
    # Jan 31 + 1 month → Feb 28 (2026 is not a leap year).
    assert recurring.advance(date(2026, 1, 31), "monthly", 1) == date(2026, 2, 28)
    assert recurring.advance(date(2026, 1, 15), "quarterly", 1) == date(2026, 4, 15)
    assert recurring.advance(date(2026, 1, 1), "yearly", 1) == date(2027, 1, 1)
    assert recurring.advance(date(2026, 1, 1), "weekly", 2) == date(2026, 1, 15)


@pytest.mark.asyncio
async def test_create_and_list_schedule(auth_client):
    await _activate(auth_client)
    body = {
        "template": TEMPLATE,
        "frequency": "monthly",
        "interval": 1,
        "start_date": "2026-01-01",
        "title": "Globex retainer",
    }
    r = await auth_client.post("/api/v1/issued/recurring", json=body)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["title"] == "Globex retainer"
    assert rec["next_run_date"] == "2026-01-01"
    assert rec["active"] is True
    assert rec["generated_count"] == 0

    lst = (await auth_client.get("/api/v1/issued/recurring")).json()
    assert len(lst) == 1 and lst[0]["id"] == rec["id"]


async def _org_id(db_session) -> str:
    """The single org id created by the auth_client fixture ("Acme")."""
    from sqlalchemy import select

    from app.models.organization import Organization

    return await db_session.scalar(select(Organization.id).limit(1))


@pytest.mark.asyncio
async def test_generation_is_idempotent_and_catches_up(auth_client, db_session):
    await _activate(auth_client)
    # Start well in the past so several months are due.
    body = {"template": TEMPLATE, "frequency": "monthly", "start_date": "2026-01-01"}
    rec = (await auth_client.post("/api/v1/issued/recurring", json=body)).json()

    org = await _org_id(db_session)
    # Generate everything due up to a fixed 'today' directly through the service.
    res = await recurring.generate_due(db_session, org, today=date(2026, 3, 15))
    # Jan, Feb, Mar → 3 invoices.
    assert len(res.generated) == 3
    numbers = [n for _, n in res.generated]
    assert numbers == ["INV-2026-0001", "INV-2026-0002", "INV-2026-0003"]

    # Re-running for the SAME 'today' produces nothing new (next_run_date advanced).
    res2 = await recurring.generate_due(db_session, org, today=date(2026, 3, 15))
    assert res2.generated == []

    got = (await auth_client.get(f"/api/v1/issued/recurring/{rec['id']}")).json()
    assert got["generated_count"] == 3
    assert got["next_run_date"] == "2026-04-01"


@pytest.mark.asyncio
async def test_run_endpoint_generates_invoices(auth_client):
    await _activate(auth_client)
    # A schedule whose first run is already due today.
    body = {"template": TEMPLATE, "frequency": "monthly", "start_date": "2020-01-01", "end_date": "2020-02-15"}
    await auth_client.post("/api/v1/issued/recurring", json=body)

    run = await auth_client.post("/api/v1/issued/recurring/run")
    assert run.status_code == 200, run.text
    out = run.json()
    # Jan + Feb 2020, then end_date closes it.
    assert out["generated"] == 2
    # The invoices exist and carry the template's amount (500 + 21% = 605).
    inv_list = (await auth_client.get("/api/v1/issued")).json()["items"]
    assert len(inv_list) == 2
    assert all(i["total"] == "605.00" for i in inv_list)


@pytest.mark.asyncio
async def test_pause_stops_generation(auth_client):
    await _activate(auth_client)
    body = {"template": TEMPLATE, "frequency": "monthly", "start_date": "2020-01-01"}
    rec = (await auth_client.post("/api/v1/issued/recurring", json=body)).json()

    # Pause it.
    patched = await auth_client.patch(f"/api/v1/issued/recurring/{rec['id']}", json={"active": False})
    assert patched.status_code == 200 and patched.json()["active"] is False

    run = await auth_client.post("/api/v1/issued/recurring/run")
    assert run.json()["generated"] == 0


@pytest.mark.asyncio
async def test_schedule_is_tenant_isolated(auth_client, client):
    await _activate(auth_client)
    body = {"template": TEMPLATE, "frequency": "monthly", "start_date": "2026-01-01"}
    rec = (await auth_client.post("/api/v1/issued/recurring", json=body)).json()

    # A second, unrelated workspace.
    reg = await client.post("/api/v1/auth/register", json={
        "organization_name": "Other Co", "name": "Other", "email": "other@other.io", "password": "supersecret2",
    })
    other = reg.json()["token"]["access_token"]
    client.headers["Authorization"] = f"Bearer {other}"
    assert (await client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    await client.put("/api/v1/modules/issuing", json={"enabled": True})

    # The other tenant sees none of the first tenant's schedules and cannot fetch it.
    assert (await client.get("/api/v1/issued/recurring")).json() == []
    assert (await client.get(f"/api/v1/issued/recurring/{rec['id']}")).status_code == 404
