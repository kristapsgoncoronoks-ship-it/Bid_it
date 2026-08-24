"""Next actions (WO-C): the day's work, DERIVED from records that already
exist — never a parallel task list that can rot.

Research contract (docs/design/tasks-module-research.md):
- every item SELF-COMPLETES when its underlying event happens (an offer
  nudge dies on accept/reject/supersede; a chase row dies on payment);
- a dismissal is permanent per item (and costs nothing if the item would
  have resolved anyway);
- deadline occurrences are computed from templates and completed per
  period — nothing materializes into rows, nothing accumulates into a
  guilt backlog.

Generators, v1 (the researched high-engagement set, nothing more):
  offer_followup   — offers sitting in `sent` ≥ N days (default 3)
  invoice_chase    — issued invoices past due with money outstanding
  capture_backlog  — parsed uploads waiting for human review (aggregate)
  deadline         — org deadline templates inside their lead window
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_run import ExtractionRun
from app.models.issued_invoice import IssuedInvoice
from app.models.next_action import DEADLINE_CADENCES, ActionDismissal, OrgDeadline
from app.models.project_offer import ProjectOffer

OFFER_FOLLOWUP_DAYS = 3

DISMISSIBLE_KINDS = ("offer_followup", "invoice_chase", "acceptance_suggest", "final_invoice")


class NextActionsError(Exception):
    pass


class NotFoundError(NextActionsError):
    pass


class InvalidError(NextActionsError):
    pass


@dataclass
class Action:
    kind: str
    ref_id: str
    title: str
    detail: str
    link: str
    age_days: int | None = None
    due_date: str | None = None
    dismissible: bool = False


def _period_for(cadence: str, today: date) -> str:
    if cadence == "monthly":
        return f"{today.year}-{today.month:02d}"
    if cadence == "quarterly":
        return f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    return str(today.year)


def _due_date_for(row: OrgDeadline, today: date) -> date:
    """The CURRENT period's due date (due_day in the period's last month)."""
    if row.cadence == "monthly":
        return date(today.year, today.month, row.due_day)
    if row.cadence == "quarterly":
        last_month = ((today.month - 1) // 3 + 1) * 3
        return date(today.year, last_month, row.due_day)
    return date(today.year, 12, row.due_day)


async def _dismissed(db: AsyncSession, org_id: str) -> set[tuple[str, str]]:
    rows = await db.scalars(select(ActionDismissal).where(ActionDismissal.org_id == org_id))
    return {(d.kind, d.ref_id) for d in rows}


async def list_actions(
    db: AsyncSession, org_id: str, *, now: datetime | None = None
) -> list[Action]:
    now = now or datetime.now(UTC)
    today = now.date()
    dismissed = await _dismissed(db, org_id)
    out: list[Action] = []

    # --- offers sitting in `sent` too long -------------------------------- #
    cutoff = now - timedelta(days=OFFER_FOLLOWUP_DAYS)
    offers = await db.scalars(
        select(ProjectOffer)
        .where(ProjectOffer.org_id == org_id, ProjectOffer.status == "sent")
        .order_by(ProjectOffer.updated_at)
    )
    for o in offers:
        updated = o.updated_at if o.updated_at.tzinfo else o.updated_at.replace(tzinfo=UTC)
        if updated > cutoff or ("offer_followup", o.id) in dismissed:
            continue
        age = (now - updated).days
        out.append(
            Action(
                kind="offer_followup",
                ref_id=o.id,
                title=f"Follow up on offer {o.number}",
                detail=f"Sent {age} days ago with no answer — {o.title or 'offer'} v{o.version}.",
                link=f"/projects/{o.project_id}",
                age_days=age,
                dismissible=True,
            )
        )

    # --- overdue issued invoices ------------------------------------------ #
    invoices = await db.scalars(
        select(IssuedInvoice)
        .where(
            IssuedInvoice.org_id == org_id,
            IssuedInvoice.lifecycle == "issued",
            IssuedInvoice.due_date.is_not(None),
            IssuedInvoice.due_date < today,
        )
        .order_by(IssuedInvoice.due_date)
    )
    for inv in invoices:
        outstanding = (inv.total or Decimal("0")) - inv.credited_total - inv.amount_paid
        if inv.due_date is None or outstanding <= 0 or ("invoice_chase", inv.id) in dismissed:
            continue
        overdue = (today - inv.due_date).days
        out.append(
            Action(
                kind="invoice_chase",
                ref_id=inv.id,
                title=f"Chase invoice {inv.number}",
                detail=f"{outstanding} {inv.currency} outstanding, {overdue} days overdue."
                " Reminders follow your dunning settings.",
                link="/issue/reports",
                age_days=overdue,
                dismissible=True,
            )
        )

    # --- captures waiting for a human ------------------------------------- #
    backlog = (
        await db.scalar(
            select(func.count(ExtractionRun.id)).where(
                ExtractionRun.org_id == org_id, ExtractionRun.status == "parsed"
            )
        )
    ) or 0
    if backlog:
        out.append(
            Action(
                kind="capture_backlog",
                ref_id="captures",
                title=f"{backlog} captured document{'s' if backlog != 1 else ''} to review",
                detail="Uploads parsed and waiting for confirmation.",
                link="/captures",
            )
        )

    # --- WO-D lifecycle nudges: acceptance, then the final invoice -------- #
    # Suggest recording acceptance when every assignment on an active project
    # is finished (≥1 done, none still planned/confirmed) and no acceptance is
    # recorded; suggest the final invoice when an ACCEPTED project still has
    # contracted money uninvoiced. Both self-complete: recording acceptance
    # kills the first, issuing the remainder kills the second.
    from app.models.costing import Project
    from app.models.project_assignment import ProjectAssignment
    from app.models.project_offer import InvoicingPlanRow

    active_projects = list(
        await db.scalars(
            select(Project).where(Project.org_id == org_id, Project.status == "active")
        )
    )
    for p in active_projects:
        if p.accepted_at is None:
            statuses = set(
                (
                    await db.scalars(
                        select(ProjectAssignment.status).where(
                            ProjectAssignment.org_id == org_id,
                            ProjectAssignment.project_id == p.id,
                        )
                    )
                ).all()
            )
            if (
                "done" in statuses
                and not statuses & {"planned", "confirmed"}
                and ("acceptance_suggest", p.id) not in dismissed
            ):
                out.append(
                    Action(
                        kind="acceptance_suggest",
                        ref_id=p.id,
                        title=f"Record acceptance for {p.code}",
                        detail="All scheduled work is done and no acceptance is"
                        " recorded — a signed acceptance makes the final"
                        " invoice unarguable.",
                        link=f"/projects/{p.id}",
                        dismissible=True,
                    )
                )
        else:
            has_plan = await db.scalar(
                select(func.count(InvoicingPlanRow.id)).where(
                    InvoicingPlanRow.org_id == org_id, InvoicingPlanRow.project_id == p.id
                )
            )
            if has_plan and ("final_invoice", p.id) not in dismissed:
                from app.services import project_offers

                tracking = await project_offers.plan_tracking(db, org_id, p.id)
                remaining = Decimal(tracking["remaining"])
                if remaining > 0:
                    out.append(
                        Action(
                            kind="final_invoice",
                            ref_id=p.id,
                            title=f"Issue the final invoice for {p.code}",
                            detail=f"Accepted, with {tracking['remaining']} of the"
                            " contracted sum still uninvoiced.",
                            link=f"/projects/{p.id}",
                            dismissible=True,
                        )
                    )

    # --- recurring deadlines in their lead window ------------------------- #
    deadlines = await db.scalars(
        select(OrgDeadline).where(OrgDeadline.org_id == org_id).order_by(OrgDeadline.name)
    )
    for d in deadlines:
        period = _period_for(d.cadence, today)
        if d.last_done_period == period:
            continue
        due = _due_date_for(d, today)
        if today < due - timedelta(days=d.lead_days):
            continue
        overdue = (today - due).days
        out.append(
            Action(
                kind="deadline",
                ref_id=d.id,
                title=d.name,
                detail=(
                    f"Due {due.isoformat()}"
                    + (f" — {overdue} days overdue." if overdue > 0 else ".")
                    + " Mark done when handled."
                ),
                link="/",
                due_date=due.isoformat(),
            )
        )

    return out


async def dismiss(
    db: AsyncSession, org_id: str, *, kind: str, ref_id: str, dismissed_by: str
) -> None:
    if kind not in DISMISSIBLE_KINDS:
        raise InvalidError(f"{kind!r} items resolve by doing the work, not by dismissing")
    exists = await db.scalar(
        select(ActionDismissal).where(
            ActionDismissal.org_id == org_id,
            ActionDismissal.kind == kind,
            ActionDismissal.ref_id == ref_id,
        )
    )
    if exists is None:
        db.add(ActionDismissal(org_id=org_id, kind=kind, ref_id=ref_id, dismissed_by=dismissed_by))
        await db.flush()


# ------------------------------ deadlines CRUD ----------------------------- #


async def list_deadlines(db: AsyncSession, org_id: str) -> list[OrgDeadline]:
    return list(
        await db.scalars(
            select(OrgDeadline).where(OrgDeadline.org_id == org_id).order_by(OrgDeadline.name)
        )
    )


async def create_deadline(
    db: AsyncSession,
    org_id: str,
    *,
    name: str,
    cadence: str,
    due_day: int,
    lead_days: int,
    created_by: str,
) -> OrgDeadline:
    if cadence not in DEADLINE_CADENCES:
        raise InvalidError(f"unknown cadence {cadence!r}")
    if not 1 <= due_day <= 28:
        raise InvalidError("due_day must be 1–28")
    row = OrgDeadline(
        org_id=org_id,
        name=name,
        cadence=cadence,
        due_day=due_day,
        lead_days=lead_days,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def _deadline_or_404(db: AsyncSession, org_id: str, deadline_id: str) -> OrgDeadline:
    row = await db.scalar(
        select(OrgDeadline).where(OrgDeadline.org_id == org_id, OrgDeadline.id == deadline_id)
    )
    if row is None:
        raise NotFoundError("deadline not found")
    return row


async def complete_deadline(
    db: AsyncSession, org_id: str, deadline_id: str, *, today: date | None = None
) -> OrgDeadline:
    """Stamp the CURRENT period done — the item disappears until next period."""
    row = await _deadline_or_404(db, org_id, deadline_id)
    row.last_done_period = _period_for(row.cadence, today or datetime.now(UTC).date())
    await db.flush()
    return row


async def delete_deadline(db: AsyncSession, org_id: str, deadline_id: str) -> OrgDeadline:
    row = await _deadline_or_404(db, org_id, deadline_id)
    await db.delete(row)
    await db.flush()
    return row
