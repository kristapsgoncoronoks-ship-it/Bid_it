from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.vendor import Vendor


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. Everything else hangs off exactly one organization."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Data-validation options — OFF by default, turned on by the user's choice.
    # AI = automated rule-based checks (LLM-pluggable); human = a review gate.
    ai_validation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    human_validation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Commercial tenancy: subscription plan + lifecycle status.
    plan: Mapped[str] = mapped_column(String(20), default="trial", nullable=False)
    # The PAID archive-retention extension (owner decision 2026-08-16): NULL =
    # the included tier (archive.INCLUDED_RETENTION_YEARS). Granted by a
    # platform operator after the commercial step happens out-of-band — no
    # billing provider is wired to this yet, deliberately, same pattern as
    # every other not-yet-monetised seam: the entitlement exists, nothing
    # charges anyone.
    archive_retention_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Offer numbering is SET BY THE CLIENT (owner decision 2026-08-16): the org
    # picks its prefix; the platform enforces only per-org uniqueness. NULL =
    # the default "OFF-".
    offer_prefix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Final invoicing is LINKED to acceptance by default, gated only when the
    # org opts in (owner decision 2026-08-16): with the toggle on, the final-
    # invoice composer refuses until acceptance is recorded. A workflow aid,
    # not a hard wall — any invoice can still be issued through the normal
    # form; the gate guards the guided path.
    final_invoice_requires_acceptance: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Schedule notices (WO-E, work-calendar §B3) — ONE settings surface for both
    # audiences, as the WO-B comment promised. NULL assignment_remind_hours =
    # the code default (24h before, to the assigned employee). NULL
    # client_notice_hours = client arrival notices OFF — emailing the org's
    # CUSTOMERS automatically is outward-facing, so it is opt-in, and the UI
    # offers 24/48/72 (a per-assignment override can still enable one-off).
    assignment_remind_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_notice_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )  # active|suspended|canceled

    # Data residency (ADR-0022): the region this tenant's data is pinned to.
    # Assigned at registration; a regional deployment refuses to serve a tenant
    # pinned elsewhere when enforcement is on.
    region: Mapped[str] = mapped_column(String(20), default="eu", nullable=False)

    # Stripe linkage (ADR-0013). Set on first checkout; the signed webhook is the
    # authority for plan/status thereafter. Null until a tenant subscribes.
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # EveryPay linkage (ADR-0013). The card token captured on the initial (CIT)
    # payment, reused for merchant-initiated (MIT) recurring charges; the next
    # scheduled charge date drives the renewal job. Null unless subscribed via
    # EveryPay.
    everypay_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    everypay_next_charge: Mapped[date | None] = mapped_column(Date, nullable=True)

    users: Mapped[list[User]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    vendors: Mapped[list[Vendor]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
