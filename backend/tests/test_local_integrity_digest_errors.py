"""Real local-source interruptions stay errors and recover without lost originals."""

import pytest
from sqlalchemy import select

from app.core import storage
from app.models.issuer import IssuerProfile
from app.services import documents
from tests.test_integrity import _upload_invoice, _upload_logo
from tests.test_local_storage_read_errors import PRIVATE_DIAGNOSTIC, _observe


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["read", "close"])
@pytest.mark.parametrize("error_type", [OSError, FileNotFoundError])
async def test_post_open_digest_io_failure_is_an_error_and_remaining_files_recover(
    auth_client, db_session, tmp_path, monkeypatch, stage, error_type
):
    await _upload_logo(auth_client)
    await _upload_invoice(auth_client)
    memory = storage.get_storage()
    assert isinstance(memory, storage.MemoryStorage)
    originals = dict(memory._data)
    store = storage.LocalStorage(str(tmp_path / "objects"))
    for key, data in originals.items():
        store.put(key, data)
    storage.set_storage(store)
    profile = await db_session.scalar(select(IssuerProfile))
    key = storage.content_key(documents.LOGOS, profile.org_id, profile.logo_sha256)
    path = store._path(key)
    faults = {f"{stage}_error": error_type(PRIVATE_DIAGNOSTIC)}
    if stage == "read":
        faults["successful_reads"] = 1
    with monkeypatch.context() as patch:
        observed = _observe(patch, path, **faults)
        response = await auth_client.post("/api/v1/integrity/documents/verify")
        assert response.status_code == 200
        body = response.json()
        assert body["checked"] == 2 and body["ok"] == 1 and not body["healthy"]
        assert len(body["issues"]) == 1
        assert body["issues"][0]["kind"] == "logo"
        assert body["issues"][0]["problem"] == "error"
        assert "Check storage and retry." in response.text
        assert PRIVATE_DIAGNOSTIC not in response.text
        assert key not in response.text and str(tmp_path) not in response.text
        assert len(observed) == 1 and observed[0].source.closed
        assert observed[0].close_calls == 1
        assert all(0 < request <= 65536 for request in observed[0].requests)
    assert all(store.get(key) == data for key, data in originals.items())
    healthy = await auth_client.post("/api/v1/integrity/documents/verify")
    assert healthy.status_code == 200
    assert healthy.json() == {"checked": 2, "ok": 2, "healthy": True, "issues": []}


@pytest.mark.asyncio
async def test_digest_cleanup_cannot_turn_primary_failure_into_missing(
    auth_client, db_session, tmp_path, monkeypatch
):
    await _upload_logo(auth_client)
    profile = await db_session.scalar(select(IssuerProfile))
    key = storage.content_key(documents.LOGOS, profile.org_id, profile.logo_sha256)
    original = storage.get_storage().get(key)
    store = storage.LocalStorage(str(tmp_path / "objects"))
    store.put(key, original)
    storage.set_storage(store)
    with monkeypatch.context() as patch:
        observed = _observe(
            patch,
            store._path(key),
            read_error=ValueError(PRIVATE_DIAGNOSTIC),
            close_error=FileNotFoundError(PRIVATE_DIAGNOSTIC),
        )
        response = await auth_client.post("/api/v1/integrity/documents/verify")
        assert response.status_code == 200
        body = response.json()
        assert body["checked"] == 1 and body["ok"] == 0 and not body["healthy"]
        assert len(body["issues"]) == 1 and body["issues"][0]["problem"] == "error"
        assert "ValueError: stored document verification failed." in response.text
        assert PRIVATE_DIAGNOSTIC not in response.text
        assert observed[0].source.closed and observed[0].close_calls == 1
    assert store.get(key) == original
    assert (await auth_client.post("/api/v1/integrity/documents/verify")).json()["healthy"]
