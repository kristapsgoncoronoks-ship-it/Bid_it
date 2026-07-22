"""Membership model contract (Slice 6a — multi-org foundation).

The table is additive this slice (nothing reads it yet); these tests pin the
invariants the switching slices rely on: at most one membership per (user, org),
and a single user may hold memberships in several orgs.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User, UserRole


async def _user(db, org_id, email):
    u = User(org_id=org_id, email=email, name="U", hashed_password="h", role=UserRole.user)
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_one_membership_per_user_org(db_session):
    org = Organization(name="Org")
    db_session.add(org)
    await db_session.flush()
    user = await _user(db_session, org.id, "a@x.io")

    db_session.add(Membership(org_id=org.id, user_id=user.id, role=UserRole.owner))
    await db_session.commit()

    # A second membership for the SAME (user, org) violates the unique constraint.
    db_session.add(Membership(org_id=org.id, user_id=user.id, role=UserRole.admin))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_a_user_can_belong_to_several_orgs(db_session):
    o1 = Organization(name="One")
    o2 = Organization(name="Two")
    db_session.add_all([o1, o2])
    await db_session.flush()
    user = await _user(db_session, o1.id, "multi@x.io")

    db_session.add(Membership(org_id=o1.id, user_id=user.id, role=UserRole.owner))
    db_session.add(Membership(org_id=o2.id, user_id=user.id, role=UserRole.user))
    await db_session.commit()

    n = await db_session.scalar(
        select(func.count(Membership.id)).where(Membership.user_id == user.id)
    )
    assert n == 2
