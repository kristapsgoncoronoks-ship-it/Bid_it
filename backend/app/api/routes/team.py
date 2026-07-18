from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.tenancy import (
    InviteCreate,
    InviteOut,
    MemberOut,
    MemberUpdate,
)
from app.services import plans, team

router = APIRouter(prefix="/team", tags=["team"])


def _owner_only(current: User):
    if current.role != UserRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the workspace owner can manage the team")


@router.get("/members", response_model=list[MemberOut])
async def members(current: CurrentUser, db: DbSession):
    return await team.list_members(db, current.org_id)


@router.patch("/members/{user_id}", response_model=MemberOut)
async def update_member(user_id: str, body: MemberUpdate, current: CurrentUser, db: DbSession):
    _owner_only(current)
    member = await team.get_member(db, current.org_id, user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")

    # Never allow the last active owner to be demoted or deactivated.
    demoting = body.role is not None and member.role == UserRole.owner and body.role != UserRole.owner
    deactivating = body.is_active is False and member.is_active
    if (demoting or deactivating) and member.role == UserRole.owner:
        if await team.owner_count(db, current.org_id) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The workspace must keep at least one active owner")

    if body.role is not None:
        member.role = body.role
    if body.is_active is not None:
        member.is_active = body.is_active
    await db.commit()
    await db.refresh(member)
    return member


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(current: CurrentUser, db: DbSession):
    _owner_only(current)
    return await team.list_invitations(db, current.org_id)


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(body: InviteCreate, current: CurrentUser, db: DbSession):
    _owner_only(current)
    org = await db.get(Organization, current.org_id)

    # Seat limit (active users + outstanding invites) vs the plan.
    outstanding = len(await team.list_invitations(db, current.org_id))
    if (await plans.active_seats(db, current.org_id)) + outstanding >= plans.plan_for(org.plan).seats:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Seat limit reached for the {plans.plan_for(org.plan).name} plan. Upgrade to add more members.",
        )

    email = body.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")

    return await team.create_invitation(db, current.org_id, email, body.role, current.email)


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: str, current: CurrentUser, db: DbSession):
    _owner_only(current)
    from app.models.invitation import Invitation

    inv = await db.scalar(
        select(Invitation).where(Invitation.id == invite_id, Invitation.org_id == current.org_id)
    )
    if inv is not None:
        await db.delete(inv)
        await db.commit()
