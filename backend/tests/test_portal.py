"""WO-I: the client portal — the contracts, pinned test by test.

1. Link lifecycle: get-or-create is stable; regenerate kills the old URL
   the moment the new one exists; revoke kills outright; every credential
   event is audited.
2. The portal shows ONLY the token's customer's world — a sibling customer
   in the SAME workspace sees none of it (cross-workspace is the parity
   probe's job).
3. Rendering the portal stamps the quote-viewed signal exactly once, the
   CRM timeline surfaces it, and accept/decline rides the one existing
   transition machinery (plan seeded on accept, audited with the portal
   actor). A non-sent offer is unreachable (opaque 404).
4. Documents appear and download ONLY while explicitly shared; draft
   invoices stay invisible.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
}

PDF = b"%PDF-1.7 portal-doc" + b"\x00" * 16


async def _setup_issuing(client) -> None:
    await client.put("/api/v1/modules/issuing", json={"enabled": True})
    await client.put("/api/v1/issuer", json=ISSUER)


async def _customer(client, name="Riverbank Office") -> str:
    r = await client.post("/api/v1/customers", json={"name": name, "country": "LV"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _project_for(client, customer_id: str, code="POR-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    r = await client.put(
        f"/api/v1/masters/projects/{pid}/customer", json={"customer_id": customer_id}
    )
    assert r.status_code == 200, r.text
    return pid


async def _sent_offer(client, project_id: str, amount="500.00") -> dict:
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Quote", "lines": [{"description": "Work", "amount": amount}]},
    )
    assert r.status_code == 201, r.text
    offer = r.json()
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "sent"},
    )
    assert r.status_code == 200, r.text
    return offer


async def _link(client, customer_id: str) -> str:
    r = await client.get(f"/api/v1/customers/{customer_id}/portal-link")
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.mark.asyncio
async def test_link_lifecycle_stable_regenerate_revoke_audited(auth_client, db_session):
    await _setup_issuing(auth_client)
    cid = await _customer(auth_client)

    t1 = await _link(auth_client, cid)
    t2 = await _link(auth_client, cid)
    assert t1 == t2, "get-or-create is stable, not a token mill"
    assert (await auth_client.get(f"/api/v1/portal/{t1}")).status_code == 200

    t3 = (await auth_client.post(f"/api/v1/customers/{cid}/portal-link/regenerate")).json()["token"]
    assert t3 != t1
    assert (await auth_client.get(f"/api/v1/portal/{t1}")).status_code == 404, "old URL dies"
    assert (await auth_client.get(f"/api/v1/portal/{t3}")).status_code == 200

    r = await auth_client.delete(f"/api/v1/customers/{cid}/portal-link")
    assert r.status_code == 204
    assert (await auth_client.get(f"/api/v1/portal/{t3}")).status_code == 404, "revoked dies"
    assert (await auth_client.get("/api/v1/portal/not-a-token")).status_code == 404

    actions = set(
        await db_session.scalars(
            select(AuditEvent.action).where(AuditEvent.action.like("customer.portal_link%"))
        )
    )
    assert actions == {
        "customer.portal_link_issue",
        "customer.portal_link_regenerate",
        "customer.portal_link_revoke",
    }


@pytest.mark.asyncio
async def test_portal_shows_only_this_customers_world(auth_client):
    await _setup_issuing(auth_client)
    mine = await _customer(auth_client, "Riverbank Office")
    sibling = await _customer(auth_client, "Harbour Cafe")
    my_project = await _project_for(auth_client, mine, "POR-MINE")
    their_project = await _project_for(auth_client, sibling, "POR-THEIRS")
    await _sent_offer(auth_client, my_project, "500.00")
    their_offer = await _sent_offer(auth_client, their_project, "999.00")

    # One issued invoice each, linked by customer.
    for cust, price in ((mine, "100"), (sibling, "777")):
        r = await auth_client.post(
            "/api/v1/issued",
            json={
                "customer_id": cust,
                "buyer_name": "x",
                "issue_date": "2026-08-01",
                "vat_scheme": "standard",
                "lines": [
                    {"description": "S", "quantity": "1", "unit_price": price, "vat_rate": "21"}
                ],
            },
        )
        assert r.status_code in (200, 201), r.text

    token = await _link(auth_client, mine)
    data = (await auth_client.get(f"/api/v1/portal/{token}")).json()
    assert data["customer"] == "Riverbank Office"
    assert [o["project"] for o in data["offers"]] == ["Job POR-MINE"]
    assert all("999" not in o["total"] for o in data["offers"])
    assert len(data["invoices"]) == 1
    assert "121.00" in data["invoices"][0]["total"], "my 100+21% — never the sibling's 777"

    # My token cannot decide the SIBLING customer's offer — opaque 404.
    r = await auth_client.post(
        f"/api/v1/portal/{token}/offers/{their_offer['id']}/decision",
        json={"decision": "accepted"},
    )
    assert r.status_code == 404, "a portal token must not reach another customer's offers"


@pytest.mark.asyncio
async def test_viewed_stamp_decision_and_timeline(auth_client, db_session):
    await _setup_issuing(auth_client)
    cid = await _customer(auth_client)
    pid = await _project_for(auth_client, cid, "POR-DEC")
    offer = await _sent_offer(auth_client, pid)
    token = await _link(auth_client, cid)

    one = (await auth_client.get(f"/api/v1/portal/{token}")).json()
    from app.models.project_offer import ProjectOffer

    row = await db_session.get(ProjectOffer, offer["id"])
    first_view = row.viewed_at
    assert first_view is not None, "rendering the portal stamps the quote-viewed signal"
    await auth_client.get(f"/api/v1/portal/{token}")
    await db_session.refresh(row)
    assert row.viewed_at == first_view, "first view only — the stamp never moves"
    assert one["offers"][0]["decidable"] is True

    tl = (await auth_client.get(f"/api/v1/customers/{cid}/timeline")).json()["events"]
    assert any("viewed by the customer" in e["title"] for e in tl)

    r = await auth_client.post(
        f"/api/v1/portal/{token}/offers/{offer['id']}/decision",
        json={"decision": "accepted"},
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted"

    plan = (await auth_client.get(f"/api/v1/masters/projects/{pid}/invoicing-plan")).json()
    assert plan["rows"], "portal acceptance seeds the plan like any acceptance"
    ev = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "offer.portal_decision")
    )
    assert ev is not None

    # Decided → no longer reachable for a second decision (opaque).
    again = await auth_client.post(
        f"/api/v1/portal/{token}/offers/{offer['id']}/decision",
        json={"decision": "rejected"},
    )
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_documents_gated_by_the_shared_flag_and_drafts_hidden(auth_client):
    await _setup_issuing(auth_client)
    cid = await _customer(auth_client)
    pid = await _project_for(auth_client, cid, "POR-DOC")
    r = await auth_client.post(
        f"/api/v1/masters/projects/{pid}/documents",
        params={"kind": "contract"},
        files={"file": ("contract.pdf", PDF, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    token = await _link(auth_client, cid)

    data = (await auth_client.get(f"/api/v1/portal/{token}")).json()
    assert data["documents"] == [], "sharing is OFF by default"
    dl = await auth_client.get(f"/api/v1/portal/{token}/documents/{doc_id}")
    assert dl.status_code == 404, "unshared bytes are unreachable, opaquely"

    r = await auth_client.put(
        f"/api/v1/masters/projects/{pid}/documents/{doc_id}/share", json={"shared": True}
    )
    assert r.status_code == 200 and r.json()["shared_with_customer"] is True
    data = (await auth_client.get(f"/api/v1/portal/{token}")).json()
    assert [d["filename"] for d in data["documents"]] == ["contract.pdf"]
    dl = await auth_client.get(f"/api/v1/portal/{token}/documents/{doc_id}")
    assert dl.status_code == 200 and dl.content == PDF

    r = await auth_client.put(
        f"/api/v1/masters/projects/{pid}/documents/{doc_id}/share", json={"shared": False}
    )
    assert r.status_code == 200
    assert (await auth_client.get(f"/api/v1/portal/{token}/documents/{doc_id}")).status_code == 404

    # A draft (unissued) invoice never reaches the portal.
    r = await auth_client.post(
        "/api/v1/issued",
        json={
            "customer_id": cid,
            "buyer_name": "x",
            "issue_date": "2026-08-01",
            "vat_scheme": "standard",
            "draft": True,
            "lines": [{"description": "S", "quantity": "1", "unit_price": "50", "vat_rate": "21"}],
        },
    )
    assert r.status_code in (200, 201)
    data = (await auth_client.get(f"/api/v1/portal/{token}")).json()
    assert data["invoices"] == [], "unissued work is not the client's yet"
