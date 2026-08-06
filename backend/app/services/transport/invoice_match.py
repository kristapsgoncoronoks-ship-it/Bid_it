"""Note→invoice resolution — one source of truth for turning a fuel
transaction's raw `invoice_ref` into a registered AP invoice (G2.4;
`BA_fleet_fuel.md` C1/C3/C4, R2/R16).

`BA_fleet_fuel.md` C3 is explicit that this must be ONE function shared by
every consumer "so the two can never drift" — mirrors why `claim_gates.
is_synthetic()` (R3, WO-49) is centralized. The harvested resolution order
(`_resolve_inv`), verbatim:

    1. note-match heuristic 1 (note prefix vs registered ref prefix)
    2. note-match heuristic 2 (registered ref stem contained in note)
    3. admin-curated note→invoice override (only REDUCES UNMATCHED; never
       displaces a successful heuristic match)
    4. sole-registered fallback (exactly one registered invoice for that
       supplier+refund_country)
    5. else → UNMATCHED (a hard block, never an invented aggregate)

WHAT "REGISTERED" MEANS IN THIS CODEBASE
-----------------------------------------
Fleet Fuel's "registered invoice" was an admin-confirmed row in its own
`invoice_documents` vault index. This codebase already has the equivalent
concept: an `Invoice` row belongs to a `Vendor` (the AP master-data record) —
a fuel transaction's free-text `supplier` field (the fuel-card network code
captured at ingestion, `fuel_transaction.py`) is matched to a `Vendor.name`
EXACTLY (mirrors `app.services.vendors.get_or_create_vendor`'s own exact-
match convention), and "registered invoices for (supplier, refund_country)"
= that vendor's `Invoice` rows, filtered to `Vendor.country == refund_country`
when the vendor declares one. A vendor with no country set is not excluded —
this codebase does not yet model a per-country legal-entity registration for
a vendor the way G3.1 (capture: reading the legal entity off the invoice,
future work) will; this is a documented interpretation of an underspecified
boundary, not an invented requirement (mirrors WO-50's own `line_seq`
disclosure).

WHY HEURISTICS 1/2 ARE DEFINED THE WAY THEY ARE
--------------------------------------------------
`BA_fleet_fuel.md` names the two heuristics by description only ("note prefix
vs registered ref prefix", "registered ref stem contained in note") without
literal code — underspecified, like the natural-key phrase WO-50 interpreted.
This module's reading: normalize both strings to their alphanumeric-only
uppercase form (`_normalize`), then:
  - Heuristic 1 ("prefix"): one normalized string is a PREFIX of the other —
    covers a note that is a truncated/padded version of the registered
    number or vice versa.
  - Heuristic 2 ("stem contained"): the normalized registered ref appears
    ANYWHERE inside the normalized note — covers a note that embeds the
    invoice number as a substring (e.g. a card-statement line that prefixes
    or suffixes extra text around the real number).
A minimum shared-length floor (`_MIN_MATCH_LEN`) stops a single stray digit
from producing a false match. Each heuristic ONLY resolves when it matches
EXACTLY ONE candidate — an ambiguous heuristic match (two or more candidates)
falls through to the next step rather than guessing, the same fail-toward-
blocking discipline `is_synthetic()`'s docstring states explicitly.

WHY THE OVERRIDE TARGET IS RE-VALIDATED ON EVERY READ, NOT CACHED
--------------------------------------------------------------------
`resolve_invoice_ref` always re-computes `registered_invoices()` live and
only honours an override whose `target_invoice_id` is STILL among that live
set — see `app/models/transport/note_override.py`'s module docstring for why
the target column itself is a `SET NULL` FK (structural half of "de-register
⇒ stops resolving") and this function's own re-check (behavioural half,
covering a vendor/country reassignment that a bare NULL check would miss).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionError, ValidationError
from app.models.transport.note_override import VatNoteInvoiceOverride
from app.services import audit, modules, vendors
from app.services import invoices as ap_invoices
from app.services.transport.claim_gates import is_synthetic

# Below this many normalized characters, a prefix/substring match is treated
# as noise, not a real hit (a bare "1" matching every invoice starting with
# "1" would be worse than useless). Documented interpretation, not harvested.
_MIN_MATCH_LEN = 4


@dataclass(frozen=True)
class MatchedInvoice:
    """A resolved registered invoice — deliberately NOT the AP `Invoice` ORM
    model itself. Keeping this a small transport-local type (rather than
    returning/annotating with `app.models.invoice.Invoice`) means no module
    under `services/transport/` ever needs to name a foreign domain's model,
    which is exactly what `tests/test_boundaries.py::
    test_transport_services_do_not_import_other_domain_models` enforces."""

    invoice_id: str
    invoice_number: str


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _prefix_match(note: str, ref: str) -> bool:
    n_note, n_ref = _normalize(note), _normalize(ref)
    if len(n_note) < _MIN_MATCH_LEN or len(n_ref) < _MIN_MATCH_LEN:
        return False
    return n_note.startswith(n_ref) or n_ref.startswith(n_note)


def _stem_contained(note: str, ref: str) -> bool:
    n_note, n_ref = _normalize(note), _normalize(ref)
    if len(n_ref) < _MIN_MATCH_LEN:
        return False
    return n_ref in n_note


async def registered_invoices(
    db: AsyncSession, org_id: str, *, supplier: str, refund_country: str
) -> list[MatchedInvoice]:
    """Every "registered invoice" for (supplier, refund_country) — see module
    docstring for what "registered" means in this codebase. Read-only; never
    creates a vendor (`vendors.get_by_name`, not `get_or_create_vendor`)."""
    vendor = await vendors.get_by_name(db, org_id, supplier)
    if vendor is None:
        return []
    if vendor.country is not None and vendor.country.upper() != refund_country.upper():
        return []
    rows = await ap_invoices.list_by_vendor(db, org_id, vendor.id)
    return [MatchedInvoice(invoice_id=r.id, invoice_number=r.invoice_number) for r in rows]


async def resolve_invoice_ref(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    refund_country: str,
    invoice_ref: str | None,
) -> MatchedInvoice | None:
    """The ONE resolution order (C3) — see module docstring. Returns the
    matched invoice or `None` (UNMATCHED); the caller renders `None` as the
    literal string `"UNMATCHED"`, which `claim_gates.is_synthetic()`
    recognizes (R3).

    An already-synthetic `invoice_ref` (or a missing one) short-circuits to
    `None` immediately — heuristic-matching over an `"INPUT:..."`/`"ALL:..."`
    placeholder would be resolving noise, not a real reference, and would
    also make an override keyed on a synthetic note reachable, which
    `set_note_override` separately refuses to create in the first place.

    Gated on the `transport` module entitlement first, identical pattern to
    every other transport service — this function does real queries (unlike
    `claim_gates.is_synthetic()`, a pure predicate with nothing to gate) and
    is independently callable, not only reachable through
    `claim_lines.build_claim_lines`.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    if invoice_ref is None or is_synthetic(invoice_ref):
        return None

    refund_country = refund_country.upper()
    candidates = await registered_invoices(
        db, org_id, supplier=supplier, refund_country=refund_country
    )
    if not candidates:
        return None

    matches1 = [c for c in candidates if _prefix_match(invoice_ref, c.invoice_number)]
    if len(matches1) == 1:
        return matches1[0]

    matches2 = [c for c in candidates if _stem_contained(invoice_ref, c.invoice_number)]
    if len(matches2) == 1:
        return matches2[0]

    # Step 3 — admin override. Only consulted once BOTH heuristics failed to
    # resolve uniquely: an override never displaces a successful heuristic
    # match (C4, verbatim).
    override = await db.scalar(
        select(VatNoteInvoiceOverride).where(
            VatNoteInvoiceOverride.org_id == org_id,
            VatNoteInvoiceOverride.entity_id == entity_id,
            VatNoteInvoiceOverride.supplier == supplier,
            VatNoteInvoiceOverride.refund_country == refund_country,
            VatNoteInvoiceOverride.invoice_ref == invoice_ref,
        )
    )
    if override is not None:
        # `target_invoice_id` is always NOT NULL when this row exists (the
        # target FK is `ondelete=CASCADE` — de-registering the target
        # deletes THIS ROW rather than nulling the column, see
        # `app/models/transport/note_override.py`'s docstring). Still
        # re-validated against the LIVE registered set (read-time
        # re-validation, C4): a target reassigned to a vendor/country outside
        # this pair silently falls through rather than resolving, R16's
        # acceptance line.
        by_id = {c.invoice_id: c for c in candidates}
        target = by_id.get(override.target_invoice_id)
        if target is not None:
            return target

    # Step 4 — sole-registered fallback.
    if len(candidates) == 1:
        return candidates[0]

    # Step 5 — UNMATCHED. A hard block, never an invented aggregate (C1/R2).
    return None


