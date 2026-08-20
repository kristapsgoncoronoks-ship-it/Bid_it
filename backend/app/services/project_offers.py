"""Offers/estimates + the invoicing plan — phase 4 of the project lifecycle.

`docs/design/project-profitability.md` §5a. The three owner decisions this
module encodes (2026-08-16):

- **Offer numbering is set by the client** — `organizations.offer_prefix`
  chooses the scheme; the platform enforces exactly ONE rule regardless:
  a number is unique within the org. No invoice-grade locking — an offer is
  not a legal series, and the unique constraint is the backstop if two race.
- **Offers are versionable.** A revision is a NEW row (same number,
  version+1); the prior version flips to `superseded`. History is part of the
  record: what was offered, in what order, survives every edit.
- **An accepted offer seeds the invoicing plan** — the contracted schedule the
  P&L's revenue is then tracked against. It seeds only an EMPTY plan: a plan
  someone already shaped by hand is theirs, and acceptance must not silently
  rewrite it.

The estimate is also the other half of the single most instructive number a
project business can see: `pnl` gains `estimated_revenue` (the latest accepted
offer's total) so estimated-vs-actual margin is readable the moment the work
starts landing.

Industry-neutral throughout (owner requirement). No commits — routes commit
each mutation with its audit event.
"""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.organization import Organization
from app.models.project_offer import OFFER_STATUSES, InvoicingPlanRow, ProjectOffer
from app.services.project_profit import NotFoundError, ProjectProfitError, _live_figures, _project

DEFAULT_OFFER_PREFIX = "OFF-"

# sent→draft is deliberate: pulling a sent offer back to editable is a real
# workflow (caught a mistake before the customer replied). accepted/rejected/
# superseded are terminal — revise instead, so history survives.
_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"sent", "accepted", "rejected"},
    "sent": {"accepted", "rejected", "draft"},
    "accepted": set(),
    "rejected": set(),
    "superseded": set(),
}


def _lines_total(lines: list[dict]) -> Decimal:
    total = Decimal("0")
    for li in lines:
        try:
            amount = Decimal(str(li.get("amount", "0")))
        except ArithmeticError as exc:  # pragma: no cover - Decimal parse
            raise ProjectProfitError(f"Unreadable line amount: {li.get('amount')!r}") from exc
        total += amount
    return money.q2(total)


async def _next_number(db: AsyncSession, org_id: str) -> str:
    """The client's prefix + the next free integer. The unique constraint is
    the correctness backstop; this is only the convenience that picks a free
    slot — an offer number is a reference, not a gap-free legal series."""
    org = await db.get(Organization, org_id)
    prefix = (org.offer_prefix if org and org.offer_prefix else DEFAULT_OFFER_PREFIX).strip()
    count = await db.scalar(
        select(func.count(func.distinct(ProjectOffer.number))).where(ProjectOffer.org_id == org_id)
    )
    n = int(count or 0) + 1
    while await db.scalar(
        select(ProjectOffer.id)
        .where(ProjectOffer.org_id == org_id, ProjectOffer.number == f"{prefix}{n}")
        .limit(1)
    ):
        n += 1
    return f"{prefix}{n}"


async def create_offer(
    db: AsyncSession,
    org_id: str,
    project_id: str,
    *,
    title: str | None,
    lines: list[dict],
    note: str | None = None,
    created_by: str | None = None,
) -> ProjectOffer:
    await _project(db, org_id, project_id)
    if not lines:
        raise ProjectProfitError("An offer needs at least one line")
    total = _lines_total(lines)
    offer = ProjectOffer(
        org_id=org_id,
        project_id=project_id,
        number=await _next_number(db, org_id),
        version=1,
        status="draft",
        title=title,
        total=total,
        line_items_json=json.dumps(lines, default=str),
        note=note,
        created_by=created_by,
    )
    db.add(offer)
    return offer


async def revise_offer(
    db: AsyncSession,
    org_id: str,
    offer_id: str,
    *,
    title: str | None,
    lines: list[dict],
    note: str | None = None,
    created_by: str | None = None,
) -> ProjectOffer:
    """A revision, not an edit: a new version-row under the same number; the
    prior version flips to superseded. A terminal offer (accepted/rejected)
    revises the same way — the new version starts the decision over."""
    prior = await _offer(db, org_id, offer_id)
    if prior.status == "superseded":
        raise ProjectProfitError("Revise the LATEST version of this offer, not a superseded one")
    if not lines:
        raise ProjectProfitError("An offer needs at least one line")
    revision = ProjectOffer(
        org_id=org_id,
        project_id=prior.project_id,
        number=prior.number,
        version=prior.version + 1,
        status="draft",
        title=title if title is not None else prior.title,
        total=_lines_total(lines),
        line_items_json=json.dumps(lines, default=str),
        note=note,
        created_by=created_by,
    )
    prior.status = "superseded"
    db.add(revision)
    return revision


async def _offer(db: AsyncSession, org_id: str, offer_id: str) -> ProjectOffer:
    row = await db.scalar(
        select(ProjectOffer).where(ProjectOffer.org_id == org_id, ProjectOffer.id == offer_id)
    )
    if row is None:
        raise NotFoundError("Offer not found")
    return row


