"""CAP-CAPACITY: exhausted multipart staging is server capacity, not bad input."""

import errno

import pytest
from fastapi import FastAPI, File, HTTPException, UploadFile
from httpx import ASGITransport, AsyncClient
from starlette import formparsers

from app.api.attachment_body_limit import AttachmentBodyLimitMiddleware
from app.core.config import settings
from app.main import app as production_app


def application(path):
    app = FastAPI(exception_handlers=production_app.exception_handlers)
    handled = []

    @app.post(path)
    async def upload(files: list[UploadFile] = File()):
        handled.append([await file.read() for file in files])
        return {"received": len(files)}

    app.add_middleware(AttachmentBodyLimitMiddleware)
    return app, handled


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [errno.ENOSPC, errno.EDQUOT])
@pytest.mark.parametrize(
    "path",
    ["/api/v1/invoices/upload", "/api/v1/invoices/upload/batch", "/api/v1/email/inbound/mailgun"],
)
async def test_full_spool_is_retryable_and_closes_all_partial_files(monkeypatch, path, fault):
    monkeypatch.setattr(settings, "upload_max_active_requests", 1)
    original = formparsers.SpooledTemporaryFile
    spools = []
    exhausted = True
    count = 2 if path.endswith(("batch", "mailgun")) else 1

    class Spool(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            spools.append(self)

        def write(self, data):
            result = super().write(data)
            if exhausted and len(spools) == count:
                raise OSError(fault, "private synthetic path must not be returned")
            return result

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", Spool)
    monkeypatch.setattr(formparsers.MultiPartParser, "spool_max_size", 32)
    app, handled = application(path)
    files = [("files", ("a.csv", b"a" * 128)), ("files", ("b.csv", b"b" * 128))][:count]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, files=files)
        assert response.status_code == 503, response.text
        assert response.json()["code"] == "upload_storage_full"
        assert "Check upload status before retrying" in response.json()["detail"]
        assert "private" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["retry-after"] == "1"
        assert not handled
        assert len(spools) == count and all(file.closed for file in spools)
        assert spools[0]._rolled
        exhausted = False
        recovered = await client.post(path, files=files)
        assert recovered.status_code == 200
        assert handled == [[b"a" * 128, b"b" * 128][:count]]
        assert all(file.closed for file in spools)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [errno.EACCES, errno.EIO])
async def test_other_parse_io_errors_keep_the_framework_contract(monkeypatch, fault):
    def unavailable(*args, **kwargs):
        raise OSError(fault, "synthetic internal information")

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", unavailable)
    path = "/api/v1/invoices/upload"
    app, handled = application(path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, files={"files": ("a.csv", b"a")})
    assert response.status_code == 400
    assert response.json() == {"detail": "There was an error parsing the body"}
    assert not handled


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/invoices/upload", "/api/v1/ordinary"])
@pytest.mark.parametrize("caused", [False, True])
async def test_unrelated_bad_requests_are_not_reclassified(path, caused):
    app = FastAPI(exception_handlers=production_app.exception_handlers)

    @app.post(path)
    async def reject():
        if caused:
            raise HTTPException(
                400, "Existing contract", headers={"X-Synthetic": "yes"}
            ) from OSError(errno.ENOSPC, "synthetic capacity")
        raise HTTPException(400, "Existing contract", headers={"X-Synthetic": "yes"})

    app.add_middleware(AttachmentBodyLimitMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path)
    assert response.status_code == 400
    assert response.json() == {"detail": "Existing contract"}
    assert response.headers["x-synthetic"] == "yes"


@pytest.mark.asyncio
async def test_capacity_failure_on_unclassified_form_keeps_bad_request(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic internal information")

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", unavailable)
    app, handled = application("/api/v1/ordinary")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/ordinary", files={"files": ("a.csv", b"a")})
    assert response.status_code == 400
    assert response.json() == {"detail": "There was an error parsing the body"}
    assert not handled


@pytest.mark.asyncio
async def test_authentication_challenge_keeps_its_status_body_and_headers():
    app = FastAPI(exception_handlers=production_app.exception_handlers)

    @app.post("/api/v1/invoices/upload")
    async def reject():
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})

    app.add_middleware(AttachmentBodyLimitMiddleware)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/invoices/upload")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_malformed_multipart_stays_a_bad_request():
    app, handled = application("/api/v1/invoices/upload")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/invoices/upload",
            content=b"invalid",
            headers={"Content-Type": "multipart/form-data"},
        )
    assert response.status_code == 400
    assert "upload_storage_full" not in response.text
    assert not handled
