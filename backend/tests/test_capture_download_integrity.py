"""Original download serves verified bytes, including legacy registry fallback."""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core import storage
from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.services import documents

CSV = b"description,quantity,unit_price,tax_rate\nSynthetic download source,1,100,21\n"


@pytest_asyncio.fixture
async def original(auth_client, db_session):
    accepted = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("original.csv", CSV, "text/csv")}
    )
    assert accepted.status_code == 202
    run_id = accepted.json()["extraction_run_id"]
    run = await db_session.get(ExtractionRun, run_id)
    return f"/api/v1/invoices/captures/{run_id}/source", run


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["bytes", "registry_size"])
async def test_corrupt_original_is_not_returned_as_a_success(
    auth_client, db_session, original, corruption
):
    path, run = original
    if corruption == "bytes":
        storage.get_storage().put(
            storage.content_key(documents.UPLOADS, run.org_id, run.source_sha256),
            b"X" + CSV[1:],
            "text/csv",
        )
    else:
        row = await db_session.scalar(select(Document).where(Document.kind == documents.UPLOADS))
        row.size += 1
        await db_session.commit()
    response = await auth_client.get(path)
    assert response.status_code == 503
    assert "integrity" in response.json()["detail"].lower()
    assert "restore" in response.json()["detail"].lower()
    assert "content-disposition" not in response.headers


@pytest.mark.asyncio
async def test_valid_original_preserves_bytes_and_has_private_cache_policy(auth_client, original):
    path, _ = original
    response = await auth_client.get(path)
    assert response.status_code == 200
    assert response.content == CSV
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_legacy_missing_registry_still_requires_valid_source_hash(
    auth_client, db_session, original
):
    path, _ = original
    await db_session.execute(delete(Document).where(Document.kind == documents.UPLOADS))
    await db_session.commit()
    response = await auth_client.get(path)
    assert response.status_code == 200 and response.content == CSV
    assert response.headers["content-type"] == "application/octet-stream"
