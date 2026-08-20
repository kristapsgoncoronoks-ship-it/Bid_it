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

import json as _json
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.costing import Project
from app.models.expense import ExpenseItem, ExpenseReport
from app.models.invoice import Invoice, LineItem
from app.models.invoice_project_split import InvoiceProjectSplit
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


async def _live_figures(db: AsyncSession, org_id: str, project_id: str) -> dict:
    """The seven headline figures, computed live, as decimal STRINGS (the same
    shape the frozen snapshot stores — one shape, no conversion drift)."""

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

    inv_costs = await _invoice_costs_for(db, org_id, project_id)

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
    return {
        "revenue": str(revenue),
        "credited": str(credited),
        "costs": str(costs),
        "invoice_costs": str(inv_costs),
        "expense_costs": str(expense_costs),
        "manual_costs": str(entry_costs),
        "profit": str(profit),
    }


async def pnl(db: AsyncSession, org_id: str, project_id: str) -> dict:
    """The project P&L, NET EUR. LIVE while the project is open; FROZEN once it
    closes with a snapshot (phase 2): the stored figure is what every screen
    shows, and anything that arrived after the close surfaces as a labelled
    `adjustments` delta per figure — displayed drift, never silent drift. The
    wire's `basis` field is the single source of which mode the numbers are in.

    Every aggregate re-asserts org_id even though the tenant guard also applies
    — belt-and-braces on the one figure a client quotes to their bank."""
    project = await _project(db, org_id, project_id)
    live = await _live_figures(db, org_id, project_id)

    adjustments: dict[str, str] = {}
    if project.status == "closed" and project.closed_pnl_json:
        frozen = _json.loads(project.closed_pnl_json)
        for key in _FROZEN_KEYS:
            delta = money.q2(Decimal(live.get(key, "0")) - Decimal(frozen.get(key, "0")))
            if delta != Decimal("0"):
                adjustments[key] = str(delta)
        figures = frozen
        basis = "net_eur_frozen"
    else:
        figures = live
        basis = "net_eur_live"

    revenue = Decimal(figures["revenue"])
    profit = Decimal(figures["profit"])
    margin = (
        str((profit / revenue * 100).quantize(Decimal("0.1"))) if revenue > Decimal("0") else None
    )

    return {
        "project_id": project.id,
        "code": project.code,
        "name": project.name,
        "status": project.status,
        **figures,
        "margin_pct": margin,
        # Stated by the server so no screen can misstate it — the same rule the
        # Trash and Archive screens follow for their windows.
        "basis": basis,
        "adjustments": adjustments,
        "pnl_frozen_at": project.pnl_frozen_at.isoformat() if project.pnl_frozen_at else None,
    }


# --------------------------------------------------------------------------- #
# Phase 2 — allocation: line > split > whole-invoice
# --------------------------------------------------------------------------- #


def _split_shares(remainder: Decimal, splits: list) -> dict[str, Decimal]:
    """Divide `remainder` by the split percentages. Each share is quantized;
    the rounding residue lands on the largest PERCENTAGE (deterministic
    project-id tiebreak), so the parts always sum to exactly the remainder —
    an allocation must never invent or lose a cent.

    Largest PERCENT, not largest rounded share: 33.33/33.33/33.34 of 10.00
    rounds every share to 3.33, so "largest share" degenerates to an arbitrary
    id tiebreak — the drift test caught exactly that."""
    if not splits or remainder <= Decimal("0"):
        return {}
    shares = {
        row.project_id: money.q2(remainder * Decimal(row.percent) / Decimal("100"))
        for row in splits
    }
    residue = money.q2(remainder - sum(shares.values(), Decimal("0")))
    if residue != Decimal("0"):
        largest = max(splits, key=lambda r: (Decimal(r.percent), r.project_id)).project_id
        shares[largest] = money.q2(shares[largest] + residue)
    return shares


