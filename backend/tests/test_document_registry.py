"""Document registry (Slice 5d): every stored object is registered at the storage
choke point; content-addressed dedup (same bytes+kind → one row, touched);
different kinds are separate; the list API is admin-gated."""

import hashlib

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import document_registry, documents


async def _org(db):
    return await db.scalar(select(Organization.id))


@pytest.mark.asyncio
async def test_store_registers_document(auth_client, db_session):
    org_id = await _org(db_session)
    await documents.store(
        documents.RECEIPTS,
        org_id,
        b"hello",
        "image/png",
        db=db_session,
        filename="r.png",
        uploaded_by="u@x.io",
    )
    await db_session.commit()
    docs = await document_registry.list_for_org(db_session, org_id)
    assert len(docs) == 1
    d = docs[0]
    assert d.kind == "receipts" and d.size == 5
    assert d.filename == "r.png" and d.uploaded_by == "u@x.io"
    assert d.sha256 == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.asyncio
async def test_dedup_by_content_and_kind(auth_client, db_session):
    org_id = await _org(db_session)
    await documents.store(documents.RECEIPTS, org_id, b"x", "image/png", db=db_session)
    # Same bytes + kind → touches the existing row (updates filename), no duplicate.
    await documents.store(
        documents.RECEIPTS, org_id, b"x", "image/png", db=db_session, filename="new.png"
    )
    await db_session.commit()
    docs = await document_registry.list_for_org(db_session, org_id)
    assert len(docs) == 1 and docs[0].filename == "new.png"

    # Same bytes, DIFFERENT kind → a separate registry row.
    await documents.store(documents.LOGOS, org_id, b"x", "image/png", db=db_session)
    await db_session.commit()
    assert len(await document_registry.list_for_org(db_session, org_id)) == 2
    assert len(await document_registry.list_for_org(db_session, org_id, kind="logos")) == 1


@pytest.mark.asyncio
async def test_store_without_db_does_not_register(auth_client, db_session):
    org_id = await _org(db_session)
    await documents.store(documents.RECEIPTS, org_id, b"nope", "image/png")  # no db
    assert await document_registry.list_for_org(db_session, org_id) == []


@pytest.mark.asyncio
async def test_list_api_admin_only(auth_client, client, db_session):
    org_id = await _org(db_session)
    await documents.store(documents.RECEIPTS, org_id, b"abc", "image/png", db=db_session)
    await db_session.commit()

    r = await auth_client.get("/api/v1/documents")
    assert r.status_code == 200 and len(r.json()) == 1
    assert (await auth_client.get("/api/v1/documents?kind=logos")).json() == []

    inv = await auth_client.post("/api/v1/team/invites", json={"email": "u@x.io", "role": "user"})
    tok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv.json()["token"], "name": "U", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    forbidden = await client.get("/api/v1/documents", headers={"Authorization": f"Bearer {tok}"})
    assert forbidden.status_code == 403
