"""WO-3 — Partners router lockdown.

Partner mutations (create/update, document upload and above all document SIGN —
the commercial assertion that unlocks issuing) require `ISSUED_WRITE`; reads
require `ISSUED_READ`. The `issuing` module-entitlement gate stays as an
INDEPENDENT second check (entitlement and permission are orthogonal), signing is
audited with full attribution, and a cross-tenant partner id stays an opaque 404.
"""

from __future__ import annotations

import json

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
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


async def _activate(auth_client):
    """Complete the issuer profile + enable the issuing module for the org."""
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


async def _member_headers(auth_client, client, email, role):
    """Invite a second member into the OWNER's org with the given stored role
    (`user` → EMPLOYEE, `user_free` → READ_ONLY) and return their auth headers."""
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    return {"Authorization": f"Bearer {acc.json()['token']['access_token']}"}


async def _partner(auth_client, **kw):
    body = {"name": "Meridian Freight", "email": "ap@meridian.io", **kw}
    r = await auth_client.post("/api/v1/partners", json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_employee_cannot_create_partner(auth_client, client):
    await _activate(auth_client)
    emp = await _member_headers(auth_client, client, "emp@acme.io", "user")
    r = await client.post("/api/v1/partners", json={"name": "Rogue Partner"}, headers=emp)
    assert r.status_code == 403, r.text
    assert "permission" in r.json()["detail"].lower()  # the PERMISSION gate, not the module gate


@pytest.mark.asyncio
async def test_employee_cannot_sign_partner_document(auth_client, client):
    await _activate(auth_client)
    p = await _partner(auth_client, requires_contract=True)
    doc = (
        await auth_client.post(
            f"/api/v1/partners/{p['id']}/documents",
            json={"kind": "contract", "title": "Framework agreement"},
        )
    ).json()
    emp = await _member_headers(auth_client, client, "emp2@acme.io", "user")
    r = await client.post(
        f"/api/v1/partners/{p['id']}/documents/{doc['id']}/sign", json={}, headers=emp
    )
    assert r.status_code == 403, r.text
    # The document is UNCHANGED — the refusal happened before any write.
    detail = (await auth_client.get(f"/api/v1/partners/{p['id']}")).json()
    assert detail["documents"][0]["status"] == "draft"


@pytest.mark.asyncio
async def test_read_only_can_list_partners(auth_client, client):
    await _activate(auth_client)
    await _partner(auth_client)
    ro = await _member_headers(auth_client, client, "ro@acme.io", "user_free")
    r = await client.get("/api/v1/partners", headers=ro)
    assert r.status_code == 200, r.text
    assert [p["name"] for p in r.json()] == ["Meridian Freight"]


@pytest.mark.asyncio
async def test_read_only_cannot_update_partner(auth_client, client):
    await _activate(auth_client)
    p = await _partner(auth_client)
    ro = await _member_headers(auth_client, client, "ro2@acme.io", "user_free")
    r = await client.patch(f"/api/v1/partners/{p['id']}", json={"name": "Hijacked"}, headers=ro)
    assert r.status_code == 403, r.text
    assert (await auth_client.get(f"/api/v1/partners/{p['id']}")).json()[
        "name"
    ] == "Meridian Freight"


@pytest.mark.asyncio
async def test_partner_routes_still_require_issuing_module(auth_client):
    """The two gates are independent and BOTH live: a fully-permissioned OWNER
    (holds every Permission) with the issuing module disabled is still refused —
    by the module-entitlement gate, whose message names the module."""
    r = await auth_client.get("/api/v1/partners")
    assert r.status_code == 403, r.text
    assert "module is not activated" in r.json()["detail"]


@pytest.mark.asyncio
async def test_document_sign_is_audited(auth_client, db_session):
    await _activate(auth_client)
    p = await _partner(auth_client, requires_contract=True)
    doc = (
        await auth_client.post(
            f"/api/v1/partners/{p['id']}/documents",
            json={"kind": "contract", "title": "Framework agreement"},
        )
    ).json()
    r = await auth_client.post(
        f"/api/v1/partners/{p['id']}/documents/{doc['id']}/sign",
        json={"signed_by": "CEO", "signed_date": "2026-07-01"},
    )
    assert r.status_code == 200, r.text

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "partner.document_sign")
    )
    assert event is not None, "signing must emit a partner.document_sign audit event"
    assert event.actor_id is not None
    assert event.actor_email == "owner@acme.io"
    assert event.target_type == "partner_document"
    assert event.target_id == doc["id"]
    meta = json.loads(event.meta)
    assert meta["partner_id"] == p["id"]
    assert meta["kind"] == "contract"
    assert meta["signed_by"] == "CEO"
    assert meta["signed_date"] == "2026-07-01"


