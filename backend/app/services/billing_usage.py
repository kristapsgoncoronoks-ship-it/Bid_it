"""Metered-usage reporting to the billing provider (ADR-0013).

Our `usage_counters` are the entitlement authority; Stripe is a downstream
projection. Each outbound segment is FROZEN before the network call
(BILL-METER-001, Lago reference integration 2026-09-07): `reporting_target` is
the cumulative count the segment ends at, `reported` the cumulative count the
provider has acknowledged, and the meter event's `identifier` is derived from
the target — so it is the same on every replay of the same segment.

Why the freeze: the old code sent `count - reported` recomputed at call time
with `identifier=…:{count}`. If Stripe accepted 10 units but the response was
lost and usage grew to 15 before the retry, the retry sent a fresh 15-unit event
under a NEW identifier — an over-report the provider's idempotency could not
catch. Now the retry resends the original 10-unit segment with the original
identifier; only once that is acknowledged do the later 5 units become a new
segment. Same class of fix as BE-005 (commit the claim BEFORE the provider call).

Only active when the Stripe provider is selected and a meter is mapped for the
metric (`stripe_meter_<metric>`); otherwise a no-op — EveryPay/None have no
metered API.
"""

from __future__ import annotations

import logging
from typing import cast

from sqlalchemy import CursorResult, select, update
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
    """Report fixed, retry-safe usage segments for a tenant to Stripe. Returns
    the quantity ACKNOWLEDGED per metric in this run ({} when nothing to do,
    not on Stripe, or the provider did not acknowledge).

    Per metric: if no segment is in flight (`reporting_target == reported`) and
    usage grew, freeze a new segment at the current `count` and COMMIT; then send
    `target - reported` under `identifier=…:{target}`; on success advance
    `reported` to the target and commit; on a provider error leave
    `reported < reporting_target` so the next run replays this exact segment."""
    provider = get_billing_provider()
    if provider.name != "stripe":
        return {}
    org = await db.get(Organization, org_id)
    if org is None or not org.stripe_customer_id:
        return {}

    period = period or _period()
    acknowledged: dict[str, int] = {}
    for metric in METERED_METRICS:
        meter_event = settings.stripe_meter_for(metric)
        if not meter_event:
            continue
        # Serialise segment creation across concurrent runs (a row lock on
        # Postgres, a no-op on SQLite). Released by the pre-call commit; a
        # concurrent run after that can only resend the SAME segment under the
        # same identifier, which the provider dedupes.
        counter = await db.scalar(
            select(UsageCounter)
            .where(
                UsageCounter.org_id == org_id,
                UsageCounter.period == period,
                UsageCounter.metric == metric,
            )
            .with_for_update()
        )
        if counter is None:
            continue

        reported = int(counter.reported or 0)
        target = int(counter.reporting_target or 0)
        if target < reported:
            # Rows from before the migration (the server default is 0), or a
            # manual correction: the in-flight boundary can never be behind the
            # acknowledged one. The repair rides the next commit below — or,
            # if nothing is owed, the enclosing job's `_complete` commit.
            target = reported
            counter.reporting_target = target

        if target == reported:
            count = int(counter.count or 0)
            if count <= reported:
                continue
            target = count
            counter.reporting_target = target

        delta = target - reported
        if delta <= 0:
            continue
        # CRITICAL ORDER: persist the frozen segment (and release the row lock)
        # BEFORE the money-affecting provider call — on the freeze path so a
        # timeout or a dead worker leaves enough state to retry exactly this
        # segment, and on the REPLAY path too, because holding `FOR UPDATE`
        # across an 80 s Stripe call would stall this tenant's upload counter
        # (`access.record_usage` writes the same row) — review finding P1.
        await db.commit()
        identifier = f"{org_id}:{period}:{metric}:{target}"
        try:
            await provider.report_usage(
                customer_id=org.stripe_customer_id,
                meter_event=meter_event,
                quantity=delta,
                identifier=identifier,
            )
        except BillingError as exc:
            # Leave reported < reporting_target: the next run replays THIS
            # segment rather than folding in later usage.
            log.warning("usage report failed for %s/%s target=%s: %s", org_id, metric, target, exc)
            continue
        # Compare-and-set, not a blind write (review finding A3): a slower
        # overlapping run that acknowledged an OLDER target must not regress a
        # newer `reported`. Zero rows means someone else already advanced past
        # this segment — nothing to record for this run.
        result = await db.execute(
            update(UsageCounter)
            .where(
                UsageCounter.id == counter.id,
                UsageCounter.reporting_target == target,
                UsageCounter.reported < target,
            )
            .values(reported=target)
        )
        await db.commit()
        if cast(CursorResult, result).rowcount == 1:
            counter.reported = target
            counter.reporting_target = target
            acknowledged[metric] = delta
        else:
            await db.refresh(counter)
    return acknowledged