async def _invoice_costs_for(db: AsyncSession, org_id: str, project_id: str) -> Decimal:
    """This project's share of received-invoice costs under the precedence rule:

        line-level project  >  percentage split  >  the invoice's project_id

    A tagged line claims its own amount. The invoice's REMAINDER — net base
    minus every project-tagged line — follows the splits when any exist, else
    the invoice's own project. The remainder is floored at zero: tagged lines
    exceeding the parsed net base is a document-quality problem that must not
    manufacture negative cost on some other project."""
    # Lines explicitly tagged to this project (their invoices are org-checked;
    # the tenant guard covers Invoice, and the join re-asserts org anyway).
    line_part = Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(LineItem.amount), 0))
            .join(Invoice, Invoice.id == LineItem.invoice_id)
            .where(Invoice.org_id == org_id, LineItem.project_id == project_id)
        )
        or 0
    )

    # Invoices where this project can hold a remainder share: via a split row,
    # or via the invoice's own project_id.
    candidate_ids = set(
        await db.scalars(
            select(InvoiceProjectSplit.invoice_id).where(
                InvoiceProjectSplit.org_id == org_id,
                InvoiceProjectSplit.project_id == project_id,
            )
        )
    ) | set(
        await db.scalars(
            select(Invoice.id).where(Invoice.org_id == org_id, Invoice.project_id == project_id)
        )
    )

    remainder_part = Decimal("0")
    for invoice_id in sorted(candidate_ids):
        inv = await db.scalar(
            select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id)
        )
        if inv is None:  # binned since the id was collected — not a live cost
            continue
        base = Decimal(inv.subtotal if inv.subtotal is not None else inv.total or 0)
        tagged = Decimal(
            await db.scalar(
                select(func.coalesce(func.sum(LineItem.amount), 0)).where(
                    LineItem.invoice_id == invoice_id, LineItem.project_id.is_not(None)
                )
            )
            or 0
        )
        remainder = money.q2(base - tagged)
        if remainder <= Decimal("0"):
            continue
        splits = list(
            await db.scalars(
                select(InvoiceProjectSplit).where(
                    InvoiceProjectSplit.org_id == org_id,
                    InvoiceProjectSplit.invoice_id == invoice_id,
                )
            )
        )
        if splits:
            remainder_part += _split_shares(remainder, splits).get(project_id, Decimal("0"))
        elif inv.project_id == project_id:
            remainder_part += remainder

    return money.q2(line_part + remainder_part)


async def set_allocation(
    db: AsyncSession,
    org_id: str,
    invoice_id: str,
    *,
    project_id: str | None,
    splits: list[tuple[str, Decimal]] | None,
    lines: dict[str, str | None] | None,
) -> dict:
    """Replace one invoice's allocation in a single call — all three levels at
    once, so the caller can never leave them contradicting each other.

    - `project_id`: the whole-invoice fallback (None clears it).
    - `splits`: (project_id, percent) rows replacing ALL existing splits; they
      must sum to exactly 100. An empty list clears the splits.
    - `lines`: {line_id: project_id | None} for the lines being (re)tagged;
      lines not named are left alone.

    Every referenced project must exist IN THIS ORG (opaque 404 otherwise) and
    every line must belong to this invoice. Does NOT commit — the route commits
    the change with its audit event."""
    inv = await db.scalar(select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id))
    if inv is None:
        raise NotFoundError("Invoice not found")

    referenced = {p for p, _ in (splits or [])}
    referenced |= {p for p in (lines or {}).values() if p}
    if project_id:
        referenced.add(project_id)
    for pid in referenced:
        await _project(db, org_id, pid)

    if splits is not None:
        if splits:
            total = sum((Decimal(pct) for _, pct in splits), Decimal("0"))
            if total != Decimal("100"):
                raise ProjectProfitError(f"Split percentages must sum to exactly 100 (got {total})")
            if len({p for p, _ in splits}) != len(splits):
                raise ProjectProfitError("Each project may appear in the split only once")
            if any(Decimal(pct) <= Decimal("0") for _, pct in splits):
                raise ProjectProfitError("Split percentages must be positive")
        await db.execute(
            sa_delete(InvoiceProjectSplit).where(
                InvoiceProjectSplit.org_id == org_id,
                InvoiceProjectSplit.invoice_id == invoice_id,
            )
        )
        for pid, pct in splits or []:
            db.add(
                InvoiceProjectSplit(
                    org_id=org_id,
                    invoice_id=invoice_id,
                    project_id=pid,
                    percent=money.q2(Decimal(pct)),
                )
            )

    if lines:
        line_rows = {
            li.id: li
            for li in await db.scalars(select(LineItem).where(LineItem.invoice_id == invoice_id))
        }
        for line_id, line_pid in lines.items():
            li = line_rows.get(line_id)
            if li is None:
                raise NotFoundError("Line not found on this invoice")
            li.project_id = line_pid

    inv.project_id = project_id
    return {
        "invoice_id": invoice_id,
        "project_id": project_id,
        "splits": [(p, str(money.q2(Decimal(pct)))) for p, pct in (splits or [])],
        "lines_tagged": len(lines or {}),
    }


