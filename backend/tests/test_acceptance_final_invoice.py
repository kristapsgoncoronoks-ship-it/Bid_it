"""WO-D: acceptance & handover + the adjustable final invoice.

The owner's decisions, pinned test by test:

1. Acceptance is a stamped, audited EVENT: record it (optionally with the
   countersigned document), see it on the P&L wire, revoke it (audited with
   what was revoked). Double-record and revoke-nothing are refused.
2. The final invoice is ADJUSTABLE: contracted remainder ± labelled
   adjustment lines, each explicit. A zero or unlabelled adjustment is
   refused — the label IS the reconciliation.
3. The sign rule: an adjusted total at or below zero is refused, naming the
   credit-note path — never a negative invoice.
4. The gate is per-org and OFF by default: with it on, the composer refuses
   until acceptance is recorded (409), and works right after.
5. The lifecycle nudges self-complete: all-work-done suggests acceptance;
   an accepted project with contracted money uninvoiced suggests the final
   invoice; recording/issuing clears them without anyone touching a task.
6. The org's offer prefix is settable (and clearable) from settings, and the
   next offer number uses it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent


async def _project(client, code) -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _accepted_offer_with_plan(client, project_id, total_lines=None) -> dict:
    lines = total_lines or [
        {"description": "Advance", "amount": "3000.00"},
        {"description": "On completion", "amount": "7000.00"},
    ]
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Quote", "lines": lines},
    )
    assert r.status_code == 201, r.text
    offer = r.json()
    for status_ in ("sent", "accepted"):
        r = await client.post(
            f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
            json={"status": status_},
        )
        assert r.status_code == 200, r.text
    return offer


@pytest.mark.asyncio
async def test_acceptance_records_shows_on_wire_and_revokes_audited(auth_client, db_session):
    project_id = await _project(auth_client, "ACC-1")

    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/acceptance",
        json={"note": "Handover walked through together"},
    )
    assert r.status_code == 200, r.text
    pnl = r.json()
    assert pnl["accepted_at"] is not None
    assert pnl["accepted_by"] == "owner@acme.io"
    assert pnl["acceptance_note"] == "Handover walked through together"

    # Double-record refused; the stamp is not silently movable.
    again = await auth_client.post(f"/api/v1/masters/projects/{project_id}/acceptance", json={})
    assert again.status_code == 400

    r = await auth_client.delete(f"/api/v1/masters/projects/{project_id}/acceptance")
    assert r.status_code == 200
    assert r.json()["accepted_at"] is None

    # Revoking nothing is an error, and both moves were audited — the revoke
    # carrying WHAT was revoked.
    r = await auth_client.delete(f"/api/v1/masters/projects/{project_id}/acceptance")
    assert r.status_code == 400
    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action.like("project.acceptance%"))
        )
    )
    actions = [e.action for e in events]
    assert "project.acceptance_record" in actions
    assert "project.acceptance_revoke" in actions
    revoke = next(e for e in events if e.action == "project.acceptance_revoke")
    assert "accepted_at" in (revoke.meta or "")


@pytest.mark.asyncio
async def test_acceptance_document_must_live_on_this_project(auth_client):
    a = await _project(auth_client, "ACC-DOC-A")
    r = await auth_client.post(
        f"/api/v1/masters/projects/{a}/acceptance",
        json={"document_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_final_invoice_composes_remainder_plus_labelled_adjustments(auth_client):
    project_id = await _project(auth_client, "FIN-1")
    await _accepted_offer_with_plan(auth_client, project_id)

    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/final-invoice-draft",
        json={
            "adjustments": [
                {"label": "additional work agreed on site", "amount": "500.00"},
                {"label": "deduction for late delivery", "amount": "-200.00"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["remainder"] == "10000.00"
    assert draft["total"] == "10300.00"
    descriptions = [line["description"] for line in draft["lines"]]
    assert descriptions[0].startswith("Final invoice — contracted remainder")
    assert "Adjustment — additional work agreed on site" in descriptions
    assert "Adjustment — deduction for late delivery" in descriptions

    # Unlabelled and zero adjustments are refused — the label IS the point.
    for bad in ({"label": " ", "amount": "10.00"}, {"label": "x", "amount": "0"}):
        r = await auth_client.post(
            f"/api/v1/masters/projects/{project_id}/final-invoice-draft",
            json={"adjustments": [bad]},
        )
        assert r.status_code in (400, 422), bad


@pytest.mark.asyncio
async def test_sign_flip_is_credit_note_territory(auth_client):
    project_id = await _project(auth_client, "FIN-NEG")
    await _accepted_offer_with_plan(
        auth_client, project_id, [{"description": "Everything", "amount": "1000.00"}]
    )
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/final-invoice-draft",
        json={"adjustments": [{"label": "storm damage on our side", "amount": "-1500.00"}]},
    )
    assert r.status_code == 400
    assert "credit note" in r.json()["detail"]


@pytest.mark.asyncio
async def test_gate_toggle_blocks_until_acceptance(auth_client):
    project_id = await _project(auth_client, "FIN-GATE")
    await _accepted_offer_with_plan(auth_client, project_id)

    r = await auth_client.put(
        "/api/v1/settings/lifecycle", json={"final_invoice_requires_acceptance": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["final_invoice_requires_acceptance"] is True

    blocked = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/final-invoice-draft", json={}
    )
    assert blocked.status_code == 409

    r = await auth_client.post(f"/api/v1/masters/projects/{project_id}/acceptance", json={})
    assert r.status_code == 200
    allowed = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/final-invoice-draft", json={}
    )
    assert allowed.status_code == 200
    assert allowed.json()["total"] == "10000.00"


@pytest.mark.asyncio
async def test_lifecycle_nudges_selfcomplete(auth_client):
    project_id = await _project(auth_client, "FIN-NUDGE")
    await _accepted_offer_with_plan(auth_client, project_id)
    me = (await auth_client.get("/api/v1/auth/me")).json()["user"]

    # One assignment, marked done → suggest acceptance.
    r = await auth_client.post(
        "/api/v1/schedule/assignments",
        json={
            "project_id": project_id,
            "assignee_user_id": me["id"],
            "starts_at": "2026-09-01T09:00:00+00:00",
            "ends_at": "2026-09-01T17:00:00+00:00",
        },
    )
    aid = r.json()["assignment"]["id"]
    r = await auth_client.post(
        f"/api/v1/schedule/assignments/{aid}/transition", json={"status": "done"}
    )
    assert r.status_code == 200

    def kinds(items):
        return [a["kind"] for a in items if a["ref_id"] == project_id]

    actions = (await auth_client.get("/api/v1/next-actions")).json()
    assert kinds(actions) == ["acceptance_suggest"]

    # Recording acceptance clears the suggestion and raises the final-invoice
    # nudge (contracted money still uninvoiced) — nothing was ticked by hand.
    r = await auth_client.post(f"/api/v1/masters/projects/{project_id}/acceptance", json={})
    assert r.status_code == 200
    actions = (await auth_client.get("/api/v1/next-actions")).json()
    assert kinds(actions) == ["final_invoice"]


@pytest.mark.asyncio
async def test_offer_prefix_is_client_set_from_settings(auth_client):
    r = await auth_client.put("/api/v1/settings/lifecycle", json={"offer_prefix": "QUO-"})
    assert r.status_code == 200
    assert r.json()["offer_prefix"] == "QUO-"

    project_id = await _project(auth_client, "PRFX-1")
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Quote", "lines": [{"description": "Work", "amount": "100.00"}]},
    )
    assert r.status_code == 201
    assert r.json()["number"].startswith("QUO-")

    r = await auth_client.put("/api/v1/settings/lifecycle", json={"clear_offer_prefix": True})
    assert r.json()["offer_prefix"] is None


@pytest.mark.asyncio
async def test_generated_acceptance_document_carries_its_kind(auth_client):
    r = await auth_client.put(
        "/api/v1/issuer",
        json={
            "legal_name": "Acme OU",
            "reg_number": "12345678",
            "vat_number": "EE101234567",
            "address_line1": "Main 1",
            "city": "Tallinn",
            "postal_code": "10111",
            "country": "EE",
            "invoice_prefix": "ACM-",
        },
    )
    assert r.status_code == 200
    project_id = await _project(auth_client, "ACC-GEN")
    masters = (await auth_client.get("/api/v1/templates")).json()["platform"]
    acceptance = next(t for t in masters if t["kind"] == "acceptance")
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/generate-document",
        json={"template_scope": "platform", "template_id": acceptance["id"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "acceptance"

    # And the generated document is linkable as THE acceptance document.
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/acceptance",
        json={"document_id": r.json()["id"]},
    )
    assert r.status_code == 200
    assert r.json()["acceptance_document_id"] is not None
