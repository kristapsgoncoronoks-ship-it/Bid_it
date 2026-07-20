from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An append-only, hash-chained audit record — the tamper-evident trail for
    internal audit. One row per action (who did what, to which target, when).

    Integrity: each event hashes the previous event's hash into its own, so a
    deleted or edited row breaks the chain (detectable by `verify_chain`). `seq`
    is a per-tenant monotonic ordinal; `(org_id, seq)` is unique so a concurrent
    append conflicts loudly instead of forking the chain silently.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("org_id", "seq", name="uq_audit_org_seq"),
        Index("ix_audit_org_seq", "org_id", "seq"),
        Index("ix_audit_org_action", "org_id", "action"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # None = system
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    action: Mapped[str] = mapped_column(String(64), nullable=False)           # e.g. invoice.create
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)             # small JSON, no secrets

    at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)  # epoch ms, exact round-trip (hashed)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
