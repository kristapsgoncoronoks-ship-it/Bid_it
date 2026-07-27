"""Tenant tax-code catalog service (Slice 4a).

CRUD-lite over the per-tenant VAT/tax-code registry with a duplicate-code guard,
an active/archived flag, optimistic concurrency, a `resolve` lookup, and an
idempotent standard-catalogue seed. All queries are tenant-scoped by the caller's
`org_id`; the ORM guard + RLS are the belt-and-braces. Codes are archived, never
hard-deleted, so a historical reference still resolves its rate.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import money
from app.models.tax_code import CATEGORIES, TaxCode
from app.services import audit


class TaxCodeError(Exception):
    """Business-rule violation (duplicate code, bad category, stale write)."""


class ConcurrencyError(TaxCodeError):
    """Optimistic-concurrency conflict: the row changed since it was read."""


# A compact, realistic default catalogue (Baltic-first, plus common EU + the
# zero-VAT schemes). Seeded per tenant; edit/extend in-app afterwards.
_STANDARD: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("LV-STD", "Latvia standard", "21", "standard", "LV"),
    ("LV-RED", "Latvia reduced", "12", "reduced", "LV"),
    ("LV-RED5", "Latvia reduced 5%", "5", "reduced", "LV"),
    ("EE-STD", "Estonia standard", "22", "standard", "EE"),
    ("LT-STD", "Lithuania standard", "21", "standard", "LT"),
    ("DE-STD", "Germany standard", "19", "standard", "DE"),
    ("DE-RED", "Germany reduced", "7", "reduced", "DE"),
    ("ZERO", "Zero-rated", "0", "zero", None),
    ("EXEMPT", "Exempt", "0", "exempt", None),
    ("RC", "Reverse charge", "0", "reverse_charge", None),
)


async def _code_taken(db: AsyncSession, org_id: str, code: str) -> bool:
    return bool(
        await db.scalar(
            select(TaxCode.id).where(
                TaxCode.org_id == org_id, func.lower(TaxCode.code) == code.lower()
            )
        )
    )


async def create(
    db: AsyncSession,
    org_id: str,
    *,
    code: str,
    name: str,
    rate,
    category: str = "standard",
    country: str | None = None,
) -> TaxCode:
    if category not in CATEGORIES:
        raise TaxCodeError(f"unknown tax category '{category}'")
    if await _code_taken(db, org_id, code):
        raise TaxCodeError(f"tax code '{code}' already exists")
    tc = TaxCode(
        org_id=org_id,
        code=code,
        name=name,
        rate=money.q(Decimal(str(rate)), Decimal("0.001")),
        category=category,
        country=(country.upper() if country else None),
    )
    db.add(tc)
    # §4.16: audited in the same commit as the insert (flush materialises the id).
    await db.flush()
    await audit.record(
        db,
        audit.A.TAX_CODE_CREATE,
        target_type="tax_code",
        target_id=tc.id,
        meta={"code": tc.code, "name": tc.name, "rate": str(tc.rate), "category": tc.category},
        org_id=org_id,
    )
    await db.commit()
    await db.refresh(tc)
    return tc


async def list_codes(
    db: AsyncSession, org_id: str, *, country: str | None = None, include_inactive: bool = False
) -> list[TaxCode]:
    stmt = select(TaxCode).where(TaxCode.org_id == org_id)
    if not include_inactive:
        stmt = stmt.where(TaxCode.active.is_(True))
    if country:
        stmt = stmt.where(TaxCode.country == country.upper())
    return list(await db.scalars(stmt.order_by(TaxCode.code.asc())))


async def resolve(db: AsyncSession, org_id: str, code: str) -> TaxCode | None:
    """Look up ONE tax code by code (case-insensitive), any status — a historical
    reference to an archived code must still resolve its rate."""
    if not code or not code.strip():
        return None
    return await db.scalar(
        select(TaxCode).where(
            TaxCode.org_id == org_id, func.lower(TaxCode.code) == code.strip().lower()
        )
    )


async def set_active(
    db: AsyncSession, org_id: str, code_id: str, *, active: bool, expected_version: int
) -> TaxCode:
    row = await db.scalar(select(TaxCode).where(TaxCode.org_id == org_id, TaxCode.id == code_id))
    if row is None:
        raise TaxCodeError("tax code not found")
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
        audit.A.TAX_CODE_SET_ACTIVE,
        target_type="tax_code",
        target_id=row.id,
        meta={"code": row.code, "old": old, "new": active},
        org_id=org_id,
    )
    await db.commit()
    await db.refresh(row)
    return row


async def seed_standard(db: AsyncSession, org_id: str) -> int:
    """Seed the default catalogue for a tenant; skips any code already present
    (idempotent). Commits once. Returns how many were created."""
    made = 0
    for code, name, rate, category, country in _STANDARD:
        if await _code_taken(db, org_id, code):
            continue
        db.add(
            TaxCode(
                org_id=org_id,
                code=code,
                name=name,
                rate=money.q(Decimal(rate), Decimal("0.001")),
                category=category,
                country=country,
            )
        )
        made += 1
    if made:
        await db.commit()
    return made
