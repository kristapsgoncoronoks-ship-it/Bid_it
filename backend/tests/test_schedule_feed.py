"""WO-B: assignment notifications, exact-time reminders, and the ICS feed.

The contracts, pinned test by test:

1. Assigning work emails the assignee; rescheduling and cancelling email too.
2. The reminder is armed on the durable queue at (start − lead) and sends
   EXACTLY ONCE — re-running the handler after success is a no-op
   (at-least-once queue, one-reminder contract), and a cancelled assignment
   never reminds.
3. A reminder whose assignment moved LATER re-arms itself instead of firing
   early.
4. The feed token is lazily created, stable across reads, and regenerating
   kills the old URL (404) while the new one works.
5. The feed is valid-enough ICS: VCALENDAR/VEVENT wrapping, CRLF endings,
   RFC 5545 text escaping, UTC times, cancelled assignments absent, and NO
   financial figures.
6. An unknown token 404s.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.email_message import EmailMessage
from app.models.job import Job
from app.models.project_assignment import ProjectAssignment
from app.services import scheduling


async def _project(client, code="FED-1") -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _me(client) -> dict:
    return (await client.get("/api/v1/auth/me")).json()["user"]


def _soon(hours_from_now: float, length_h: float = 8) -> tuple[str, str]:
    s = datetime.now(UTC) + timedelta(hours=hours_from_now)
    return s.isoformat(), (s + timedelta(hours=length_h)).isoformat()


async def _assign(client, project_id, user_id, *, start_h=100.0, remind=None, note=None):
    starts, ends = _soon(start_h)
    body = {
        "project_id": project_id,
        "assignee_user_id": user_id,
        "starts_at": starts,
        "ends_at": ends,
    }
    if remind is not None:
        body["remind_hours_before"] = remind
    if note is not None:
        body["note"] = note
    r = await client.post("/api/v1/schedule/assignments", json=body)
    assert r.status_code == 201, r.text
    return r.json()["assignment"]


@pytest.mark.asyncio
async def test_lifecycle_events_email_the_assignee(auth_client, db_session):
    project_id = await _project(auth_client, "FED-NTF")
    me = await _me(auth_client)
    a = await _assign(auth_client, project_id, me["id"], note="Bring the signed contract")

    r = await auth_client.patch(
        f"/api/v1/schedule/assignments/{a['id']}", json={"note": "Gate code 4711"}
    )
    assert r.status_code == 200
    r = await auth_client.post(
        f"/api/v1/schedule/assignments/{a['id']}/transition", json={"status": "cancelled"}
    )
    assert r.status_code == 200

    subjects = list(
        await db_session.scalars(
            select(EmailMessage.subject).where(EmailMessage.to_email == me["email"])
        )
    )
    assert "New work assignment" in subjects
    assert "Your work assignment changed" in subjects
    assert "Work assignment cancelled" in subjects


@pytest.mark.asyncio
async def test_reminder_is_armed_fires_once_and_never_for_cancelled(auth_client, db_session):
    project_id = await _project(auth_client, "FED-REM")
    me = await _me(auth_client)
    # Starts in 100h, remind 99.9h before → due ~6 minutes from now: armed, not due.
    a = await _assign(auth_client, project_id, me["id"], start_h=100, remind=None)

    job = await db_session.scalar(
        select(Job).where(Job.kind == scheduling.ASSIGNMENT_REMINDER)
    )
    assert job is not None, "creating an assignment must arm the reminder job"
    assert job.payload_json and a["id"] in job.payload_json

    # Simulate the queue firing at/after the due moment: force the due time to
    # the past by shrinking the lead directly (the handler reads CURRENT state).
    row = await db_session.get(ProjectAssignment, a["id"])
    row.remind_hours_before = 200  # due = start − 200h → long past
    await db_session.commit()

    out = await scheduling.send_due_reminder(db_session, row.org_id, a["id"])
    await db_session.commit()
    assert out == {"sent": True}
    out2 = await scheduling.send_due_reminder(db_session, row.org_id, a["id"])
    assert out2 == {"sent": False, "reason": "stale"}, "one reminder EVER — the stamp holds"

    reminders = list(
        await db_session.scalars(
            select(EmailMessage).where(EmailMessage.kind == "assignment_reminder")
        )
    )
    assert len(reminders) == 1

    # A cancelled assignment never reminds.
    b = await _assign(auth_client, project_id, me["id"], start_h=50)
    r = await auth_client.post(
        f"/api/v1/schedule/assignments/{b['id']}/transition", json={"status": "cancelled"}
    )
    assert r.status_code == 200
    row_b = await db_session.get(ProjectAssignment, b["id"])
    out3 = await scheduling.send_due_reminder(db_session, row_b.org_id, b["id"])
    assert out3 == {"sent": False, "reason": "stale"}


@pytest.mark.asyncio
async def test_stale_reminder_rearms_when_start_moved_later(auth_client, db_session):
    project_id = await _project(auth_client, "FED-ARM")
    me = await _me(auth_client)
    a = await _assign(auth_client, project_id, me["id"], start_h=30, remind=24)

    before = len(
        list(await db_session.scalars(select(Job).where(Job.kind == scheduling.ASSIGNMENT_REMINDER)))
    )
    # The job fires "now", but the assignment has been moved far later → the
    # handler must NOT send; it re-arms for the new due moment instead.
    starts, ends = _soon(500)
    r = await auth_client.patch(
        f"/api/v1/schedule/assignments/{a['id']}", json={"starts_at": starts, "ends_at": ends}
    )
    assert r.status_code == 200

    row = await db_session.get(ProjectAssignment, a["id"])
    out = await scheduling.send_due_reminder(db_session, row.org_id, a["id"])
    await db_session.commit()
    assert out == {"sent": False, "reason": "rearmed"}
    after = len(
        list(await db_session.scalars(select(Job).where(Job.kind == scheduling.ASSIGNMENT_REMINDER)))
    )
    assert after >= before
    assert not list(
        await db_session.scalars(
            select(EmailMessage).where(EmailMessage.kind == "assignment_reminder")
        )
    ), "an early firing must not send"


@pytest.mark.asyncio
async def test_feed_token_lifecycle(auth_client):
    t1 = await auth_client.get("/api/v1/schedule/feed-token")
    assert t1.status_code == 200, t1.text
    t2 = await auth_client.get("/api/v1/schedule/feed-token")
    assert t1.json()["token"] == t2.json()["token"], "reads are stable, not regenerating"

    old = t1.json()["token"]
    new = (await auth_client.post("/api/v1/schedule/feed-token/regenerate")).json()["token"]
    assert new != old

    dead = await auth_client.get(f"/api/v1/calendar/feed/{old}.ics")
    assert dead.status_code == 404, "the OLD URL must die on regenerate"
    alive = await auth_client.get(f"/api/v1/calendar/feed/{new}.ics")
    assert alive.status_code == 200

    unknown = await auth_client.get("/api/v1/calendar/feed/no-such-token.ics")
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_feed_is_wellformed_ics_without_money_or_cancelled_rows(auth_client):
    project_id = await _project(auth_client, "FED-ICS")
    me = await _me(auth_client)
    await _assign(
        auth_client, project_id, me["id"], start_h=48, note="Gate code; ring twice, then wait"
    )
    gone = await _assign(auth_client, project_id, me["id"], start_h=72)
    r = await auth_client.post(
        f"/api/v1/schedule/assignments/{gone['id']}/transition", json={"status": "cancelled"}
    )
    assert r.status_code == 200

    token = (await auth_client.get("/api/v1/schedule/feed-token")).json()["token"]
    feed = await auth_client.get(f"/api/v1/calendar/feed/{token}.ics")
    assert feed.status_code == 200
    assert feed.headers["content-type"].startswith("text/calendar")

    text = feed.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "\r\n" in text and "BEGIN:VEVENT" in text
    assert text.count("BEGIN:VEVENT") == 1, "the cancelled assignment must be absent"
    assert "FED-ICS" in text
    # RFC 5545 escaping: the semicolon and comma in the note arrive escaped.
    assert "Gate code\\; ring twice\\, then wait" in text
    assert "TENTATIVE" in text  # planned → tentative
    # No money on the wire — an event body carries schedule facts only.
    assert "€" not in text and "EUR" not in text

    # The authenticated one-off download matches the feed's content model.
    dl = await auth_client.get("/api/v1/schedule/export.ics")
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].startswith("attachment")
    assert "FED-ICS" in dl.text
