"""The admin-curated note→invoice override (G2.4; `BA_fleet_fuel.md` C3/C4,
R16).

WHY THIS TABLE EXISTS
---------------------
`app.services.transport.invoice_match.resolve_invoice_ref()` (G2.4) resolves a
fuel transaction's raw `invoice_ref` to a registered AP invoice through two
note-matching heuristics; when NEITHER heuristic uniquely resolves it, an
admin can curate an explicit override — harvested verbatim from
`BA_fleet_fuel.md` C3/C4: *"note_invoice_overrides (supplier, country, note)
→ invoice_ref"*, validated at BOTH set time and read time, and an override
"changes only the invoice ASSOCIATION (bucketing) — never an amount."

WHY THE TARGET IS A TYPED FK (`target_invoice_id`), NOT A TEXT `invoice_ref`
COLUMN LIKE THE HARVESTED SCHEMA
------------------------------------------------------------------------------
Fleet Fuel's own table maps a note to an `invoice_ref` STRING, which then has
to be re-looked-up against the registered set on every read to catch a
de-registration ("de-register the target ⇒ the override silently stops
resolving", R16's acceptance line). This codebase's convention is a typed FK
wherever a cross-table reference exists (master-context §4.3) — pointing the
override directly at `invoices.id` gets the SAME "de-register stops it"
behaviour STRUCTURALLY: `ondelete=CASCADE` means deleting the target invoice
deletes THIS OVERRIDE ROW, with no re-validation logic needed to go stale.
Any code that resolves an override still re-checks the target is CURRENTLY
among the registered set for the exact (supplier, refund_country) pair (see
`invoice_match.resolve_invoice_ref`), so a vendor reassignment/re-country
edge case is covered too, not just an outright delete.

WHY `CASCADE` (DELETE THE OVERRIDE ROW), NOT `SET NULL` (NULL THE COLUMN)
----------------------------------------------------------------------------
The natural first instinct — `ondelete=SET NULL` on `target_invoice_id` alone
— does NOT work for a COMPOSITE foreign key: both SQLite and Postgres null
EVERY column that is part of the constraint on a `SET NULL` action, which
here includes `org_id` (part of the composite `(org_id, target_invoice_id) ->
invoices(org_id, id)` FK, master-context §4.3's own composite-FK convention).
`org_id` is `NOT NULL` (every tenant-scoped row's own invariant), so a
`SET NULL` composite FK firing against this table would itself raise a
constraint violation on the SAME delete it is supposed to handle gracefully
— caught live while writing this order's own de-registration test on SQLite
with `PRAGMA foreign_keys=ON`, and equally true on Postgres (identical
composite-`SET NULL` semantics). `ondelete=CASCADE` sidesteps this entirely:
CASCADE deletes the whole referencing row rather than nulling any column, so
it never conflicts with `org_id NOT NULL`. A dead override (its target
de-registered) has nothing useful to keep anyway — deleting it outright is
at least as correct as nulling it, and it is the option that is actually
race- and constraint-safe on both database engines this project targets.
`target_invoice_id` is therefore `NOT NULL` here (never `NULL` — a live
override with a null target could never happen; the row simply stops
existing instead).

WHY `entity_id` IS PART OF THE KEY WHEN THE HARVESTED TEXT OMITS IT
--------------------------------------------------------------------
Identical widening to every other transport table in this codebase (WO-49's
claim grain, WO-50's transaction natural key, WO-51's lock key): the harvested
BA text predates multi-tenancy AND multi-entity claimants. Scoping the
override to our OWN claimant entity, not just (supplier, country, note),
means two different legal entities of the same org never share an override
namespace by accident.

Tenant-scoped: composite `(org_id, id)` FK targets are not needed here (no
child table references this one), but the two outbound FKs
(`entity_id` -> issuer_profiles, `target_invoice_id` -> invoices) ARE
composite per master-context §4.3. RLS-policy'd in the SAME migration that
creates this table.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class VatNoteInvoiceOverride(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One admin-curated (supplier, refund_country, invoice_ref) → invoice
    association. `app.services.transport.invoice_match.set_note_override` is
    the only writer; it refuses a target that is not a CURRENTLY registered
    invoice for the exact (supplier, refund_country) pair (validated at set
    time) and a synthetic `invoice_ref` key (there is nothing to override
    a synthetic ref onto — `resolve_invoice_ref` short-circuits before ever
    consulting this table for one).
    """

    __tablename__ = "vat_note_invoice_overrides"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "entity_id",
            "supplier",
            "refund_country",
            "invoice_ref",
            name="uq_vat_note_invoice_overrides_key",
        ),
        ForeignKeyConstraint(
            ["org_id", "entity_id"],
            ["issuer_profiles.org_id", "issuer_profiles.id"],
            name="fk_vat_note_invoice_overrides_entity",
            ondelete="RESTRICT",
        ),
        # CASCADE, not SET NULL — see module docstring "WHY CASCADE...": a
        # composite-FK SET NULL would try to null org_id too (NOT NULL,
        # violating the constraint it's meant to satisfy). Deleting the
        # target invoice deletes THIS override row, which is exactly R16's
        # "the override silently stops resolving" (there is simply nothing
        # left to resolve).
        ForeignKeyConstraint(
            ["org_id", "target_invoice_id"],
            ["invoices.org_id", "invoices.id"],
            name="fk_vat_note_invoice_overrides_target",
            ondelete="CASCADE",
        ),
        Index("ix_vat_note_invoice_overrides_org_entity", "org_id", "entity_id"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    supplier: Mapped[str] = mapped_column(String(60), nullable=False)
    refund_country: Mapped[str] = mapped_column(String(2), nullable=False)
    # The raw matching key this override applies to — a fuel transaction's
    # `invoice_ref`, verbatim (never normalized at storage; matching
    # normalizes at read time in `invoice_match`).
    invoice_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    # Always NOT NULL — see module docstring "WHY CASCADE...": once the
    # target invoice is de-registered (deleted), this ROW is deleted with it
    # (CASCADE), never left behind with a null target.
    target_invoice_id: Mapped[str] = mapped_column(GUID(), nullable=False)
