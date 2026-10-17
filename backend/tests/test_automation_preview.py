"""FS16-04 preview shares eligibility/cap decisions without action side effects."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.automation import AutomationRule, AutomationRun
from app.models.crm import CustomerNote
from app.models.email_message import EmailMessage
from app.models.job import Job
from app.models.webhook import WebhookDelivery
from app.services import automation
from tests.automation_client import reviewed_request
from tests.test_automation import STALE_RULE, _project, _stale_sent_offer


async def counts(db):
    return [
        await db.scalar(select(func.count()).select_from(model))
        for model in (AutomationRun, CustomerNote, EmailMessage, Job, WebhookDelivery)
    ]


@pytest.mark.asyncio
async def test_preview_reports_once_suppression_without_writes(auth_client, db_session):
    pid = await _project(auth_client, "SYNTH-PREVIEW")
    await _stale_sent_offer(auth_client, db_session, pid, 10)
    row = (
        await auth_client.post(
            "/api/v1/automation/rules", json={**STALE_RULE, "actions": [{"kind": "emit_webhook"}]}
        )
    ).json()
    path = f"/api/v1/automation/rules/{row['id']}"
    await reviewed_request(auth_client, "post", path + "/publish")
    rule = await db_session.get(AutomationRule, row["id"])
    assert (await automation.sweep(db_session, rule.org_id))["fired"] == 1
    await db_session.commit()
    before = await counts(db_session)
    response = await auth_client.post(path + "/dry-run?basis=published")
    assert response.status_code == 200
    data = response.json()
    assert data["basis"] == "published" and data["version"] == 1 and data["evaluated_at"]
    assert data["outcomes"][0]["status"] == "suppressed"
    assert data["outcomes"][0]["reason"] == "once_per_record"
    assert await counts(db_session) == before


@pytest.mark.asyncio
async def test_preview_cap_and_cooldown_match_execution(auth_client, db_session, monkeypatch):
    pid = await _project(auth_client, "SYNTH-PREVIEW-CAP")
    for _ in range(2):
        await _stale_sent_offer(auth_client, db_session, pid, 10)
    row = (
        await auth_client.post(
            "/api/v1/automation/rules",
            json={
                **STALE_RULE,
                "fire_policy": "cooldown",
                "cooldown_hours": 24,
                "actions": [{"kind": "emit_webhook"}],
            },
        )
    ).json()
    path = f"/api/v1/automation/rules/{row['id']}"
    await reviewed_request(auth_client, "post", path + "/publish")
    rule = await db_session.get(AutomationRule, row["id"])
    monkeypatch.setattr(automation, "MAX_FIRES_PER_SWEEP", 1)
    now = datetime.now(UTC)
    before = await counts(db_session)
    preview = await automation.evaluate_rule(
        db_session, rule.org_id, rule, now=now, dry_run=True, preview_basis="published"
    )
    assert [r["status"] for r in preview] == ["would_fire", "throttled"]
    assert await counts(db_session) == before
    actual = await automation.evaluate_rule(db_session, rule.org_id, rule, now=now)
    assert [(r["ref_id"], r["status"]) for r in actual] == [
        (r["ref_id"], "ok" if r["status"] == "would_fire" else r["status"]) for r in preview
    ]
    await db_session.commit()
    later = await automation.evaluate_rule(
        db_session,
        rule.org_id,
        rule,
        now=now + timedelta(hours=1),
        dry_run=True,
        preview_basis="published",
    )
    assert later[0]["status"] == "suppressed" and later[0]["reason"] == "cooldown"
    assert later[1]["status"] == "would_fire"


@pytest.mark.asyncio
async def test_preview_names_draft_and_published_basis(auth_client, db_session):
    pid = await _project(auth_client, "SYNTH-PREVIEW-BASIS")
    await _stale_sent_offer(auth_client, db_session, pid, 10)
    row = (await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)).json()
    path = f"/api/v1/automation/rules/{row['id']}"
    await reviewed_request(auth_client, "post", path + "/publish")
    await reviewed_request(
        auth_client,
        "patch",
        path,
        json={"set_condition": True, "condition": {">": [{"var": "days_quiet"}, 14]}},
    )
    draft = (await auth_client.post(path + "/dry-run")).json()
    live = (await auth_client.post(path + "/dry-run?basis=published")).json()
    assert draft["basis"] == "draft" and draft["version"] is None and draft["outcomes"] == []
    assert live["basis"] == "published" and live["version"] == 1
    assert live["outcomes"][0]["status"] == "would_fire"


@pytest.mark.asyncio
async def test_preview_overflow_refuses_instead_of_hiding_candidates(
    auth_client, db_session, monkeypatch
):
    pid = await _project(auth_client, "SYNTH-PREVIEW-LIMIT")
    for _ in range(2):
        await _stale_sent_offer(auth_client, db_session, pid, 10)
    row = (await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)).json()
    before = await counts(db_session)
    monkeypatch.setattr(automation, "MAX_PREVIEW_RECORDS", 1)
    response = await auth_client.post(f"/api/v1/automation/rules/{row['id']}/dry-run")
    assert response.status_code == 400 and "no actions" in response.json()["detail"]
    assert await counts(db_session) == before
