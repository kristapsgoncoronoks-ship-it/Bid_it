"""Schedule changes commit before optional external delivery is attempted."""

import json
import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.config import settings
from app.models.email_message import EmailMessage
from app.models.job import Job
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.project_assignment import ProjectAssignment
from app.models.user import User
from app.services import audit, jobs, mailer, schedule_email, scheduling


async def assignment_input(auth_client, **patch):
    project = await auth_client.post(
        "/api/v1/masters/projects", json={"code": "DEL-1", "name": "Synthetic work"}
    )
    assert project.status_code == 201, project.text
    me = (await auth_client.get("/api/v1/auth/me")).json()["user"]
    start = datetime.now(UTC) + timedelta(days=7)
    return {
        "project_id": project.json()["id"],
        "assignee_user_id": me["id"],
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
        **patch,
    }


@pytest.mark.asyncio
async def test_audit_refusal_rolls_back_assignment_mail_and_jobs(
    auth_client, db_session, monkeypatch
):
    body = await assignment_input(auth_client)
    sent = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: sent.append(args))
    original = audit.record

    async def refuse(db, action, *args, **kwargs):
        if action == "assignment.create":
            raise RuntimeError("Synthetic audit refusal")
        return await original(db, action, *args, **kwargs)

    monkeypatch.setattr(audit, "record", refuse)
    with pytest.raises(RuntimeError, match="Synthetic audit refusal"):
        await auth_client.post("/api/v1/schedule/assignments", json=body)
    assert sent == [], "External SMTP must not precede the assignment/audit commit"
    assert await db_session.scalar(select(func.count()).select_from(ProjectAssignment)) == 0
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "assignment")
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.kind.in_(
                    (schedule_email.KIND, "assignment.reminder", "assignment.client_notice")
                )
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_save_queues_email_without_external_io(auth_client, db_session, monkeypatch):
    body = await assignment_input(auth_client)
    sent = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: sent.append(args))
    response = await auth_client.post("/api/v1/schedule/assignments", json=body)
    assert response.status_code == 201, response.text
    assert sent == []
    messages = list(
        await db_session.scalars(select(EmailMessage).where(EmailMessage.kind == "assignment"))
    )
    assert len(messages) == 1
    assert messages[0].status == "queued"
    assert messages[0].sent_at is None
    assert (
        await db_session.scalar(
            select(Job).where(
                Job.kind == "schedule.email", Job.payload_json.contains(messages[0].id)
            )
        )
        is not None
    )


async def saved_assignment(auth_client, **patch):
    response = await auth_client.post(
        "/api/v1/schedule/assignments", json=await assignment_input(auth_client, **patch)
    )
    assert response.status_code == 201, response.text
    return response.json()["assignment"]


async def email_payload(db, message_id=None):
    query = select(Job).where(Job.kind == schedule_email.KIND)
    if message_id:
        query = query.where(Job.payload_json.contains(message_id))
    job = await db.scalar(query.order_by(Job.created_at.desc()).limit(1))
    assert job is not None
    return json.loads(job.payload_json)


@pytest.mark.asyncio
async def test_preferences_and_timezone_round_trip_without_any_email(auth_client, db_session):
    row = await saved_assignment(
        auth_client,
        time_zone="Europe/Riga",
        notify_assignee=False,
        reminders_enabled=False,
        client_notice_enabled=False,
    )
    assert row["time_zone"] == "Europe/Riga" and row["revision"] == 1
    assert row["notification_status"] == "off"
    assert row["project_code"] == "DEL-1" and row["project_name"] == "Synthetic work"
    assert row["client_notice_enabled"] is False
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "assignment")
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.kind.in_(
                    (schedule_email.KIND, scheduling.ASSIGNMENT_REMINDER, scheduling.CLIENT_NOTICE)
                )
            )
        )
        == 0
    )
    changed = await auth_client.patch(
        f"/api/v1/schedule/assignments/{row['id']}",
        json={
            "expected_revision": 1,
            "notify_assignee": True,
            "client_notice_enabled": None,
        },
    )
    assert changed.status_code == 200, changed.text
    current = changed.json()["assignment"]
    assert current["revision"] == 2 and current["client_notice_enabled"] is None
    assert current["notification_status"] == "blocked"
    assert current["reminders_enabled"] is False


@pytest.mark.asyncio
async def test_invalid_timezone_is_refused_without_an_assignment(auth_client, db_session):
    response = await auth_client.post(
        "/api/v1/schedule/assignments",
        json=await assignment_input(auth_client, time_zone="Unsupported/Synthetic"),
    )
    assert response.status_code == 400
    assert await db_session.scalar(select(func.count()).select_from(ProjectAssignment)) == 0


