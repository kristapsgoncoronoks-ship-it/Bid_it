"""Subscription plans — seats, add-on modules, and usage quotas per plan.

Core modules are always available; add-on modules (e.g. `issuing`) are gated by
plan. Seat limits cap how many active users a tenant can have. Prices are
indicative — nothing charges anyone until billing is wired to a provider.

WO-47: `monthly_invoice_limit`/`monthly_upload_limit` are the ORG-LEVEL usage
caps (0 = unlimited) — the single source of truth `app.services.access` reads
by default. INDICATIVE and sysadmin-overridable per plan (see `access.matrix` /
`access.set_limits`).

THE LADDER — resolved 2026-08-15 (`docs/DECISIONS-NEEDED.md` §2a)
-----------------------------------------------------------------
§2a had been open since WO-47: the code implemented `trial/starter/pro/
enterprise` at €0/€29/€99/custom while `docs/product/pricing-hypothesis.md`
proposed a different five-tier ladder plus a Practice partner plan. The owner
chose the pricing doc's ladder, so this table now follows it:

    Free €0 · Starter €39 · Team €99 · Business €249 · Enterprise custom
    + Practice (accountancy partner) · trial stays as the 14-day mechanic

THREE DELIBERATE DEPARTURES FROM THE DOC, each for a stated reason:

1. **`pro` KEEPS ITS KEY and gains the name "Team".** The doc says "rename
   pro→Team". A plan KEY is an identifier stored in `organizations.plan`, quoted
   by `config.stripe_price_for`, and seeded in `seed.py`; a plan NAME is what a
   customer reads. Renaming the key would need a data migration and a Stripe
   price-id remap to change a label. The customer-facing rename lands; the
   identifier does not move.

2. **`trial` and `free` are BOTH kept, and they are different things.** The doc
   describes Free as a perpetual micro tier AND says the 14-day full-feature
   trial "maps to existing `trial`". Those cannot be one row: a trial that
   expires into nothing is not a free tier, and a free tier that starts
   full-featured is not a trial. `trial` stays the 14-day full-feature mechanic;
   `free` is the perpetual 1-seat/25-doc floor it lands on.

3. **The doc's single "Docs/mo" allowance is applied to BOTH counters**, which
   over-grants. The pricing model has ONE document allowance; this code meters
   invoices and uploads separately, so a tenant on Starter can in principle
   consume 150 of each rather than 150 in total. Deliberately over-generous
   rather than under: a customer who is cut off early at a limit they were told
   they had is a support incident and a refund conversation. **Flagged for the
   billing work** — reconciling one allowance against two counters is a metering
   decision, not a table edit.

NOT MODELLED: the doc's "Entities" column (1/1/3/10/unlimited). There is no
entity cap in the code and inventing one here would be a silent, untested
restriction on existing tenants.

Prices remain INDICATIVE — nothing charges anyone until billing is wired
(`docs/DECISIONS-NEEDED.md` §2).
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
    # The 14-day full-feature trial. Everything unlocked so the evaluation is
    # honest; it expires onto `free`, not into nothing.
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
    # Perpetual micro tier. Core capture/review/dashboards only — no issuing.
    "free": Plan(
        "free",
        "Free",
        seats=1,
        price_eur=0,
        modules=frozenset(),
        monthly_invoice_limit=25,
        monthly_upload_limit=25,
    ),
    "starter": Plan(
        "starter",
        "Starter",
        seats=3,
        price_eur=39,
        modules=frozenset({"expenses", "budget", "email_intake"}),
        monthly_invoice_limit=150,
        monthly_upload_limit=150,
    ),
    # Key stays `pro` — see the module docstring, departure 1.
    "pro": Plan(
        "pro",
        "Team",
        seats=10,
        price_eur=99,
        modules=frozenset({"issuing", "expenses", "email_intake", "budget"}),
        monthly_invoice_limit=750,
        monthly_upload_limit=750,
    ),
    "business": Plan(
        "business",
        "Business",
        seats=25,
        price_eur=249,
        modules=frozenset({"issuing", "expenses", "email_intake", "budget"}),
        monthly_invoice_limit=3000,
        monthly_upload_limit=3000,
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
    # The accountancy-practice partner plan — the beachhead's economics. Priced
    # per seat plus client packs, so `price_eur` is None ("contact us") rather
    # than a number that would be wrong for every practice.
    "practice": Plan(
        "practice",
        "Practice (Partner)",
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
