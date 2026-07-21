"""Metered-usage reporting to the billing provider (ADR-0013).

Our `usage_counters` are the entitlement authority; for Stripe metered/overage
pricing we also REPORT the usage. Reporting is incremental and idempotent: each
counter tracks how much has been `reported`, so a run sends only
`count - reported` (never double-counting) and stamps the watermark forward. The
Stripe meter event also carries a deterministic `identifier`, so a network retry
is deduped provider-side too.

Only active when the Stripe provider is selected and a meter is mapped for the
metric (`stripe_meter_<metric>`); otherwise a no-op — EveryPay/None have no
metered API.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.organization import Organization
from app.models.usage import UsageCounter
from app.services.access import _period
from app.services.billing_provider import BillingError, get_billing_provider

log = logging.getLogger("invoiceiq.billing.usage")

# Internal meters that can be reported for overage (must map via stripe_meter_*).
METERED_METRICS = ("upload",)


async def orgs_with_stripe(db: AsyncSession) -> list[str]:
    """Org ids with a Stripe customer (UNSCOPED — scheduler context)."""
    return list(
        await db.scalars(
            select(Organization.id).where(Organization.stripe_customer_id.is_not(None))
        )
    )


async def report_org_usage(
    db: AsyncSession, org_id: str, *, period: str | None = None
) -> dict[str, int]:
    """Report the unreported usage delta for a tenant to Stripe. Returns the
    delta reported per metric ({} when nothing to do / not on Stripe)."""
    provider = get_billing_provider()
    if provider.name != "stripe":
        return {}
    org = await db.get(Organization, org_id)
    if org is None or not org.stripe_customer_id:
        return {}

    period = period or _period()
    reported: dict[str, int] = {}
    for metric in METERED_METRICS:
        meter_event = settings.stripe_meter_for(metric)
        if not meter_event:
            continue
        counter = await db.scalar(
            select(UsageCounter).where(
                UsageCounter.org_id == org_id,
                UsageCounter.period == period,
                UsageCounter.metric == metric,
            )
        )
        if counter is None:
            continue
        delta = (counter.count or 0) - (counter.reported or 0)
        if delta <= 0:
            continue
        try:
            await provider.report_usage(
                customer_id=org.stripe_customer_id,
                meter_event=meter_event,
                quantity=delta,
                identifier=f"{org_id}:{period}:{metric}:{counter.count}",
            )
        except BillingError as exc:  # leave the watermark so a later run retries
            log.warning("usage report failed for %s/%s: %s", org_id, metric, exc)
            continue
        counter.reported = counter.count
        reported[metric] = delta

    if reported:
        await db.commit()
    return reported