@pytest.mark.asyncio
async def test_stale_changes_and_moves_have_no_secondary_effects(auth_client, db_session):
    row = await saved_assignment(auth_client)
    changed = await auth_client.patch(
        f"/api/v1/schedule/assignments/{row['id']}",
        json={"expected_revision": 1, "note": "Current note"},
    )
    assert changed.status_code == 200 and changed.json()["assignment"]["revision"] == 2
    before = await db_session.scalar(
        select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "assignment")
    )
    for path, body in (
        (
            f"/api/v1/schedule/assignments/{row['id']}",
            {"expected_revision": 1, "note": "Stale note"},
        ),
        (
            f"/api/v1/schedule/assignments/{row['id']}/transition",
            {"expected_revision": 1, "status": "done"},
        ),
    ):
        response = await (
            auth_client.post(path, json=body)
            if path.endswith("transition")
            else auth_client.patch(path, json=body)
        )
        assert response.status_code == 409, response.text
    current = await db_session.get(ProjectAssignment, row["id"])
    assert current.note == "Current note" and current.status == "planned" and current.revision == 2
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "assignment")
        )
        == before
    )


@pytest.mark.asyncio
async def test_noop_save_does_not_create_another_notification(auth_client, db_session):
    row = await saved_assignment(auth_client)
    response = await auth_client.patch(
        f"/api/v1/schedule/assignments/{row['id']}",
        json={"expected_revision": 1, "notify_assignee": True},
    )
    assert response.status_code == 200 and response.json()["assignment"]["revision"] == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "assignment")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_worker_acceptance_is_replay_safe_and_only_then_marks_sent(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: sent.append(args))
    row = await saved_assignment(auth_client)
    payload = await email_payload(db_session)
    message = await db_session.get(EmailMessage, payload["message_id"])
    assert message.status == "queued" and message.sent_at is None and sent == []
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {"status": "sent"}
    assert message.sent_at is not None and len(sent) == 1
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {"status": "sent"}
    assert len(sent) == 1
    assert (
        await auth_client.get(f"/api/v1/schedule/assignments/{row['id']}/notifications")
    ).json()["items"][0]["status"] == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unknown", "failed"])
async def test_smtp_outcomes_control_explicit_retry(auth_client, db_session, monkeypatch, outcome):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    calls = []

    def reject(*args):
        calls.append(args)
        if outcome == "failed":
            raise smtplib.SMTPRecipientsRefused({"synthetic@example.test": (550, b"No recipient")})
        raise OSError("Synthetic lost SMTP response")

    monkeypatch.setattr(mailer, "_smtp_send", reject)
    row = await saved_assignment(auth_client)
    payload = await email_payload(db_session)
    message = await db_session.get(EmailMessage, payload["message_id"])
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {"status": outcome}
    assert message.sent_at is None
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {"status": outcome}
    assert len(calls) == 1
    retry = await auth_client.post(
        f"/api/v1/schedule/assignments/{row['id']}/notifications/{message.id}/retry"
    )
    assert retry.status_code == (200 if outcome == "failed" else 409), retry.text
    if outcome == "failed":
        assert retry.json()["status"] == "queued"
        again = await auth_client.post(
            f"/api/v1/schedule/assignments/{row['id']}/notifications/{message.id}/retry"
        )
        assert again.status_code == 200 and again.json()["status"] == "queued"
        count = await db_session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == schedule_email.KIND)
        )
        assert count == 2, "a repeated queued retry must not enqueue another job"


@pytest.mark.asyncio
async def test_reclaimed_sending_state_becomes_unknown_without_resend(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    await saved_assignment(auth_client)
    payload = await email_payload(db_session)
    message = await db_session.get(EmailMessage, payload["message_id"])
    message.status = "sending"
    await db_session.commit()
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: sent.append(args))
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {
        "status": "unknown"
    }
    assert message.sent_at is None and sent == []


@pytest.mark.asyncio
async def test_offboarded_recipient_and_tampered_job_never_send(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    row = await saved_assignment(auth_client)
    payload = await email_payload(db_session)
    message = await db_session.get(EmailMessage, payload["message_id"])
    sent = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: sent.append(args))
    with pytest.raises(jobs.PermanentJobError, match="queued identity"):
        await schedule_email.deliver(db_session, message.org_id, {**payload, "revision": 99})
    member = await db_session.scalar(
        select(Membership).where(
            Membership.org_id == message.org_id, Membership.user_id == row["assignee_user_id"]
        )
    )
    member.status = "suspended"
    await db_session.commit()
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {
        "status": "blocked",
        "reason": "stale",
    }
    assert sent == [] and message.sent_at is None


