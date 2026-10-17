"""In-flight capture downloads must not bypass changed access/visibility (PG)."""

import io
import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, update

from app.models.extraction_run import ExtractionRun
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.session import Session
from app.models.user import User
from app.schemas.capture_pages import CapturePage, CapturePages
from app.services import capture_pages, documents
from tests.test_capture_admission_pg import CSV
from tests.test_capture_admission_pg import quota_workspace as quota_workspace

pytestmark = pytest.mark.skipif(
    not os.environ.get("RLS_TEST_DATABASE_URL"), reason="requires migrated non-superuser PostgreSQL"
)


@pytest_asyncio.fixture
async def capture_download(quota_workspace):
    client, factory, org_id, _ = quota_workspace
    uid = (await client.get("/api/v1/auth/me")).json()["user"]["id"]
    accepted = await client.post(
        "/api/v1/invoices/upload", files={"file": ("original.csv", CSV, "text/csv")}
    )
    assert accepted.status_code == 202
    run_id = accepted.json()["extraction_run_id"]
    with Image.new("RGB", (8, 8), "white") as image:
        out = io.BytesIO()
        image.save(out, "JPEG")
    data = out.getvalue()
    async with factory() as db:
        sha, size = await documents.store(capture_pages.PREFIX, org_id, data, "image/jpeg", db=db)
        run = await db.get(ExtractionRun, run_id)
        run.preview_json = CapturePages(
            status="ready",
            total_pages=1,
            pages=[CapturePage(number=1, sha256=sha, size=size, width=8, height=8)],
        ).model_dump_json()
        await db.commit()
    return client, factory, org_id, uid, run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["source", "page"])
@pytest.mark.parametrize("change", ["user", "membership", "session", "organization"])
async def test_access_revoked_during_capture_storage_read(
    capture_download, monkeypatch, surface, change
):
    client, factory, org_id, uid, run_id = capture_download
    original = documents.load

    async def revoke_after_read(*args, **kwargs):
        data = await original(*args, **kwargs)
        model, condition, values = {
            "user": (User, User.id == uid, {"is_active": False}),
            "membership": (Membership, Membership.user_id == uid, {"status": "suspended"}),
            "session": (Session, Session.user_id == uid, {"revoked_at": datetime.now(UTC)}),
            "organization": (Organization, Organization.id == org_id, {"status": "suspended"}),
        }[change]
        async with factory() as db:
            await db.execute(update(model).where(condition).values(**values))
            await db.commit()
        return data

    monkeypatch.setattr(documents, "load", revoke_after_read)
    suffix = "source" if surface == "source" else "pages/1/doc"
    response = await client.get(f"/api/v1/invoices/captures/{run_id}/{suffix}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "attachment" not in response.headers.get("content-disposition", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["source", "page"])
@pytest.mark.parametrize("change", ["deleted", "retargeted"])
async def test_capture_visibility_rechecked_after_storage_read(
    capture_download, monkeypatch, surface, change
):
    client, factory, _, _, run_id = capture_download
    original = documents.load

    async def alter_after_read(*args, **kwargs):
        data = await original(*args, **kwargs)
        async with factory() as db:
            if change == "deleted":
                await db.execute(delete(ExtractionRun).where(ExtractionRun.id == run_id))
            else:
                values = (
                    {"source_sha256": "0" * 64} if surface == "source" else {"preview_json": None}
                )
                await db.execute(
                    update(ExtractionRun).where(ExtractionRun.id == run_id).values(**values)
                )
            await db.commit()
        return data

    monkeypatch.setattr(documents, "load", alter_after_read)
    suffix = "source" if surface == "source" else "pages/1/doc"
    response = await client.get(f"/api/v1/invoices/captures/{run_id}/{suffix}")
    assert response.status_code == 404
