"""Stored issued-attachment reads must honor fresh access and visibility (PG)."""

import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceAttachment
from app.models.membership import Membership
from app.models.module import OrgModule
from app.models.organization import Organization
from app.models.session import Session
from app.models.user import User, UserRole
from app.services import documents
from tests.test_capture_admission_pg import quota_workspace as quota_workspace
from tests.test_project_profitability import _issuer_ready

pytestmark = pytest.mark.skipif(
    not os.environ.get("RLS_TEST_DATABASE_URL"), reason="requires migrated non-superuser PostgreSQL"
)


@pytest_asyncio.fixture
async def issued_attachment(quota_workspace):
    client, factory, org_id, _ = quota_workspace
    identity = (await client.get("/api/v1/auth/me")).json()
    uid = identity["user"]["id"]
    await _issuer_ready(client)
    created = await client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Synthetic attachment download buyer",
            "draft": True,
            "lines": [
                {
                    "description": "Synthetic attachment download work",
                    "quantity": "1",
                    "unit_price": "10",
                    "vat_rate": "0",
                }
            ],
        },
    )
    assert created.status_code == 201
    invoice_id = created.json()["id"]
    data = b"Synthetic supporting document for fresh attachment download checks."
    uploaded = await client.post(
        f"/api/v1/issued/{invoice_id}/attachments",
        headers={"Idempotency-Key": "synthetic-pg-download-attachment"},
        files={"file": ("synthetic.txt", data, "text/plain")},
    )
    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["size"] == len(data)
    return client, factory, org_id, uid, invoice_id, attachment["id"], data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected_status"),
    [
        ("user", 401),
        ("membership", 401),
        ("session", 401),
        ("organization", 401),
        ("permission", 403),
        ("module", 403),
    ],
)
async def test_access_rechecked_after_issued_attachment_read(
    issued_attachment, monkeypatch, change, expected_status
):
    client, factory, org_id, uid, invoice_id, attachment_id, data = issued_attachment
    original = documents.load
    observed_limits = []

    async def revoke_after_read(*args, **kwargs):
        content = await original(*args, **kwargs)
        assert content == data
        observed_limits.append(kwargs.get("max_bytes"))
        model, condition, values = {
            "user": (User, User.id == uid, {"is_active": False}),
            "membership": (
                Membership,
                (Membership.user_id == uid) & (Membership.org_id == org_id),
                {"status": "suspended"},
            ),
            "session": (Session, Session.user_id == uid, {"revoked_at": datetime.now(UTC)}),
            "organization": (Organization, Organization.id == org_id, {"status": "suspended"}),
            "permission": (
                Membership,
                (Membership.user_id == uid) & (Membership.org_id == org_id),
                {"role": UserRole.user},
            ),
            "module": (
                OrgModule,
                (OrgModule.org_id == org_id) & (OrgModule.key == "issuing"),
                {"enabled": False},
            ),
        }[change]
        async with factory() as db:
            result = await db.execute(update(model).where(condition).values(**values))
            assert result.rowcount == 1
            await db.commit()
        return content

    monkeypatch.setattr(documents, "load", revoke_after_read)
    response = await client.get(f"/api/v1/issued/{invoice_id}/attachments/{attachment_id}/download")
    assert observed_limits == [len(data)]
    assert response.status_code == expected_status
    if expected_status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert "content-disposition" not in response.headers
    assert data not in response.content


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["binned", "removed", "invoice_removed"])
async def test_visibility_rechecked_after_issued_attachment_read(
    issued_attachment, monkeypatch, change
):
    client, factory, _, _, invoice_id, attachment_id, data = issued_attachment
    original = documents.load
    observed_limits = []

    async def remove_after_read(*args, **kwargs):
        content = await original(*args, **kwargs)
        assert content == data
        observed_limits.append(kwargs.get("max_bytes"))
        async with factory() as db:
            if change == "binned":
                statement = (
                    update(IssuedInvoiceAttachment)
                    .where(IssuedInvoiceAttachment.id == attachment_id)
                    .values(deleted_at=datetime.now(UTC), deleted_by="synthetic@example.com")
                )
            elif change == "removed":
                statement = delete(IssuedInvoiceAttachment).where(
                    IssuedInvoiceAttachment.id == attachment_id
                )
            else:
                statement = delete(IssuedInvoice).where(IssuedInvoice.id == invoice_id)
            result = await db.execute(statement)
            assert result.rowcount == 1
            await db.commit()
        return content

    monkeypatch.setattr(documents, "load", remove_after_read)
    response = await client.get(f"/api/v1/issued/{invoice_id}/attachments/{attachment_id}/download")
    assert observed_limits == [len(data)]
    assert response.status_code == 404
    assert "content-disposition" not in response.headers
    assert data not in response.content
