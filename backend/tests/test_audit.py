"""Immutable, hash-chained audit trail — attribution, tamper-evidence, access."""

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent


def _inv(n):
    return {
        "vendor_name": "Acme",
        "invoice_number": f"AUD-{n}",
        "issue_date": "2026-07-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }


@pytest.mark.asyncio
async def test_actions_are_recorded_and_chained(auth_client, db_session):
    # register already logged one event; a login + two invoice creates add more.
    await auth_client.post("/api/v1/invoices", json=_inv(1))
    await auth_client.post("/api/v1/invoices", json=_inv(2))

    audit = (await auth_client.get("/api/v1/audit")).json()
    actions = [e["action"] for e in audit["items"]]
    assert "invoice.create" in actions
    assert "auth.register" in actions
    assert audit["total"] >= 3

    # seq is monotonic and the chain links prev->hash.
    rows = list(await db_session.scalars(select(AuditEvent).order_by(AuditEvent.seq)))
    assert [r.seq for r in rows] == list(range(1, len(rows) + 1))
    prev = None
    for r in rows:
        assert r.prev_hash == (prev.hash if prev else None)
        prev = r


@pytest.mark.asyncio
async def test_verify_ok_then_detects_tampering(auth_client, db_session):
    await auth_client.post("/api/v1/invoices", json=_inv(9))
    ok = (await auth_client.get("/api/v1/audit/verify")).json()
    assert ok["ok"] is True and ok["events"] >= 2

    # Tamper with a stored event's metadata; the chain must break.
    row = await db_session.scalar(select(AuditEvent).where(AuditEvent.action == "invoice.create"))
    row.meta = '{"number":"HACKED"}'
    await db_session.commit()

    broken = (await auth_client.get("/api/v1/audit/verify")).json()
    assert broken["ok"] is False
    assert broken["broken_at_seq"] == row.seq


@pytest.mark.asyncio
async def test_audit_requires_audit_read(auth_client, client):
    # AUDIT_READ (matrix-aligned): a plain user has none → denied; an administrator
    # holds it → allowed.
    inv = await auth_client.post(
        "/api/v1/team/invites", json={"email": "u@acme.io", "role": "user"}
    )
    utok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv.json()["token"], "name": "U", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    r = await client.get("/api/v1/audit", headers={"Authorization": f"Bearer {utok}"})
    assert r.status_code == 403

    inv2 = await auth_client.post(
        "/api/v1/team/invites", json={"email": "a@acme.io", "role": "admin"}
    )
    atok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv2.json()["token"], "name": "A", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    r2 = await client.get("/api/v1/audit", headers={"Authorization": f"Bearer {atok}"})
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_document_download_is_logged(auth_client, client, monkeypatch):
    # Activate email intake, ingest a CSV, confirm, then download the original.
    from app.core.config import settings

    monkeypatch.setattr(settings, "inbound_email_secret", "audit-test-secret")
    await auth_client.put("/api/v1/modules/email_intake", json={"enabled": True})
    addr = (await auth_client.get("/api/v1/email/settings")).json()["address"]
    token = addr.split("@")[0]
    import base64

    csv = base64.b64encode(b"vendor,invoice_number,amount\nAcme,DL-1,10.00\n").decode()
    await client.post(
        "/api/v1/email/inbound",
        json={"token": token, "attachments": [{"filename": "d.csv", "content_base64": csv}]},
        headers={"X-Inbound-Secret": "audit-test-secret"},
    )
    row = (await auth_client.get("/api/v1/email/inbox")).json()["items"][0]
    dl = await auth_client.get(f"/api/v1/email/inbox/{row['id']}/file")
    assert dl.status_code == 200

    events = (await auth_client.get("/api/v1/audit?action=document.download")).json()
    assert events["total"] == 1
    assert events["items"][0]["target_id"] == row["id"]


@pytest.mark.asyncio
async def test_audit_is_tenant_scoped(auth_client, client):
    await auth_client.post("/api/v1/invoices", json=_inv(3))
    # A second workspace sees only its own events (just its own register).
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Beta",
            "name": "B",
            "email": "b@beta.io",
            "password": "supersecret",
        },
    )
    tok = reg.json()["token"]["access_token"]
    other = (await client.get("/api/v1/audit", headers={"Authorization": f"Bearer {tok}"})).json()
    assert all(e["action"] == "auth.register" for e in other["items"])
    assert other["total"] == 1
