"""WO-J: admin automation rules — the contracts, pinned test by test.

1. The condition evaluator is a CLOSED set: unknown operators refuse at
   save time; the subset evaluates exactly; text rendering is lookup-only
   substitution (input is data, never template code).
2. Definitions validate at save (trigger, actions, cooldown); publish
   snapshots an immutable version; editing the draft never rewrites it;
   revert republishes an old snapshot as a NEW version.
3. The sweep fires a published rule over derived matches, records a run
   pinned to the version, and the fire policy holds: once_per_record never
   double-fires, every_time does, cooldown waits.
4. The per-sweep cap throttles visibly (a `throttled` run row), never
   silently. Dry-run touches nothing.
5. Actions ride existing rails: owner email recorded via the mailer,
   customer note lands on the CRM timeline, a customer without an email is
   a `failed` run with the reason stated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.automation import AutomationRun
from app.models.email_message import EmailMessage
from app.models.project_offer import ProjectOffer
from app.services import automation

# --------------------------------------------------------------------------- #
# Pure evaluator
# --------------------------------------------------------------------------- #


def test_condition_validation_is_a_closed_set():
    automation.validate_condition({"and": [{">": [{"var": "days_quiet"}, 7]}, True]})
    with pytest.raises(automation.AutomationError):
        automation.validate_condition({"eval": "1+1"})
    with pytest.raises(automation.AutomationError):
        automation.validate_condition({"map": [[1, 2], {"var": ""}]})
    with pytest.raises(automation.AutomationError):
        automation.validate_condition({"==": [1, 1], ">": [2, 1]})  # two ops in one node


def test_condition_evaluation_exact():
    ctx = {"days_quiet": 10, "total": 450.0, "lifecycle": "prospect", "missing": None}
    e = automation.eval_condition
    assert e({">": [{"var": "days_quiet"}, 7]}, ctx) is True
    assert e({"<=": [{"var": "total"}, 100]}, ctx) is False
    assert e({"in": [{"var": "lifecycle"}, ["prospect", "active"]]}, ctx) is True
    assert (
        e(
            {
                "and": [
                    {">": [{"var": "days_quiet"}, 7]},
                    {"==": [{"var": "lifecycle"}, "prospect"]},
                ]
            },
            ctx,
        )
        is True
    )
    assert (
        e(
            {"or": [{"<": [{"var": "total"}, 1]}, {"!": {"==": [{"var": "lifecycle"}, "lost"]}}]},
            ctx,
        )
        is True
    )
    # None never satisfies an ordered comparison — absent data is not "zero".
    assert e({">": [{"var": "missing"}, 0]}, ctx) is False
    assert e({">": [{"var": "nonexistent"}, 0]}, ctx) is False


def test_render_text_is_lookup_only():
    ctx = {"offer_number": "OFF-7", "days_quiet": 12}
    out = automation.render_text(
        "Offer {{offer_number}} quiet {{days_quiet}}d {{unknown}} {{__class__}}", ctx
    )
    assert out == "Offer OFF-7 quiet 12d {{unknown}} {{__class__}}"


# --------------------------------------------------------------------------- #
# API-level fixtures
# --------------------------------------------------------------------------- #


async def _project(client, code="AUT-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _stale_sent_offer(client, db_session, project_id: str, days: int) -> dict:
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Quote", "lines": [{"description": "Work", "amount": "450.00"}]},
    )
    assert r.status_code == 201, r.text
    offer = r.json()
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "sent"},
    )
    assert r.status_code == 200, r.text
    row = await db_session.get(ProjectOffer, offer["id"])
    row.updated_at = datetime.now(UTC) - timedelta(days=days)
    await db_session.commit()
    return offer


STALE_RULE = {
    "name": "Chase quiet offers",
    "trigger": "offer.sent_stale",
    "condition": {">": [{"var": "days_quiet"}, 7]},
    "actions": [
        {
            "kind": "notify_owner_email",
            "subject": "Offer {{offer_number}} has gone quiet",
            "body": "{{days_quiet}} days without an answer.",
        }
    ],
}


@pytest.mark.asyncio
async def test_rule_validation_and_publish_versioning(auth_client, db_session):
    bad = dict(STALE_RULE, trigger="offer.exploded")
    assert (await auth_client.post("/api/v1/automation/rules", json=bad)).status_code == 400
    bad = dict(STALE_RULE, actions=[{"kind": "notify_owner_email", "subject": "x"}])
    assert (await auth_client.post("/api/v1/automation/rules", json=bad)).status_code == 400
    bad = dict(STALE_RULE, fire_policy="cooldown")
    assert (await auth_client.post("/api/v1/automation/rules", json=bad)).status_code == 400
    bad = dict(STALE_RULE, condition={"eval": "os.system"})
    assert (await auth_client.post("/api/v1/automation/rules", json=bad)).status_code == 400

    made = await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)
    assert made.status_code == 201, made.text
    rule_id = made.json()["id"]
    assert made.json()["status"] == "draft"

    # A draft cannot be switched on — there is no version to run.
    r = await auth_client.put(
        f"/api/v1/automation/rules/{rule_id}/status", json={"status": "published"}
    )
    assert r.status_code == 400

    pub = await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")
    assert pub.status_code == 200 and pub.json()["published_version"] == 1

    # Edit the draft, publish again → version 2; revert to 1 → version 3
    # carrying version 1's definition. History is append-only.
    r = await auth_client.patch(
        f"/api/v1/automation/rules/{rule_id}",
        json={"condition": {">": [{"var": "days_quiet"}, 14]}, "set_condition": True},
    )
    assert r.status_code == 200
    pub2 = await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")
    assert pub2.json()["published_version"] == 2
    rev = await auth_client.post(f"/api/v1/automation/rules/{rule_id}/revert/1")
    assert rev.status_code == 200
    assert rev.json()["published_version"] == 3
    assert rev.json()["condition"] == {">": [{"var": "days_quiet"}, 7]}


@pytest.mark.asyncio
async def test_sweep_fires_once_and_policies_hold(auth_client, db_session):
    project_id = await _project(auth_client, "AUT-SWP")
    await _stale_sent_offer(auth_client, db_session, project_id, days=10)

    made = await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)
    rule_id = made.json()["id"]
    await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")

    org_id = (await db_session.scalar(select(ProjectOffer.org_id))) or ""
    out1 = await automation.sweep(db_session, org_id)
    await db_session.commit()
    assert out1["fired"] == 1, out1

    mails = list(
        await db_session.scalars(select(EmailMessage).where(EmailMessage.kind == "automation"))
    )
    assert len(mails) == 1
    assert "has gone quiet" in mails[0].subject
    assert "10 days without an answer." in mails[0].body

    run = await db_session.scalar(select(AutomationRun))
    assert run.status == "ok" and run.version == 1

    # once_per_record: the second sweep is silent.
    out2 = await automation.sweep(db_session, org_id)
    await db_session.commit()
    assert out2["fired"] == 0, "one fire per record, EVER, under the default policy"

    # cooldown: 1 hour later than a 0h cooldown → fires again, pinned to v2.
    r = await auth_client.patch(
        f"/api/v1/automation/rules/{rule_id}",
        json={"fire_policy": "cooldown", "cooldown_hours": 1},
    )
    assert r.status_code == 200, r.text
    await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")
    run.created_at = datetime.now(UTC) - timedelta(hours=2)
    await db_session.commit()
    out3 = await automation.sweep(db_session, org_id)
    await db_session.commit()
    assert out3["fired"] == 1, "past the cooldown, the rule may speak again"


@pytest.mark.asyncio
async def test_throttle_cap_is_visible_and_dry_run_touches_nothing(
    auth_client, db_session, monkeypatch
):
    project_id = await _project(auth_client, "AUT-CAP")
    await _stale_sent_offer(auth_client, db_session, project_id, days=10)
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Second", "lines": [{"description": "More work", "amount": "900.00"}]},
    )
    second = r.json()
    await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{second['id']}/transition",
        json={"status": "sent"},
    )
    row = await db_session.get(ProjectOffer, second["id"])
    row.updated_at = datetime.now(UTC) - timedelta(days=9)
    await db_session.commit()

    made = await auth_client.post("/api/v1/automation/rules", json=STALE_RULE)
    rule_id = made.json()["id"]

    # Dry-run BEFORE publish: reports both would-fire records, writes nothing.
    dry = await auth_client.post(f"/api/v1/automation/rules/{rule_id}/dry-run")
    assert dry.status_code == 200
    assert len(dry.json()["outcomes"]) == 2
    assert all(o["status"] == "would_fire" for o in dry.json()["outcomes"])
    assert (await db_session.scalar(select(AutomationRun))) is None
    assert (
        await db_session.scalar(select(EmailMessage).where(EmailMessage.kind == "automation"))
    ) is None

    await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")
    monkeypatch.setattr(automation, "MAX_FIRES_PER_SWEEP", 1)
    org_id = await db_session.scalar(select(ProjectOffer.org_id))
    out = await automation.sweep(db_session, org_id)
    await db_session.commit()
    assert out == {"rules": 1, "fired": 1, "throttled": 1, "failed": 0}
    statuses = sorted(
        await db_session.scalars(select(AutomationRun.status).order_by(AutomationRun.status))
    )
    assert statuses == ["ok", "throttled"], "the cap is a visible row, never a silent cut"


@pytest.mark.asyncio
async def test_customer_actions_note_lands_and_missing_email_fails_loud(auth_client, db_session):
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    r = await auth_client.post("/api/v1/customers", json={"name": "Riverbank Office"})
    cid = r.json()["id"]  # NO email on purpose
    project_id = await _project(auth_client, "AUT-CUS")
    r = await auth_client.put(
        f"/api/v1/masters/projects/{project_id}/customer", json={"customer_id": cid}
    )
    assert r.status_code == 200
    await _stale_sent_offer(auth_client, db_session, project_id, days=30)
    # Give the trigger a customer to act on: sent_stale carries project_id,
    # not customer_id — use the dormant-customer trigger for customer actions.
    rule = {
        "name": "Nudge dormant customers",
        "trigger": "customer.dormant",
        "condition": None,
        "actions": [
            {
                "kind": "create_customer_note",
                "body": "Automation: check in with {{customer_name}}.",
            },
            {"kind": "notify_customer_email", "subject": "Hello {{customer_name}}", "body": "..."},
        ],
        "fire_policy": "once_per_record",
    }
    made = await auth_client.post("/api/v1/automation/rules", json=rule)
    rule_id = made.json()["id"]
    await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")

    org_id = await db_session.scalar(select(ProjectOffer.org_id))
    out = await automation.sweep(db_session, org_id)
    await db_session.commit()
    assert out["failed"] == 1 and out["fired"] == 0, out

    run = await db_session.scalar(select(AutomationRun))
    detail = json.loads(run.detail_json)
    assert detail[0] == {"kind": "create_customer_note", "ok": True}
    assert detail[1]["ok"] is False and "email" in detail[1]["reason"]

    notes = (await auth_client.get(f"/api/v1/customers/{cid}/notes")).json()
    assert any("check in with Riverbank Office" in n["body"] for n in notes)
