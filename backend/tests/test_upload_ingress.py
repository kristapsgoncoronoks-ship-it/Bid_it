"""CAP-INGRESS: real Starlette parsing must stop before unbounded spooling."""

import asyncio
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.attachment_body_limit import AttachmentBodyLimitMiddleware
from app.core.config import settings
from app.services import filesec

SINGLE = [
    ("/invoices/upload", None),
    ("/invoices/synthetic/attachments", None),
    ("/masters/projects/synthetic/documents", None),
    ("/issuer/logo", "logo"),
    ("/issued/synthetic/attachments", None),
    ("/reconciliation/import", None),
    ("/expenses/import/bank-statement", None),
    ("/expenses/receipt-scan", "receipt"),
    ("/expenses/synthetic/items/synthetic/receipt", "receipt"),
    ("/transport/customers/synthetic/documents", None),
    ("/transport/statements", None),
    ("/transport/estimate", None),
]


def multipart(parts):
    body = b""
    for name, data in parts:
        body += (
            b'--synthetic\r\nContent-Disposition: form-data; name="'
            + name.encode()
            + b'"; filename="synthetic.csv"\r\nContent-Type: text/csv\r\n\r\n'
            + data
            + b"\r\n"
        )
    return body + b"--synthetic--\r\n"


HEADERS = {"Content-Type": "multipart/form-data; boundary=synthetic"}


@pytest.fixture
def budget(monkeypatch):
    monkeypatch.setattr(
        filesec, "max_bytes", lambda purpose=None: {None: 64, "receipt": 32, "logo": 16}[purpose]
    )
    monkeypatch.setitem(filesec.RESOURCE_BYTES, "multipart_overhead", 512)
    monkeypatch.setitem(settings.__dict__, "upload_max_active_requests", 2)
    monkeypatch.setitem(settings.__dict__, "upload_max_body_mb", None)


def application(path):
    app = FastAPI()
    handled = []

    async def receive_form(request: Request):
        if path == "/email/inbound":
            handled.append(await request.json())
            return JSONResponse({"received": True})
        async with request.form() as form:
            handled.append(form)
            return JSONResponse(
                {"files": sum(hasattr(v, "filename") for _, v in form.multi_items())}
            )

    app.add_route(settings.api_v1_prefix + path, receive_form, methods=["POST"])
    app.add_middleware(AttachmentBodyLimitMiddleware)
    return app, handled


