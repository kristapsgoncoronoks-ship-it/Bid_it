"""Explicit fixture setup for legacy role tests; membership grants authority."""

from app.models.user import User, UserRole
from app.services import memberships


async def set_role(db, user: User, role: UserRole) -> None:
    user.role = role  # keep password-login/default projection in this fixture
    membership = await memberships.get(db, user.org_id, user.id)
    assert membership is not None
    membership.role = role
