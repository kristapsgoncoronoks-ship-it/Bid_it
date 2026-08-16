"""Project profitability — phase 1 of `docs/design/project-profitability.md`.

The loop this serves, for EVERY kind of customer (industry-neutral by owner
requirement): open a project for a won contract, attach the contract, issue
sales invoices under it, allocate supplier/subcontractor invoices and expense
reports to it, add manual cost lines, and read revenue − costs.

WHAT COUNTS, AND WHY — the P&L's basis, stated once here and echoed on the
wire so a screen can never misstate it:

- **Revenue** = issued invoices on the project, NET (subtotal, before VAT),
  minus credit notes the same way. Drafts and cancelled documents are excluded
  (neither is money); approved-but-unissued IS included — the work is sold
  even if the paper is not yet numbered.
- **Invoice costs** = received invoices allocated to the project, NET where
  the parse captured a subtotal, gross total as the fallback (an OCR'd receipt
  with no tax split is better counted gross than dropped). Whole-invoice
  allocation in phase 1; line-level + % splits are phase 2.
- **Expense costs** = expense items on the project from reports that are
  approved or beyond — a rejected or still-editable draft report is not yet a
  cost. NET of VAT (amount − vat_amount): VAT is a pass-through, and where it
  is NOT recoverable the phase-2 recovered-VAT overlay is the honest place to
  show that, not a silently grossed-up cost.
- **Manual cost lines** = as entered (EUR).

The recycle bin composes correctly for free: a binned invoice vanishes from
every ordinary SELECT (central soft-delete guard), so it leaves the live P&L
and returns on restore — no special handling here, which is the point of a
central guard.

Phase 2 adds the close-time FREEZE. Until then the figure is live and the
screen says so.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.costing import Project
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.invoice import Invoice
from app.models.issued_invoice import IssuedInvoice
from app.models.project_link import (
    COST_CATEGORIES,
    DOCUMENT_KINDS,
    ProjectCostEntry,
    ProjectDocument,
)
from app.services import documents

PROJECT_DOCS = "project-docs"
"""Object-store prefix for project attachments (contracts). Its own class, like
receipts/logos/email-attachments, so the integrity sweep and any future
retention rule can address these bytes distinctly."""

# Expense-report states whose items count as project cost. A draft or submitted
# report can still be edited or rejected; approval is the moment the org accepts
# the money left.
_EXPENSE_COST_STATES = ("approved", "processing", "reimbursed")

# Issued lifecycles excluded from revenue: a draft is not a sale yet, a
# cancelled document never was.
_NON_REVENUE_LIFECYCLES = ("draft", "cancelled")


class ProjectProfitError(Exception):
    """Business refusal; routes map it to 4xx."""


class NotFoundError(ProjectProfitError):
    """Unknown OR cross-tenant — indistinguishable by design (§4.4)."""


async def _project(db: AsyncSession, org_id: str, project_id: str) -> Project:
    row = await db.scalar(select(Project).where(Project.org_id == org_id, Project.id == project_id))
    if row is None:
        raise NotFoundError("Project not found")
    return row


# --------------------------------------------------------------------------- #
# Manual cost lines
# --------------------------------------------------------------------------- #


async def add_cost_entry(
    db: AsyncSession,
    org_id: str,
    project_id: str,
    *,
    label: str,
    category: str,
    amount: Decimal,
    entry_date: date | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> ProjectCostEntry:
    """Book one uninvoiced cost onto the project. Does NOT commit (the route
    commits the entry with its audit event — engineering-rules §3).

    Negative amounts are allowed on purpose: a correction to an earlier line is
    itself a cost line, and forcing deletions instead would erase the history a
    P&L reader wants. Zero is refused — it can only be a mistake."""
    await _project(db, org_id, project_id)
    if category not in COST_CATEGORIES:
        raise ProjectProfitError(f"Unknown cost category '{category}'")
    quantized = money.q2(amount)
    if quantized == Decimal("0"):
        raise ProjectProfitError("A cost entry needs a non-zero amount")
    entry = ProjectCostEntry(
        org_id=org_id,
        project_id=project_id,
        label=label.strip(),
        category=category,
        amount=quantized,
        currency="EUR",
        entry_date=entry_date,
        note=note,
        created_by=created_by,
    )
    if not entry.label:
        raise ProjectProfitError("A cost entry needs a label")
    db.add(entry)
    return entry


async def list_cost_entries(
    db: AsyncSession, org_id: str, project_id: str
) -> list[ProjectCostEntry]:
    await _project(db, org_id, project_id)
    return list(
        await db.scalars(
            select(ProjectCostEntry)
            .where(ProjectCostEntry.org_id == org_id, ProjectCostEntry.project_id == project_id)
            .order_by(ProjectCostEntry.created_at.desc(), ProjectCostEntry.id)
        )
    )


async def delete_cost_entry(
    db: AsyncSession, org_id: str, project_id: str, entry_id: str
) -> ProjectCostEntry:
    """Remove a cost line. Returns the deleted row so the route can audit WHAT
    was removed, not just that something was. Does NOT commit."""
    entry = await db.scalar(
        select(ProjectCostEntry).where(
            ProjectCostEntry.org_id == org_id,
            ProjectCostEntry.project_id == project_id,
            ProjectCostEntry.id == entry_id,
        )
    )
    if entry is None:
        raise NotFoundError("Cost entry not found")
    await db.delete(entry)
    return entry


# --------------------------------------------------------------------------- #
# The contract (and other project documents)
# --------------------------------------------------------------------------- #


async def attach_document(
    db: AsyncSession,
    org_id: str,
    project_id: str,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    kind: str = "contract",
    uploaded_by: str | None = None,
) -> ProjectDocument:
    """Store the bytes and link them to the project. Does NOT commit."""
    await _project(db, org_id, project_id)
    if kind not in DOCUMENT_KINDS:
        raise ProjectProfitError(f"Unknown document kind '{kind}'")
    if not data:
        raise ProjectProfitError("The uploaded file is empty")
    sha, _size = await documents.store(
        PROJECT_DOCS, org_id, data, content_type, db=db, filename=filename, uploaded_by=uploaded_by
    )
    row = ProjectDocument(
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        sha256=sha,
        filename=filename[:255],
        content_type=content_type,
        uploaded_by=uploaded_by,
    )
    db.add(row)
    return row


async def list_documents(db: AsyncSession, org_id: str, project_id: str) -> list[ProjectDocument]:
    await _project(db, org_id, project_id)
    return list(
        await db.scalars(
            select(ProjectDocument)
            .where(ProjectDocument.org_id == org_id, ProjectDocument.project_id == project_id)
            .order_by(ProjectDocument.created_at.desc(), ProjectDocument.id)
        )
    )


async def load_document(
    db: AsyncSession, org_id: str, project_id: str, document_id: str
) -> tuple[ProjectDocument, bytes]:
    row = await db.scalar(
        select(ProjectDocument).where(
            ProjectDocument.org_id == org_id,
            ProjectDocument.project_id == project_id,
            ProjectDocument.id == document_id,
        )
    )
    if row is None:
        raise NotFoundError("Document not found")
    data = await documents.load(PROJECT_DOCS, org_id, row.sha256)
    if data is None:
        raise NotFoundError("Stored document missing")
    return row, data


# --------------------------------------------------------------------------- #
# The P&L
# --------------------------------------------------------------------------- #


async def pnl(db: AsyncSession, org_id: str, project_id: str) -> dict:
    """The live project P&L: revenue − costs, NET EUR basis (see module
    docstring for exactly what counts). Every aggregate re-asserts org_id even
    though the tenant guard also applies — belt-and-braces on the one figure a
    client will quote to their customer or bank."""
    project = await _project(db, org_id, project_id)

    async def _issued_sum(credit: bool) -> Decimal:
        val = await db.scalar(
            select(func.coalesce(func.sum(IssuedInvoice.subtotal), 0)).where(
                IssuedInvoice.org_id == org_id,
                IssuedInvoice.project_id == project_id,
                IssuedInvoice.lifecycle.not_in(_NON_REVENUE_LIFECYCLES),
                (IssuedInvoice.doc_type == "credit_note")
                if credit
                else (IssuedInvoice.doc_type != "credit_note"),
            )
        )
        return money.q2(Decimal(val or 0))

    invoiced = await _issued_sum(credit=False)
    credited = await _issued_sum(credit=True)
    revenue = money.q2(invoiced - credited)

    inv_costs = money.q2(
        Decimal(
            await db.scalar(
                select(
                    func.coalesce(func.sum(func.coalesce(Invoice.subtotal, Invoice.total)), 0)
                ).where(Invoice.org_id == org_id, Invoice.project_id == project_id)
            )
            or 0
        )
    )

    expense_costs = money.q2(
        Decimal(
            await db.scalar(
                select(func.coalesce(func.sum(ExpenseItem.amount - ExpenseItem.vat_amount), 0))
                .join(ExpenseReport, ExpenseReport.id == ExpenseItem.report_id)
                .where(
                    ExpenseReport.org_id == org_id,
                    ExpenseItem.project_id == project_id,
                    ExpenseReport.status.in_(_EXPENSE_COST_STATES),
                )
            )
            or 0
        )
    )

    entry_costs = money.q2(
        Decimal(
            await db.scalar(
                select(func.coalesce(func.sum(ProjectCostEntry.amount), 0)).where(
                    ProjectCostEntry.org_id == org_id,
                    ProjectCostEntry.project_id == project_id,
                )
            )
            or 0
        )
    )

    costs = money.q2(inv_costs + expense_costs + entry_costs)
    profit = money.q2(revenue - costs)
    margin = (
        str((profit / revenue * 100).quantize(Decimal("0.1"))) if revenue > Decimal("0") else None
    )

    return {
        "project_id": project.id,
        "code": project.code,
        "name": project.name,
        "status": project.status,
        "revenue": str(revenue),
        "credited": str(credited),
        "costs": str(costs),
        "invoice_costs": str(inv_costs),
        "expense_costs": str(expense_costs),
        "manual_costs": str(entry_costs),
        "profit": str(profit),
        "margin_pct": margin,
        # Stated by the server so no screen can misstate it — the same rule the
        # Trash and Archive screens follow for their windows.
        "basis": "net_eur_live",
    }
