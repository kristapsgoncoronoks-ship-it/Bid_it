"""Subscription plans — seats and which add-on modules each plan unlocks.

Core modules are always available; add-on modules (e.g. `issuing`) are gated by
plan. Seat limits cap how many active users a tenant can have. Prices are
indicative — nothing charges anyone until billing is wired to a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    seats: int
    price_eur: int | None            # None = "contact us"
    modules: frozenset[str] = field(default_factory=frozenset)  # add-on modules unlocked
    trial: bool = False


PLANS: dict[str, Plan] = {
    "trial": Plan("trial", "Trial", seats=3, price_eur=0, modules=frozenset({"issuing", "expenses", "email_intake", "budget"}), trial=True),
    "starter": Plan("starter", "Starter", seats=2, price_eur=29, modules=frozenset({"expenses", "budget"})),
    "pro": Plan("pro", "Pro", seats=10, price_eur=99, modules=frozenset({"issuing", "expenses", "email_intake", "budget"})),
    "enterprise": Plan("enterprise", "Enterprise", seats=200, price_eur=None, modules=frozenset({"issuing", "expenses", "email_intake", "budget"})),
}
DEFAULT_PLAN = "trial"


def plan_for(key: str | None) -> Plan:
    return PLANS.get(key or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def allows_module(plan_key: str | None, module_key: str) -> bool:
    return module_key in plan_for(plan_key).modules


async def active_seats(db: AsyncSession, org_id: str) -> int:
    return await db.scalar(
        select(func.count(User.id)).where(User.org_id == org_id, User.is_active.is_(True))
    ) or 0


async def seats_available(db: AsyncSession, org_id: str, plan_key: str | None) -> bool:
    return (await active_seats(db, org_id)) < plan_for(plan_key).seats
