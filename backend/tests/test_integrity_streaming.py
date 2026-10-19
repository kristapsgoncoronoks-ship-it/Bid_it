"""Historical original verification uses bounded memory without an upload-size cutoff."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import storage
from app.core.config import settings
from app.models.issuer import IssuerProfile
from app.services import documents
from tests.test_integrity import _PNG, _upload_logo


@pytest.mark.asyncio
async def test_historical_verification_uses_finite_reads_and_recovers(
    auth_client, db_session, tmp_path, monkeypatch
):
    await _upload_logo(auth_client)
    profile = await db_session.scalar(select(IssuerProfile))
    original = _PNG + b"\0" * (1536 * 1024 - len(_PNG))
    admitted = await auth_client.post(
        "/api/v1/issuer/logo", files={"file": ("historical.png", original, "image/png")}
    )
    assert admitted.status_code == 200
    await db_session.refresh(profile)
    assert profile.logo_sha256 == hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    assert len(original) > 1024 * 1024
    store = storage.LocalStorage(str(tmp_path / "objects"))
    sha = hashlib.sha256(original).hexdigest()
    key = storage.content_key(documents.LOGOS, profile.org_id, sha)
    store.put(key, original)
    profile.logo_sha256, profile.logo_size = sha, len(original)
    await db_session.commit()
    storage.set_storage(store)
    source_path = store._path(key)
    original_open = Path.open
    handles = []

    class Source:
        def __init__(self, file):
            self.file = file
            self.requested = []
            handles.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def read(self, size=-1):
            assert 0 < size <= 65536, "unbounded integrity object read"
            self.requested.append(size)
            return self.file.read(size)

    def observed_open(path, *args, **kwargs):
        file = original_open(path, *args, **kwargs)
        return Source(file) if path == source_path and args == ("rb",) else file

    monkeypatch.setattr(Path, "open", observed_open)
    response = await auth_client.post("/api/v1/integrity/documents/verify")
    assert response.status_code == 200
    assert response.json() == {"checked": 1, "ok": 1, "healthy": True, "issues": []}
    assert handles and all(h.file.closed and len(h.requested) > 1 for h in handles)
    with original_open(source_path, "wb") as out:
        out.write(b"!" + original[1:])
    changed = (await auth_client.post("/api/v1/integrity/documents/verify")).json()
    assert changed["ok"] == 0 and changed["issues"][0]["problem"] == "mismatch"
    with original_open(source_path, "wb") as out:
        out.write(original)
    restored = (await auth_client.post("/api/v1/integrity/documents/verify")).json()
    assert restored["healthy"] and restored["ok"] == 1
    assert all(h.file.closed for h in handles)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["storage", "unexpected"])
async def test_failed_read_is_sanitized_error_and_remaining_objects_are_verified(
    auth_client, monkeypatch, failure
):
    from tests.test_integrity import _upload_invoice

    await _upload_logo(auth_client)
    await _upload_invoice(auth_client)
    real_digest = documents.digest

    async def digest(prefix, org_id, sha):
        if prefix == documents.LOGOS:
            if failure == "storage":
                raise storage.StorageReadError("synthetic-private-provider-token")
            raise ValueError("synthetic-private-provider-token")
        return await real_digest(prefix, org_id, sha)

    monkeypatch.setattr(documents, "digest", digest)
    result = await auth_client.post("/api/v1/integrity/documents/verify")
    assert result.status_code == 200
    body = result.json()
    assert body["checked"] == 2 and body["ok"] == 1 and not body["healthy"]
    assert len(body["issues"]) == 1 and body["issues"][0]["problem"] == "error"
    assert "synthetic-private-provider-token" not in result.text
