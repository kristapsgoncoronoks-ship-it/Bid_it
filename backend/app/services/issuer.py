"""Issuer registry: the org's own legal entities (the SELLER on issued invoices).

An org may register MULTIPLE issuer entities; each owns its OWN gap-free numbering
series. Exactly one is the default (used when an invoice names no issuer). The
legacy single-issuer callers keep working: `get_or_create`/`lock` resolve the
default entity.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.issuer import IssuerProfile

# EN 16931 / Art. 226 minimum for a valid supplier identity on an invoice.
REQUIRED_FIELDS = ("legal_name", "vat_number", "address_line1", "city", "postal_code", "country")


def _default_first(org_id: str):
    # Prefer the flagged default; fall back to the oldest row (deterministic).
    return (
        select(IssuerProfile)
        .where(IssuerProfile.org_id == org_id)
        .order_by(IssuerProfile.is_default.desc(), IssuerProfile.created_at.asc())
        .limit(1)
    )


async def get_or_create(db: AsyncSession, org_id: str) -> IssuerProfile:
    """The org's DEFAULT issuer entity, creating one if the org has none yet."""
    profile = await db.scalar(_default_first(org_id))
    if profile is None:
        profile = IssuerProfile(org_id=org_id, is_default=True)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


async def list_issuers(db: AsyncSession, org_id: str) -> list[IssuerProfile]:
    return list(
        await db.scalars(
            select(IssuerProfile)
            .where(IssuerProfile.org_id == org_id)
            .order_by(IssuerProfile.is_default.desc(), IssuerProfile.created_at.asc())
        )
    )


async def get_by_id(db: AsyncSession, org_id: str, issuer_id: str) -> IssuerProfile | None:
    return await db.scalar(
        select(IssuerProfile).where(
            IssuerProfile.id == issuer_id, IssuerProfile.org_id == org_id
        )
    )


async def resolve(db: AsyncSession, org_id: str, issuer_id: str | None) -> IssuerProfile:
    """The issuer for an invoice: the named entity (validated to belong to the org)
    or the org's default when none is named. Raises ValueError on an unknown id."""
    if issuer_id:
        prof = await get_by_id(db, org_id, issuer_id)
        if prof is None:
            raise ValueError("issuer not found")
        return prof
    return await get_or_create(db, org_id)


async def create_issuer(db: AsyncSession, org_id: str, **fields) -> IssuerProfile:
    """Register a new issuer entity. The first entity an org creates is its default."""
    existing = await db.scalar(select(IssuerProfile.id).where(IssuerProfile.org_id == org_id))
    prof = IssuerProfile(org_id=org_id, is_default=(existing is None), **fields)
    db.add(prof)
    await db.flush()
    return prof


async def set_default(db: AsyncSession, org_id: str, issuer_id: str) -> IssuerProfile | None:
    """Make `issuer_id` the org's default (clears the flag on the others)."""
    target = await get_by_id(db, org_id, issuer_id)
    if target is None:
        return None
    for prof in await list_issuers(db, org_id):
        prof.is_default = prof.id == issuer_id
    return target


async def lock(db: AsyncSession, org_id: str, issuer_id: str | None = None) -> IssuerProfile:
    """Load the issuer entity FOR UPDATE so invoice-number allocation is
    concurrency-safe: two overlapping issue transactions on the SAME entity
    serialize on this row (Postgres row lock; SQLite serializes writes anyway), so
    they cannot read the same `next_number`. Held until the caller's commit; with
    the UniqueConstraint(org_id, number) backstop, duplicate numbers are impossible.
    Different entities lock different rows, so they number in parallel.
    """
    if issuer_id:
        prof = await db.scalar(
            select(IssuerProfile)
            .where(IssuerProfile.id == issuer_id, IssuerProfile.org_id == org_id)
            .with_for_update()
        )
        if prof is not None:
            return prof
    prof = await db.scalar(_default_first(org_id).with_for_update())
    if prof is None:
        prof = await get_or_create(db, org_id)
    return prof


def missing_fields(profile: IssuerProfile | None) -> list[str]:
    if profile is None:
        return list(REQUIRED_FIELDS)
    return [f for f in REQUIRED_FIELDS if not (getattr(profile, f) or "").strip()]


def is_complete(profile: IssuerProfile | None) -> bool:
    return not missing_fields(profile)


def seller_snapshot(p: IssuerProfile) -> dict:
    """Frozen seller details stored on each issued invoice + used for XML/PDF."""
    return {
        "legal_name": p.legal_name,
        "trade_name": p.trade_name,
        "vat_number": p.vat_number,
        "registration_number": p.registration_number,
        "address_line1": p.address_line1,
        "address_line2": p.address_line2,
        "city": p.city,
        "postal_code": p.postal_code,
        "country": p.country,
        "email": p.email,
        "phone": p.phone,
        "iban": p.iban,
        "bic": p.bic,
        "payment_instructions": p.payment_instructions,
        "notes": p.notes,
    }
