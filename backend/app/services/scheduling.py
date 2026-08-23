"""Work-planning assignments: who works on which project, when.

Phase A of `docs/design/work-calendar.md` (WO-A). The rules this module owns:

- **Membership is the assignee check** (B1.5): a person can be scheduled only
  if they hold an ACTIVE membership in the org — `users.org_id` is just the
  active-org pointer and proves nothing. The email snapshot is taken at write
  time so the row stays readable after someone leaves.
- **Transitions are enforced**: planned ⇄ confirmed → done | cancelled;
  done and cancelled are terminal. The assignee may move their OWN assignment
  forward (confirm / done) without planning rights — marking your work done
  is not planning other people's.
- **Overlaps are advisory** (owner design): creating or moving an assignment
  reports which other assignments of the same person overlap the window, and
  proceeds. Real life double-books; the screen warns, the server never blocks.
- Services never commit — routes commit mutation + audit together (§audit).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar_token import CalendarFeedToken
from app.models.costing import Project
from app.models.membership import Membership
from app.models.project_assignment import ProjectAssignment
from app.models.user import User
from app.services import jobs, mailer

#: Default reminder lead time when the assignment carries no override (WO-B;
#: the per-ORG default becomes configurable with the client-notice settings
#: of phase B3 — one settings surface for both, not two).
DEFAULT_REMIND_HOURS = 24

ASSIGNMENT_REMINDER = "assignment.reminder"


def _as_utc(dt: datetime) -> datetime:
    """Normalize to aware-UTC on the way IN (SQLite hands back naive-UTC)."""
    return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SchedulingError(Exception):
    """Base for scheduling failures the route maps to HTTP."""


class NotFoundError(SchedulingError):
    """Unknown (or other-tenant — indistinguishable, §4.4) id."""


class InvalidError(SchedulingError):
    """Bad input: unknown assignee, bad window, forbidden transition."""


_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"confirmed", "done", "cancelled"},
    "confirmed": {"planned", "done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}

# What the assignee may do to their OWN assignment without planning rights:
# accept it and finish it. Cancelling or un-confirming is the planner's call.
_SELF_TRANSITIONS = {"confirmed", "done"}


async def _project_or_404(db: AsyncSession, org_id: str, project_id: str) -> Project:
    project = await db.scalar(
        select(Project).where(Project.org_id == org_id, Project.id == project_id)
    )
    if project is None:
        raise NotFoundError("project not found")
    return project


async def _member_or_invalid(db: AsyncSession, org_id: str, user_id: str) -> User:
    """The assignee must be an ACTIVE member of this org (memberships are the
    authority, not users.org_id)."""
    member = await db.scalar(
        select(Membership).where(
            Membership.org_id == org_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    if member is None:
        raise InvalidError("assignee is not an active member of this workspace")
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:  # membership without a user row would be corruption
        raise InvalidError("assignee is not an active member of this workspace")
    return user


async def overlaps_for(
    db: AsyncSession,
    org_id: str,
    assignee_user_id: str,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: str | None = None,
) -> list[ProjectAssignment]:
    """Other non-cancelled assignments of the same person intersecting the
    window — the ADVISORY warning list, never a constraint."""
    q = (
        select(ProjectAssignment)
        .where(
            ProjectAssignment.org_id == org_id,
            ProjectAssignment.assignee_user_id == assignee_user_id,
            ProjectAssignment.status != "cancelled",
            ProjectAssignment.starts_at < ends_at,
            ProjectAssignment.ends_at > starts_at,
        )
        .order_by(ProjectAssignment.starts_at)
    )
    if exclude_id is not None:
        q = q.where(ProjectAssignment.id != exclude_id)
    return list((await db.scalars(q)).all())


def _validate_window(starts_at: datetime, ends_at: datetime) -> None:
    if ends_at <= starts_at:
        raise InvalidError("the assignment must end after it starts")


async def create(
    db: AsyncSession,
    org_id: str,
    *,
    project_id: str,
    assignee_user_id: str,
    starts_at: datetime,
    ends_at: datetime,
    all_day: bool,
    note: str | None,
    created_by: str,
    remind_hours_before: int | None = None,
) -> tuple[ProjectAssignment, list[ProjectAssignment]]:
    """Create an assignment; returns (row, advisory overlaps). Sends the
    assignee their notice and arms the reminder in the same transaction."""
    starts_at, ends_at = _as_utc(starts_at), _as_utc(ends_at)
    _validate_window(starts_at, ends_at)
    await _project_or_404(db, org_id, project_id)
    assignee = await _member_or_invalid(db, org_id, assignee_user_id)

    row = ProjectAssignment(
        org_id=org_id,
        project_id=project_id,
        assignee_user_id=assignee_user_id,
        assignee_email=assignee.email,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        status="planned",
        note=note,
        created_by=created_by,
        remind_hours_before=remind_hours_before,
    )
    warnings = await overlaps_for(db, org_id, assignee_user_id, starts_at, ends_at)
    db.add(row)
    await db.flush()
    await notify_assigned(db, row)
    await arm_reminder(db, row)
    return row, warnings


async def get(db: AsyncSession, org_id: str, assignment_id: str) -> ProjectAssignment:
    row = await db.scalar(
        select(ProjectAssignment).where(
            ProjectAssignment.org_id == org_id, ProjectAssignment.id == assignment_id
        )
    )
    if row is None:
        raise NotFoundError("assignment not found")
    return row


async def update(
    db: AsyncSession,
    org_id: str,
    assignment_id: str,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    all_day: bool | None = None,
    note: str | None = None,
    clear_note: bool = False,
    assignee_user_id: str | None = None,
    remind_hours_before: int | None = None,
    set_remind: bool = False,
) -> tuple[ProjectAssignment, list[ProjectAssignment]]:
    """Edit window/note/assignee on a non-terminal assignment; returns
    (row, advisory overlaps for the resulting window)."""
    row = await get(db, org_id, assignment_id)
    if row.status in ("done", "cancelled"):
        raise InvalidError(f"a {row.status} assignment is history — plan a new one instead")

    if assignee_user_id is not None and assignee_user_id != row.assignee_user_id:
        assignee = await _member_or_invalid(db, org_id, assignee_user_id)
        row.assignee_user_id = assignee_user_id
        row.assignee_email = assignee.email
    if starts_at is not None:
        row.starts_at = _as_utc(starts_at)
    if ends_at is not None:
        row.ends_at = _as_utc(ends_at)
    if all_day is not None:
        row.all_day = all_day
    if clear_note:
        row.note = None
    elif note is not None:
        row.note = note
    if set_remind:
        row.remind_hours_before = remind_hours_before

    _validate_window(_as_utc(row.starts_at), _as_utc(row.ends_at))
    warnings = await overlaps_for(
        db, org_id, row.assignee_user_id, row.starts_at, row.ends_at, exclude_id=row.id
    )
    await db.flush()
    await notify_changed(db, row)
    await arm_reminder(db, row)
    return row, warnings


async def transition(
    db: AsyncSession,
    org_id: str,
    assignment_id: str,
    new_status: str,
    *,
    actor_user_id: str,
    actor_may_plan: bool,
) -> ProjectAssignment:
    """Move an assignment through its lifecycle. Planners may make any legal
    move; the assignee may confirm/finish their OWN assignment."""
    row = await get(db, org_id, assignment_id)
    if new_status not in _TRANSITIONS:
        raise InvalidError(f"unknown status {new_status!r}")
    if new_status not in _TRANSITIONS[row.status]:
        raise InvalidError(f"cannot move a {row.status} assignment to {new_status}")
    if not actor_may_plan:
        own = row.assignee_user_id == actor_user_id
        if not (own and new_status in _SELF_TRANSITIONS):
            raise NotFoundError("assignment not found")  # opaque, §4.4
    row.status = new_status
    await db.flush()
    if new_status == "cancelled":
        await notify_cancelled(db, row)
    return row


# --------------------------------------------------------------------------- #
# WO-B: notifications + reminders. Notices go straight through the mailer
# (recorded rows; delivered when SMTP is configured) and commit atomically
# with the mutation. The exact-time reminder rides the durable queue:
# enqueued at (starts_at − lead), idempotent per (assignment, due-moment),
# and the handler re-checks CURRENT state — a reschedule simply enqueues a
# new job and the stale one no-ops or re-arms itself.
# --------------------------------------------------------------------------- #


def _window_text(row: ProjectAssignment) -> str:
    if row.all_day:
        return f"{_as_utc(row.starts_at).date().isoformat()} (all day)"
    s, e = _as_utc(row.starts_at), _as_utc(row.ends_at)
    return f"{s.date().isoformat()} {s.strftime('%H:%M')}–{e.strftime('%H:%M')} UTC"


async def _notify(
    db: AsyncSession, row: ProjectAssignment, *, kind: str, subject: str, lead: str
) -> None:
    project = await db.scalar(select(Project).where(Project.id == row.project_id))
    label = f"{project.code} · {project.name}" if project else "an assignment"
    body = (
        f"{lead}\n\n"
        f"Project:  {label}\n"
        f"When:     {_window_text(row)}\n"
        + (f"Note:     {row.note}\n" if row.note else "")
        + "\nThis is an automatic notice from your workspace's schedule."
    )
    await mailer.send(
        db, row.org_id, kind=kind, to_email=row.assignee_email, subject=subject, body=body
    )


async def notify_assigned(db: AsyncSession, row: ProjectAssignment) -> None:
    await _notify(
        db,
        row,
        kind="assignment",
        subject="New work assignment",
        lead="You have been scheduled for work.",
    )


async def notify_changed(db: AsyncSession, row: ProjectAssignment) -> None:
    await _notify(
        db,
        row,
        kind="assignment",
        subject="Your work assignment changed",
        lead="One of your assignments was updated — check the new details.",
    )


async def notify_cancelled(db: AsyncSession, row: ProjectAssignment) -> None:
    await _notify(
        db,
        row,
        kind="assignment",
        subject="Work assignment cancelled",
        lead="This assignment was cancelled — you are no longer expected.",
    )


def reminder_due_at(row: ProjectAssignment) -> datetime:
    lead = row.remind_hours_before if row.remind_hours_before is not None else DEFAULT_REMIND_HOURS
    return _as_utc(row.starts_at) - timedelta(hours=lead)


async def arm_reminder(db: AsyncSession, row: ProjectAssignment) -> None:
    """Enqueue the reminder job for this assignment's CURRENT window. Keyed by
    (assignment, due-moment) so a reschedule arms a fresh job; already-past due
    moments are skipped (assigning tomorrow's work tonight sends no reminder —
    the assignment notice itself just arrived)."""
    due = reminder_due_at(row)
    if due <= datetime.now(UTC):
        return
    await jobs.enqueue(
        db,
        ASSIGNMENT_REMINDER,
        {"assignment_id": row.id},
        org_id=row.org_id,
        run_after=due,
        idempotency_key=f"{ASSIGNMENT_REMINDER}:{row.id}:{due.isoformat()}",
    )


async def send_due_reminder(db: AsyncSession, org_id: str, assignment_id: str) -> dict:
    """The queue handler's work. Re-checks CURRENT state: the job may be stale
    (rescheduled later → re-arm; cancelled/done/deleted/already-reminded →
    no-op). One reminder per assignment, ever — reminder_sent_at is the stamp."""
    row = await db.scalar(
        select(ProjectAssignment).where(
            ProjectAssignment.org_id == org_id, ProjectAssignment.id == assignment_id
        )
    )
    if row is None or row.status in ("cancelled", "done") or row.reminder_sent_at is not None:
        return {"sent": False, "reason": "stale"}
    now = datetime.now(UTC)
    due = reminder_due_at(row)
    if now < due - timedelta(seconds=60):
        await arm_reminder(db, row)  # moved later since this job was armed
        return {"sent": False, "reason": "rearmed"}
    await _notify(
        db,
        row,
        kind="assignment_reminder",
        subject="Reminder: upcoming work assignment",
        lead="A scheduled assignment is coming up.",
    )
    row.reminder_sent_at = now
    await db.flush()
    return {"sent": True}


# --------------------------------------------------------------------------- #
# WO-B2: the personal calendar feed. The token is a per-(org, user) secret
# capability; regenerating replaces it (old URL dies). The public feed route
# resolves it UNSCOPED — the email-intake pattern — then queries under the
# resolved tenant.
# --------------------------------------------------------------------------- #


async def get_or_create_feed_token(db: AsyncSession, org_id: str, user_id: str) -> str:
    row = await db.scalar(
        select(CalendarFeedToken).where(
            CalendarFeedToken.org_id == org_id, CalendarFeedToken.user_id == user_id
        )
    )
    if row is None:
        row = CalendarFeedToken(
            org_id=org_id, user_id=user_id, token=secrets.token_urlsafe(32)
        )
        db.add(row)
        await db.flush()
    return row.token


async def regenerate_feed_token(db: AsyncSession, org_id: str, user_id: str) -> str:
    await get_or_create_feed_token(db, org_id, user_id)
    row = await db.scalar(
        select(CalendarFeedToken).where(
            CalendarFeedToken.org_id == org_id, CalendarFeedToken.user_id == user_id
        )
    )
    assert row is not None
    row.token = secrets.token_urlsafe(32)
    await db.flush()
    return row.token


async def resolve_feed_token(db: AsyncSession, token: str) -> tuple[str, str] | None:
    """(org_id, user_id) for a live token — UNSCOPED, for the public route."""
    row = await db.scalar(select(CalendarFeedToken).where(CalendarFeedToken.token == token))
    return (row.org_id, row.user_id) if row else None


async def feed_rows(
    db: AsyncSession, org_id: str, user_id: str, *, now: datetime | None = None
) -> tuple[list[ProjectAssignment], dict[str, str]]:
    """The user's own non-cancelled assignments, recent past → next year,
    plus the project-label map the ICS summary needs."""
    now = now or datetime.now(UTC)
    rows = [
        a
        for a in await list_window(
            db,
            org_id,
            start=now - timedelta(days=30),
            end=now + timedelta(days=365),
            assignee_user_id=user_id,
        )
        if a.status != "cancelled"
    ]
    names: dict[str, str] = {}
    if rows:
        projects = await db.scalars(
            select(Project).where(Project.id.in_({a.project_id for a in rows}))
        )
        names = {p.id: f"{p.code} · {p.name}" for p in projects}
    return rows, names


async def list_window(
    db: AsyncSession,
    org_id: str,
    *,
    start: datetime,
    end: datetime,
    assignee_user_id: str | None = None,
    project_id: str | None = None,
) -> list[ProjectAssignment]:
    """Assignments intersecting [start, end) — the calendar's read."""
    q = (
        select(ProjectAssignment)
        .where(
            ProjectAssignment.org_id == org_id,
            ProjectAssignment.starts_at < end,
            ProjectAssignment.ends_at > start,
        )
        .order_by(ProjectAssignment.starts_at)
    )
    if assignee_user_id is not None:
        q = q.where(ProjectAssignment.assignee_user_id == assignee_user_id)
    if project_id is not None:
        q = q.where(ProjectAssignment.project_id == project_id)
    return list((await db.scalars(q)).all())
