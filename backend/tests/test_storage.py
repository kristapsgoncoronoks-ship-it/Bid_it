"""Object-storage abstraction (ADR-0008): the backends behave identically, keys
are content-addressed, and document bytes leave the database on write."""

import hashlib

import pytest

from app.core import storage
from app.services import documents


def test_content_key_is_sharded_and_tenant_prefixed():
    sha = "a" * 64
    key = storage.content_key("receipts", "org-1", sha)
    assert key == f"receipts/org-1/aa/aa/{sha}"


def test_sha256_hex_matches_hashlib():
    assert storage.sha256_hex(b"hello") == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.parametrize("backend", ["memory", "local"])
def test_backends_roundtrip(backend, tmp_path):
    store = storage.MemoryStorage() if backend == "memory" else storage.LocalStorage(str(tmp_path))
    store.put("a/b/c", b"payload", "text/plain")
    assert store.exists("a/b/c")
    assert store.get("a/b/c") == b"payload"
    store.delete("a/b/c")
    assert not store.exists("a/b/c")


def test_get_missing_raises_storage_error():
    store = storage.MemoryStorage()
    with pytest.raises(storage.StorageError):
        store.get("nope")


def test_local_storage_rejects_path_escape(tmp_path):
    store = storage.LocalStorage(str(tmp_path))
    with pytest.raises(storage.StorageError):
        store.put("../escape", b"x")


@pytest.mark.asyncio
async def test_documents_store_and_load_roundtrip():
    # The autouse conftest fixture installs a MemoryStorage.
    data = b"%PDF-1.4 fake receipt"
    sha, size = await documents.store(documents.RECEIPTS, "org-9", data, "application/pdf")
    assert sha == storage.sha256_hex(data)
    assert size == len(data)
    loaded = await documents.load(documents.RECEIPTS, "org-9", sha)
    assert loaded == data


@pytest.mark.asyncio
async def test_documents_load_returns_none_without_reference():
    # No sha256 reference → nothing to load (the legacy in-DB blob was dropped).
    assert await documents.load(documents.RECEIPTS, "org-9", None) is None


# --- End-to-end: bytes go to storage, NOT the database -------------------------

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "email": "b@i.test",
}


@pytest.mark.asyncio
async def test_logo_bytes_leave_the_database(auth_client, db_session):
    from sqlalchemy import select

    from app.models.issuer import IssuerProfile

    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    up = await auth_client.post(
        "/api/v1/issuer/logo", files={"file": ("logo.png", _PNG, "image/png")}
    )
    assert up.status_code == 200
    assert up.json()["has_logo"] is True

    # The DB row holds the sha256 reference, NOT the bytes.
    row = await db_session.scalar(select(IssuerProfile))
    assert row.logo_sha256 == storage.sha256_hex(_PNG)
    assert row.logo_size == len(_PNG)

    # And the serve endpoint reads them back from storage.
    got = await auth_client.get("/api/v1/issuer/logo")
    assert got.status_code == 200
    assert got.content == _PNG


@pytest.mark.asyncio
async def test_receipt_bytes_leave_the_database(auth_client, db_session):
    from sqlalchemy import select

    from app.models.expense import ExpenseItem

    await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    rep = await auth_client.post(
        "/api/v1/expenses",
        json={
            "title": "T",
            "items": [
                {
                    "spend_date": "2026-05-05",
                    "category": "travel",
                    "description": "Taxi",
                    "amount": "20.00",
                    "comment": "client visit",
                }
            ],
        },
    )
    report = rep.json()
    item_id = report["items"][0]["id"]
    up = await auth_client.post(
        f"/api/v1/expenses/{report['id']}/items/{item_id}/receipt",
        files={"file": ("r.png", _PNG, "image/png")},
    )
    assert up.status_code == 200

    item = await db_session.scalar(select(ExpenseItem).where(ExpenseItem.id == item_id))
    assert item.receipt_sha256 == storage.sha256_hex(_PNG)

    got = await auth_client.get(f"/api/v1/expenses/{report['id']}/items/{item_id}/receipt")
    assert got.status_code == 200 and got.content == _PNG
