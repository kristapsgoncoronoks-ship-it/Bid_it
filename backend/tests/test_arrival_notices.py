"""WO-E: client arrival notices — "we arrive in 48h", to the project's CUSTOMER.

The contracts, pinned test by test:

1. The schedule-settings surface serves BOTH audiences (employee reminder
   default + client notice hours); the client lead is pinned to 24/48/72.
2. The notice is OPT-IN: nothing is armed while the org default is off and
   the assignment has no override; enabling either arms the job.
3. Quiet hours: a due moment in [20:00, 07:00) UTC is deferred to 07:00 that
   morning — and never past the work itself.
4. The notice goes to the CUSTOMER's email, exactly once (at-least-once
   queue, one-notice stamp), with no money in the body.
5. No recipient (no customer on the project, or no email on the customer)
   → nothing sent, nothing stamped — pointing the project at a customer
   later still works.
6. Cancelled assignments never notify; one moved LATER re-arms instead of
   firing early.
7. The org-level employee-reminder default is honored by the reminder job.
8. Linking a cross-tenant customer to a project 404s opaquely (§4.4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.email_message import EmailMessage
from app.models.job import Job
from app.models.project_assignment import ProjectAssignment
from app.services import scheduling


async def _project(client, code="ARR-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _customer(client, name="Riverside Office", email="reception@riverside.example") -> str:
    await client.put("/api/v1/modules/issuing", json={"enabled": True})
    r = await client.post("/api/v1/customers", json={"name": name, "email": email})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _link(client, project_id: str, customer_id: str | None) -> None:
    r = await client.put(
        f"/api/v1/masters/projects/{project_id}/customer", json={"customer_id": customer_id}
    )
    assert r.status_code == 200, r.text


async def _me(client) -> dict:
    return (await client.get("/api/v1/auth/me")).json()["user"]


def _soon(hours_from_now: float, length_h: float = 8) -> tuple[str, str]:
    s = datetime.now(UTC) + timedelta(hours=hours_from_now)
    return s.isoformat(), (s + timedelta(hours=length_h)).isoformat()


async def _assign(client, project_id, user_id, *, start_h=100.0, client_notice=None):
    starts, ends = _soon(start_h)
    body = {
        "project_id": project_id,
        "assignee_user_id": user_id,
        "starts_at": starts,
        "ends_at": ends,
    }
    if client_notice is not None:
        body["client_notice_hours_before"] = client_notice
    r = await client.post("/api/v1/schedule/assignments", json=body)
    assert r.status_code == 201, r.text
    return r.json()["assignment"]


async def _notice_jobs(db_session) -> list[Job]:
    return list(await db_session.scalars(select(Job).where(Job.kind == scheduling.CLIENT_NOTICE)))


async def _enable_org_notices(client, hours=48) -> None:
    r = await client.put("/api/v1/settings/schedule", json={"client_notice_hours": hours})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_schedule_settings_roundtrip_and_validation(auth_client):
    r = await auth_client.get("/api/v1/settings/schedule")
    assert r.status_code == 200
    assert r.json() == {"assignment_remind_hours": None, "client_notice_hours": None}

    r = await auth_client.put(
        "/api/v1/settings/schedule",
        json={"assignment_remind_hours": 48, "client_notice_hours": 72},
    )
    assert r.status_code == 200
    assert r.json() == {"assignment_remind_hours": 48, "client_notice_hours": 72}

    # The client lead is a pinned choice — a typo cannot schedule a week out.
    r = await auth_client.put("/api/v1/settings/schedule", json={"client_notice_hours": 36})
    assert r.status_code == 400

    r = await auth_client.put(
        "/api/v1/settings/schedule",
        json={"clear_assignment_remind_hours": True, "clear_client_notice_hours": True},
    )
    assert r.status_code == 200
    assert r.json() == {"assignment_remind_hours": None, "client_notice_hours": None}


@pytest.mark.asyncio
async def test_notice_is_opt_in_org_default_or_override_arms(auth_client, db_session):
    project_id = await _project(auth_client, "ARR-OPT")
    me = await _me(auth_client)

    await _assign(auth_client, project_id, me["id"])
    assert await _notice_jobs(db_session) == [], "off by default: outward email is opt-in"

    # A per-assignment override enables the notice even with the org off.
    await _assign(auth_client, project_id, me["id"], client_notice=24)
    assert len(await _notice_jobs(db_session)) == 1

    # The org default arms every subsequent assignment.
    await _enable_org_notices(auth_client, 48)
    await _assign(auth_client, project_id, me["id"])
    assert len(await _notice_jobs(db_session)) == 2


def test_quiet_hours_deferral_is_deterministic():
    def row(start: datetime, lead: int) -> ProjectAssignment:
        return ProjectAssignment(
            starts_at=start, ends_at=start + timedelta(hours=8), client_notice_hours_before=lead
        )

    # Daytime due moment passes through untouched.
    start = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
    assert scheduling.client_notice_due_at(row(start, 6), None) == start - timedelta(hours=6)
    # 03:00 → 07:00 the same morning.
    assert scheduling.client_notice_due_at(row(start, 15), None) == datetime(
        2026, 9, 10, 7, 0, tzinfo=UTC
    )
    # 22:00 the evening before → 07:00 the NEXT morning.
    assert scheduling.client_notice_due_at(row(start, 20), None) == datetime(
        2026, 9, 10, 7, 0, tzinfo=UTC
    )
    # The deferral never moves a notice past the work itself.
    early = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
    assert scheduling.client_notice_due_at(row(early, 1), None) == early
    # No lead anywhere → the feature is off for this assignment.
    off = ProjectAssignment(starts_at=start, ends_at=start + timedelta(hours=8))
    assert scheduling.client_notice_due_at(off, None) is None


@pytest.mark.asyncio
async def test_notice_sends_once_to_the_customer_without_money(auth_client, db_session):
    project_id = await _project(auth_client, "ARR-SND")
    customer_id = await _customer(auth_client)
    await _link(auth_client, project_id, customer_id)
    await _enable_org_notices(auth_client)
    me = await _me(auth_client)
    a = await _assign(auth_client, project_id, me["id"], start_h=100)

    # Force the due moment into the past (the handler reads CURRENT state).
    row = await db_session.get(ProjectAssignment, a["id"])
    row.client_notice_hours_before = 200
    await db_session.commit()

    out = await scheduling.send_due_client_notice(db_session, row.org_id, a["id"])
    await db_session.commit()
    assert out == {"sent": True, "to": "reception@riverside.example"}

    out2 = await scheduling.send_due_client_notice(db_session, row.org_id, a["id"])
    assert out2 == {"sent": False, "reason": "stale"}, "one notice EVER — the stamp holds"

    notices = list(
        await db_session.scalars(select(EmailMessage).where(EmailMessage.kind == "client_notice"))
    )
    assert len(notices) == 1
    assert notices[0].to_email == "reception@riverside.example"
    assert "reschedule" in notices[0].body
    assert "€" not in notices[0].body and "EUR" not in notices[0].body


@pytest.mark.asyncio
async def test_no_recipient_sends_nothing_and_does_not_stamp(auth_client, db_session):
    project_id = await _project(auth_client, "ARR-NOR")
    await _enable_org_notices(auth_client)
    me = await _me(auth_client)
    a = await _assign(auth_client, project_id, me["id"], start_h=100)
    row = await db_session.get(ProjectAssignment, a["id"])
    row.client_notice_hours_before = 200  # due long past
    await db_session.commit()

    # No customer on the project yet.
    out = await scheduling.send_due_client_notice(db_session, row.org_id, a["id"])
    assert out == {"sent": False, "reason": "no-recipient"}

    # A customer without an email is still not a recipient.
    silent = await _customer(auth_client, name="Walk-in", email=None)
    await _link(auth_client, project_id, silent)
    out = await scheduling.send_due_client_notice(db_session, row.org_id, a["id"])
    assert out == {"sent": False, "reason": "no-recipient"}

    # Nothing was stamped, so fixing the link makes the SAME job's retry send.
    reachable = await _customer(auth_client, name="Riverside", email="front@riverside.example")
    await _link(auth_client, project_id, reachable)
    out = await scheduling.send_due_client_notice(db_session, row.org_id, a["id"])
    await db_session.commit()
    assert out == {"sent": True, "to": "front@riverside.example"}


@pytest.mark.asyncio
async def test_cancelled_never_notifies_and_moved_later_rearms(auth_client, db_session):
    project_id = await _project(auth_client, "ARR-STL")
    customer_id = await _customer(auth_client, email="site@riverside.example")
    await _link(auth_client, project_id, customer_id)
    await _enable_org_notices(auth_client)
    me = await _me(auth_client)

    a = await _assign(auth_client, project_id, me["id"], start_h=50)
    r = await auth_client.post(
        f"/api/v1/schedule/assignments/{a['id']}/transition", json={"status": "cancelled"}
    )
    assert r.status_code == 200
    row = await db_session.get(ProjectAssignment, a["id"])
    out = await scheduling.send_due_client_notice(db_session, row.org_id, a["id"])
    assert out == {"sent": False, "reason": "stale"}

    b = await _assign(auth_client, project_id, me["id"], start_h=50)
    before = len(await _notice_jobs(db_session))
    starts, ends = _soon(500)
    r = await auth_client.patch(
        f"/api/v1/schedule/assignments/{b['id']}", json={"starts_at": starts, "ends_at": ends}
    )
    assert r.status_code == 200
    row_b = await db_session.get(ProjectAssignment, b["id"])
    out = await scheduling.send_due_client_notice(db_session, row_b.org_id, b["id"])
    await db_session.commit()
    assert out == {"sent": False, "reason": "rearmed"}
    assert len(await _notice_jobs(db_session)) >= before
    assert not list(
        await db_session.scalars(select(EmailMessage).where(EmailMessage.kind == "client_notice"))
    ), "an early firing must not send"


@pytest.mark.asyncio
async def test_org_remind_default_is_honored(auth_client, db_session):
    project_id = await _project(auth_client, "ARR-DEF")
    me = await _me(auth_client)
    r = await auth_client.put("/api/v1/settings/schedule", json={"assignment_remind_hours": 90})
    assert r.status_code == 200

    a = await _assign(auth_client, project_id, me["id"], start_h=100)
    job = await db_session.scalar(
        select(Job).where(
            Job.kind == scheduling.ASSIGNMENT_REMINDER,
            Job.payload_json.contains(a["id"]),
        )
    )
    assert job is not None
    starts = datetime.fromisoformat(a["starts_at"])
    if starts.tzinfo is None:  # SQLite hands back naive-UTC
        starts = starts.replace(tzinfo=UTC)
    run_after = job.run_after if job.run_after.tzinfo else job.run_after.replace(tzinfo=UTC)
    assert abs((starts - run_after) - timedelta(hours=90)) < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_linking_a_cross_tenant_customer_404s(auth_client, client, db_session):
    project_id = await _project(auth_client, "ARR-XT")
    # A second, unrelated workspace with its own customer.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Firm",
            "email": "owner@otherfirm.example",
            "password": "supersecret1",
            "name": "Other Owner",
        },
    )
    assert reg.status_code in (200, 201), reg.text
    bearer = reg.json()["token"]["access_token"]
    await client.put(
        "/api/v1/modules/issuing",
        json={"enabled": True},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    foreign = await client.post(
        "/api/v1/customers",
        json={"name": "Foreign Co", "email": "x@foreign.example"},
        headers={"Authorization": f"Bearer {bearer}"},
    )
    assert foreign.status_code in (200, 201)

    r = await auth_client.put(
        f"/api/v1/masters/projects/{project_id}/customer",
        json={"customer_id": foreign.json()["id"]},
    )
    assert r.status_code == 404, "unknown and other-tenant must be indistinguishable"
