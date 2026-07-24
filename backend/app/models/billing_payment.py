from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class BillingPayment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A redirect-flow payment attempt (EveryPay) awaiting confirmation.

    Stripe drives plan/status through subscription webhooks and needs no such
    record. A hosted-card gateway instead redirects the buyer to a payment page
    and reports the result on return / via a server callback — so we persist the
    provider reference ↔ (tenant, plan, amount) here to resolve and verify that
    result server-side and apply the plan idempotently.

    Tenant-scoped (org_id). The confirm path runs unscoped (the callback has no
    authenticated user) and looks the row up by the provider `reference`.
    """

    __tablename__ = "billing_payments"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "everypay"
    reference: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    order_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_key: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_eur: Mapped[float] = mapped_column(Float, nullable=False)
    # initial | settled | failed — mirrors the provider's terminal states.
    state: Mapped[str] = mapped_column(String(20), default="initial", nullable=False)
