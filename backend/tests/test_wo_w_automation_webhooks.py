"""WO-W — automation reaches outward, and delivery stops duplicating.

Two halves of one theme: the engine could not tell an external system anything,
and the subsystem that talks to external systems could not tell a retry from a
second event.

WHAT THESE PIN
----------------
1. **A rule fires a webhook end to end**, through the same durable queue,
   HMAC signing and SSRF guard the manual emit path uses. The engine composes;
   it does not learn to make an HTTP request.
2. **A duplicate emit delivers ONCE.** Until WO-W, `emit` created a delivery per
   endpoint unconditionally, so a caller that retried — a resubmit after a
   timeout, a job re-running — sent "the invoice was approved" to the customer's
   system twice. A receiver that books on those events cannot tell a duplicate
   from a second occurrence.
3. **The key is opt-in**, and an unkeyed emit still delivers every time. A
   caller with no natural key must not be forced to invent one: an invented key
   that collided would SUPPRESS a real delivery, which is worse than the
   duplicate it prevents.
4. **Dry-run sends nothing** — the property that makes the builder's preview
   safe to press.
5. **A rule cannot invent an event type.** `webhooks.EVENT_TYPES` is a catalog
   receivers subscribe against; a workspace publishing names nobody could
   subscribe to would be a silent no-op wearing the shape of an integration.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.models.automation import ACTIONS, AUTOMATION_EVENT
from app.models.project_offer import ProjectOffer
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services import automation, webhooks

pytestmark = pytest.mark.asyncio

WEBHOOK_RULE = {
    "name": "Tell the ops system a quote went quiet",
    "trigger": "offer.sent_stale",
    "condition": {">": [{"var": "days_quiet"}, 7]},
    "actions": [{"kind": "emit_webhook", "body": "Quiet for {{days_quiet}} days."}],
}


async def _endpoint(db_session, org_id: str, *, events: str = "*") -> WebhookEndpoint:
    ep = WebhookEndpoint(
        org_id=org_id,
        url=f"https://hooks.example.test/{uuid.uuid4().hex[:8]}",
        secret=webhooks.new_secret(),
        events=events,
        active=True,
    )
    db_session.add(ep)
    await db_session.flush()
    return ep


async def _deliveries(db_session, org_id: str) -> list[WebhookDelivery]:
    return list(
        await db_session.scalars(select(WebhookDelivery).where(WebhookDelivery.org_id == org_id))
    )


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


async def test_a_repeated_emit_under_one_key_delivers_once(db_session):
    """The defect this closes: `emit` used to create a delivery per endpoint
    unconditionally, so a retried caller double-delivered."""
    org_id = str(uuid.uuid4())
    from app.models.organization import Organization

    db_session.add(Organization(id=org_id, name="WO-W Org"))
    await db_session.flush()
    await _endpoint(db_session, org_id)

    first = await webhooks.emit(
        db_session, org_id, "ping", {"n": 1}, idempotency_key="invoice-42-approved"
    )
    second = await webhooks.emit(
        db_session, org_id, "ping", {"n": 1}, idempotency_key="invoice-42-approved"
    )
    await db_session.commit()

    assert first == 1
    # 0, not 1: the honest count of what was ENQUEUED, which is what a caller
    # wanting to log "already sent" needs.
    assert second == 0
    assert len(await _deliveries(db_session, org_id)) == 1


async def test_an_unkeyed_emit_still_delivers_every_time(db_session):
    """The key is OPT-IN. A caller with no natural key keeps the old behaviour
    exactly — the partial index excludes NULLs on purpose."""
    org_id = str(uuid.uuid4())
    from app.models.organization import Organization

    db_session.add(Organization(id=org_id, name="WO-W Org"))
    await db_session.flush()
    await _endpoint(db_session, org_id)

    assert await webhooks.emit(db_session, org_id, "ping", {"n": 1}) == 1
    assert await webhooks.emit(db_session, org_id, "ping", {"n": 1}) == 1
    await db_session.commit()

    assert len(await _deliveries(db_session, org_id)) == 2


async def test_a_key_dedupes_per_endpoint_not_globally(db_session):
    """An endpoint registered BETWEEN two emits must still get its first
    delivery. The collision is per (endpoint, key), and each insert runs in its
    own SAVEPOINT so one duplicate does not roll back the others."""
    org_id = str(uuid.uuid4())
    from app.models.organization import Organization

    db_session.add(Organization(id=org_id, name="WO-W Org"))
    await db_session.flush()
    first_ep = await _endpoint(db_session, org_id)

    assert await webhooks.emit(db_session, org_id, "ping", {"n": 1}, idempotency_key="k") == 1
    second_ep = await _endpoint(db_session, org_id)
    # The same key again: endpoint 1 is a duplicate, endpoint 2 is not.
    assert await webhooks.emit(db_session, org_id, "ping", {"n": 1}, idempotency_key="k") == 1
    await db_session.commit()

    rows = await _deliveries(db_session, org_id)
    assert len(rows) == 2
    assert {r.endpoint_id for r in rows} == {first_ep.id, second_ep.id}


async def test_a_dedupe_does_not_poison_the_callers_transaction(db_session):
    """`emit` is called from inside business operations that have already done
    their real work. A unique-index collision must not take that work down with
    it — hence the per-endpoint SAVEPOINT."""
    org_id = str(uuid.uuid4())
    from app.models.organization import Organization

    db_session.add(Organization(id=org_id, name="WO-W Org"))
    await db_session.flush()
    await _endpoint(db_session, org_id)

    await webhooks.emit(db_session, org_id, "ping", {}, idempotency_key="dup")
    await webhooks.emit(db_session, org_id, "ping", {}, idempotency_key="dup")
    # The session is still usable — a write after the collision commits.
    db_session.add(Organization(id=str(uuid.uuid4()), name="Still Working"))
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Organization)) >= 2


# --------------------------------------------------------------------------- #
# The automation action
# --------------------------------------------------------------------------- #


async def test_the_action_catalog_gained_exactly_one_outward_kind():
    assert "emit_webhook" in ACTIONS
    # …and the event it publishes is in the documented catalog receivers
    # subscribe against. A rule cannot invent an event name.
    assert AUTOMATION_EVENT in webhooks.EVENT_TYPES


async def test_a_rule_fires_a_webhook_end_to_end(auth_client, db_session):
    """Through the real sweep, the real action executor and the real emit —
    landing a queued delivery, which is what the durable queue then signs and
    POSTs."""
    from tests.test_automation import _project, _stale_sent_offer

    project_id = await _project(auth_client, "WOW-1")
    await _stale_sent_offer(auth_client, db_session, project_id, days=10)
    org_id = (await db_session.scalar(select(ProjectOffer.org_id))) or ""
    await _endpoint(db_session, org_id)
    await db_session.commit()

    made = await auth_client.post("/api/v1/automation/rules", json=WEBHOOK_RULE)
    assert made.status_code in (200, 201), made.text
    rule_id = made.json()["id"]
    await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")

    out = await automation.sweep(db_session, org_id)
    await db_session.commit()
    assert out["fired"] == 1, out

    rows = await _deliveries(db_session, org_id)
    assert len(rows) == 1
    assert rows[0].event_type == AUTOMATION_EVENT
    # The payload names the rule and carries the record — which rule fired is
    # IN the payload, not in the event name.
    assert WEBHOOK_RULE["name"] in rows[0].payload_json
    assert "Quiet for 10 days." in rows[0].payload_json
    # …and it is keyed, so a re-fire of the same rule on the same record cannot
    # double-notify.
    assert rows[0].idempotency_key is not None
    assert rule_id in rows[0].idempotency_key


async def test_dry_run_enqueues_no_delivery(auth_client, db_session):
    """The property that makes the builder's preview safe to press."""
    from tests.test_automation import _project, _stale_sent_offer

    project_id = await _project(auth_client, "WOW-2")
    await _stale_sent_offer(auth_client, db_session, project_id, days=10)
    org_id = (await db_session.scalar(select(ProjectOffer.org_id))) or ""
    await _endpoint(db_session, org_id)
    await db_session.commit()

    made = await auth_client.post("/api/v1/automation/rules", json=WEBHOOK_RULE)
    rule_id = made.json()["id"]
    await auth_client.post(f"/api/v1/automation/rules/{rule_id}/publish")

    preview = await auth_client.post(f"/api/v1/automation/rules/{rule_id}/dry-run")
    assert preview.status_code == 200, preview.text
    # Positive anchor: the dry run really did match the record…
    assert any(o["status"] == "would_fire" for o in preview.json()["outcomes"]), preview.json()
    # …and sent nothing.
    assert await _deliveries(db_session, org_id) == []
