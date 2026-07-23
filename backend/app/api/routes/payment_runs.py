"""Supplier payment-run routes (Phase 14).

Group scheduled-for-payment supplier invoices into a payout run, mark it paid
(which settles each invoice via the AP payment ledger and advances it to `paid`),
and export it as a bank CSV. Gated on the PAYMENT_* permissions (cash application):
router-level PAYMENT_READ, PAYMENT_WRITE on the mutations. Every action is audited.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core import authz
from app.models.payment_run import PaymentRun
from app.schemas.payment_run import (
    RunCreate,
    RunDetailOut,
    RunInvoiceOut,
    RunOut,
    RunPay,
)
from app.services import audit, payment_run, sepa, webhooks


def _require_payment_read(current: CurrentUser) -> None:
    authz.require(current, authz.Permission.PAYMENT_READ)


router = APIRouter(
    prefix="/payment-runs",
    tags=["payment-runs"],
    dependencies=[Depends(_require_payment_read)],
)


def _invoice_out(inv) -> RunInvoiceOut:
    return RunInvoiceOut(
        id=inv.id,
        invoice_number=inv.invoice_number,
        vendor_name=inv.vendor.name if inv.vendor else None,
        total=inv.total,
        currency=inv.currency,
        total_eur=inv.total_eur,
        workflow_state=inv.workflow_state.value,
    )


def _run_out(run: PaymentRun, invoice_count: int) -> RunOut:
    return RunOut(
        id=run.id,
        reference=run.reference,
        method=run.method,
        status=run.status,
        note=run.note,
        total_eur=run.total_eur,
        paid_at=run.paid_at,
        created_by=run.created_by,
        version=run.version,
        created_at=run.created_at,
        invoice_count=invoice_count,
    )


async def _load(db: DbSession, org_id: str, run_id: str, *, lock: bool = False) -> PaymentRun:
    stmt = select(PaymentRun).where(PaymentRun.id == run_id, PaymentRun.org_id == org_id)
    if lock:
        # Serialize concurrent state-changing operations (pay/cancel) on the run so
        # the version check + mark_paid are atomic and can't double-settle. SQLite
        # ignores FOR UPDATE (writes already serialize); Postgres takes the row lock.
        stmt = stmt.with_for_update()
    r = await db.scalar(stmt)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment run not found")
    return r


async def _detail(db: DbSession, org_id: str, run: PaymentRun) -> RunDetailOut:
    invoices = await payment_run.run_invoices(db, org_id, run.id)
    return RunDetailOut(
        **_run_out(run, len(invoices)).model_dump(),
        invoices=[_invoice_out(i) for i in invoices],
    )


@router.get("/payable", response_model=list[RunInvoiceOut])
async def list_payable(current: CurrentUser, db: DbSession):
    """Scheduled-for-payment invoices not yet in a run (the run candidate pool)."""
    rows = await payment_run.payable_invoices(db, current.org_id)
    return [_invoice_out(i) for i in rows]


@router.get("", response_model=list[RunOut])
async def list_runs(current: CurrentUser, db: DbSession):
    rows = list(
        await db.scalars(
            select(PaymentRun)
            .where(PaymentRun.org_id == current.org_id)
            .order_by(PaymentRun.created_at.desc())
        )
    )
    out = []
    for r in rows:
        invoices = await payment_run.run_invoices(db, current.org_id, r.id)
        out.append(_run_out(r, len(invoices)))
    return out


@router.post("", response_model=RunDetailOut, status_code=status.HTTP_201_CREATED)
async def create_run(body: RunCreate, current: CurrentUser, db: DbSession):
    authz.require(current, authz.Permission.PAYMENT_WRITE)
    try:
        run = await payment_run.create_run(
            db,
            current.org_id,
            body.invoice_ids,
            method=body.method,
            note=body.note,
            created_by=current.email,
        )
    except payment_run.PaymentRunError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    await audit.record(
        db,
        audit.A.AP_RUN_CREATE,
        target_type="payment_run",
        target_id=run.id,
        meta={"invoices": len(body.invoice_ids), "method": run.method},
    )
    await db.commit()
    await db.refresh(run)
    return await _detail(db, current.org_id, run)


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: str, current: CurrentUser, db: DbSession):
    run = await _load(db, current.org_id, run_id)
    return await _detail(db, current.org_id, run)


@router.post("/{run_id}/pay", response_model=RunDetailOut)
async def pay_run(run_id: str, body: RunPay, current: CurrentUser, db: DbSession):
    authz.require(current, authz.Permission.PAYMENT_WRITE)
    run = await _load(db, current.org_id, run_id, lock=True)
    if body.version != run.version:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Run changed (your {body.version}, current {run.version})."
        )
    try:
        invoices = await payment_run.mark_paid(
            db, current.org_id, run, reference=body.reference, method=body.method
        )
    except payment_run.PaymentRunError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await audit.record(
        db,
        audit.A.AP_RUN_PAID,
        target_type="payment_run",
        target_id=run.id,
        meta={
            "invoices": len(invoices),
            "reference": body.reference,
            "total_eur": str(run.total_eur),
        },
    )
    for inv in invoices:
        await webhooks.emit(
            db, current.org_id, "invoice.paid", {"id": inv.id, "invoice_number": inv.invoice_number}
        )
    await db.commit()
    await db.refresh(run)
    return await _detail(db, current.org_id, run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(run_id: str, current: CurrentUser, db: DbSession):
    authz.require(current, authz.Permission.PAYMENT_WRITE)
    run = await _load(db, current.org_id, run_id, lock=True)
    try:
        await payment_run.cancel_run(db, current.org_id, run)
    except payment_run.PaymentRunError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await audit.record(
        db, audit.A.AP_RUN_CANCEL, target_type="payment_run", target_id=run.id, meta={}
    )
    await db.commit()


@router.get("/{run_id}/export")
async def export_run(run_id: str, current: CurrentUser, db: DbSession):
    run = await _load(db, current.org_id, run_id)
    invoices = await payment_run.run_invoices(db, current.org_id, run.id)
    csv_text = payment_run.export_csv(run, invoices)
    fname = f"payment-run-{(run.reference or run.id)}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{run_id}/sepa")
async def export_sepa(run_id: str, current: CurrentUser, db: DbSession):
    """The run as an ISO 20022 pain.001 SEPA credit-transfer file for the bank."""
    run = await _load(db, current.org_id, run_id)
    try:
        xml, _skipped = await sepa.payment_run_sepa(db, current.org_id, run)
    except sepa.SepaError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    fname = f"payment-run-{(run.reference or run.id)}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