# --------------------------------------------------------------------------- #
# Phase 2 — the freeze at close
# --------------------------------------------------------------------------- #

_FROZEN_KEYS = (
    "revenue",
    "credited",
    "costs",
    "invoice_costs",
    "expense_costs",
    "manual_costs",
    "profit",
)


async def snapshot_close(db: AsyncSession, org_id: str, project_id: str) -> dict:
    """Compute and store the P&L at the moment of closing. Called by
    `costing.update` inside the SAME transaction as the status change, so the
    close and its frozen figure commit together or not at all. Does NOT commit."""
    figures = await _live_figures(db, org_id, project_id)
    project = await _project(db, org_id, project_id)
    project.closed_pnl_json = _json.dumps(figures)
    project.pnl_frozen_at = datetime.now(UTC)
    return figures


async def clear_snapshot(db: AsyncSession, org_id: str, project_id: str) -> None:
    """Reopening discards the snapshot — a reopened project is live again by
    definition, and the caller (costing.update) audits the reopen. No commit."""
    project = await _project(db, org_id, project_id)
    project.closed_pnl_json = None
    project.pnl_frozen_at = None


async def pnl_summary(db: AsyncSession, org_id: str) -> list[dict]:
    """Every project's headline figures for the list screen — the question the
    list answers is "which contracts lose money", so profit and margin ride
    along with code/name/status. Frozen figures for closed projects with a
    snapshot, live otherwise."""
    projects = list(
        await db.scalars(select(Project).where(Project.org_id == org_id).order_by(Project.code))
    )
    out = []
    for pr in projects:
        row = await pnl(db, org_id, pr.id)
        out.append(row)
    return out


async def get_allocation(db: AsyncSession, org_id: str, invoice_id: str) -> dict:
    """One invoice's current allocation, in exactly the shape `set_allocation`
    accepts — read it, edit it, PUT it back, no translation in between."""
    inv = await db.scalar(select(Invoice).where(Invoice.org_id == org_id, Invoice.id == invoice_id))
    if inv is None:
        raise NotFoundError("Invoice not found")
    splits = list(
        await db.scalars(
            select(InvoiceProjectSplit)
            .where(
                InvoiceProjectSplit.org_id == org_id,
                InvoiceProjectSplit.invoice_id == invoice_id,
            )
            .order_by(InvoiceProjectSplit.percent.desc(), InvoiceProjectSplit.project_id)
        )
    )
    tagged = list(
        await db.execute(
            select(LineItem.id, LineItem.project_id).where(
                LineItem.invoice_id == invoice_id, LineItem.project_id.is_not(None)
            )
        )
    )
    return {
        "invoice_id": invoice_id,
        "project_id": inv.project_id,
        "splits": [{"project_id": s.project_id, "percent": str(s.percent)} for s in splits],
        "lines": {row.id: row.project_id for row in tagged},
    }