@pytest.mark.asyncio
async def test_disabled_reminder_and_client_notice_do_not_arm_or_claim_sent(
    auth_client, db_session
):
    await auth_client.put("/api/v1/settings/schedule", json={"client_notice_hours": 48})
    row = await saved_assignment(
        auth_client, notify_assignee=False, reminders_enabled=False, client_notice_enabled=False
    )
    stored = await db_session.get(ProjectAssignment, row["id"])
    assert await scheduling.send_due_reminder(db_session, stored.org_id, stored.id) == {
        "sent": False,
        "reason": "stale",
    }
    assert await scheduling.send_due_client_notice(db_session, stored.org_id, stored.id) == {
        "sent": False,
        "reason": "disabled",
    }
    assert stored.reminder_sent_at is None and stored.client_notice_sent_at is None


@pytest.mark.asyncio
async def test_unconfigured_reminder_is_blocked_without_a_sent_stamp(auth_client, db_session):
    row = await saved_assignment(auth_client, notify_assignee=False)
    stored = await db_session.get(ProjectAssignment, row["id"])
    stored.remind_hours_before = 336
    await db_session.commit()
    outcome = await scheduling.send_due_reminder(db_session, stored.org_id, stored.id)
    assert outcome["sent"] is False and outcome["status"] == "blocked"
    assert stored.reminder_sent_at is None
    repeated = await scheduling.send_due_reminder(db_session, stored.org_id, stored.id)
    assert repeated["message_id"] == outcome["message_id"]


async def invited_employee(auth_client, email):
    invitation = await auth_client.post(
        "/api/v1/team/invites", json={"email": email, "role": "user"}
    )
    assert invitation.status_code == 201, invitation.text
    accepted = await auth_client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invitation.json()["token"], "name": "Crew", "password": "supersecret"},
    )
    assert accepted.status_code == 201, accepted.text
    return accepted.json()["user"], {
        "Authorization": f"Bearer {accepted.json()['token']['access_token']}"
    }


