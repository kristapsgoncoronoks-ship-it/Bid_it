from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
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
    # DB-001 (audit 2026-09-05): this was the ONLY Float money column in the
    # model layer — every other amount is Numeric(14, 2). It is the server-side
    # record a redirect-flow payment result is VERIFIED against, and a float
    # 29.99 is 29.989999999999998: an exact comparison with the provider's
    # "29.99" fails and any sum over the table drifts.
    amount_eur: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # initial | settled | failed — mirrors the provider's terminal states.
    state: Mapped[str] = mapped_column(String(20), default="initial", nullable=False)
