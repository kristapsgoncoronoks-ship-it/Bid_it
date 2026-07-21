"""Issuer profile: the org's own company registration details (the SELLER)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.issuer import IssuerProfile

# EN 16931 / Art. 226 minimum for a valid supplier identity on an invoice.
REQUIRED_FIELDS = ("legal_name", "vat_number", "address_line1", "city", "postal_code", "country")


async def get_or_create(db: AsyncSession, org_id: str) -> IssuerProfile:
    profile = await db.scalar(select(IssuerProfile).where(IssuerProfile.org_id == org_id))
    if profile is None:
        profile = IssuerProfile(org_id=org_id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


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
        "notes": p.notes,
    }
