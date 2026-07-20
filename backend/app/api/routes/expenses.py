from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.models.expense import (
    ExpenseComment,
    ExpenseItem,
    ExpenseReport,
    ExpenseTransaction,
)
from app.core.roles import is_admin_or_above
from app.models.user import User
from app.schemas.expense import (
    BankImportResult,
    CategoryTotal,
    ExpenseCommentIn,
    ExpenseCommentOut,
    ExpenseDecision,
    ExpenseItemOut,
    ExpenseReportCreate,
    ExpenseReportDetail,
    ExpenseReportListOut,
    ExpenseReportOut,
    ExpenseReportUpdate,
    ExpenseSummary,
    ExpenseTransactionOut,
    ItemFromTransaction,
)
from app.services import bank_statement, expenses, filesec, fx, modules

router = APIRouter(prefix="/expenses", tags=["expenses"])


async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "expenses")


def _is_manager(user: User) -> bool:
    # Managers (admin/sysadmin) approve and reimburse expense reports.
    return is_admin_or_above(user)


def _detail(r: ExpenseReport) -> ExpenseReportDetail:
    d = ExpenseReportDetail.model_validate(r)
    d.items = [
        ExpenseItemOut(
            id=it.id, spend_date=it.spend_date, category=it.category, description=it.description,
            merchant=it.merchant, amount=it.amount, vat_amount=it.vat_amount,
            payment_method=it.payment_method, comment=it.comment,
            has_receipt=it.receipt_data is not None,
        )
        for it in r.items
    ]
    return d


async def _load(db: DbSession, org_id: str, report_id: str) -> ExpenseReport:
    r = await db.scalar(
        select(ExpenseReport)
        .where(ExpenseReport.id == report_id, ExpenseReport.org_id == org_id)
        .options(selectinload(ExpenseReport.items))
    )
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense report not found")
    return r


def _require_view(r: ExpenseReport, user: User):
    if not _is_manager(user) and r.employee_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Expense report not found")


def _require_owner_editable(r: ExpenseReport, user: User):
    if r.employee_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only edit your own reports")
    if r.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft reports can be edited")


