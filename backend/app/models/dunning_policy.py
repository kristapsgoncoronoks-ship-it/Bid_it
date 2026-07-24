"""Configurable dunning ladder (Phase 16).

A per-tenant escalation ladder for overdue-invoice reminders: each row is one
LEVEL that fires once the invoice is `days_overdue` past due, with an escalating
`tone`. The ABSENCE of any row for an org means "use the built-in default ladder"
(see services/dunning.DEFAULT_LADDER) — so the ladder is opt-in to customise and
safe by default (mirrors the RetentionPolicy pattern). `(org_id, level)` is unique.
An invoice fires each level at most once, tracked by `issued_invoices.dunning_level`.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class DunningPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "dunning_policies"
    __table_args__ = (
        UniqueConstraint("org_id", "level", name="uq_dunning_org_level"),
        UniqueConstraint("org_id", "id", name="uq_dunning_org_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 1-based escalation step; higher = later/firmer.
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    # Days past the due date at which this level becomes eligible.
    days_overdue: Mapped[int] = mapped_column(Integer, nullable=False)
    # Message tone: reminder | firm | final (see services/mailer).
    tone: Mapped[str] = mapped_column(String(16), default="reminder", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
