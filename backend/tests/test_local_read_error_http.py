"""Filesystem I/O failure gives retry guidance without partial or missing downloads."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core import storage
from app.main import app
from app.models.audit import AuditEvent
from app.models.issuer import IssuerProfile
from app.services import documents, modules
from tests.test_capture_page_read_bounds import _page
from tests.test_issued_attachment_read_bounds import _attachment
from tests.test_storage import _PNG

PRIVATE = "synthetic-private-filesystem-diagnostic"


async def _download(client, db, tmp_path, surface):
    store = storage.LocalStorage(str(tmp_path / "objects"))
    storage.set_storage(store)
    org_id = (await client.get("/api/v1/auth/me")).json()["organization"]["id"]
    if surface == "issued":
        _, url, key = await _attachment(client, db)
    elif surface in {"page", "source"}:
        run, _, page_key = await _page(client, db)
        key = (
            page_key
            if surface == "page"
            else storage.content_key(documents.UPLOADS, org_id, run.source_sha256)
        )
        suffix = "pages/1/doc" if surface == "page" else "source"
        url = f"/api/v1/invoices/captures/{run.id}/{suffix}"
    elif surface == "logo":
        response = await client.post(
            "/api/v1/issuer/logo", files={"file": ("synthetic.png", _PNG, "image/png")}
        )
        assert response.status_code == 200
        profile = await db.scalar(select(IssuerProfile))
        key = storage.content_key(documents.LOGOS, org_id, profile.logo_sha256)
        url = "/api/v1/issuer/logo"
    else:
        await modules.set_enabled(db, org_id, "transport", True)
        sha, _ = await documents.store(
            documents.STATEMENTS,
            org_id,
            b"Synthetic statement original",
            "text/csv",
            db=db,
            filename="synthetic.csv",
        )
        await db.commit()
        key = storage.content_key(documents.STATEMENTS, org_id, sha)
        url = f"/api/v1/transport/statements/{sha}/file"
    return store, url, key


def _fault(monkeypatch, path, failure):
    original_open = Path.open
    sources = []

    class Source:
        def __init__(self, file):
            self.file = file
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def read(self, size=-1):
            if failure in {"read", "late_missing"}:
                if size > 0 and self.reads == 0:
                    self.reads += 1
                    return self.file.read(1)
                error = FileNotFoundError if failure == "late_missing" else OSError
                raise error(PRIVATE)
            return self.file.read(size)

        def close(self):
            self.file.close()
            if failure == "close":
                raise OSError(PRIVATE)

    def open_file(candidate, *args, **kwargs):
        if candidate == path and args == ("rb",):
            if failure == "open":
                raise PermissionError(PRIVATE)
            if failure == "typed":
                raise storage.StorageReadError(PRIVATE)
            source = Source(original_open(candidate, *args, **kwargs))
            sources.append(source)
            return source
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    return sources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "failure"),
    [(s, "read") for s in ("issued", "source", "page", "logo", "statement")]
    + [("issued", f) for f in ("open", "close", "late_missing")]
    + [("logo", "typed")],
)
async def test_local_failure_refuses_with_sanitized_retry_then_recovers(
    auth_client, db_session, tmp_path, monkeypatch, caplog, surface, failure
):
    store, url, key = await _download(auth_client, db_session, tmp_path, surface)
    original = store.get(key)
    before = await db_session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "document.download")
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers=dict(auth_client.headers),
    ) as client:
        with monkeypatch.context() as patch:
            sources = _fault(patch, store._path(key), failure)
            caplog.clear()
            refused = await client.get(url)
            assert refused.status_code == 503
            assert "retry" in refused.json()["detail"].lower()
            assert "content-disposition" not in refused.headers
            assert "x-request-id" in refused.headers
            assert PRIVATE not in refused.text and PRIVATE not in caplog.text
            assert key not in refused.text and str(tmp_path) not in refused.text
            assert all(s.file.closed for s in sources)
            if surface != "issued":
                assert refused.json()["code"] == "storage_read_unavailable"
                assert refused.headers["cache-control"] == "private, no-store"
        assert store.get(key) == original
        after = await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "document.download")
        )
        assert after == before
        recovered = await client.get(url)
        assert recovered.status_code == 200 and recovered.content == original
        assert recovered.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["source", "page", "statement"])
async def test_actual_missing_source_remains_opaque_404(auth_client, db_session, tmp_path, surface):
    store, url, key = await _download(auth_client, db_session, tmp_path, surface)
    store.delete(key)
    response = await auth_client.get(url)
    assert response.status_code == 404
    assert "content-disposition" not in response.headers
    assert key not in response.text and str(tmp_path) not in response.text