async def _load_available_txns(db: DbSession, org_id: str, user_id: str, ids: list[str]) -> list[ExpenseTransaction]:
    if not ids:
        return []
    rows = list(await db.scalars(
        select(ExpenseTransaction).where(
            ExpenseTransaction.id.in_(ids),
            ExpenseTransaction.org_id == org_id,
            ExpenseTransaction.employee_id == user_id,
            ExpenseTransaction.status == "available",
        )
    ))
    if len(rows) != len(set(ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more transactions were not found or already used")
    return rows


def _item_from_txn(t: ExpenseTransaction, category: str = "other", vat=Decimal("0")) -> ExpenseItem:
    return ExpenseItem(
        spend_date=t.txn_date, category=category, description=t.description[:300],
        merchant=t.merchant, amount=expenses.q(t.amount), vat_amount=expenses.q(vat),
        payment_method="company_card" if t.source in ("bank_statement", "card") else "personal",
    )


@router.post("", response_model=ExpenseReportDetail, status_code=status.HTTP_201_CREATED)
async def create_report(body: ExpenseReportCreate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    items = [expenses.item_from(i) for i in body.items]

    # Concur-style: also build entries from selected inbox transactions.
    txns = await _load_available_txns(db, current.org_id, current.id, body.transaction_ids)
    txn_items = [_item_from_txn(t) for t in txns]
    items += txn_items

    total, vat = expenses.compute_totals(items)
    report = ExpenseReport(
        org_id=current.org_id, employee_id=current.id, employee_name=current.name,
        title=body.title, currency=body.currency.upper(), note=body.note,
        total=total, vat_total=vat, items=items,
    )
    db.add(report)
    await db.flush()
    for t, it in zip(txns, txn_items):
        t.status = "assigned"
        t.item_id = it.id
    await db.commit()
    await db.refresh(report, attribute_names=["items"])
    return _detail(report)


@router.post("/import/bank-statement", response_model=BankImportResult)
async def import_bank_statement(current: CurrentUser, db: DbSession, file: UploadFile):
    """Read a bank statement (PDF via OCR, or CSV) and drop the transactions into
    the employee's 'available expenses' inbox (SAP Concur style)."""
    await _guard(db, current.org_id)
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Statement too large (max 15 MB)")
    # Security gate before any parsing/OCR of the (untrusted) statement.
    try:
        filesec.check(file.filename or "statement", content, allowed=frozenset({"pdf", "csv"}))
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    try:
        result = await run_in_threadpool(bank_statement.parse, file.filename or "statement", content)
    except bank_statement.pdf_ocr.OcrUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"OCR unavailable: {exc}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    created: list[ExpenseTransaction] = []
    for t in result.transactions:
        if t.direction != "debit":
            continue  # only outflows are expensable
        txn = ExpenseTransaction(
            org_id=current.org_id, employee_id=current.id, txn_date=t.date,
            description=t.description[:300], merchant=None, amount=expenses.q(t.amount),
            currency="EUR", direction="debit", source="bank_statement", status="available",
        )
        db.add(txn)
        created.append(txn)
    await db.commit()
    for t in created:
        await db.refresh(t)

    return BankImportResult(
        method=result.method,
        imported=len(created),
        transactions=[ExpenseTransactionOut.model_validate(t) for t in created],
        warnings=result.warnings + [f"{len(created)} transaction(s) added to your available expenses; credits excluded."],
    )


@router.get("/transactions", response_model=list[ExpenseTransactionOut])
async def list_transactions(current: CurrentUser, db: DbSession, status_: str = Query(default="available", alias="status")):
    """The employee's 'available expenses' inbox."""
    await _guard(db, current.org_id)
    rows = await db.scalars(
        select(ExpenseTransaction)
        .where(ExpenseTransaction.employee_id == current.id, ExpenseTransaction.status == status_)
        .order_by(ExpenseTransaction.txn_date.desc())
    )
    return [ExpenseTransactionOut.model_validate(t) for t in rows]


@router.delete("/transactions/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transaction(txn_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    t = await db.scalar(
        select(ExpenseTransaction).where(ExpenseTransaction.id == txn_id, ExpenseTransaction.employee_id == current.id)
    )
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    if t.status != "available":
        raise HTTPException(status.HTTP_409_CONFLICT, "Transaction is already on a report")
    await db.delete(t)
    await db.commit()


@router.get("", response_model=ExpenseReportListOut)
async def list_reports(
    current: CurrentUser,
    db: DbSession,
    status_: str | None = Query(default=None, alias="status"),
    mine: bool = False,
    employee_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    await _guard(db, current.org_id)
    filters = [ExpenseReport.org_id == current.org_id]
    if not _is_manager(current) or mine:
        filters.append(ExpenseReport.employee_id == current.id)
    elif employee_id:
        filters.append(ExpenseReport.employee_id == employee_id)
    if status_:
        filters.append(ExpenseReport.status == status_)

    total = await db.scalar(select(func.count(ExpenseReport.id)).where(*filters))
    rows = await db.scalars(
        select(ExpenseReport).where(*filters).order_by(ExpenseReport.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return ExpenseReportListOut(items=[ExpenseReportOut.model_validate(r) for r in rows], total=total or 0)


@router.get("/summary", response_model=ExpenseSummary)
async def summary(current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    mine = ExpenseReport.employee_id == current.id

    my_draft = await db.scalar(select(func.count()).where(mine, ExpenseReport.status == "draft")) or 0
    my_submitted = await db.scalar(select(func.count()).where(mine, ExpenseReport.status == "submitted")) or 0
    my_reimbursable = await db.scalar(
        select(func.coalesce(func.sum(ExpenseReport.total), 0)).where(mine, ExpenseReport.status == "approved")
    ) or 0
    reclaimable_vat = await db.scalar(
        select(func.coalesce(func.sum(ExpenseReport.vat_total), 0)).where(mine)
    ) or 0
    pending = 0
    if _is_manager(current):
        pending = await db.scalar(
            select(func.count()).where(ExpenseReport.org_id == current.org_id, ExpenseReport.status == "submitted")
        ) or 0

    cat_rows = (await db.execute(
        select(ExpenseItem.category, func.coalesce(func.sum(ExpenseItem.amount), 0))
        .join(ExpenseReport, ExpenseReport.id == ExpenseItem.report_id)
        .where(ExpenseReport.employee_id == current.id)
        .group_by(ExpenseItem.category)
        .order_by(func.sum(ExpenseItem.amount).desc())
    )).all()

    return ExpenseSummary(
        my_draft=my_draft, my_submitted=my_submitted,
        my_reimbursable=my_reimbursable, reclaimable_vat=reclaimable_vat,
        pending_approvals=pending,
        by_category=[CategoryTotal(category=c, total=t) for c, t in cat_rows],
    )


@router.get("/{report_id}", response_model=ExpenseReportDetail)
async def get_report(report_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_view(r, current)
    return _detail(r)


@router.patch("/{report_id}", response_model=ExpenseReportDetail)
async def update_report(report_id: str, body: ExpenseReportUpdate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_owner_editable(r, current)

    if body.title is not None:
        r.title = body.title
    if body.currency is not None:
        r.currency = body.currency.upper()
    if body.note is not None:
        r.note = body.note
    if body.items is not None:
        r.items.clear()
        for i in body.items:
            r.items.append(expenses.item_from(i))
        r.total, r.vat_total = expenses.compute_totals(r.items)
    await db.commit()
    await db.refresh(r, attribute_names=["items"])
    return _detail(r)


@router.post("/{report_id}/submit", response_model=ExpenseReportDetail)
async def submit_report(report_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    if r.employee_id != current.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only submit your own reports")
    if r.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Report is not a draft")
    if not r.items:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add at least one expense before submitting")

    r.status = "submitted"
    r.submitted_at = expenses.now()
    if r.currency.upper() == "EUR":
        r.total_eur = r.total
    else:
        eur, _resolved = await fx.to_eur(db, r.total, r.currency, date.today())
        r.total_eur = eur
    await db.commit()
    await db.refresh(r, attribute_names=["items"])
    return _detail(r)


@router.post("/{report_id}/decision", response_model=ExpenseReportDetail)
async def decide(report_id: str, body: ExpenseDecision, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    if not _is_manager(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a manager (owner) can approve expenses")
    r = await _load(db, current.org_id, report_id)

    if body.action in ("approve", "reject") and r.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only submitted reports can be approved or rejected")
    if body.action == "reimburse" and r.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only approved reports can be reimbursed")

    r.status = {"approve": "approved", "reject": "rejected", "reimburse": "reimbursed"}[body.action]
    r.decided_at = expenses.now()
    r.decided_by = current.email
    r.decision_note = body.note
    await db.commit()
    await db.refresh(r, attribute_names=["items"])
    return _detail(r)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(report_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    if r.employee_id != current.id or r.status != "draft":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only your own draft reports can be deleted")
    await db.delete(r)
    await db.commit()


@router.post("/{report_id}/items/from-transaction", response_model=ExpenseReportDetail)
async def add_item_from_transaction(report_id: str, body: ItemFromTransaction, current: CurrentUser, db: DbSession):
    """Add an inbox transaction to a draft report as an expense entry."""
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_owner_editable(r, current)
    txns = await _load_available_txns(db, current.org_id, current.id, [body.transaction_id])
    txn = txns[0]
    item = _item_from_txn(txn, body.category, body.vat_amount)
    r.items.append(item)
    r.total, r.vat_total = expenses.compute_totals(r.items)
    await db.flush()
    txn.status = "assigned"
    txn.item_id = item.id
    await db.commit()
    await db.refresh(r, attribute_names=["items"])
    return _detail(r)


@router.get("/{report_id}/comments", response_model=list[ExpenseCommentOut])
async def list_comments(report_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_view(r, current)
    rows = await db.scalars(
        select(ExpenseComment).where(ExpenseComment.report_id == report_id).order_by(ExpenseComment.created_at)
    )
    return [ExpenseCommentOut.model_validate(c) for c in rows]


@router.post("/{report_id}/comments", response_model=ExpenseCommentOut, status_code=status.HTTP_201_CREATED)
async def add_comment(report_id: str, body: ExpenseCommentIn, current: CurrentUser, db: DbSession):
    """Comment on a report — the employee↔approver thread (either side can post)."""
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_view(r, current)
    c = ExpenseComment(
        org_id=current.org_id, report_id=report_id,
        author_id=current.id, author_name=current.name, body=body.body,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return ExpenseCommentOut.model_validate(c)


@router.post("/{report_id}/items/{item_id}/receipt", response_model=ExpenseReportDetail)
async def upload_receipt(report_id: str, item_id: str, current: CurrentUser, db: DbSession, file: UploadFile):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_owner_editable(r, current)
    item = next((i for i in r.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Receipt too large (max 5 MB)")
    # Security gate: validate the real type (PNG/JPEG/PDF) + malware-scan.
    try:
        kind = filesec.check(file.filename or "receipt", content, allowed=filesec.RECEIPT_KINDS)
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    item.receipt_mime = {"png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf"}[kind]
    item.receipt_data = content
    await db.commit()
    await db.refresh(r, attribute_names=["items"])
    return _detail(r)


@router.get("/{report_id}/items/{item_id}/receipt")
async def get_receipt(report_id: str, item_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_view(r, current)
    item = next((i for i in r.items if i.id == item_id), None)
    if item is None or not item.receipt_data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Receipt not found")
    # Serve inert: force download and stop MIME sniffing.
    return Response(
        content=item.receipt_data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{report_id}/pdf")
async def report_pdf(report_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_view(r, current)
    try:
        pdf = await run_in_threadpool(expenses.build_pdf, r)
    except expenses.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")
    fname = f"expense-{r.title[:40].strip().replace(' ', '_')}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