async def transition_offer(
    db: AsyncSession, org_id: str, offer_id: str, status: str
) -> tuple[ProjectOffer, int]:
    """Move an offer through its lifecycle. Returns (offer, plan_rows_seeded).

    ACCEPTANCE SEEDS THE PLAN — but only an empty one: each offer line becomes
    one instalment row, or the whole total becomes a single row when the lines
    don't carry amounts. A plan someone already shaped by hand is theirs;
    acceptance never rewrites it."""
    if status not in OFFER_STATUSES:
        raise ProjectProfitError(f"Unknown offer status '{status}'")
    offer = await _offer(db, org_id, offer_id)
    allowed = _TRANSITIONS.get(offer.status, set())
    if status not in allowed:
        raise ProjectProfitError(f"Cannot move an offer from '{offer.status}' to '{status}'")
    offer.status = status

    seeded = 0
    if status == "accepted":
        existing = await db.scalar(
            select(func.count(InvoicingPlanRow.id)).where(
                InvoicingPlanRow.org_id == org_id,
                InvoicingPlanRow.project_id == offer.project_id,
            )
        )
        if not existing:
            lines = json.loads(offer.line_items_json or "[]")
            rows = [
                (
                    str(li.get("description") or f"Instalment {i + 1}"),
                    money.q2(Decimal(str(li["amount"]))),
                )
                for i, li in enumerate(lines)
                if li.get("amount") not in (None, "", "0")
            ]
            if not rows:
                rows = [(f"Per offer {offer.number}", money.q2(Decimal(offer.total)))]
            for position, (label, amount) in enumerate(rows):
                db.add(
                    InvoicingPlanRow(
                        org_id=org_id,
                        project_id=offer.project_id,
                        label=label[:200],
                        amount=amount,
                        position=position,
                    )
                )
                seeded += 1
    return offer, seeded


async def list_offers(db: AsyncSession, org_id: str, project_id: str) -> list[ProjectOffer]:
    await _project(db, org_id, project_id)
    return list(
        await db.scalars(
            select(ProjectOffer)
            .where(ProjectOffer.org_id == org_id, ProjectOffer.project_id == project_id)
            .order_by(ProjectOffer.number, ProjectOffer.version.desc())
        )
    )


async def estimated_revenue(db: AsyncSession, org_id: str, project_id: str) -> Decimal | None:
    """The latest ACCEPTED offer's total — the estimate half of
    estimated-vs-actual margin. None when nothing was ever accepted."""
    val = await db.scalar(
        select(ProjectOffer.total)
        .where(
            ProjectOffer.org_id == org_id,
            ProjectOffer.project_id == project_id,
            ProjectOffer.status == "accepted",
        )
        .order_by(ProjectOffer.version.desc(), ProjectOffer.created_at.desc())
        .limit(1)
    )
    return money.q2(Decimal(val)) if val is not None else None


# --------------------------------------------------------------------------- #
# The invoicing plan
# --------------------------------------------------------------------------- #


async def set_plan(
    db: AsyncSession, org_id: str, project_id: str, rows: list[tuple[str, Decimal]]
) -> list[InvoicingPlanRow]:
    """Replace the plan wholesale (PUT semantics — the caller's list is the
    whole truth). An empty list clears it."""
    from sqlalchemy import delete as sa_delete

    await _project(db, org_id, project_id)
    for label, amount in rows:
        if not label.strip():
            raise ProjectProfitError("Every instalment needs a label")
        if money.q2(Decimal(amount)) <= Decimal("0"):
            raise ProjectProfitError("Every instalment needs a positive amount")
    await db.execute(
        sa_delete(InvoicingPlanRow).where(
            InvoicingPlanRow.org_id == org_id, InvoicingPlanRow.project_id == project_id
        )
    )
    out = []
    for position, (label, amount) in enumerate(rows):
        row = InvoicingPlanRow(
            org_id=org_id,
            project_id=project_id,
            label=label.strip()[:200],
            amount=money.q2(Decimal(amount)),
            position=position,
        )
        db.add(row)
        out.append(row)
    return out


async def plan_tracking(db: AsyncSession, org_id: str, project_id: str) -> dict:
    """The plan vs. reality: contracted total, issued so far (the P&L's revenue
    figure — same basis, so the two screens can never disagree), and what
    remains to invoice. The remainder is the live receivable this exists to
    surface — and, when the final-invoicing stage lands (phase 5), the starting
    point the owner decided is ADJUSTABLE, never a locked figure."""
    await _project(db, org_id, project_id)
    rows = list(
        await db.scalars(
            select(InvoicingPlanRow)
            .where(InvoicingPlanRow.org_id == org_id, InvoicingPlanRow.project_id == project_id)
            .order_by(InvoicingPlanRow.position, InvoicingPlanRow.id)
        )
    )
    contracted = money.q2(sum((Decimal(r.amount) for r in rows), Decimal("0")))
    live = await _live_figures(db, org_id, project_id)
    issued = Decimal(live["revenue"])
    return {
        "project_id": project_id,
        "rows": [
            {"id": r.id, "label": r.label, "amount": str(r.amount), "position": r.position}
            for r in rows
        ],
        "contracted_total": str(contracted),
        "issued_total": str(issued),
        "remaining": str(money.q2(contracted - issued)),
    }
