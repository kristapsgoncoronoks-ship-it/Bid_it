"""Next-actions support tables (WO-C, docs/design/tasks-module-research.md).

The module's core rule: action items are DERIVED views over real records —
there is no freeform task table, nothing to keep in sync, nothing to rot.
Only two things need rows of their own:

- `org_deadlines` — the workspace's recurring obligations ("prepare the VAT
  report") as templates: a due day, a cadence, a lead window. Occurrences
  are computed on read; completing one stamps `last_done_period` (the
  confirm-style materialization the research recommends over silent
  auto-creation). Nothing accumulates.
- `action_dismissals` — "stop showing me this one": a dismissed derived item
  (an offer nudge, a chase row) stays dismissed, per (kind, ref). Audited at
  the route. A dismissal of something that later RESOLVES costs nothing —
  the item would have vanished anyway; that is the self-completing contract.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

DEADLINE_CADENCES = ("monthly", "quarterly", "yearly")
_CADENCE_CHECK = "cadence IN ('monthly', 'quarterly', 'yearly')"


class OrgDeadline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A recurring obligation template — occurrences are computed, never stored."""

    __tablename__ = "org_deadlines"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_org_deadlines_org_id_id"),
        CheckConstraint(_CADENCE_CHECK, name="ck_org_deadlines_cadence"),
        CheckConstraint("due_day >= 1 AND due_day <= 28", name="ck_org_deadlines_due_day"),
        Index("ix_org_deadlines_org_id", "org_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cadence: Mapped[str] = mapped_column(String(12), default="monthly", nullable=False)
    # Day-of-month the obligation is due (1–28 — no month-length surprises).
    # For quarterly/yearly cadences: the due day in the period's LAST month.
    due_day: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    lead_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    # The most recent period marked done, e.g. "2026-08" / "2026-Q3" / "2026".
    last_done_period: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class ActionDismissal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A standing 'not this one' for a single derived action item."""

    __tablename__ = "action_dismissals"
    __table_args__ = (
        UniqueConstraint("org_id", "kind", "ref_id", name="uq_action_dismissals_org_kind_ref"),
        Index("ix_action_dismissals_org_id", "org_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dismissed_by: Mapped[str] = mapped_column(String(255), nullable=False)