async def list_note_overrides(db: AsyncSession, org_id: str) -> list[VatNoteInvoiceOverride]:
    """Every admin-curated note→invoice override in the org, in the
    deterministic (supplier, refund_country, invoice_ref) order (WO-77 —
    the route-facing read; `set_note_override` was previously write-only
    over the wire). Module-gated first, the transport-wide entry-point
    pattern (fails CLOSED, ADR-P3 rule 3). Read-only, and deliberately NOT
    re-validated against the live registered set: that read-time re-check
    belongs to `resolve_invoice_ref` (C4), whereas listing what an admin
    CURATED must show a row whose target has since been reassigned —
    that is exactly the state an admin needs to see in order to fix it."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    return list(
        await db.scalars(
            select(VatNoteInvoiceOverride)
            .where(VatNoteInvoiceOverride.org_id == org_id)
            .order_by(
                VatNoteInvoiceOverride.supplier,
                VatNoteInvoiceOverride.refund_country,
                VatNoteInvoiceOverride.invoice_ref,
            )
        )
    )


async def set_note_override(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    refund_country: str,
    invoice_ref: str,
    target_invoice_id: str,
) -> VatNoteInvoiceOverride:
    """C4/R16 — create or update the admin-curated override for one
    (entity, supplier, refund_country, invoice_ref) key. Refuses (422):

    - `code="override_note_is_synthetic"` — the note key itself is already
      synthetic (`claim_gates.is_synthetic`); there is nothing real to
      override it onto.
    - `code="override_target_not_registered"` — `target_invoice_id` does not
      name a CURRENTLY registered invoice for this exact
      (supplier, refund_country) pair.

    Idempotent on the key: a second call updates the existing row's target
    (an override changes only the invoice ASSOCIATION — never an amount, C4)
    rather than creating a duplicate. Gated on the `transport` module
    entitlement first, identical pattern to every other transport service.
    """
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    refund_country = refund_country.upper()

    if is_synthetic(invoice_ref):
        raise ValidationError(
            f"'{invoice_ref}' is already a synthetic reference — nothing to override it onto",
            code="override_note_is_synthetic",
        )

    candidates = await registered_invoices(
        db, org_id, supplier=supplier, refund_country=refund_country
    )
    by_id = {c.invoice_id: c for c in candidates}
    if target_invoice_id not in by_id:
        raise ValidationError(
            "Override target must be a currently registered invoice for this supplier and refund country",
            code="override_target_not_registered",
        )

    existing = await db.scalar(
        select(VatNoteInvoiceOverride).where(
            VatNoteInvoiceOverride.org_id == org_id,
            VatNoteInvoiceOverride.entity_id == entity_id,
            VatNoteInvoiceOverride.supplier == supplier,
            VatNoteInvoiceOverride.refund_country == refund_country,
            VatNoteInvoiceOverride.invoice_ref == invoice_ref,
        )
    )
    if existing is not None:
        old_target = existing.target_invoice_id
        existing.target_invoice_id = target_invoice_id
        await db.flush()
        await audit.record(
            db,
            audit.A.TRANSPORT_NOTE_OVERRIDE_SET,
            target_type="vat_note_invoice_override",
            target_id=existing.id,
            meta={"old_target_invoice_id": old_target, "new_target_invoice_id": target_invoice_id},
            org_id=org_id,
        )
        return existing

    override = VatNoteInvoiceOverride(
        org_id=org_id,
        entity_id=entity_id,
        supplier=supplier,
        refund_country=refund_country,
        invoice_ref=invoice_ref,
        target_invoice_id=target_invoice_id,
    )
    db.add(override)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_NOTE_OVERRIDE_SET,
        target_type="vat_note_invoice_override",
        target_id=override.id,
        meta={"target_invoice_id": target_invoice_id},
        org_id=org_id,
    )
    return override
