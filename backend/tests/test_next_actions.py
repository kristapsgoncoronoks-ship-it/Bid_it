"""Next actions (WO-C) — the research contract, pinned test by test:

1. Items are DERIVED and SELF-COMPLETING: an offer nudge exists only while
   the offer sits in `sent` past the threshold, and dies the moment the
   offer is accepted — no task row to clean up, nothing to rot.
2. An overdue issued invoice becomes a chase item; paying it clears the
   item without anyone touching a task.
3. A dismissal is permanent for that item; non-dismissible kinds refuse.
4. Deadline templates surface inside their lead window and complete PER
   PERIOD — done this month, back next month.
5. The surface is planner-facing: an employee-role user gets 403.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.models.project_offer import ProjectOffer
from app.services import next_actions


async def _project(client, code) -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _sent_offer(client, db_session, project_id, *, days_ago=5) -> dict:
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={"title": "Quote", "lines": [{"description": "Work", "amount": "1000.00"}]},
    )
    assert r.status_code == 201, r.text
    offer = r.json()
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "sent"},
    )
    assert r.status_code == 200, r.text
    # Age it: the generator keys off updated_at.
    await db_session.execute(
        update(ProjectOffer)
        .where(ProjectOffer.id == offer["id"])
        .values(updated_at=datetime.now(UTC) - timedelta(days=days_ago))
    )
    await db_session.commit()
    return offer


def _by_kind(items, kind):
    return [a for a in items if a["kind"] == kind]


async def _actions(client):
    r = await client.get("/api/v1/next-actions")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_offer_nudge_appears_and_self_completes(auth_client, db_session):
    project_id = await _project(auth_client, "NA-OFF")
    offer = await _sent_offer(auth_client, db_session, project_id, days_ago=5)

    nudges = _by_kind(await _actions(auth_client), "offer_followup")
    assert [n["ref_id"] for n in nudges] == [offer["id"]]
    assert "Follow up on offer" in nudges[0]["title"]
    assert nudges[0]["age_days"] >= 5
    assert nudges[0]["link"] == f"/projects/{project_id}"

    # The work happens (offer accepted) → the item is GONE, untouched by hand.
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "accepted"},
    )
    assert r.status_code == 200
    # Re-age the row PAST the freshness cutoff again: accepting touches
    # updated_at, and without this the age filter would mask a broken status
    # filter — a seeded status-ignoring generator sailed through the naive
    # version of this assertion.
    await db_session.execute(
        update(ProjectOffer)
        .where(ProjectOffer.id == offer["id"])
        .values(updated_at=datetime.now(UTC) - timedelta(days=5))
    )
    await db_session.commit()
    assert _by_kind(await _actions(auth_client), "offer_followup") == [], (
        "an accepted offer must never nudge — self-completion is the contract"
    )


@pytest.mark.asyncio
async def test_fresh_offers_do_not_nag(auth_client, db_session):
    project_id = await _project(auth_client, "NA-FRESH")
    await _sent_offer(auth_client, db_session, project_id, days_ago=1)
    assert _by_kind(await _actions(auth_client), "offer_followup") == []


@pytest.mark.asyncio
async def test_overdue_invoice_becomes_chase_and_payment_clears_it(auth_client):
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
    assert r.status_code == 200, r.text
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    due = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()
    r = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Customer OU",
            "due_date": due,
            "lines": [{"description": "Work performed", "quantity": "1", "unit_price": "500.00"}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()

    chases = _by_kind(await _actions(auth_client), "invoice_chase")
    assert [c["ref_id"] for c in chases] == [inv["id"]]
    assert "10 days overdue" in chases[0]["detail"]

    # Money arrives → the chase item disappears by itself.
    r = await auth_client.patch(
        f"/api/v1/issued/{inv['id']}/payment", json={"amount_paid": inv["total"]}
    )
    assert r.status_code == 200, r.text
    assert _by_kind(await _actions(auth_client), "invoice_chase") == []


@pytest.mark.asyncio
async def test_dismissal_is_permanent_and_gated_by_kind(auth_client, db_session):
    project_id = await _project(auth_client, "NA-DIS")
    offer = await _sent_offer(auth_client, db_session, project_id, days_ago=7)

    r = await auth_client.post(
        "/api/v1/next-actions/dismiss", json={"kind": "offer_followup", "ref_id": offer["id"]}
    )
    assert r.status_code == 204
    assert _by_kind(await _actions(auth_client), "offer_followup") == []
    # Dismissing again is idempotent, not an error.
    r = await auth_client.post(
        "/api/v1/next-actions/dismiss", json={"kind": "offer_followup", "ref_id": offer["id"]}
    )
    assert r.status_code == 204

    # Work-queue kinds resolve by DOING, not by dismissing.
    r = await auth_client.post(
        "/api/v1/next-actions/dismiss", json={"kind": "capture_backlog", "ref_id": "captures"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_deadlines_surface_in_window_and_complete_per_period(auth_client):
    today = datetime.now(UTC).date()
    r = await auth_client.post(
        "/api/v1/next-actions/deadlines",
        json={
            "name": "Prepare the VAT report",
            "cadence": "monthly",
            "due_day": min(today.day, 28),
            "lead_days": 7,
        },
    )
    assert r.status_code == 201, r.text
    deadline = r.json()

    items = _by_kind(await _actions(auth_client), "deadline")
    assert [d["ref_id"] for d in items] == [deadline["id"]]
    assert "Prepare the VAT report" in items[0]["title"]

    # Done for THIS period → gone; the template survives for next period.
    r = await auth_client.post(f"/api/v1/next-actions/deadlines/{deadline['id']}/complete")
    assert r.status_code == 200
    assert r.json()["last_done_period"] == f"{today.year}-{today.month:02d}"
    assert _by_kind(await _actions(auth_client), "deadline") == []

    listed = await auth_client.get("/api/v1/next-actions/deadlines")
    assert [d["id"] for d in listed.json()] == [deadline["id"]]

    # A deadline far outside its lead window stays silent.
    far = await auth_client.post(
        "/api/v1/next-actions/deadlines",
        json={"name": "Year-end close", "cadence": "yearly", "due_day": 28, "lead_days": 0},
    )
    assert far.status_code == 201
    if today.month < 12:
        assert _by_kind(await _actions(auth_client), "deadline") == []


@pytest.mark.asyncio
async def test_period_math_is_exact():
    from datetime import date

    assert next_actions._period_for("monthly", date(2026, 8, 23)) == "2026-08"
    assert next_actions._period_for("quarterly", date(2026, 8, 23)) == "2026-Q3"
    assert next_actions._period_for("yearly", date(2026, 8, 23)) == "2026"


@pytest.mark.asyncio
async def test_surface_is_planner_facing(auth_client, client):
    invite = await auth_client.post(
        "/api/v1/team/invites", json={"email": "crew-na@acme.io", "role": "user"}
    )
    token = invite.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "name": "Crew", "password": "supersecret"},
    )
    bearer = acc.json()["token"]["access_token"]
    r = await client.get("/api/v1/next-actions", headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 403
