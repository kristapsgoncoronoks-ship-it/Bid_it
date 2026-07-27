"""Tenant currency-catalog service (Slice 5a).

CRUD-lite over the per-tenant currency registry: a duplicate-code guard, an
active/archived flag with optimistic concurrency, a `resolve` lookup, and an
idempotent standard seed. Tenant-scoped by the caller's `org_id`; the ORM guard +
RLS are the belt-and-braces. Codes are archived, never hard-deleted.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.currency import Currency
from app.services import audit, fx


class CurrencyError(Exception):
    """Business-rule violation (duplicate code, stale write)."""


class ConcurrencyError(CurrencyError):
    """Optimistic-concurrency conflict: the row changed since it was read."""


# A compact, realistic default set (the euro-area home currency plus the
# majors the FX layer already handles). C1.5 (WO-23): this module no longer
# keeps its own copy of each code's name/symbol/decimal_places — those are
# typed ONCE in `fx.CURRENCY_BY_CODE` (the one currency-identity registry) and
# looked up here at seed time, so the tenant catalogue and the FX module
# cannot disagree about what a currency is (ADR-0026).
_STANDARD_CODES: tuple[str, ...] = (
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "JPY",
)


async def _code_taken(db: AsyncSession, org_id: str, code: str) -> bool:
    return bool(
        await db.scalar(
            select(Currency.id).where(Currency.org_id == org_id, Currency.code == code.upper())
        )
    )


async def create(
    db: AsyncSession,
    org_id: str,
    *,
    code: str,
    name: str,
    symbol: str | None = None,
    decimal_places: int = 2,
) -> Currency:
    code = code.upper()
    if await _code_taken(db, org_id, code):
        raise CurrencyError(f"currency '{code}' already exists")
    cur = Currency(
        org_id=org_id, code=code, name=name, symbol=symbol, decimal_places=decimal_places
    )
    db.add(cur)
    # §4.16: audited in the same commit as the insert (flush materialises the id).
    await db.flush()
    await audit.record(
        db,
        audit.A.CURRENCY_CREATE,
        target_type="currency",
        target_id=cur.id,
        meta={"code": cur.code, "name": cur.name, "decimal_places": cur.decimal_places},
        org_id=org_id,
    )
    await db.commit()
    await db.refresh(cur)
    return cur


async def list_currencies(
    db: AsyncSession, org_id: str, *, include_inactive: bool = False
) -> list[Currency]:
    stmt = select(Currency).where(Currency.org_id == org_id)
    if not include_inactive:
        stmt = stmt.where(Currency.active.is_(True))
    return list(await db.scalars(stmt.order_by(Currency.code.asc())))


async def resolve(db: AsyncSession, org_id: str, code: str) -> Currency | None:
    """Look up ONE currency by code (any status)."""
    if not code or not code.strip():
        return None
    return await db.scalar(
        select(Currency).where(Currency.org_id == org_id, Currency.code == code.strip().upper())
    )


async def set_active(
    db: AsyncSession, org_id: str, code_id: str, *, active: bool, expected_version: int
) -> Currency:
    row = await db.scalar(select(Currency).where(Currency.org_id == org_id, Currency.id == code_id))
    if row is None:
        raise CurrencyError("currency not found")
    if row.version != expected_version:
        raise ConcurrencyError(
            f"stale write: expected version {expected_version}, current is {row.version}"
        )
    old = row.active
    row.active = active
    row.version += 1
    # §4.16: old→new in the same commit as the flip.
    await audit.record(
        db,
        audit.A.CURRENCY_SET_ACTIVE,
        target_type="currency",
        target_id=row.id,
        meta={"code": row.code, "old": old, "new": active},
        org_id=org_id,
    )
    await db.commit()
    await db.refresh(row)
    return row


async def seed_standard(db: AsyncSession, org_id: str) -> int:
    """Seed the default currency set for a tenant; skips any code already present
    (idempotent). Commits once. Returns how many were created."""
    made = 0
    for code in _STANDARD_CODES:
        if await _code_taken(db, org_id, code):
            continue
        meta = fx.CURRENCY_BY_CODE[code]
        db.add(
            Currency(
                org_id=org_id,
                code=code,
                name=meta.name,
                symbol=meta.symbol,
                decimal_places=meta.decimal_places,
            )
        )
        made += 1
    if made:
        await db.commit()
    return made
