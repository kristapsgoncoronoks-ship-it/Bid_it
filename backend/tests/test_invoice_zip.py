"""Exact bounded archive selection; fake render bytes only in these unit seams."""

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.routes import issued as route
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from tests.test_issuing import _activate


async def population(db, count, *, paid_last=False):
    org = await db.scalar(select(Organization.id).where(Organization.name == "Acme"))
    rows = []
    for n in range(count):
        inv = IssuedInvoice(
            org_id=org,
            number=f"SYNTH-ZIP-{n:04d}",
            buyer_name="Synthetic buyer",
            seller_json="{}",
            issue_date=date(2020, 1, 1) + timedelta(days=n),
            currency="EUR",
            subtotal=Decimal("10"),
            total=Decimal("10"),
            amount_paid=Decimal("10") if paid_last and n == count - 1 else Decimal("0"),
            lifecycle="issued",
        )
        db.add(inv)
        rows.append(inv)
    await db.commit()
    return rows


async def fake_pdf(*args, **kwargs):
    return b"%PDF-1.7 synthetic unit bytes\n"


@pytest.mark.asyncio
async def test_zip_refuses_501_instead_of_silent_truncation(auth_client, db_session, monkeypatch):
    await _activate(auth_client)
    await population(db_session, 501)
    monkeypatch.setattr(route, "_render_pdf", fake_pdf)
    response = await auth_client.get("/api/v1/issued/export.zip")
    assert response.status_code == 422
    assert "500" in response.json()["detail"]


@pytest.mark.asyncio
async def test_zip_filters_status_before_proving_population_bound(
    auth_client, db_session, monkeypatch
):
    await _activate(auth_client)
    rows = await population(db_session, 501, paid_last=True)
    monkeypatch.setattr(route, "_render_pdf", fake_pdf)
    response = await auth_client.get("/api/v1/issued/export.zip?status=paid")
    assert response.status_code == 200
    import json
    import zipfile

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["count"] == 1 and manifest["complete"] is True
        assert manifest["documents"][0]["id"] == rows[-1].id


@pytest.mark.parametrize("count", [0, 1, 500])
@pytest.mark.asyncio
async def test_zip_population_bound_and_manifest_hashes(
    auth_client, db_session, monkeypatch, count
):
    import hashlib
    import json
    import zipfile

    await _activate(auth_client)
    rows = await population(db_session, count)
    monkeypatch.setattr(route, "_render_pdf", fake_pdf)
    response = await auth_client.get("/api/v1/issued/export.zip")
    if count == 0:
        assert response.status_code == 404
        return
    assert response.status_code == 200 and response.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["count"] == count and manifest["complete"] is True
        assert {item["id"] for item in manifest["documents"]} == {row.id for row in rows}
        assert len(archive.namelist()) == count + 1
        for item in manifest["documents"]:
            content = archive.read(item["filename"])
            assert hashlib.sha256(content).hexdigest() == item["pdf_sha256"]
            assert len(content) == item["bytes"]


@pytest.mark.asyncio
async def test_zip_names_are_safe_and_do_not_collide_after_sanitization(
    auth_client, db_session, monkeypatch
):
    import json
    import zipfile

    await _activate(auth_client)
    rows = await population(db_session, 2)
    rows[0].number = "../SYNTH/1"
    rows[1].number = "..\\SYNTH\\1"
    await db_session.commit()
    monkeypatch.setattr(route, "_render_pdf", fake_pdf)
    response = await auth_client.get("/api/v1/issued/export.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = [item["filename"] for item in manifest["documents"]]
        assert len(set(names)) == 2
        assert all(
            "/" not in name and "\\" not in name and not name.startswith(".") for name in names
        )


@pytest.mark.asyncio
async def test_zip_validates_dates_status_and_refuses_failed_source(
    auth_client, db_session, monkeypatch
):
    from app.services.invoice_pdf import PdfUnavailable

    await _activate(auth_client)
    await population(db_session, 1)
    for query in ("from=2026-12-31&to=2026-01-01", "status=made-up"):
        assert (await auth_client.get("/api/v1/issued/export.zip?" + query)).status_code == 422

    async def failed(*args):
        raise PdfUnavailable("synthetic unavailable PDF")

    monkeypatch.setattr(route, "_render_pdf", failed)
    response = await auth_client.get("/api/v1/issued/export.zip")
    assert response.status_code == 503 and not response.content.startswith(b"PK")
    assert "partial" in response.json()["detail"]


@pytest.mark.asyncio
async def test_zip_offboarding_during_render_releases_no_archive(
    auth_client, db_session, monkeypatch
):
    from sqlalchemy import update

    from app.models.user import User

    await _activate(auth_client)
    await population(db_session, 1)

    async def revoke(*args):
        await db_session.execute(update(User).values(is_active=False))
        await db_session.commit()
        return await fake_pdf()

    monkeypatch.setattr(route, "_render_pdf", revoke)
    response = await auth_client.get("/api/v1/issued/export.zip")
    assert response.status_code == 401 and not response.content.startswith(b"PK")


@pytest.mark.asyncio
async def test_zip_byte_budget_is_a_refusal_not_a_partial_result(
    auth_client, db_session, monkeypatch
):
    from app.services import filesec

    await _activate(auth_client)
    await population(db_session, 2)
    monkeypatch.setattr(route, "_render_pdf", fake_pdf)
    monkeypatch.setitem(filesec.RESOURCE_BYTES, "invoice_zip", len(await fake_pdf()) + 1)
    response = await auth_client.get("/api/v1/issued/export.zip")
    assert response.status_code == 422 and response.json()["code"] == "export_too_large"
    assert not response.content.startswith(b"PK")
