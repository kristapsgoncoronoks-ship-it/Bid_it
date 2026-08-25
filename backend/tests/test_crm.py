"""CRM light (WO-H) — the contracts, pinned test by test.

1. Notes: add/list/delete with audit; an empty note refuses; unknown or
   other-tenant customers 404 opaquely (probed further in tenancy parity).
2. Lifecycle: a customer is born 'active'; stages are a closed set; every
   move is audited with where it moved FROM.
3. The timeline is DERIVED: notes, offer stage history, projects and
   invoices appear merged newest-first without anyone curating a feed.
4. The pipeline groups offers by status with days-in-stage from the stage
   history; a `sent` offer that sat still goes stale; stage events are
   recorded on create, transition and revise.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.crm import OfferStageEvent


async def _customer(client, name="Riverbank Office", email=None) -> str:
    await client.put("/api/v1/modules/issuing", json={"enabled": True})
    r = await client.post("/api/v1/customers", json={"name": name, "email": email})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _project(client, code="CRM-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _offer(client, project_id, amount="500.00") -> dict:
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Quote", "lines": [{"description": "Work", "amount": amount}]},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_notes_crud_with_audit_and_guards(auth_client, db_session):
    cid = await _customer(auth_client)

    r = await auth_client.post(f"/api/v1/customers/{cid}/notes", json={"body": "  "})
    assert r.status_code in (400, 422), "an empty note says nothing"

    r = await auth_client.post(
        f"/api/v1/customers/{cid}/notes", json={"body": "Prefers morning calls"}
    )
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["created_by"]

    listed = await auth_client.get(f"/api/v1/customers/{cid}/notes")
    assert [n["body"] for n in listed.json()] == ["Prefers morning calls"]

    gone = await auth_client.delete(f"/api/v1/customers/{cid}/notes/{note['id']}")
    assert gone.status_code == 204
    assert (await auth_client.get(f"/api/v1/customers/{cid}/notes")).json() == []

    actions = set(
        await db_session.scalars(
            select(AuditEvent.action).where(AuditEvent.action.like("customer.note%"))
        )
    )
    assert actions == {"customer.note_add", "customer.note_delete"}
    # The delete audit carries WHAT was destroyed.
    deleted = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "customer.note_delete")
    )
    assert json.loads(deleted.meta)["body"] == "Prefers morning calls"

    missing = await auth_client.get("/api/v1/customers/no-such-customer/notes")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_lifecycle_default_closed_set_and_audit(auth_client, db_session):
    cid = await _customer(auth_client)
    me = await auth_client.get(f"/api/v1/customers/{cid}")
    assert me.json()["lifecycle"] == "active", "born active"

    bad = await auth_client.put(f"/api/v1/customers/{cid}/lifecycle", json={"lifecycle": "vip"})
    assert bad.status_code == 400, "the stage set is closed"

    moved = await auth_client.put(
        f"/api/v1/customers/{cid}/lifecycle", json={"lifecycle": "dormant"}
    )
    assert moved.status_code == 200
    assert moved.json()["lifecycle"] == "dormant"

    ev = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "customer.lifecycle_set")
    )
    assert ev is not None
    assert json.loads(ev.meta) == {"lifecycle": "dormant", "prior": "active"}


@pytest.mark.asyncio
async def test_timeline_is_derived_and_newest_first(auth_client):
    cid = await _customer(auth_client, email="front@riverbank.example")
    project_id = await _project(auth_client, "CRM-TL")
    r = await auth_client.put(
        f"/api/v1/masters/projects/{project_id}/customer", json={"customer_id": cid}
    )
    assert r.status_code == 200
    offer = await _offer(auth_client, project_id)
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "sent"},
    )
    assert r.status_code == 200
    r = await auth_client.post(
        f"/api/v1/customers/{cid}/notes", json={"body": "Asked for a revised quote"}
    )
    assert r.status_code == 201

    tl = (await auth_client.get(f"/api/v1/customers/{cid}/timeline")).json()["events"]
    kinds = {e["kind"] for e in tl}
    assert {"note", "offer", "project"} <= kinds
    titles = " | ".join(e["title"] for e in tl)
    assert "created" in titles and "sent" in titles, "stage history feeds the timeline"
    stamps = [e["at"] for e in tl]
    assert stamps == sorted(stamps, reverse=True), "newest first"
    assert all(e["at"] and e["kind"] and e["title"] for e in tl)


@pytest.mark.asyncio
async def test_pipeline_groups_days_and_staleness(auth_client, db_session):
    project_id = await _project(auth_client, "CRM-PIPE")
    fresh = await _offer(auth_client, project_id, amount="100.00")
    quiet = await _offer(auth_client, project_id, amount="900.00")
    parked_draft = await _offer(auth_client, project_id, amount="50.00")
    for oid in (fresh["id"], quiet["id"]):
        r = await auth_client.post(
            f"/api/v1/masters/projects/{project_id}/offers/{oid}/transition",
            json={"status": "sent"},
        )
        assert r.status_code == 200

    # Backdate the quiet offer's whole history: it went out 20 days ago and
    # nothing has moved since — the definition of a rotting deal. The parked
    # DRAFT is just as old but rots nobody: it is ours, not the client's.
    old = datetime.now(UTC) - timedelta(days=20)
    for se in await db_session.scalars(
        select(OfferStageEvent).where(
            OfferStageEvent.offer_id.in_((quiet["id"], parked_draft["id"]))
        )
    ):
        se.created_at = old
    await db_session.commit()

    pipe = (await auth_client.get("/api/v1/masters/offers-pipeline")).json()
    sent = pipe["columns"]["sent"]
    by_id = {row["offer_id"]: row for row in sent}
    assert by_id[quiet["id"]]["stale"] is True
    assert by_id[quiet["id"]]["days_in_stage"] >= 20
    assert by_id[fresh["id"]]["stale"] is False
    assert sent[0]["offer_id"] == quiet["id"], "stalest first within a column"
    assert by_id[quiet["id"]]["project"].startswith("CRM-PIPE")
    draft_rows = {row["offer_id"]: row for row in pipe["columns"]["draft"]}
    assert draft_rows[parked_draft["id"]]["days_in_stage"] >= 20
    assert draft_rows[parked_draft["id"]]["stale"] is False, "only SENT offers rot"


@pytest.mark.asyncio
async def test_stage_events_recorded_on_create_transition_and_revise(auth_client, db_session):
    project_id = await _project(auth_client, "CRM-EVT")
    offer = await _offer(auth_client, project_id)
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "sent"},
    )
    assert r.status_code == 200
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/revise",
        json={"lines": [{"description": "Work, revised", "amount": "750.00"}]},
    )
    assert r.status_code in (200, 201), r.text

    events = list(
        await db_session.scalars(
            select(OfferStageEvent).order_by(OfferStageEvent.created_at, OfferStageEvent.id)
        )
    )
    moves = [(e.from_status, e.to_status) for e in events]
    assert (None, "draft") in moves, "birth is recorded"
    assert ("draft", "sent") in moves
    assert ("sent", "superseded") in moves, "revising records the supersede"
    assert moves.count((None, "draft")) == 2, "the revision is born with its own history"
    assert all(e.actor for e in events if e.to_status == "sent"), "transitions carry the actor"
