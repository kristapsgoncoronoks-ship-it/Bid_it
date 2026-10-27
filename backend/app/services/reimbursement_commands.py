"""Bounded reimbursement reads and recoverable payout-batch creation.

The transaction belongs to the route. These functions never commit. Creation
shares expense_commands' tenant/actor/key identity; its membership lock guards
same-actor replay, and reimbursement's ordered report locks guard overlapping
selections made by different actors.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import authz
from app.core.errors import NotFoundError, ValidationError
from app.core.identity import Principal
from app.models.base import new_uuid
from app.models.expense import ExpenseReport, ReimbursementBatch
from app.schemas.expense import ExpenseCommandOut
from app.schemas.reimbursement import (
    BatchCreate,
    BatchDetailOut,
    BatchOut,
    BatchPage,
    EligibleReportOut,
    EligibleReportPage,
    ReimbursementReportOut,
)
from app.services import expense_commands, reimbursement, webhooks
from app.services.issued_commands import fingerprint

CREATE_OPERATION = "reimbursement_created"


def batch_summary(batch: ReimbursementBatch, report_count: int) -> BatchOut:
    return BatchOut.model_validate(
        {
            **{
                name: getattr(batch, name)
                for name in BatchOut.model_fields
                if name != "report_count"
            },
            "report_count": report_count,
        }
    )


async def batch_detail(db: AsyncSession, org_id: str, batch: ReimbursementBatch) -> BatchDetailOut:
    reports = await reimbursement.batch_reports(db, org_id, batch.id)
    summary = batch_summary(batch, len(reports))
    return BatchDetailOut(
        **summary.model_dump(),
        reports=[ReimbursementReportOut.model_validate(report) for report in reports],
    )


async def create(
    db: AsyncSession, current: Principal, body: BatchCreate, supplied_key: str | None
) -> BatchDetailOut:
    if supplied_key is not None and not body.report_versions:
        raise ValidationError(
            "Reload the eligible reports and send the current version of every selection.",
            code="reimbursement_version_required",
        )
    payload = body.model_dump(mode="json")
    payload["report_ids"] = sorted(body.report_ids)
    digest = fingerprint(payload)
    # No-key callers retain their previous new-intent/refusal behavior. A stable
    # fallback from IDs alone would incorrectly replay an old cancelled batch
    # when a legacy client legitimately batches the released reports again.
    # Recovery therefore requires an explicit client key, as for manual expenses.
    key = (
        f"legacy-reimbursement:{new_uuid()}"
        if supplied_key is None
        else expense_commands.command_key(supplied_key, CREATE_OPERATION, digest)
    )
    await expense_commands.lock_actor(db, current, permission=authz.Permission.EXPENSE_APPROVE)
    previous = await expense_commands.lookup(
        db, current.org_id, current.id, key, operation=CREATE_OPERATION, expected=digest
    )
    if previous is not None:
        return BatchDetailOut.model_validate(json.loads(previous.result_json))

    batch = await reimbursement.create_batch(
        db,
        current.org_id,
        body.report_ids,
        method=body.method,
        note=body.note,
        created_by=current.email,
        created_by_id=current.id,
        report_versions=body.report_versions,
    )
    await db.flush()
    detail = await batch_detail(db, current.org_id, batch)
    await expense_commands.record(
        db,
        current,
        key,
        CREATE_OPERATION,
        digest,
        batch.id,
        detail.model_dump(mode="json"),
    )
    await webhooks.emit(
        db,
        current.org_id,
        "expense.reimbursement_created",
        {"id": batch.id, "reports": detail.report_count, "total_eur": str(batch.total_eur)},
        idempotency_key=f"reimbursement-created:{current.id}:{key}",
        strict=True,
    )
    return detail


async def command_status(db: AsyncSession, current: Principal, key: str) -> ExpenseCommandOut:
    key = expense_commands.command_key(key, CREATE_OPERATION, "")
    await expense_commands.lock_actor(db, current, permission=authz.Permission.EXPENSE_APPROVE)
    row = await expense_commands.lookup(
        db, current.org_id, current.id, key, operation=CREATE_OPERATION
    )
    if row is None:
        raise NotFoundError(
            "No committed batch outcome is visible yet. Check again or retry the original "
            "command key and inputs.",
            code="reimbursement_command_not_found",
        )
    return ExpenseCommandOut(
        id=row.id,
        command_key=row.command_key,
        operation=row.operation,
        target_id=row.target_id,
        result=json.loads(row.result_json),
    )


async def eligible_page(
    db: AsyncSession, org_id: str, *, page: int, page_size: int
) -> EligibleReportPage:
    filters = (
        ExpenseReport.org_id == org_id,
        ExpenseReport.status.in_(("approved", "marked_for_reimbursement")),
        ExpenseReport.payout_batch_id.is_(None),
    )
    total = int(await db.scalar(select(func.count(ExpenseReport.id)).where(*filters)) or 0)
    rows = list(
        await db.scalars(
            select(ExpenseReport)
            .where(*filters)
            .order_by(ExpenseReport.created_at.desc(), ExpenseReport.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return EligibleReportPage(
        items=[
            EligibleReportOut(
                id=row.id,
                title=row.title,
                employee_name=row.employee_name,
                total=row.total,
                currency=row.currency,
                total_eur=reimbursement.eur_or_none(row),
                status=row.status,
                version=row.version,
            )
            for row in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def batch_page(db: AsyncSession, org_id: str, *, page: int, page_size: int) -> BatchPage:
    total = int(
        await db.scalar(
            select(func.count(ReimbursementBatch.id)).where(ReimbursementBatch.org_id == org_id)
        )
        or 0
    )
    batches = list(
        await db.scalars(
            select(ReimbursementBatch)
            .where(ReimbursementBatch.org_id == org_id)
            .order_by(ReimbursementBatch.created_at.desc(), ReimbursementBatch.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    # Count only the bounded page's links in one grouped query, rather than
    # materializing reports once per batch (the former N+1 list implementation).
    counts = {}
    if batches:
        counts = dict(
            (
                await db.execute(
                    select(ExpenseReport.payout_batch_id, func.count(ExpenseReport.id))
                    .where(
                        ExpenseReport.org_id == org_id,
                        ExpenseReport.payout_batch_id.in_([batch.id for batch in batches]),
                    )
                    .group_by(ExpenseReport.payout_batch_id)
                )
            ).all()
        )
    return BatchPage(
        items=[batch_summary(batch, counts.get(batch.id, 0)) for batch in batches],
        total=total,
        page=page,
        page_size=page_size,
    )
