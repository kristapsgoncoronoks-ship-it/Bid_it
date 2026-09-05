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
        select(IssuerProfile).where(IssuerProfile.id == issuer_id, IssuerProfile.org_id == org_id)
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


class PrefixInUse(ValueError):
    """Another issuer in the org already numbers with this prefix."""


async def _prefixes_in_use(db: AsyncSession, org_id: str, *, exclude_id: str | None) -> set[str]:
    rows = await db.execute(
        select(
            IssuerProfile.id, IssuerProfile.invoice_prefix, IssuerProfile.credit_note_prefix
        ).where(IssuerProfile.org_id == org_id)
    )
    used: set[str] = set()
    for pid, inv, cn in rows:
        if pid == exclude_id:
            continue
        used.add(inv)
        used.add(cn)
    return used


async def distinct_default_prefix(db: AsyncSession, org_id: str, base: str = "INV-") -> str:
    """The first prefix of the form INV-, INV2-, INV3-, … no issuer in the org
    uses yet. DB-002 (audit 2026-09-05): every issuer defaulted to `INV-`, and
    numbering is per issuer, so a second entity left on the default computed
    INV-2026-0001 — a number the first entity already held. The org-wide unique
    constraint then refused the INSERT at the moment of issuing a legal
    document AND the rolled-back allocation burned a number in a series that
    is legally required to be gap-free."""
    used = await _prefixes_in_use(db, org_id, exclude_id=None)
    if base not in used:
        return base
    stem = base.rstrip("-")
    n = 2
    while f"{stem}{n}-" in used:
        n += 1
    return f"{stem}{n}-"


async def assert_prefixes_unique(db: AsyncSession, profile: IssuerProfile) -> None:
    """Refuse a save that would give two issuers in one org the same invoice or
    credit-note prefix — the two series would then generate the same string.
    Raises `PrefixInUse` (a ValueError) naming the prefix."""
    used = await _prefixes_in_use(db, profile.org_id, exclude_id=profile.id)
    for label, value in (
        ("invoice", profile.invoice_prefix),
        ("credit-note", profile.credit_note_prefix),
    ):
        if value in used:
            raise PrefixInUse(
                f"the {label} prefix {value!r} is already used by another legal entity in this "
                "workspace; each entity numbers its own series, so prefixes must differ"
            )
    if profile.invoice_prefix == profile.credit_note_prefix:
        raise PrefixInUse("the invoice and credit-note prefixes must differ")


async def create_issuer(db: AsyncSession, org_id: str, **fields) -> IssuerProfile:
    """Register a new issuer entity. The first entity an org creates is its
    default. A second entity that does not name its own prefixes gets DISTINCT
    defaults (see `distinct_default_prefix`) rather than the model's `INV-`."""
    existing = await db.scalar(select(IssuerProfile.id).where(IssuerProfile.org_id == org_id))
    if existing is not None:
        fields.setdefault("invoice_prefix", await distinct_default_prefix(db, org_id, "INV-"))
        fields.setdefault("credit_note_prefix", await distinct_default_prefix(db, org_id, "CN-"))
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
