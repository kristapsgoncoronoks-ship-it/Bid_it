"""WO-F: job photos — pictures from the site, on the project's document rail.

The contracts, pinned test by test:

1. A photo uploads as kind='photo', lists with the project's documents, and
   downloads byte-identical through the same inert route as every document.
2. A 'photo' must actually BE an image: the declared content type must sit in
   the closed set AND the leading bytes must agree — a PDF declared as JPEG
   and a JPEG declared as PDF both refuse.
3. Non-photo kinds are untouched by the image check (a contract PDF still
   attaches), and an unknown kind still refuses.
"""

from __future__ import annotations

import pytest

# Tiny but structurally real image headers.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF = b"%PDF-1.7 " + b"\x00" * 32


async def _project(client, code="PHO-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _attach(client, project_id, *, kind, data, content_type, filename="site.jpg"):
    return await client.post(
        f"/api/v1/masters/projects/{project_id}/documents",
        params={"kind": kind},
        files={"file": (filename, data, content_type)},
    )


@pytest.mark.asyncio
async def test_photo_uploads_lists_and_downloads(auth_client):
    project_id = await _project(auth_client, "PHO-UP")

    r = await _attach(auth_client, project_id, kind="photo", data=JPEG, content_type="image/jpeg")
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["kind"] == "photo"

    listed = await auth_client.get(f"/api/v1/masters/projects/{project_id}/documents")
    assert [d["kind"] for d in listed.json()] == ["photo"]

    dl = await auth_client.get(
        f"/api/v1/masters/projects/{project_id}/documents/{doc['id']}/download"
    )
    assert dl.status_code == 200
    assert dl.content == JPEG, "bytes come back exactly as shot (EXIF untouched)"
    assert dl.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_photo_must_actually_be_an_image(auth_client):
    project_id = await _project(auth_client, "PHO-VAL")

    # Declared type outside the closed set.
    r = await _attach(
        auth_client, project_id, kind="photo", data=PDF, content_type="application/pdf"
    )
    assert r.status_code == 400
    # Declared image, but the bytes are a PDF.
    r = await _attach(auth_client, project_id, kind="photo", data=PDF, content_type="image/jpeg")
    assert r.status_code == 400
    assert "image" in r.json()["detail"].lower()
    # Real image bytes, but declared as PDF.
    r = await _attach(
        auth_client, project_id, kind="photo", data=JPEG, content_type="application/pdf"
    )
    assert r.status_code == 400
    # PNG and WebP magic both pass.
    r = await _attach(auth_client, project_id, kind="photo", data=PNG, content_type="image/png")
    assert r.status_code == 201
    webp = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
    r = await _attach(auth_client, project_id, kind="photo", data=webp, content_type="image/webp")
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_other_kinds_bypass_the_image_check_and_unknown_kind_refuses(auth_client):
    project_id = await _project(auth_client, "PHO-OTH")

    r = await _attach(
        auth_client,
        project_id,
        kind="contract",
        data=PDF,
        content_type="application/pdf",
        filename="contract.pdf",
    )
    assert r.status_code == 201, "the image gate is for photos only"

    r = await _attach(
        auth_client, project_id, kind="screenshot", data=JPEG, content_type="image/jpeg"
    )
    assert r.status_code == 400, "the kind set stays closed"
