"""FS16-01: executable versions and reviewed mutations have explicit boundaries."""

import json

import pytest
from sqlalchemy import select

from app.models.automation import AutomationRule, AutomationRuleVersion, AutomationRun
from app.services import automation
from tests.automation_client import reviewed_request
from tests.test_automation import STALE_RULE, _project, _stale_sent_offer


@pytest.mark.asyncio
async def test_v1_executes_after_v2_draft_edits_and_pins_original_actions(auth_client, db_session):
    project = await _project(auth_client, "SYNTH-PUB")
    await _stale_sent_offer(auth_client, db_session, project, days=10)
    body = {**STALE_RULE, "actions": [{"kind": "emit_webhook", "body": "original"}]}
    row = (await auth_client.post("/api/v1/automation/rules", json=body)).json()
    path = f"/api/v1/automation/rules/{row['id']}"
    published = await reviewed_request(auth_client, "post", path + "/publish")
    assert published.status_code == 200
    changed = await reviewed_request(
        auth_client,
        "patch",
        path,
        json={
            "set_condition": True,
            "condition": {">": [{"var": "days_quiet"}, 14]},
            "actions": [{"kind": "emit_webhook", "body": "unpublished"}],
        },
    )
    assert changed.status_code == 200
    rule = await db_session.get(AutomationRule, row["id"])
    live = await automation._execution_rule(db_session, rule.org_id, rule)
    assert live.actions[0]["body"] == "original"
    result = await automation.sweep(db_session, rule.org_id)
    assert result["fired"] == 1 and result["failed"] == 0
    await db_session.commit()
    run = await db_session.scalar(select(AutomationRun).where(AutomationRun.rule_id == row["id"]))
    assert run.version == 1
    # Policy ledger survives publication; no re-enrollment just from a new version.
    assert (await reviewed_request(auth_client, "post", path + "/publish")).status_code == 200
    assert (await automation.sweep(db_session, rule.org_id))["fired"] == 0


@pytest.mark.asyncio
async def test_missing_and_stale_revisions_never_publish_or_overwrite(auth_client, db_session):
    row = (await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)).json()
    path = f"/api/v1/automation/rules/{row['id']}"
    assert (await auth_client.post(path + "/publish")).status_code == 428
    changed = await auth_client.patch(
        path, headers={"If-Match": row["revision"]}, json={"name": "Reviewed change"}
    )
    assert changed.status_code == 200
    for method, target, body in (
        ("post", path + "/publish", None),
        ("patch", path, {"name": "Stale overwrite"}),
        ("put", path + "/status", {"status": "disabled"}),
    ):
        response = await getattr(auth_client, method)(
            target, headers={"If-Match": row["revision"]}, json=body
        )
        assert response.status_code == 409
    assert await db_session.scalar(select(AutomationRuleVersion.id)) is None
    fresh = (await auth_client.get("/api/v1/automation/rules")).json()[0]
    assert fresh["name"] == "Reviewed change" and fresh["revision"] == changed.json()["revision"]


@pytest.mark.asyncio
async def test_legacy_mismatch_blocks_until_explicit_reviewed_publish(auth_client, db_session):
    row = (await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)).json()
    path = f"/api/v1/automation/rules/{row['id']}"
    await reviewed_request(auth_client, "post", path + "/publish")
    version = await db_session.scalar(select(AutomationRuleVersion))
    original = json.loads(version.snapshot_json)
    original.pop("definition_version")
    version.snapshot_json = json.dumps(original)
    await db_session.commit()
    await reviewed_request(auth_client, "patch", path, json={"name": "Different legacy draft"})
    rule = await db_session.get(AutomationRule, row["id"])
    with pytest.raises(automation.ConflictError, match="Legacy"):
        await automation._execution_rule(db_session, rule.org_id, rule)
    preflight = await auth_client.get(path + "/preflight")
    assert preflight.status_code == 200 and preflight.json()["requires_reconciliation"] is True
    await reviewed_request(auth_client, "post", path + "/publish")
    rule = await db_session.get(AutomationRule, row["id"])
    live = await automation._execution_rule(db_session, rule.org_id, rule)
    assert live.name == "Different legacy draft" and live.published_version == 2
    assert json.loads(version.snapshot_json) == original


@pytest.mark.asyncio
async def test_invalid_persisted_rule_cannot_abort_another_rule(auth_client, db_session):
    project = await _project(auth_client, "SYNTH-INVALID")
    await _stale_sent_offer(auth_client, db_session, project, days=10)
    for name in ("Invalid legacy", "Valid neighbor"):
        body = {**STALE_RULE, "name": name, "actions": [{"kind": "emit_webhook"}]}
        row = (await auth_client.post("/api/v1/automation/rules", json=body)).json()
        await reviewed_request(auth_client, "post", f"/api/v1/automation/rules/{row['id']}/publish")
        if name == "Invalid legacy":
            version = await db_session.scalar(
                select(AutomationRuleVersion).where(AutomationRuleVersion.rule_id == row["id"])
            )
            snap = json.loads(version.snapshot_json)
            snap["condition"] = {"==": [1]}
            version.snapshot_json = json.dumps(snap)
            await db_session.commit()
    rule = await db_session.get(AutomationRule, row["id"])
    result = await automation.sweep(db_session, rule.org_id)
    assert result["failed"] == 1 and result["fired"] == 1
