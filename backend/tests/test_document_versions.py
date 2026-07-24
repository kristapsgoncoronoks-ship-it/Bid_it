"""Document supersession chain (Slice 5g): re-uploading a single-file slot (issuer
logo, expense receipt) appends an immutable version instead of silently replacing
it, and the history is queryable — an audit trail of every file that was there."""

import io

import pytest


def _png(marker: bytes) -> bytes:
    # A minimal-but-real PNG header so filesec accepts it; the marker varies the
    # bytes so each upload is a distinct sha256 (a new version, not a dedup).
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89" + marker
    )


@pytest.mark.asyncio
async def test_logo_reupload_appends_a_version(auth_client):
    first = {"file": ("logo1.png", io.BytesIO(_png(b"AAAA")), "image/png")}
    r1 = await auth_client.post("/api/v1/issuer/logo", files=first)
    assert r1.status_code == 200

    second = {"file": ("logo2.png", io.BytesIO(_png(b"BBBB")), "image/png")}
    r2 = await auth_client.post("/api/v1/issuer/logo", files=second)
    assert r2.status_code == 200

    versions = (await auth_client.get("/api/v1/issuer/logo/versions")).json()
    assert [v["version"] for v in versions] == [2, 1]  # newest first
    assert versions[0]["is_current"] is True
    assert versions[1]["is_current"] is False
    assert versions[0]["filename"] == "logo2.png"
    assert versions[0]["uploaded_by"]  # actor recorded
    # The two uploads are genuinely different files.
    assert versions[0]["sha256"] != versions[1]["sha256"]


@pytest.mark.asyncio
async def test_receipt_reupload_history_is_audit_trail(auth_client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200
    # A report with one item.
    report = {
        "title": "Trip",
        "currency": "EUR",
        "items": [
            {
                "spend_date": "2026-05-01",
                "category": "travel",
                "description": "Taxi",
                "amount": "20.00",
                "vat_amount": "0",
            }
        ],
    }
    created = (await auth_client.post("/api/v1/expenses", json=report)).json()
    rid = created["id"]
    item_id = created["items"][0]["id"]

    up1 = {"file": ("r1.png", io.BytesIO(_png(b"CCCC")), "image/png")}
    assert (
        await auth_client.post(f"/api/v1/expenses/{rid}/items/{item_id}/receipt", files=up1)
    ).status_code == 200
    up2 = {"file": ("r2.png", io.BytesIO(_png(b"DDDD")), "image/png")}
    assert (
        await auth_client.post(f"/api/v1/expenses/{rid}/items/{item_id}/receipt", files=up2)
    ).status_code == 200

    hist = (
        await auth_client.get(f"/api/v1/expenses/{rid}/items/{item_id}/receipt/versions")
    ).json()
    assert [v["version"] for v in hist] == [2, 1]
    assert sum(1 for v in hist if v["is_current"]) == 1
    assert hist[0]["is_current"] and hist[0]["version"] == 2


@pytest.mark.asyncio
async def test_reupload_same_bytes_still_new_version(auth_client):
    same = _png(b"SAME")
    for _ in range(2):
        r = await auth_client.post(
            "/api/v1/issuer/logo", files={"file": ("l.png", io.BytesIO(same), "image/png")}
        )
        assert r.status_code == 200
    versions = (await auth_client.get("/api/v1/issuer/logo/versions")).json()
    # Same content dedups in the object store, but the history still records that
    # a replacement action happened — two versions, same sha.
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["sha256"] == versions[1]["sha256"]
