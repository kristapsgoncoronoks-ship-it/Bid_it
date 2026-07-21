"""Built-in job handlers. Importing this module registers them with the queue.

Kept separate from `jobs.py` (the queue mechanics) so the queue has no dependency
on any particular domain. `app.main` imports this at startup; the worker imports
it too, so both the API (which enqueues) and the worker (which runs) agree on the
set of known kinds.
"""

from __future__ import annotations

from app.models.job import Job
from app.services import (
    billing,
    billing_usage,
    costing,
    dunning,
    email_intake,
    integrity,
    jobs,
    recurring,
    retention,
    webhooks,
)

RECURRING_GENERATE = "recurring.generate"
DUNNING_RUN = "dunning.run"
INTEGRITY_VERIFY = "integrity.verify_documents"
EVERYPAY_CHARGE = "everypay.charge_mit"
RETENTION_PURGE = "retention.purge"
USAGE_REPORT = "billing.report_usage"
COSTING_BACKFILL = "costing.backfill_links"
INTEGRITY_LEDGER = "integrity.verify_ledger"
INTEGRITY_VERSIONS = "integrity.verify_versions"


@jobs.handler(USAGE_REPORT)
async def _usage_report(db, payload: dict, job: Job) -> dict:
    """Report the tenant's unreported metered usage to Stripe (idempotent delta)."""
    return {"reported": await billing_usage.report_org_usage(db, job.org_id)}


@jobs.handler(EVERYPAY_CHARGE)
async def _everypay_charge(db, payload: dict, job: Job) -> dict:
    """Charge one tenant's recurring EveryPay MIT for the current period."""
    return await billing.charge_renewal(db, job.org_id)


@jobs.handler(RETENTION_PURGE)
async def _retention_purge(db, payload: dict, job: Job) -> dict:
    """Purge one tenant's records past their retention window (unless on hold)."""
    return await retention.purge(db, job.org_id)


@jobs.handler(RECURRING_GENERATE)
async def _recurring_generate(db, payload: dict, job: Job) -> dict:
    """Materialise every recurring invoice due for the job's tenant."""
    res = await recurring.generate_due(db, job.org_id)
    return {"generated": len(res.generated), "numbers": [n for _, n in res.generated]}


@jobs.handler(DUNNING_RUN)
async def _dunning_run(db, payload: dict, job: Job) -> dict:
    """Send a reminder for every overdue invoice for the job's tenant."""
    res = await dunning.run_overdue(db, job.org_id)
    return {"sent": res.sent, "skipped_no_email": res.skipped_no_email}


@jobs.handler(webhooks.WEBHOOK_DELIVER)
async def _webhook_deliver(db, payload: dict, job: Job) -> dict:
    """Deliver one recorded webhook event (signed POST; retries via the queue)."""
    return await webhooks.deliver(db, payload["delivery_id"])


@jobs.handler(email_intake.EXTRACT_KIND)
async def _email_extract(db, payload: dict, job: Job) -> dict:
    """Parse one queued inbound email attachment off the API tier (ADR-0009)."""
    return await email_intake.extract_inbound(db, payload["inbound_id"])


@jobs.handler(COSTING_BACKFILL)
async def _costing_backfill(db, payload: dict, job: Job) -> dict:
    """Link this tenant's free-text dimensions (invoices + expense items) to
    cost-allocation master rows (dual-read backfill, idempotent)."""
    return {
        "invoices": await costing.backfill_invoice_links(db, job.org_id),
        "expense_items": await costing.backfill_expense_item_links(db, job.org_id),
    }


def _report_dict(report) -> dict:
    return {
        "checked": report.checked,
        "ok": report.ok,
        "issues": len(report.issues),
        "healthy": report.healthy,
    }


@jobs.handler(INTEGRITY_VERIFY)
async def _integrity_verify(db, payload: dict, job: Job) -> dict:
    """Re-hash the tenant's stored documents against their recorded sha256."""
    return _report_dict(await integrity.verify_documents(db, job.org_id))


@jobs.handler(INTEGRITY_LEDGER)
async def _integrity_ledger(db, payload: dict, job: Job) -> dict:
    """Verify the tenant's AR-ledger invariants (amount_paid == SUM(payments);
    receipts not over-allocated)."""
    return _report_dict(await integrity.verify_ledger(db, job.org_id))


@jobs.handler(INTEGRITY_VERSIONS)
async def _integrity_versions(db, payload: dict, job: Job) -> dict:
    """Verify the tenant's document-version chain (one current per slot; current
    sha matches the owner cache; no file without a history)."""
    return _report_dict(await integrity.verify_versions(db, job.org_id))


# Kinds an authenticated user is allowed to enqueue via the API (safe, tenant
# -scoped periodic work). Other kinds can only be created internally.
USER_ENQUEUEABLE = (
    RECURRING_GENERATE,
    DUNNING_RUN,
    INTEGRITY_VERIFY,
    INTEGRITY_LEDGER,
    INTEGRITY_VERSIONS,
    COSTING_BACKFILL,
)