@pytest.mark.asyncio
async def test_cross_tenant_partner_returns_404(auth_client, client):
    """Invariant §4.4: tenant B fetching tenant A's partner id gets an opaque
    404 — indistinguishable from a nonexistent id — never a 403."""
    await _activate(auth_client)
    p = await _partner(auth_client)

    # A second, fully-independent org whose OWNER also has issuing enabled (so
    # the 404 cannot be explained by the module or permission gates).
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Rival BV",
            "name": "Rival Owner",
            "email": "owner@rival.io",
            "password": "supersecret",
        },
    )
    assert reg.status_code == 201, reg.text
    other = {"Authorization": f"Bearer {reg.json()['token']['access_token']}"}
    await client.put("/api/v1/issuer", json=ISSUER, headers=other)
    await client.put("/api/v1/modules/issuing", json={"enabled": True}, headers=other)

    r = await client.get(f"/api/v1/partners/{p['id']}", headers=other)
    assert r.status_code == 404, r.text
    # And B's list shows ZERO of A's rows.
    listing = await client.get("/api/v1/partners", headers=other)
    assert listing.status_code == 200 and listing.json() == []


@pytest.mark.asyncio
async def test_document_delete_is_audited_and_flips_readiness(auth_client, db_session):
    """WO-10 follow-up sweep: deleting a signed contract silently flips the
    partner readiness gate, so the deletion MUST leave an audit trail carrying
    the document's kind/title/status at the moment of deletion — the row itself
    is gone afterwards, the audit event is the only remaining record."""
    await _activate(auth_client)
    p = await _partner(auth_client, requires_contract=True)
    doc = (
        await auth_client.post(
            f"/api/v1/partners/{p['id']}/documents",
            json={"kind": "contract", "title": "Framework agreement"},
        )
    ).json()
    signed = (
        await auth_client.post(f"/api/v1/partners/{p['id']}/documents/{doc['id']}/sign", json={})
    ).json()
    assert signed["readiness"]["ready"] is True

    r = await auth_client.delete(f"/api/v1/partners/{p['id']}/documents/{doc['id']}")
    assert r.status_code == 204, r.text

    # The readiness gate flipped back — and the flip is attributable.
    detail = (await auth_client.get(f"/api/v1/partners/{p['id']}")).json()
    assert detail["readiness"]["ready"] is False

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "partner.document_delete")
    )
    assert event is not None, "deleting must emit a partner.document_delete audit event"
    assert event.actor_email == "owner@acme.io"
    assert event.target_type == "partner_document"
    assert event.target_id == doc["id"]
    meta = json.loads(event.meta)
    assert meta["partner_id"] == p["id"]
    assert meta["kind"] == "contract"
    assert meta["title"] == "Framework agreement"
    assert meta["status"] == "signed"


@pytest.mark.asyncio
async def test_document_delete_unknown_id_is_opaque_404(auth_client):
    """A nonexistent (or cross-tenant) document id yields an opaque 404 —
    matching the sign endpoint's contract (invariant §4.4) — and never a
    silent 204 that would mask a failed audit trail."""
    await _activate(auth_client)
    p = await _partner(auth_client)
    r = await auth_client.delete(f"/api/v1/partners/{p['id']}/documents/nonexistent-id")
    assert r.status_code == 404, r.text
