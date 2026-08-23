"""Built-in job handlers. Importing this module registers them with the queue.

Kept separate from `jobs.py` (the queue mechanics) so the queue has no dependency
on any particular domain. `app.main` imports this at startup; the worker imports
it too, so both the API (which enqueues) and the worker (which runs) agree on the
set of known kinds.
"""

from __future__ import annotations

from app.models.job import Job
from app.services import (
    ap_alerts,
    archive,
    audit,
    billing,
    billing_usage,
    costing,
    dunning,
    email_intake,
    extraction,
    fx,
    integrity,
    jobs,
    platform_billing,
    recurring,
    retention,
    webhooks,
)
from app.services import invoices as invoice_service
from app.services.transport import close as transport_close

RECURRING_GENERATE = "recurring.generate"
DUNNING_RUN = "dunning.run"
AP_DUE_ALERTS = "ap.due_alerts"
INTEGRITY_VERIFY = "integrity.verify_documents"
EVERYPAY_CHARGE = "everypay.charge_mit"
RETENTION_PURGE = "retention.purge"
BIN_PURGE = "invoice.bin_purge"
ARCHIVE_PURGE = "archive.purge_expired"
ARCHIVE_NOTICE = "archive.expiry_notice"
USAGE_REPORT = "billing.report_usage"
COSTING_BACKFILL = "costing.backfill_links"
INTEGRITY_LEDGER = "integrity.verify_ledger"
INTEGRITY_VERSIONS = "integrity.verify_versions"
FX_REFRESH = "fx.refresh"
PLATFORM_BILLING_RUN = "platform.bill_subscriptions"


@jobs.handler(FX_REFRESH)
async def _fx_refresh(db, payload: dict, job: Job) -> dict:
    """Daily ECB reference-rate refresh (WO-8).

    ECB rates are GLOBAL reference data, not per-tenant, so the scheduler
    enqueues this ONCE per day total — carried by a single deterministic org
    (see scheduler.enqueue_daily) because the queue's tenant invariants (org_id
    NOT NULL + RLS) rule out an org-less row. A per-org daily job was rejected:
    it would fetch the same public feed once per tenant, and a handler-side
    cross-org dedupe cannot see sibling jobs through the tenant guard (by
    design). Graceful degradation is preserved end to end: `fx.refresh_from_ecb`
    never raises (12s timeout, returns ok=False on any failure) and a failed
    fetch leaves the cached rates serving; the write is an idempotent upsert, so
    even a duplicate run is harmless."""
    return await fx.refresh_from_ecb(db)


@jobs.handler(USAGE_REPORT)
async def _usage_report(db, payload: dict, job: Job) -> dict:
    """Report the tenant's unreported metered usage to Stripe (idempotent delta)."""
    return {"reported": await billing_usage.report_org_usage(db, job.org_id)}


@jobs.handler(EVERYPAY_CHARGE)
async def _everypay_charge(db, payload: dict, job: Job) -> dict:
    """Charge one tenant's recurring EveryPay MIT for the current period."""
    return await billing.charge_renewal(db, job.org_id)


@jobs.handler(PLATFORM_BILLING_RUN)
async def _platform_billing_run(db, payload: dict, job: Job) -> dict:
    """Dogfood fallback (H1.6): generate this period's subscription invoice for
    every tenant that owes one and doesn't have it yet. A no-op unless
    `settings.dogfood_billing_enabled`."""
    res = await platform_billing.bill_subscriptions(db)
    return {"invoiced": len(res.invoiced)}


@jobs.handler(RETENTION_PURGE)
async def _retention_purge(db, payload: dict, job: Job) -> dict:
    """Purge one tenant's records past their retention window (unless on hold)."""
    return await retention.purge(db, job.org_id)


@jobs.handler(BIN_PURGE)
async def _bin_purge(db, payload: dict, job: Job) -> dict:
    """Empty one tenant's recycle bin of anything past its 30 days.

    Separate from RETENTION_PURGE on purpose. That one is an opt-in per-tenant
    policy over a record's AGE; this is the fixed promise made when a client
    deletes something, and it must run for every tenant whether or not they have
    configured retention at all — otherwise a binned record is invisible AND
    immortal, which is the worst of both.

    Audited with what was destroyed, not just how many: until the platform
    archive exists, this event is the only remaining trace of the record.
    """
    result = await invoice_service.purge_expired_bin(db, job.org_id)
    if result["purged"]:
        await audit.record(
            db,
            "invoice.bin_purge",
            org_id=job.org_id,
            meta={"purged": result["purged"], "records": result["records"]},
        )
        await db.commit()
    return {"held": result["held"], "purged": result["purged"]}