@pytest.mark.asyncio
async def test_notice_metadata_and_retry_are_scoped_to_owner_or_current_assignee(
    auth_client, db_session
):
    employee, employee_headers = await invited_employee(auth_client, "crew@acme.io")
    row = await saved_assignment(auth_client)
    message = await db_session.scalar(select(EmailMessage).where(EmailMessage.kind == "assignment"))
    base = f"/api/v1/schedule/assignments/{row['id']}/notifications"
    assert (await auth_client.get(base, headers=employee_headers)).status_code == 404
    assert (
        await auth_client.post(f"{base}/{message.id}/retry", headers=employee_headers)
    ).status_code == 404
    # No relay is a visible and recoverable configuration problem, never success.
    refused = await auth_client.post(f"{base}/{message.id}/retry")
    assert refused.status_code == 409 and "not configured" in refused.text
    details = await auth_client.get(base)
    assert details.status_code == 200
    assert details.json()["smtp_configured"] is False
    assert details.json()["items"][0]["can_retry"] is False
    assert not {"to_email", "body", "from_email", "subject"} & details.json()["items"][0].keys()
    changed = await auth_client.patch(
        f"/api/v1/schedule/assignments/{row['id']}",
        json={"expected_revision": 1, "assignee_user_id": employee["id"]},
    )
    assert changed.status_code == 200
    assert (await auth_client.get(base, headers=employee_headers)).status_code == 200
    outsider = await auth_client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Unrelated",
            "name": "Outsider",
            "email": "outsider@synthetic.example",
            "password": "supersecret",
        },
    )
    assert outsider.status_code == 201
    other_headers = {"Authorization": f"Bearer {outsider.json()['token']['access_token']}"}
    assert (await auth_client.get(base, headers=other_headers)).status_code == 404
    assert (
        await auth_client.post(f"{base}/{message.id}/retry", headers=other_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_reassignment_freezes_old_notice_and_obsolete_intent_cannot_send(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    employee, _ = await invited_employee(auth_client, "new-crew@acme.io")
    row = await saved_assignment(auth_client, note="Old instruction", time_zone="Europe/Riga")
    old_payload = await email_payload(db_session)
    update = await auth_client.patch(
        f"/api/v1/schedule/assignments/{row['id']}",
        json={
            "expected_revision": 1,
            "assignee_user_id": employee["id"],
            "note": "New instruction",
            "starts_at": (datetime.fromisoformat(row["starts_at"]) + timedelta(days=1)).isoformat(),
            "ends_at": (datetime.fromisoformat(row["ends_at"]) + timedelta(days=1)).isoformat(),
        },
    )
    assert update.status_code == 200, update.text
    messages = list(
        await db_session.scalars(select(EmailMessage).where(EmailMessage.kind == "assignment"))
    )
    cancellation = next(m for m in messages if ":reassigned:" in m.command_key)
    changed = next(m for m in messages if ":changed:" in m.command_key)
    assert "Old instruction" in cancellation.body and "New instruction" not in cancellation.body
    assert "New instruction" in changed.body and "Europe/Riga" in changed.body
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    assert await schedule_email.deliver(db_session, cancellation.org_id, old_payload) == {
        "status": "blocked",
        "reason": "stale",
    }
    assert calls == []
    for message in (cancellation, changed):
        payload = await email_payload(db_session, message.id)
        assert await schedule_email.deliver(db_session, message.org_id, payload) == {
            "status": "sent"
        }
    assert len(calls) == 2
    assert {call[1] for call in calls} == {"owner@acme.io", "new-crew@acme.io"}


@pytest.mark.asyncio
async def test_cancelled_notice_sends_once_but_opt_out_sends_none(
    auth_client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    row = await saved_assignment(auth_client)
    response = await auth_client.post(
        f"/api/v1/schedule/assignments/{row['id']}/transition",
        json={"status": "cancelled", "expected_revision": 1},
    )
    assert response.status_code == 200 and response.json()["revision"] == 2
    cancellation = await db_session.scalar(
        select(EmailMessage).where(EmailMessage.command_key.contains(":cancelled:"))
    )
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    payload = await email_payload(db_session, cancellation.id)
    assert await schedule_email.deliver(db_session, cancellation.org_id, payload) == {
        "status": "sent"
    }
    assert len(calls) == 1
    # The original create job became obsolete after cancellation.
    original = await db_session.scalar(
        select(EmailMessage).where(EmailMessage.command_key.contains(":assigned:"))
    )
    assert await schedule_email.deliver(
        db_session, original.org_id, await email_payload(db_session, original.id)
    ) == {"status": "blocked", "reason": "stale"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_silent_audit_failure_cannot_commit_assignment_intent(
    auth_client, db_session, monkeypatch
):
    body = await assignment_input(auth_client)
    original = audit.record

    async def refuse(db, action, *args, **kwargs):
        if action == "assignment.create":
            return None
        return await original(db, action, *args, **kwargs)

    monkeypatch.setattr(audit, "record", refuse)
    with pytest.raises(RuntimeError, match="audit could not be recorded"):
        await auth_client.post("/api/v1/schedule/assignments", json=body)
    assert await db_session.scalar(select(func.count()).select_from(ProjectAssignment)) == 0
    assert (
        await db_session.scalar(
            select(func.count()).select_from(EmailMessage).where(EmailMessage.kind == "assignment")
        )
        == 0
    )


def test_notice_window_describes_midnight_and_all_day_ranges_in_its_zone():
    row = ProjectAssignment(
        starts_at=datetime(2026, 10, 21, 19, tzinfo=UTC),
        ends_at=datetime(2026, 10, 21, 23, tzinfo=UTC),
        time_zone="Europe/Riga",
        all_day=False,
    )
    assert scheduling._window_text(row) == "2026-10-21 22:00–2026-10-22 02:00 Europe/Riga"
    row.all_day = True
    row.starts_at = datetime(2026, 10, 20, 21, tzinfo=UTC)
    row.ends_at = datetime(2026, 10, 22, 21, tzinfo=UTC)
    assert scheduling._window_text(row) == "2026-10-21–2026-10-22 (all day, Europe/Riga)"


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["disabled-user", "changed-email", "paused-org"])
async def test_worker_refreshes_cached_access_and_recipient_before_smtp(
    auth_client, db_session, monkeypatch, revocation
):
    monkeypatch.setattr(settings, "smtp_host", "smtp.synthetic.invalid")
    row = await saved_assignment(auth_client)
    payload = await email_payload(db_session)
    message = await db_session.get(EmailMessage, payload["message_id"])
    user = await db_session.get(User, row["assignee_user_id"])
    org = await db_session.get(Organization, message.org_id)
    assert user.is_active and org.status == "active"
    if revocation == "disabled-user":
        await db_session.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"), {"id": user.id}
        )
    elif revocation == "changed-email":
        await db_session.execute(
            text("UPDATE users SET email = :email WHERE id = :id"),
            {"email": "changed@synthetic.example", "id": user.id},
        )
    else:
        await db_session.execute(
            text("UPDATE organizations SET status = 'suspended' WHERE id = :id"), {"id": org.id}
        )
    await db_session.commit()
    # Raw/concurrent writes leave this session's old ORM objects unchanged.
    assert user.is_active and user.email != "changed@synthetic.example" and org.status == "active"
    calls = []
    monkeypatch.setattr(mailer, "_smtp_send", lambda *args: calls.append(args))
    assert await schedule_email.deliver(db_session, message.org_id, payload) == {
        "status": "blocked",
        "reason": "stale",
    }
    assert calls == [] and message.sent_at is None
