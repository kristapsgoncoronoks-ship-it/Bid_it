"""Dogfood subscription billing (H1.6 / ADR-0013, WO-48): invoice InvoiceIQ's
own paying tenants through the platform's OWN accounts-receivable module
instead of a payment provider.

This is the fallback the M2 exit criteria name explicitly: *"we can invoice
our own customers through our own AR module (dogfooding) if Stripe
credentials slip. Revenue is not blocked on a provider."* It reuses the SAME
building blocks every tenant's own AR uses — `issuer.py` (numbering),
`issued_service.build_invoice` (the one true construction path that assigns a
number and snapshots the seller), the existing PDF/XML/send/dunning surface —
by treating ONE designated organization (`settings.platform_org_id`) as the
operator's own tenant and every OTHER paid-plan organization as a "buyer" on
an invoice issued from it.

No new billing system, no payment collection: this module only GENERATES the
invoice. Delivery (PDF/email) and reminders reuse the pre-existing
`/issued/{id}/send` route and the pre-existing daily `dunning.run` job —
both already run for the platform org because it is just another row in
`organizations` (see `scheduler.enqueue_daily`'s per-org `DAILY_KINDS` loop).

Gated by `settings.dogfood_billing_enabled` (a platform org configured AND no
live billing provider active) so a tenant is never invoiced twice once
Stripe/EveryPay goes live.

VAT: the line rate is `settings.platform_subscription_vat_rate`, a 0%
placeholder pending the seller-of-record VAT decision
(`docs/DECISIONS-NEEDED.md` §2) — this module does not decide the real rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tenant import reset_current_org, set_current_org
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.schemas.issued import IssuedInvoiceCreate, IssuedLineIn
from app.services import audit, issued_service, plans
from app.services import issuer as issuer_svc


@dataclass
class SubscriptionBillingResult:
    invoiced: list[tuple[str, str]] = field(default_factory=list)  # (subscriber org_id, number)


async def orgs_due_for_subscription_invoice(
    db: AsyncSession, *, platform_org_id: str, period: date
) -> list[Organization]:
    """ACTIVE, paid-plan organizations (other than the platform org itself)
    with no subscription invoice yet for `period` (the first-of-month the
    billing cycle covers).

    Must be called with the tenant context already scoped to
    `platform_org_id` (the caller sets it) so the `issued_invoices` existence
    check is RLS-safe.
    """
    orgs = list(
        await db.scalars(
            select(Organization).where(
                Organization.id != platform_org_id, Organization.status == "active"
            )
        )
    )
    due = []
    for org in orgs:
        plan = plans.plan_for(org.plan)
        if not plan.price_eur:
            continue  # trial (free) / enterprise (custom, "contact us") never dogfood-billed
        exists = await db.scalar(
            select(IssuedInvoice.id).where(
                IssuedInvoice.org_id == platform_org_id,
                IssuedInvoice.subscription_org_id == org.id,
                IssuedInvoice.subscription_period == period,
            )
        )
        if exists is None:
            due.append(org)
    return due


async def bill_subscriptions(
    db: AsyncSession, *, today: date | None = None
) -> SubscriptionBillingResult:
    """Generate this period's subscription invoice for every tenant that owes
    one and doesn't have it yet.

    Idempotent per (org, calendar-month period) via the `issued_invoices`
    unique constraint + the due-query's own existence check; a pure no-op
    whenever dogfood billing isn't configured, and never raises on a business
    no-op (a missing/misconfigured platform org, or nothing due).
    """
    today = today or date.today()
    if not settings.dogfood_billing_enabled:
        return SubscriptionBillingResult()
    platform_org_id = settings.platform_org_id
    assert platform_org_id  # dogfood_billing_enabled implies this is set

    platform_org = await db.get(Organization, platform_org_id)
    if platform_org is None:
        return SubscriptionBillingResult()

    period = today.replace(day=1)
    token = set_current_org(platform_org_id)
    try:
        due = await orgs_due_for_subscription_invoice(
            db, platform_org_id=platform_org_id, period=period
        )
        if not due:
            return SubscriptionBillingResult()

        # One lock covers the whole sweep for race-free numbering, exactly like
        # recurring.generate_due's single issuer_svc.lock call.
        profile = await issuer_svc.lock(db, platform_org_id)
        result = SubscriptionBillingResult()
        for org in due:
            plan = plans.plan_for(org.plan)
            assert plan.price_eur  # orgs_due_for_subscription_invoice already filtered this
            body = IssuedInvoiceCreate(
                buyer_name=org.name,
                vat_scheme="standard",
                lines=[
                    IssuedLineIn(
                        description=f"InvoiceIQ {plan.name} plan subscription — {period:%B %Y}",
                        quantity=Decimal("1"),
                        unit_price=Decimal(plan.price_eur),
                        vat_rate=settings.platform_subscription_vat_rate,
                    )
                ],
            )
            inv = issued_service.build_invoice(
                profile, body, org_id=platform_org_id, issue_date=today
            )
            inv.subscription_org_id = org.id
            inv.subscription_period = period
            db.add(inv)
            await db.flush()  # assigns the number before the next iteration reads it
            assert inv.number is not None
            result.invoiced.append((org.id, inv.number))
            await audit.record(
                db,
                audit.A.PLATFORM_SUBSCRIPTION_INVOICE,
                target_type="issued_invoice",
                target_id=inv.id,
                meta={
                    "subscriber_org_id": org.id,
                    "plan": org.plan,
                    "period": period.isoformat(),
                    "number": inv.number,
                    "amount_eur": str(plan.price_eur),
                },
            )
        await db.commit()
        return result
    finally:
        reset_current_org(token)
