from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_owner
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.tenancy import (
    InviteCreate,
    InviteOut,
    MemberOut,
    MemberUpdate,
)
from app.services import audit, memberships, plans, team

router = APIRouter(prefix="/team", tags=["team"])


def _owner_only(current: User):
    # User-rights management (invites + roles + activation) is owner-only, and
    # only within the caller's own company.
    if not is_owner(current):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the company owner can manage users and roles"
        )


def _member_out(m) -> MemberOut:
    """Build the member view from a membership (id = the user id)."""
    return MemberOut(
        id=m.user_id,
        email=m.email or "",
        name=m.name or "",
        role=m.role,
        is_active=(m.status == "active"),
        is_expense_approver=m.is_expense_approver,
        created_at=m.created_at,
    )


@router.get("/members", response_model=list[MemberOut])
async def members(current: CurrentUser, db: DbSession):
    return [_member_out(m) for m in await team.list_members(db, current.org_id)]


@router.patch("/members/{user_id}", response_model=MemberOut)
async def update_member(user_id: str, body: MemberUpdate, current: CurrentUser, db: DbSession):
    _owner_only(current)
    m = await team.get_member(db, current.org_id, user_id)  # the membership in this org
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")

    # Never allow the last active owner to be demoted or deactivated.
    demoting = body.role is not None and m.role == UserRole.owner and body.role != UserRole.owner
    deactivating = body.is_active is False and m.status == "active"
    if (demoting or deactivating) and m.role == UserRole.owner:
        if await team.owner_count(db, current.org_id) <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "The company must keep at least one active owner"
            )

    if body.role is not None:
        m.role = body.role
        await audit.record(
            db,
            audit.A.ROLE_CHANGE,
            target_type="user",
            target_id=user_id,
            meta={"email": m.email, "role": body.role.value},
        )
    if body.is_active is not None:
        m.status = "active" if body.is_active else "suspended"
        if not body.is_active:
            await audit.record(
                db,
                audit.A.USER_DEACTIVATE,
                target_type="user",
                target_id=user_id,
                meta={"email": m.email},
            )
    if body.is_expense_approver is not None:
        # Keep at least one approver so expense reports can always be decided.
        if not body.is_expense_approver and m.is_expense_approver:
            if await team.approver_count(db, current.org_id) <= 1:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "The workspace must keep at least one expense approver",
                )
        m.is_expense_approver = body.is_expense_approver
        await audit.record(
            db,
            audit.A.APPROVER_CHANGE,
            target_type="user",
            target_id=user_id,
            meta={"email": m.email, "approver": body.is_expense_approver},
        )

    # Sync the ACTIVE user projection only if the member is currently active in
    # THIS org (a scoped fetch returns them only then). Their role/approver — and,
    # for the common single-org case, is_active — follow the membership so the
    # live session reflects the change immediately.
    member_user = await db.get(User, user_id)
    if member_user is not None and member_user.org_id == current.org_id:
        member_user.role = m.role
        member_user.is_expense_approver = m.is_expense_approver
        if body.is_active is not None:
            member_user.is_active = body.is_active
    await db.commit()
    return _member_out(m)


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(current: CurrentUser, db: DbSession):
    _owner_only(current)
    return await team.list_invitations(db, current.org_id)


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(body: InviteCreate, current: CurrentUser, db: DbSession):
    _owner_only(current)
    org = await db.get(Organization, current.org_id)

    # Seat limit (active users + outstanding invites) vs the plan.
    outstanding = await team.open_invitation_count(db, current.org_id)
    if (await plans.active_seats(db, current.org_id)) + outstanding >= plans.plan_for(
        org.plan
    ).seats:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"Seat limit reached for the {plans.plan_for(org.plan).name} plan. Upgrade to add more members.",
        )

    email = body.email.lower()
    # An existing account CAN be invited into this org (multi-org join) — unless
    # they are already a member here.
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None and await memberships.get(db, current.org_id, existing.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That person is already a member")

    inv = await team.create_invitation(db, current.org_id, email, body.role, current.email)
    await team.send_invitation_email(db, inv, org_name=org.name, invited_by=current.name)
    await audit.record(
        db,
        audit.A.INVITE_CREATE,
        target_type="invitation",
        target_id=inv.id,
        meta={"email": email, "role": body.role.value},
    )
    await db.commit()
    return inv


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