@pytest.mark.asyncio
@pytest.mark.parametrize("path,purpose", SINGLE)
@pytest.mark.parametrize("declared", [True, False], ids=["declared", "chunked"])
async def test_every_single_upload_refuses_envelope_before_handler(budget, path, purpose, declared):
    app, handled = application(path)
    body = multipart([("file", b"x" * 4096)])
    consumed = 0

    async def stream():
        nonlocal consumed
        for offset in range(0, len(body), 64):
            consumed += 64
            yield body[offset : offset + 64]
            await asyncio.sleep(0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            settings.api_v1_prefix + path, content=body if declared else stream(), headers=HEADERS
        )
    assert response.status_code == 413
    assert handled == []
    assert consumed == 0 if declared else consumed < len(body)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,max_files", [("/invoices/upload/batch", 25), ("/email/inbound/mailgun", 20)]
)
async def test_excess_file_parts_refuse_before_handler(budget, monkeypatch, path, max_files):
    monkeypatch.setitem(filesec.RESOURCE_BYTES, "multipart_overhead", 8192)
    app, handled = application(path)
    body = multipart([("files", b"")] * (max_files + 1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(settings.api_v1_prefix + path, content=body, headers=HEADERS)
    assert response.status_code == 422
    assert handled == []


@pytest.mark.asyncio
async def test_busy_upload_does_not_read_or_queue_and_health_still_works(budget):
    app, handled = application("/invoices/upload")
    started = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    busy_read = False

    async def excess():
        nonlocal busy_read
        busy_read = True
        yield multipart([("file", b"x")])

    async def stalled(index):
        started[index].set()
        await release.wait()
        yield multipart([("file", b"ok")])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        active = [
            asyncio.create_task(
                client.post("/api/v1/invoices/upload", content=stalled(i), headers=HEADERS)
            )
            for i in range(2)
        ]
        try:
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started)), timeout=2)
            busy = await client.post("/api/v1/invoices/upload", content=excess(), headers=HEADERS)
            assert busy.status_code == 503
            assert busy.headers["retry-after"] == "1"
            assert busy.json()["code"] == "upload_busy"
            assert handled == []
            assert not busy_read
            assert (await client.get("/health")).status_code == 404  # bypasses upload admission
        finally:
            release.set()
            await asyncio.gather(*active)
        recovered = await client.post(
            "/api/v1/invoices/upload", content=multipart([("file", b"ok")]), headers=HEADERS
        )
        assert recovered.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,max_files",
    [("/invoices/upload/batch", 25), ("/email/inbound/mailgun", 20), ("/invoices/upload", 1)],
)
async def test_exact_part_count_accepts_and_closes_all_files(budget, monkeypatch, path, max_files):
    monkeypatch.setitem(filesec.RESOURCE_BYTES, "multipart_overhead", 8192)
    app, handled = application(path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1" + path, content=multipart([("files", b"ok")] * max_files), headers=HEADERS
        )
    assert response.status_code == 200
    assert response.json()["files"] == max_files
    assert all(v.file.closed for _, v in handled[0].multi_items())


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["oversize", "io-error", "cancelled", "disconnect", "count"])
async def test_partial_spools_close_and_admission_recovers(budget, monkeypatch, fault):
    from starlette import formparsers
    from starlette.requests import ClientDisconnect

    original = formparsers.SpooledTemporaryFile
    spools = []

    def spool(*args, **kwargs):
        f = original(*args, **kwargs)
        spools.append(f)
        return f

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", spool)
    monkeypatch.setattr(formparsers.MultiPartParser, "spool_max_size", 32)
    monkeypatch.setattr(settings, "upload_max_active_requests", 1)
    app, handled = application("/invoices/upload")
    body = multipart([("file", b"x" * 256), ("file", b"x" * 256)])

    async def broken():
        prefix = body if fault in {"oversize", "count"} else body[:350]
        if fault == "oversize":
            prefix = multipart([("file", b"x" * 4096)])
        for offset in range(0, len(prefix), 32):
            yield prefix[offset : offset + 32]
            await asyncio.sleep(0)
        if fault == "io-error":
            raise OSError("Synthetic ingress failure")
        if fault == "cancelled":
            raise asyncio.CancelledError()
        if fault == "disconnect":
            raise ClientDisconnect()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        if fault in {"oversize", "count"}:
            response = await client.post(
                "/api/v1/invoices/upload", content=broken(), headers=HEADERS
            )
            assert response.status_code == (413 if fault == "oversize" else 422)
        else:
            error = {
                "io-error": OSError,
                "cancelled": asyncio.CancelledError,
                "disconnect": ClientDisconnect,
            }[fault]
            with pytest.raises(error):
                await client.post("/api/v1/invoices/upload", content=broken(), headers=HEADERS)
        assert spools and any(f._rolled for f in spools)
        assert all(f.closed for f in spools)
        assert handled == []
        recovered = await client.post(
            "/api/v1/invoices/upload", content=multipart([("file", b"ok")]), headers=HEADERS
        )
        assert recovered.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_json_webhook_envelope_is_bounded_before_buffering(budget, declared):
    app, handled = application("/email/inbound")
    body = b'{"subject":"' + b"x" * 4096 + b'"}'
    consumed = 0

    async def stream():
        nonlocal consumed
        for offset in range(0, len(body), 64):
            consumed += 64
            yield body[offset : offset + 64]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/email/inbound",
            content=body if declared else stream(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413 and handled == []
    assert consumed == 0 if declared else consumed < len(body)


def test_every_documented_multipart_path_has_an_ingress_policy():
    from app.main import app

    middleware = AttachmentBodyLimitMiddleware(app)
    paths = {
        path
        for path, ops in app.openapi()["paths"].items()
        if "multipart/form-data" in ops.get("post", {}).get("requestBody", {}).get("content", {})
    }
    assert len(paths) == 13  # semantic inventory change requires review
    assert all(middleware.policy_for(path) is not None for path in paths)
    assert middleware.policy_for("/api/v1/email/inbound/mailgun").max_files == 20
    assert middleware.policy_for("/api/v1/email/inbound").encoded
    assert middleware.policy_for("/api/v1/invoices/upload-policy") is None
    assert middleware.policy_for("/api/v1/invoices/upload/extra") is None


def test_effective_policy_reserves_envelope_and_preserves_configured_file_cap(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 50)
    monkeypatch.setattr(settings, "upload_max_body_mb", None)
    assert filesec.capture_upload_policy()["max_bytes"] == filesec.max_bytes()
    monkeypatch.setattr(settings, "upload_max_body_mb", 1)
    policy = filesec.capture_upload_policy()
    assert policy["max_batch_bytes"] == policy["max_bytes"]
    assert policy["max_batch_bytes"] + filesec.RESOURCE_BYTES["multipart_overhead"] == 1048576
    assert policy["max_files"] == 25


@pytest.mark.asyncio
async def test_oversized_headers_refuse_before_spool_creation(budget, monkeypatch):
    from starlette import formparsers

    created = []
    original = formparsers.SpooledTemporaryFile

    def spool(*args, **kwargs):
        created.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", spool)
    monkeypatch.setattr(filesec, "max_bytes", lambda purpose=None: 10000)
    app, handled = application("/invoices/upload")
    body = multipart([("x" * 1024, b"ok")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/invoices/upload", content=body, headers=HEADERS)
    assert response.status_code == 413
    assert created == handled == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [True, False])
async def test_encoded_filename_parts_count_like_starlette_and_prefixes_match(
    budget, monkeypatch, fallback
):
    monkeypatch.setattr(settings, "api_v1_prefix", "/custom")
    app, handled = application("/invoices/upload")
    body = multipart([("file", b"ok"), ("file", b"ok")]).replace(
        b'filename="synthetic.csv"',
        (b'filename="synthetic.csv"; ' if fallback else b"") + b"filename*=UTF-8''synthetic.csv",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/custom/invoices/upload/", content=body, headers=HEADERS, follow_redirects=True
        )
    # Match the installed Starlette parser: filename* alone is a text field;
    # a regular filename with an extended parameter is still a file.
    assert response.status_code == (422 if fallback else 200)
    if fallback:
        assert not handled
    else:
        assert response.json()["files"] == 0


@pytest.mark.asyncio
async def test_malformed_multipart_returns_400_and_releases_slot(budget, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_active_requests", 1)
    app, handled = application("/invoices/upload")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        broken = await client.post(
            "/api/v1/invoices/upload", content=b"--wrong\r\n", headers=HEADERS
        )
        assert broken.status_code == 400 and not handled
        ok = await client.post(
            "/api/v1/invoices/upload", content=multipart([("file", b"ok")]), headers=HEADERS
        )
        assert ok.status_code == 200


@pytest.mark.parametrize(
    "field,value",
    [
        ("upload_max_active_requests", 0),
        ("upload_max_active_requests", 65),
        ("upload_max_body_mb", 0),
    ],
)
def test_invalid_ingress_configuration_fails_closed(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        type(settings)(_env_file=None, environment="development", **{field: value})


def test_deployment_defaults_match_origin_and_ingress_body_limits():
    import yaml

    from tests.test_ops_deploy_hygiene import _load

    root = Path(__file__).resolve().parents[2]
    sizes = []
    for name in ("nginx.conf", "nginx.prod.conf"):
        text = (root / "frontend" / name).read_text()
        sizes.append(int(re.search(r"client_max_body_size\s+(\d+)m;", text).group(1)))
    for name in ("docker-compose.yml", "docker-compose.prod.yml", "docker-compose.hostinger.yml"):
        env = _load(name)["services"]["backend"]["environment"]
        sizes.append(int(re.search(r":-(\d+)}", env["UPLOAD_MAX_BODY_MB"]).group(1)))
        assert env["UPLOAD_MAX_ACTIVE_REQUESTS"].endswith(":-2}")
    config = next(
        doc["data"]
        for doc in yaml.safe_load_all((root / "deploy/k8s/10-config.yaml").read_text())
        if doc.get("kind") == "ConfigMap"
    )
    sizes.append(int(config["UPLOAD_MAX_BODY_MB"]))
    assert int(config["UPLOAD_MAX_ACTIVE_REQUESTS"]) == 2
    ingress = yaml.safe_load((root / "deploy/k8s/50-ingress.yaml").read_text())
    sizes.append(
        int(
            ingress["metadata"]["annotations"][
                "nginx.ingress.kubernetes.io/proxy-body-size"
            ].removesuffix("m")
        )
    )
    assert len(set(sizes)) == 1, "API, NGINX and ingress envelope defaults drifted"
