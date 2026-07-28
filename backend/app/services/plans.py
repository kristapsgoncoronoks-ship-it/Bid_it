"""Subscription plans — seats, add-on modules, and usage quotas per plan.

Core modules are always available; add-on modules (e.g. `issuing`) are gated by
plan. Seat limits cap how many active users a tenant can have. Prices are
indicative — nothing charges anyone until billing is wired to a provider.

WO-47: `monthly_invoice_limit`/`monthly_upload_limit` are the ORG-LEVEL usage
caps (0 = unlimited) — the single source of truth `app.services.access` reads
by default. They are a straight carry-forward of the numbers the (wrong,
role-keyed) model used before this order: what was the free-tier default
(10/20) becomes the `trial` default, what was the paying-individual default
(1000/2000) becomes `starter` (the cheapest paid plan), and what was the
admin/owner/business-role "unlimited" default (0/0) becomes `pro`/`enterprise`
(the business-oriented paid plans). These are INDICATIVE, sysadmin-overridable
per plan (see `access.matrix`/`access.set_limits`) — not a re-litigation of the
plan ladder itself, which is a separate, owner-blocked decision
(`docs/DECISIONS-NEEDED.md`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Membership


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    seats: int
    price_eur: int | None  # None = "contact us"
    modules: frozenset[str] = field(default_factory=frozenset)  # add-on modules unlocked
    trial: bool = False
    monthly_invoice_limit: int = 0  # org-wide default cap; 0 = unlimited
    monthly_upload_limit: int = 0  # org-wide default cap; 0 = unlimited


PLANS: dict[str, Plan] = {
    "trial": Plan(
        "trial",
        "Trial",
        seats=3,
        price_eur=0,
        modules=frozenset({"issuing", "expenses", "email_intake", "budget"}),
        trial=True,
        monthly_invoice_limit=10,
        monthly_upload_limit=20,
    ),
    "starter": Plan(
        "starter",
        "Starter",
        seats=2,
        price_eur=29,
        modules=frozenset({"expenses", "budget"}),
        monthly_invoice_limit=1000,
        monthly_upload_limit=2000,
    ),
    "pro": Plan(
        "pro",
        "Pro",
        seats=10,
        price_eur=99,
        modules=frozenset({"issuing", "expenses", "email_intake", "budget"}),
        monthly_invoice_limit=0,
        monthly_upload_limit=0,
    ),
    "enterprise": Plan(
        "enterprise",
        "Enterprise",
        seats=200,
        price_eur=None,
        modules=frozenset({"issuing", "expenses", "email_intake", "budget"}),
        monthly_invoice_limit=0,
        monthly_upload_limit=0,
    ),
}
DEFAULT_PLAN = "trial"


def plan_for(key: str | None) -> Plan:
    return PLANS.get(key or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def allows_module(plan_key: str | None, module_key: str) -> bool:
    return module_key in plan_for(plan_key).modules


async def active_seats(db: AsyncSession, org_id: str) -> int:
    # A seat = an active membership in this org (Slice 6d) — so a member who is
    # currently active in another org still occupies their seat here.
    return (
        await db.scalar(
            select(func.count(Membership.id)).where(
                Membership.org_id == org_id, Membership.status == "active"
            )
        )
        or 0
    )


async def seats_available(db: AsyncSession, org_id: str, plan_key: str | None) -> bool:
    return (await active_seats(db, org_id)) < plan_for(plan_key).seats
