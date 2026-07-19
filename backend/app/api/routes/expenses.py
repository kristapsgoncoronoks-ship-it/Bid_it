from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.user import User, UserRole
from app.schemas.expense import (
    BankStatementDraft,
    BankTransaction,
    CategoryTotal,
    ExpenseDecision,
    ExpenseItemIn,
    ExpenseItemOut,
    ExpenseReportCreate,
    ExpenseReportDetail,
    ExpenseReportListOut,
    ExpenseReportOut,
    ExpenseReportUpdate,
    ExpenseSummary,
)
from app.services import bank_statement, expenses, fx, modules

router = APIRouter(prefix="/expenses", tags=["expenses"])

_ALLOWED_RECEIPT = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF"}


async def _guard(db: DbSession, org_id: str):
    if not await modules.is_enabled(db, org_id, "expenses"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The employee expenses module is not activated.")


def _is_manager(user: User) -> bool:
    return user.role == UserRole.owner


def _detail(r: ExpenseReport) -> ExpenseReportDetail:
    d = ExpenseReportDetail.model_validate(r)
    d.items = [
        ExpenseItemOut(
            id=it.id, spend_date=it.spend_date, category=it.category, description=it.description,
            merchant=it.merchant, amount=it.amount, vat_amount=it.vat_amount,
            payment_method=it.payment_method, has_receipt=it.receipt_data is not None,
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


@router.post("", response_model=ExpenseReportDetail, status_code=status.HTTP_201_CREATED)
async def create_report(body: ExpenseReportCreate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    items = [expenses.item_from(i) for i in body.items]
    total, vat = expenses.compute_totals(items)
    report = ExpenseReport(
        org_id=current.org_id,
        employee_id=current.id,
        employee_name=current.name,
        title=body.title,
        currency=body.currency.upper(),
        note=body.note,
        total=total,
        vat_total=vat,
        items=items,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report, attribute_names=["items"])
    return _detail(report)


@router.post("/import/bank-statement", response_model=BankStatementDraft)
async def import_bank_statement(current: CurrentUser, db: DbSession, file: UploadFile):
    """Read transactions from a bank statement (PDF via OCR, or CSV) and return a
    DRAFT of expense items (the debits) to review — nothing is saved."""
    await _guard(db, current.org_id)
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Statement too large (max 15 MB)")
    try:
        result = bank_statement.parse(file.filename or "statement", content)
    except bank_statement.pdf_ocr.OcrUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"OCR unavailable: {exc}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    suggested = [
        ExpenseItemIn(
            spend_date=t.date, category="other", description=t.description[:300],
            merchant=None, amount=t.amount, vat_amount=0, payment_method="personal",
        )
        for t in result.transactions if t.direction == "debit"
    ]
    return BankStatementDraft(
        method=result.method,
        transactions=[BankTransaction(date=t.date, description=t.description, amount=t.amount,
                                      direction=t.direction, balance=t.balance) for t in result.transactions],
        suggested_items=suggested,
        warnings=result.warnings + ([f"{len(suggested)} debit(s) suggested as expenses; credits excluded."] if suggested else []),
    )


@router.get("", response_model=ExpenseReportListOut)
async def list_reports(
    current: CurrentUser,
    db: DbSession,
    status_: str | None = Query(default=None, alias="status"),
    mine: bool = False,
    employee_id: str | None = None,
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
    mime = file.content_type or "application/pdf"
    magic = _ALLOWED_RECEIPT.get(mime)
    if magic is None or not content.startswith(magic):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Receipt must be a PNG, JPEG, or PDF")
    item.receipt_mime = mime
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
    return Response(content=item.receipt_data, media_type=item.receipt_mime or "application/octet-stream")


@router.get("/{report_id}/pdf")
async def report_pdf(report_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    r = await _load(db, current.org_id, report_id)
    _require_view(r, current)
    try:
        pdf = expenses.build_pdf(r)
    except expenses.PdfUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"PDF generation unavailable: {e}")
    fname = f"expense-{r.title[:40].strip().replace(' ', '_')}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
