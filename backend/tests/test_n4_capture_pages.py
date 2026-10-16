import io
import subprocess

import pytest
from PIL import Image
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from app.core import storage
from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.services import capture_pages
from tests.test_pdf_ocr import _text_pdf


def pdf(pages=2):
    stream = io.BytesIO()
    document = canvas.Canvas(stream)
    for number in range(pages):
        document.drawString(30, 700, f"Synthetic invoice page {number + 1}")
        document.showPage()
    document.save()
    return stream.getvalue()


async def uploaded(auth_client, parse_upload):
    result = await parse_upload(
        auth_client, {"file": ("invoice.pdf", io.BytesIO(_text_pdf()), "application/pdf")}
    )
    return result["extraction_run_id"]


def test_pdf_pages_are_real_bounded_jpegs_with_honest_truncation():
    import base64

    result = capture_pages.render(pdf(8))
    assert result["total_pages"] == 8 and result["truncated"] is True
    assert len(result["pages"]) == 6
    total = 0
    for page in result["pages"]:
        data = base64.b64decode(page["data"])
        total += len(data)
        with Image.open(io.BytesIO(data)) as image:
            assert image.format == "JPEG" and max(image.size) <= 960
            assert image.width == page["width"] and image.height == page["height"]
            assert not image.getexif()
    assert total <= capture_pages.MAX_BYTES


def test_png_preview_and_unsupported_data():
    image = Image.new("RGBA", (1200, 600), (255, 0, 0, 50))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    image.close()
    result = capture_pages.render(stream.getvalue())
    assert result["total_pages"] == 1 and result["pages"][0]["width"] == 960
    assert capture_pages.render(b"<Invoice/>") is None


async def test_worker_persists_and_serves_scoped_pages(auth_client, parse_upload, db_session):
    run_id = await uploaded(auth_client, parse_upload)
    response = await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages")
    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["status"] == "ready" and manifest["total_pages"] == 1
    page = manifest["pages"][0]
    image = await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages/1/doc")
    assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.headers["content-disposition"].startswith("inline;")
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Document).where(Document.kind == capture_pages.PREFIX)
        )
        == 1
    )
    row = await db_session.get(ExtractionRun, run_id)
    before = row.preview_json
    await capture_pages.generate(db_session, row, _text_pdf())
    assert row.preview_json == before
    assert (
        await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages/7/doc")
    ).status_code == 404
    storage.get_storage().delete(
        storage.content_key(capture_pages.PREFIX, row.org_id, page["sha256"])
    )
    assert (
        await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages/1/doc")
    ).status_code == 404


async def test_timeout_is_advisory_and_extraction_still_completes(
    auth_client, parse_upload, monkeypatch
):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("synthetic", 12)

    monkeypatch.setattr(capture_pages, "render", timeout)
    run_id = await uploaded(auth_client, parse_upload)
    status = (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()
    assert status["status"] == "parsed" and status["draft"]["draft"]["line_items"]
    manifest = (await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages")).json()
    assert manifest["status"] == "unavailable" and manifest["pages"] == []


async def test_storage_failure_does_not_roll_back_parsed_capture(
    auth_client, parse_upload, monkeypatch
):
    from app.services import documents

    original = documents.store

    async def fail(prefix, *args, **kwargs):
        if prefix == capture_pages.PREFIX:
            raise storage.StorageError("Synthetic failure")
        return await original(prefix, *args, **kwargs)

    monkeypatch.setattr(documents, "store", fail)
    run_id = await uploaded(auth_client, parse_upload)
    assert (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()["status"] == "parsed"
    assert (await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages")).json()[
        "status"
    ] == "unavailable"


async def test_other_tenant_has_no_page_access(auth_client, parse_upload, client):
    run_id = await uploaded(auth_client, parse_upload)
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "Owner",
            "email": "n4-other@example.com",
            "password": "synthetic-test-password",
        },
    )
    client.headers["Authorization"] = "Bearer " + registration.json()["token"]["access_token"]
    for path in ("pages", "pages/1/doc"):
        assert (await client.get(f"/api/v1/invoices/captures/{run_id}/{path}")).status_code == 404


async def test_historical_capture_falls_back_without_rendering(
    auth_client, parse_upload, db_session
):
    run_id = await uploaded(auth_client, parse_upload)
    row = await db_session.get(ExtractionRun, run_id)
    row.preview_json = None
    await db_session.commit()
    manifest = (await auth_client.get(f"/api/v1/invoices/captures/{run_id}/pages")).json()
    assert manifest["status"] == "unavailable" and "not recorded" in manifest["message"]


@pytest.mark.parametrize("suffix", ["pages", "pages/1/doc"])
async def test_malformed_capture_id_is_opaque(auth_client, suffix):
    response = await auth_client.get(f"/api/v1/invoices/captures/not-a-uuid/{suffix}")
    assert response.status_code == 404 and response.json()["detail"] == "Capture not found"