@jobs.handler(ARCHIVE_PURGE)
async def _archive_purge(db, payload: dict, job: Job) -> dict:
    """Destroy one tenant's archive rows past `expires_at`, then their bytes.

    The end of the deletion chain, and until it existed the chain had no end:
    `expires_at` was stamped, published and printed on the client screen while
    nothing enforced it — "kept for three years, then removed" was true only up
    to the comma. In DAILY_KINDS for every tenant, like BIN_PURGE: expiry is a
    promise stated on every archived record, not an opt-in policy.

    Order matters here. The rows are destroyed and AUDITED in one commit (after
    which that event is the only remaining trace of the records), and only then
    are the document bytes collected, best-effort — so a rollback can never
    leave surviving rows pointing at bytes that are already gone. The service
    has already excluded every sha still referenced by a surviving archive row
    or a live invoice's extraction run.
    """
    result = await archive.purge_expired(db, job.org_id)
    if result["purged"]:
        await audit.record(
            db,
            "archive.purge",
            org_id=job.org_id,
            meta={"purged": result["purged"], "records": result["records"]},
        )
        await db.commit()
        collected = await archive.collect_bytes(job.org_id, result["collectable_shas"])
    else:
        collected = 0
    return {"held": result["held"], "purged": result["purged"], "bytes_collected": collected}


@jobs.handler(ARCHIVE_NOTICE)
async def _archive_notice(db, payload: dict, job: Job) -> dict:
    """Warn one tenant's owners about archive records inside the notice window.

    Its own daily kind rather than a rider on ARCHIVE_PURGE: the purge destroys
    and the notice warns, and a failure emailing must never be able to delay a
    purge (or the reverse). Audited with WHICH records were covered — the stamp
    on the rows says "told", the event says told about what, when, to how many
    addresses. `skipped_no_email` surfaces the one silent failure mode this has:
    a tenant whose owners have no address is owed a notice nothing can deliver,
    and the rows stay unstamped so it keeps being owed rather than marked done.
    """
    result = await archive.send_expiry_notices(db, job.org_id)
    if result["sent"] or result["skipped_no_email"]:
        await audit.record(
            db,
            "archive.expiry_notice",
            org_id=job.org_id,
            meta={
                "records": result["records"],
                "sent": result["sent"],
                "skipped_no_email": result["skipped_no_email"],
                "record_ids": result.get("record_ids", []),
                "earliest": result.get("earliest"),
            },
        )
        await db.commit()
    return {
        "sent": result["sent"],
        "records": result["records"],
        "skipped_no_email": result["skipped_no_email"],
    }


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


@jobs.handler(AP_DUE_ALERTS)
async def _ap_due_alerts(db, payload: dict, job: Job) -> dict:
    """Email the tenant a digest of supplier invoices due soon / overdue."""
    return await ap_alerts.send_digest(db, job.org_id)


@jobs.handler(webhooks.WEBHOOK_DELIVER)
async def _webhook_deliver(db, payload: dict, job: Job) -> dict:
    """Deliver one recorded webhook event (signed POST; retries via the queue)."""
    return await webhooks.deliver(db, payload["delivery_id"])


@jobs.handler(email_intake.EXTRACT_KIND)
async def _email_extract(db, payload: dict, job: Job) -> dict:
    """Parse one queued inbound email attachment off the API tier (ADR-0009)."""
    return await email_intake.extract_inbound(db, payload["inbound_id"])


@jobs.handler(extraction.UPLOAD_EXTRACT_KIND)
async def _upload_extract(db, payload: dict, job: Job) -> dict:
    """Parse one queued UI direct upload off the API tier (Stage B). Keeps
    CPU-heavy OCR out of the web request path."""
    return await extraction.extract_upload(db, payload["run_id"])


@jobs.handler(transport_close.CLOSE_KIND)
async def _transport_close(db, payload: dict, job: Job) -> dict:
    """G1.3 — the transport-vertical monthly close: (re)build live claim
    lines for every draft claim whose scope includes `payload["period"]`
    (`"YYYY-MM"`). See `app.services.transport.close`'s module docstring for
    why this fully satisfies R31/R60's durability requirements on the
    existing job framework, with no new mechanism."""
    return await transport_close.run_close(db, job.org_id, payload["period"])


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


@jobs.handler("assignment.reminder")
async def _assignment_reminder(db, payload: dict, job: Job) -> dict:
    """One upcoming-work reminder to the assignee (WO-B). Armed at exact time
    via run_after when the assignment is created/updated; the service re-checks
    current state so stale jobs (rescheduled/cancelled) no-op or re-arm."""
    from app.services import scheduling

    return await scheduling.send_due_reminder(db, job.org_id, payload["assignment_id"])


# Kinds an authenticated user is allowed to enqueue via the API (safe, tenant
# -scoped periodic work). Other kinds can only be created internally.
USER_ENQUEUEABLE = (
    RECURRING_GENERATE,
    DUNNING_RUN,
    AP_DUE_ALERTS,
    INTEGRITY_VERIFY,
    INTEGRITY_LEDGER,
    INTEGRITY_VERSIONS,
    COSTING_BACKFILL,
)
