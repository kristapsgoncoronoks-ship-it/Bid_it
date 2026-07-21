from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.recurring import (
    GenerateResult,
    RecurringCreate,
    RecurringOut,
    RecurringUpdate,
)
from app.services import audit, issuer, modules, partners, recurring

# Mounted under the issuing module. Included BEFORE the `issued` router so
# `/issued/recurring*` resolves here and never hits `/issued/{invoice_id}`.
router = APIRouter(prefix="/issued/recurring", tags=["issuing"])


async def _guard(db: DbSession, org_id: str):
    await modules.require_enabled(db, org_id, "issuing")
    profile = await issuer.get_or_create(db, org_id)
    missing = issuer.missing_fields(profile)
    if missing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Complete your company registration details first (missing: {', '.join(missing)}).",
        )


@router.post("", response_model=RecurringOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: RecurringCreate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    # If the template links a partner, it must exist (its signed-doc gate is
    # re-checked at each generation, not here).
    if body.template.partner_id:
        p = await partners.get_partner(db, current.org_id, body.template.partner_id)
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Partner not found")
    rec = await recurring.create(
        db,
        current.org_id,
        body=body.template,
        frequency=body.frequency,
        interval=body.interval,
        start_date=body.start_date,
        end_date=body.end_date,
        title=body.title,
        payment_terms_days=body.payment_terms_days,
    )
    return RecurringOut.model_validate(rec)


@router.get("", response_model=list[RecurringOut])
async def list_schedules(current: CurrentUser, db: DbSession):
    await modules.require_enabled(db, current.org_id, "issuing")
    rows = await recurring.list_all(db, current.org_id)
    return [RecurringOut.model_validate(r) for r in rows]


@router.post("/run", response_model=GenerateResult)
async def run_due(current: CurrentUser, db: DbSession):
    """Generate every invoice that is due now across this tenant's schedules."""
    await _guard(db, current.org_id)
    result = await recurring.generate_due(db, current.org_id)
    return GenerateResult(generated=len(result.generated), numbers=[n for _, n in result.generated])


@router.get("/{rec_id}", response_model=RecurringOut)
async def get_schedule(rec_id: str, current: CurrentUser, db: DbSession):
    await modules.require_enabled(db, current.org_id, "issuing")
    rec = await recurring.get(db, current.org_id, rec_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return RecurringOut.model_validate(rec)


@router.patch("/{rec_id}", response_model=RecurringOut)
async def update_schedule(rec_id: str, body: RecurringUpdate, current: CurrentUser, db: DbSession):
    await modules.require_enabled(db, current.org_id, "issuing")
    rec = await recurring.get(db, current.org_id, rec_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    for field in (
        "active",
        "frequency",
        "interval",
        "next_run_date",
        "end_date",
        "title",
        "payment_terms_days",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(rec, field, val)
    await audit.record(
        db,
        audit.A.RECURRING_UPDATE,
        target_type="recurring_invoice",
        target_id=rec.id,
        meta={"active": rec.active},
    )
    await db.commit()
    await db.refresh(rec)
    return RecurringOut.model_validate(rec)


@router.delete("/{rec_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(rec_id: str, current: CurrentUser, db: DbSession):
    await modules.require_enabled(db, current.org_id, "issuing")
    rec = await recurring.get(db, current.org_id, rec_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    await db.delete(rec)
    await db.commit()
