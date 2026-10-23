"""Capture-linked confirmation binds the current saved review before side effects."""

import copy

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from tests.test_capture_region import application, finish, seed


async def applied_capture(client, db, monkeypatch):
    run, _, _, before, _, _ = await finish(client, db, monkeypatch)
    old_draft = copy.deepcopy(before["current_draft"])
    response = await client.post(
        f"/api/v1/invoices/captures/{run.id}/region-reads/{before['id']}/apply",
        json=application(before),
    )
    assert response.status_code == 200, response.text
    return run, before, response.json(), old_draft


async def side_effect_counts(db):
    return [
        await db.scalar(select(func.count()).select_from(model))
        for model in (Vendor, Invoice, AuditEvent)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,expected_code",
    [
        ("missing", "capture_review_required"),
        ("stale", "capture_review_changed"),
        ("fresh_token_old_values", "capture_draft_changed"),
    ],
)
async def test_region_capture_confirm_refuses_stale_values_before_side_effects(
    auth_client, db_session, monkeypatch, case, expected_code
):
    run, before, applied, old = await applied_capture(auth_client, db_session, monkeypatch)
    body = copy.deepcopy(applied["current_draft"])
    body["extraction_run_id"] = run.id
    if case == "stale":
        body["capture_review_fingerprint"] = before["review_fingerprint"]
    elif case == "fresh_token_old_values":
        body = {**old, "extraction_run_id": run.id}
        body["capture_review_fingerprint"] = applied["review_fingerprint"]
    counts = await side_effect_counts(db_session)
    response = await auth_client.post("/api/v1/invoices", json=body)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == expected_code
    assert await side_effect_counts(db_session) == counts
    await db_session.refresh(run)
    assert run.invoice_id is None and run.status == "parsed"


@pytest.mark.asyncio
async def test_current_saved_review_confirms_once_with_decimal_equivalent_values(
    auth_client, db_session, monkeypatch
):
    run, _, applied, _ = await applied_capture(auth_client, db_session, monkeypatch)
    body = {
        **applied["current_draft"],
        "extraction_run_id": run.id,
        "capture_review_fingerprint": applied["review_fingerprint"],
        "notes": "An independent invoice note is still permitted",
    }
    body["line_items"][0]["quantity"] = "1.000"
    body["line_items"][0]["amount"] = "10.0000"
    saved = await auth_client.post("/api/v1/invoices", json=body)
    assert saved.status_code == 201, saved.text
    assert saved.json()["invoice_number"] == "AFTER-2"
    assert saved.json()["notes"] == body["notes"]
    repeated = await auth_client.post("/api/v1/invoices", json=body)
    assert repeated.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(Invoice)) == 1


@pytest.mark.asyncio
async def test_legacy_capture_without_applied_regions_keeps_optional_confirm_contract(
    auth_client, db_session
):
    run, _, _ = await seed(auth_client, db_session)
    import json

    body = json.loads(run.draft_json)["draft"]
    response = await auth_client.post(
        "/api/v1/invoices", json={**body, "extraction_run_id": run.id}
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_manual_review_uses_the_loaded_base_and_returns_the_committed_next_base(
    auth_client, db_session, monkeypatch
):
    run, before, applied, _ = await applied_capture(auth_client, db_session, monkeypatch)
    path = f"/api/v1/invoices/captures/{run.id}/review"
    update = {"fields": [{"field": "vendor_name", "reviewed_value": "Saved supplier correction"}]}
    for token, code in [
        (None, "capture_review_required"),
        (before["review_fingerprint"], "capture_review_changed"),
    ]:
        body = {**update}
        if token is not None:
            body["expected_review_fingerprint"] = token
        counts = await side_effect_counts(db_session)
        refused = await auth_client.post(path, json=body)
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == code
        assert await side_effect_counts(db_session) == counts
    accepted = await auth_client.post(
        path,
        json={**update, "expected_review_fingerprint": applied["review_fingerprint"]},
        headers={"Origin": "http://localhost:5173"},
    )
    assert accepted.status_code == 200, accepted.text
    token = accepted.headers.get("x-capture-review-fingerprint")
    assert token and token != applied["review_fingerprint"]
    assert (
        "x-capture-review-fingerprint"
        in accepted.headers.get("access-control-expose-headers", "").lower()
    )
    state = (await auth_client.get(f"/api/v1/invoices/captures/{run.id}/region-reads")).json()
    assert state["review_fingerprint"] == token
    assert state["current_draft"]["vendor_name"] == "Saved supplier correction"
    saved = await auth_client.post(
        "/api/v1/invoices",
        json={
            **state["current_draft"],
            "extraction_run_id": run.id,
            "capture_review_fingerprint": token,
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["vendor_name"] == "Saved supplier correction"
