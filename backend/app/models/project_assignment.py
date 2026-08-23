"""Work-planning assignments: a person scheduled onto a project for a window.

Phase A of `docs/design/work-calendar.md`. INDUSTRY-NEUTRAL by owner rule:
an assignment is "this person works on this project then" — the same row for
a cleaning crew, a survey team or a consultancy.

Deliberate shape choices:

- `starts_at`/`ends_at` are timestamps and `all_day` is a flag, so one model
  covers both "Tuesday" and "Tuesday 09:00–12:00" (owner question 1 answered
  as BOTH, per the design doc's proposal).
- The assignee is referenced by user id + an email SNAPSHOT, not a composite
  FK: `users` carries no UNIQUE(org_id, id) and memberships — not `users.org_id`
  — are the authority on who belongs to the org (B1.5), so membership is
  validated in the service at write time and the snapshot keeps the row
  readable after a member leaves.
- Overlaps are NOT constrained: double-booking is real life; the service
  reports them as advisory warnings (design doc: advisory, never blocking).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

# Enforced transitions live in services/scheduling.py; the CHECK pins the set.
ASSIGNMENT_STATUSES = ("planned", "confirmed", "done", "cancelled")
_STATUS_CHECK = "status IN ('planned', 'confirmed', 'done', 'cancelled')"


class ProjectAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One person on one project for one window of time."""

    __tablename__ = "project_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "project_id"],
            ["projects.org_id", "projects.id"],
            name="fk_project_assignments_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_project_assignments_org_id_id"),
        CheckConstraint(_STATUS_CHECK, name="ck_project_assignments_status"),
        CheckConstraint("ends_at > starts_at", name="ck_project_assignments_window"),
        # The calendar's questions: "who is where this week" and "what is
        # planned on this project".
        Index("ix_project_assignments_org_assignee_start", "org_id", "assignee_user_id", "starts_at"),
        Index("ix_project_assignments_org_start", "org_id", "starts_at"),
        Index("ix_project_assignments_org_project", "org_id", "project_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(GUID(), nullable=False)

    assignee_user_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    assignee_email: Mapped[str] = mapped_column(String(255), nullable=False)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # WO-B reminders: per-assignment override of the default lead time (24h in
    # code), and the one-reminder idempotency stamp (the queue is at-least-once).
    remind_hours_before: Mapped[int | None] = mapped_column(nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
