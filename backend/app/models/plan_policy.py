from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlanPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The **system matrix** — per-PLAN usage limits, system-wide (not tenant
    scoped). Sysadmin-editable. A limit of 0 means unlimited.

    WO-47: this was originally keyed by the acting user's ROLE
    (`RolePolicy.role`), which was wrong for a hybrid seats+usage commercial
    model — two members of the same paying org could see different caps (or
    an admin could bypass the org's cap entirely) purely because of their
    internal permission role, which has nothing to do with what the ORG pays
    for. Usage is metered per-organization (`UsageCounter`/`invoices_this_month`
    already count org-wide, never per-user), so the cap must key off the same
    dimension: the org's subscription `plan` (`app.services.plans.PLANS`
    key — trial/starter/pro/enterprise). Every member of an org, regardless of
    role, now shares one org-level cap.

    Kept deliberately small and typed: the two numeric limits that gate the
    plan ladder today. New limits append as columns as the product grows.
    """

    __tablename__ = "plan_policies"
    __table_args__ = (UniqueConstraint("plan", name="uq_plan_policy_plan"),)

    plan: Mapped[str] = mapped_column(String(20), nullable=False)
    monthly_invoice_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monthly_upload_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
