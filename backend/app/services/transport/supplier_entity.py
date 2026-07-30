"""The per-country supplier legal-entity registration (G3.1 slice 1, R21/
R22). See `app/models/transport/supplier_registration.py`'s module
docstring for the full design rationale (why `entity_name` is a plain
string, why `source="admin"` always wins, why learning never queues a
pending-change request).

WHY R20 IS EXPLICITLY NOT CLOSED BY THIS ORDER
--------------------------------------------------
R20 ("capture reads the seller printed on the document") needs actual
text/PDF extraction — a Eurowag invoice's per-country footer, an E100
anchor-to-marker parse. That is `G3.2` (the fuel-card parser registry, a
separate, XL-effort, 7-network build) — genuinely not this order's scope.
This module ships the STORAGE + LEARNING RULE only; `learn_registration`
has no real caller yet (mirrors `is_synthetic()`'s own WO-49 debut: a
standalone predicate with zero consumers wired in, proven correct at the
function level until a real caller exists).

WHY MATCHING IS "MARKER-ONLY" (R21)
--------------------------------------
`get_registration` is a single exact-key SELECT — no normalization, no
similarity scoring, no ranking. There is no OTHER function anywhere in
this module (or called by it) that assigns a supplier/entity pairing
without either an explicit admin marker (`set_registration`) or a
`learn_registration` call. A future "fall back to the group's home
country" convenience, if ever wanted, would be a NEW, explicitly-named
function — never a silent broadening of this one.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.services import audit, modules

_SOURCE_ADMIN = "admin"
_SOURCE_CAPTURE = "capture"


async def _get_row(
    db: AsyncSession, org_id: str, supplier: str, country: str
) -> SupplierVatRegistration | None:
    return await db.scalar(
        select(SupplierVatRegistration).where(
            SupplierVatRegistration.org_id == org_id,
            SupplierVatRegistration.supplier == supplier,
            SupplierVatRegistration.country == country.upper(),
        )
    )


async def get_registration(
    db: AsyncSession, org_id: str, *, supplier: str, country: str
) -> SupplierVatRegistration | None:
    """Marker-only exact lookup (R21) — `None` means no registration exists
    for this exact (supplier, country) key. Never a fuzzy/fallback match."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")
    return await _get_row(db, org_id, supplier, country)


async def set_registration(
    db: AsyncSession,
    org_id: str,
    *,
    supplier: str,
    country: str,
    vat_number: str,
    entity_name: str,
) -> SupplierVatRegistration:
    """The ONLY admin-curated writer — creates or overwrites the (supplier,
    country) registration with `source="admin"`. ALWAYS wins, whether the
    key is new or previously `"capture"`-seeded (an admin's explicit marker
    is authoritative over anything the learning rule inferred)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    country = country.upper()
    row = await _get_row(db, org_id, supplier, country)
    if row is None:
        row = SupplierVatRegistration(
            org_id=org_id,
            supplier=supplier,
            country=country,
            vat_number=vat_number,
            entity_name=entity_name,
            source=_SOURCE_ADMIN,
        )
        db.add(row)
        old = None
    else:
        old = {
            "vat_number": row.vat_number,
            "entity_name": row.entity_name,
            "source": row.source,
        }
        row.vat_number = vat_number
        row.entity_name = entity_name
        row.source = _SOURCE_ADMIN

    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_SUPPLIER_REGISTRATION_SET,
        target_type="supplier_vat_registration",
        target_id=row.id,
        meta={
            "supplier": supplier,
            "country": country,
            "old": old,
            "new": {"vat_number": vat_number, "entity_name": entity_name, "source": _SOURCE_ADMIN},
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return row


async def learn_registration(
    db: AsyncSession,
    org_id: str,
    *,
    supplier: str,
    country: str,
    vat_number: str,
    entity_name: str,
) -> SupplierVatRegistration:
    """R22 — the per-country LEARNING rule. Seeds a NEW `source="capture"`
    row ONLY when no registration exists yet for this exact (supplier,
    country) key. An EXISTING row — of either source — is returned
    UNCHANGED: this function never overwrites a curated admin marker, and
    never lets a second, possibly-conflicting capture silently replace an
    earlier one. Never queues a pending-change request (R22's own
    acceptance line — unlike A2.3's vendor-bank-detail dual control, this
    is diagnostic/filing metadata, not a payment-redirection vector) and
    audits NOTHING when the call is a no-op (no mutation occurred)."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    country = country.upper()
    existing = await _get_row(db, org_id, supplier, country)
    if existing is not None:
        return existing

    row = SupplierVatRegistration(
        org_id=org_id,
        supplier=supplier,
        country=country,
        vat_number=vat_number,
        entity_name=entity_name,
        source=_SOURCE_CAPTURE,
    )
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_SUPPLIER_REGISTRATION_SET,
        target_type="supplier_vat_registration",
        target_id=row.id,
        meta={
            "supplier": supplier,
            "country": country,
            "old": None,
            "new": {
                "vat_number": vat_number,
                "entity_name": entity_name,
                "source": _SOURCE_CAPTURE,
            },
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return row
