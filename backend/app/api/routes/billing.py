from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models.organization import Organization
from app.core.roles import is_admin_or_above
from app.schemas.tenancy import BillingOut, PlanChange, PlanOut
from app.services import modules as modules_svc
from app.services import plans

router = APIRouter(prefix="/billing", tags=["billing"])


def _plan_out(p) -> PlanOut:
    return PlanOut(
        key=p.key, name=p.name, seats=p.seats, price_eur=p.price_eur,
        modules=sorted(p.modules), trial=p.trial,
    )


@router.get("", response_model=BillingOut)
async def get_billing(current: CurrentUser, db: DbSession):
    org = await db.get(Organization, current.org_id)
    plan = plans.plan_for(org.plan)
    return BillingOut(
        plan=_plan_out(plan),
        status=org.status,
        seats_used=await plans.active_seats(db, current.org_id),
        seats_limit=plan.seats,
        available_plans=[_plan_out(p) for p in plans.PLANS.values()],
    )


@router.put("/plan", response_model=BillingOut)
async def change_plan(body: PlanChange, current: CurrentUser, db: DbSession):
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can change the plan")
    if body.plan not in plans.PLANS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown plan")

    org = await db.get(Organization, current.org_id)
    target = plans.plan_for(body.plan)

    # Downgrade guards: can't drop below current seat usage.
    used = await plans.active_seats(db, current.org_id)
    if used > target.seats:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{target.name} allows {target.seats} seats but {used} are in use. Remove members first.",
        )
    # ...or below required modules: disable add-ons the new plan doesn't include.
    enabled = await modules_svc.enabled_keys(db, current.org_id)
    for key in enabled:
        m = modules_svc.MODULES_BY_KEY.get(key)
        if m and not m.core and key not in target.modules:
            await modules_svc.set_enabled(db, current.org_id, key, False)

    org.plan = body.plan
    await db.commit()
    return await get_billing(current, db)
