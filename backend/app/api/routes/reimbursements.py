"""Expense reimbursement payout routes (Phase 09).

A designated approver (EXPENSE_APPROVE) groups APPROVED expense reports into a
payout batch, marks it paid (which flips each report to `reimbursed` with the
payment method + reference), and exports it as a bank/payroll CSV. Gated behind
the expenses module; every action is audited and emits a webhook.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_perm
from app.api.routes.expenses import _guard
from app.core import authz
from app.core.security_headers import content_disposition
from app.models.expense import ReimbursementBatch
from app.schemas.reimbursement import (
    BatchCreate,
    BatchDetailOut,
    BatchOut,
    BatchPay,
    ReimbursementReportOut,
)
from app.services import audit, reimbursement, sepa, webhooks

# Structural authorization (ADR-0024): every reimbursement-payout route is an
# approver/finance action — router-level EXPENSE_APPROVE.
router = APIRouter(
    prefix="/reimbursements",
    tags=["reimbursements"],
    dependencies=[Depends(require_perm(authz.Permission.EXPENSE_APPROVE))],
)


async def _load(
    db: DbSession, org_id: str, batch_id: str, *, lock: bool = False
) -> ReimbursementBatch:
    stmt = select(ReimbursementBatch).where(
        ReimbursementBatch.id == batch_id, ReimbursementBatch.org_id == org_id
    )
    if lock:
        # Serialize state-changing operations (pay/cancel) so the version check +
        # settlement are atomic and can't double-write the ledger. SQLite ignores
        # FOR UPDATE (writes serialize); Postgres takes the row lock.
        stmt = stmt.with_for_update()
    b = await db.scalar(stmt)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reimbursement batch not found")
    return b


async def _detail(db: DbSession, org_id: str, b: ReimbursementBatch) -> BatchDetailOut:
    reports = await reimbursement.batch_reports(db, org_id, b.id)
    return BatchDetailOut(
        id=b.id,
        reference=b.reference,
        method=b.method,
        status=b.status,
        note=b.note,
        total_eur=b.total_eur,
        paid_at=b.paid_at,
        created_by=b.created_by,
        exported_at=b.exported_at,
        export_count=b.export_count,
        last_msg_id=b.last_msg_id,
        version=b.version,
        created_at=b.created_at,
        report_count=len(reports),
        reports=[
            ReimbursementReportOut(
                id=r.id,
                title=r.title,
                employee_name=r.employee_name,
                total=r.total,
                currency=r.currency,
                total_eur=r.total_eur,
                status=r.status,
                payment_reference=r.payment_reference,
                reimbursed_at=r.reimbursed_at,
            )
            for r in reports
        ],
    )


@router.get("", response_model=list[BatchOut])
async def list_batches(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    rows = list(
        await db.scalars(
            select(ReimbursementBatch)
            .where(ReimbursementBatch.org_id == current.org_id)
            .order_by(ReimbursementBatch.created_at.desc())
        )
    )
    out = []
    for b in rows:
        reports = await reimbursement.batch_reports(db, current.org_id, b.id)
        out.append(
            BatchOut(
                id=b.id,
                reference=b.reference,
                method=b.method,
                status=b.status,
                note=b.note,
                total_eur=b.total_eur,
                paid_at=b.paid_at,
                created_by=b.created_by,
                exported_at=b.exported_at,
                export_count=b.export_count,
                last_msg_id=b.last_msg_id,
                version=b.version,
                created_at=b.created_at,
                report_count=len(reports),
            )
        )
    return out


@router.post("", response_model=BatchDetailOut, status_code=status.HTTP_201_CREATED)
async def create_batch(body: BatchCreate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        batch = await reimbursement.create_batch(
            db,
            current.org_id,
            body.report_ids,
            method=body.method,
            note=body.note,
            created_by=current.email,
            created_by_id=current.id,
        )
    except reimbursement.ReimbursementError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    await audit.record(
        db,
        audit.A.REIMBURSE_BATCH,
        target_type="reimbursement_batch",
        target_id=batch.id,
        meta={"reports": len(body.report_ids), "method": batch.method},
    )
    await webhooks.emit(
        db,
        current.org_id,
        "expense.reimbursement_created",
        {"id": batch.id, "reports": len(body.report_ids), "total_eur": str(batch.total_eur)},
    )
    await db.commit()
    await db.refresh(batch)
    return await _detail(db, current.org_id, batch)


@router.get("/{batch_id}", response_model=BatchDetailOut)
async def get_batch(batch_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    b = await _load(db, current.org_id, batch_id)
    return await _detail(db, current.org_id, b)


@router.post("/{batch_id}/pay", response_model=BatchDetailOut)
async def pay_batch(batch_id: str, body: BatchPay, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    b = await _load(db, current.org_id, batch_id, lock=True)
    if body.version != b.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Batch changed (your {body.version}, current {b.version})."
        )
    try:
        reports = await reimbursement.mark_paid(
            db,
            current.org_id,
            b,
            reference=body.reference,
            method=body.method,
            actor_id=current.id,
            actor_email=current.email,
            is_platform_admin=bool(current.is_platform_admin),
            override_sod=body.override_sod,
        )
    except reimbursement.ReimbursementError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await audit.record(
        db,
        audit.A.REIMBURSE_PAID,
        target_type="reimbursement_batch",
        target_id=b.id,
        meta={"reports": len(reports), "reference": body.reference, "total_eur": str(b.total_eur)},
    )
    await webhooks.emit(
        db,
        current.org_id,
        "expense.reimbursed",
        {
            "id": b.id,
            "reports": len(reports),
            "reference": body.reference,
            "total_eur": str(b.total_eur),
        },
    )
    await db.commit()
    await db.refresh(b)
    return await _detail(db, current.org_id, b)


@router.delete("/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_batch(batch_id: str, current: CurrentUser, db: DbSession):
    """Cancel an OPEN batch, releasing its reports back to the payable pool.

    Loaded FOR UPDATE like the pay path — WO-Y. `_load` has always documented
    the lock as covering "pay/cancel"; cancel was the one state-changing route
    that did not take it. Concurrently with a pay, the plain read decided
    "still open" in Python, then blocked on the payer's row lock and wrote
    `cancelled` over a batch that had just been PAID — unlinking reports the
    payment had already stamped `reimbursed`, leaving money recorded as sent
    against a batch recorded as never run."""
    await _guard(db, current.org_id)
    b = await _load(db, current.org_id, batch_id, lock=True)
    try:
        await reimbursement.cancel_batch(db, current.org_id, b)
    except reimbursement.ReimbursementError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await audit.record(
        db, audit.A.REIMBURSE_CANCEL, target_type="reimbursement_batch", target_id=b.id, meta={}
    )
    await db.commit()


@router.get("/{batch_id}/export")
async def export_batch(
    batch_id: str, current: CurrentUser, db: DbSession, confirm_reexport: bool = False
):
    """The batch's bank/payroll CSV. WO-9 export guard (same treatment as payment
    runs): never a cancelled batch, export-once (`confirm_reexport` for a second
    file), every export audited. Loaded FOR UPDATE to serialize the counter."""
    await _guard(db, current.org_id)
    b = await _load(db, current.org_id, batch_id, lock=True)
    reimbursement.assert_exportable(b, confirm_reexport=confirm_reexport)
    reports = await reimbursement.batch_reports(db, current.org_id, b.id)
    csv_text = reimbursement.export_csv(b, reports)
    reimbursement.record_export(b, msg_id=None)
    await audit.record(
        db,
        audit.A.REIMBURSE_EXPORTED,
        target_type="reimbursement_batch",
        target_id=b.id,
        meta={
            "format": "csv",
            "export_count": b.export_count,
            "msg_id": None,
            "payees": len(reports),
            "total_eur": str(b.total_eur),
        },
    )
    await db.commit()
    fname = f"reimbursement-{(b.reference or b.id)}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": content_disposition(fname),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{batch_id}/sepa")
async def export_batch_sepa(
    batch_id: str,
    current: CurrentUser,
    db: DbSession,
    confirm_reexport: bool = False,
    acknowledge_skipped: bool = False,
):
    """Export the batch as an ISO 20022 pain.001 SEPA credit-transfer file (paid to
    employees with an IBAN on file). 422 if the issuer has no IBAN or no payee has
    one. WO-9: export-once (`confirm_reexport`), a UNIQUE MsgId per generation, and
    employees without an IBAN are NAMED in a 409 refusal (`acknowledge_skipped` to
    export without them) — never only counted in a header. The `X-Skipped` header
    is kept for compatibility (the test suite reads it; the SPA never did)."""
    await _guard(db, current.org_id)
    b = await _load(db, current.org_id, batch_id, lock=True)
    reimbursement.assert_exportable(b, confirm_reexport=confirm_reexport)
    msg_id = sepa.new_msg_id("REIMB", b.id, b.export_count + 1)
    try:
        xml, skipped = await reimbursement.batch_sepa(
            db, current.org_id, b, msg_id=msg_id, acknowledge_skipped=acknowledge_skipped
        )
    except (reimbursement.ReimbursementError, sepa.SepaError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    reimbursement.record_export(b, msg_id=msg_id)
    reports = await reimbursement.batch_reports(db, current.org_id, b.id)
    await audit.record(
        db,
        audit.A.REIMBURSE_EXPORTED,
        target_type="reimbursement_batch",
        target_id=b.id,
        meta={
            "format": "sepa",
            "export_count": b.export_count,
            "msg_id": msg_id,
            "payees": len(reports) - len(skipped),
            "skipped": skipped,
            "total_eur": str(b.total_eur),
        },
    )
    await db.commit()
    fname = f"reimbursement-{(b.reference or b.id)}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={
            "Content-Disposition": content_disposition(fname),
            "X-Content-Type-Options": "nosniff",
            "X-Skipped": str(len(skipped)),
        },
    )
