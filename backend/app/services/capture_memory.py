"""Extraction learning loop (E1.5) — per-(vendor x field) correction memory.

`ExtractionField.reviewed_value` was captured for audit only (E1.1/WO-12);
nothing fed it back. This module is the feedback loop: every human correction
recorded via `POST .../captures/{run_id}/review` updates a per-(org x vendor x
field) memory row (`CaptureFieldMemory`), and `hints_for` surfaces the most
recent correction as an ADVISORY suggestion on the next capture from the same
vendor.

§4.19 (AI never silently mutates a financial record) governs this even though
no AI model is involved: the same discipline applies to any pattern-derived
suggestion. `hints_for` is READ-ONLY and additive — the capture-review routes
attach its result to a field's live provenance as `suggested_value` /
`suggestion_observed_count` / `suggestion_corrected_count`. NOTHING in this
module ever writes `ExtractionField.value` or `.reviewed_value`, or any other
record; a human still applies a hint by typing it into the SAME existing
review-correction endpoint. This module cannot bypass that endpoint because it
never writes a field.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capture_field_memory import CaptureFieldMemory

_MAX_VALUE_LEN = 500
_MAX_VENDOR_LEN = 255


def normalize_vendor(name: str | None) -> str | None:
    """The SAME exact-stripped key `vendors.get_or_create_vendor` matches a
    vendor on, so memory recorded against a capture's raw `draft.vendor_name`
    lines up with what a later capture from the same vendor re-produces.
    `None`/blank/whitespace-only all normalize to `None` — no vendor identity,
    nothing to key memory by."""
    if not name:
        return None
    key = name.strip()
    return key[:_MAX_VENDOR_LEN] or None


async def observe_fields(
    db: AsyncSession,
    org_id: str,
    vendor_name: str | None,
    fields: list[str],
    *,
    commit: bool = True,
) -> None:
    """Bump `observed_count` once per DISTINCT field name for a freshly-parsed
    run — the accuracy denominator. Per-(vendor x field-name), not per line
    occurrence: `category`/`quantity`/etc. appear on every line item, but a
    3-line invoice one month and a 7-line invoice the next share no line
    positions, so counting per-name is what actually accumulates history.

    A capture without a resolvable vendor is skipped. `commit=False` joins the
    direct-upload result transaction, so replay cannot double its observation.
    Persistence errors then retry the whole publication through the native job;
    they cannot leave a parsed result missing its evidence."""
    key = normalize_vendor(vendor_name)
    names = sorted(set(fields) - {"supplier_iban"})
    if key is None or not names:
        return
    for name in names:
        await _increment(db, org_id, key, name, observed=1)
    if commit:
        await db.commit()


async def record_correction(
    db: AsyncSession,
    org_id: str,
    *,
    vendor_name: str | None,
    field: str,
    original_value: str | None,
    corrected_value: str,
) -> None:
    """Record ONE genuine human correction as the learning signal — call this
    ONLY when the reviewed value actually differs from the field's prior
    effective value (a same-value resubmit teaches nothing; see the caller in
    `app.api.routes.invoices.review_capture_fields`).

    Does NOT commit — the caller records this in the SAME transaction as the
    field update + the existing `capture.field_review` audit event (§4.16 is
    already satisfied by that event; this row is a derived index over it, not
    a second user-facing mutation)."""
    if field == "supplier_iban":
        return  # Bank instructions must never become reusable cross-document suggestions.
    key = normalize_vendor(vendor_name)
    if key is None:
        return  # no vendor identity yet — nothing to learn against
    await _increment(
        db,
        org_id,
        key,
        field,
        corrected=1,
        original=(original_value or "")[:_MAX_VALUE_LEN] or None,
        value=corrected_value[:_MAX_VALUE_LEN],
    )


async def _increment(
    db: AsyncSession,
    org_id: str,
    vendor_name: str,
    field: str,
    *,
    observed: int = 0,
    corrected: int = 0,
    original: str | None = None,
    value: str | None = None,
) -> None:
    """One atomic counter change, including concurrent first observation/correction.

    A SELECT followed by INSERT races independent capture transactions sharing
    this vendor/field. SQL increments also avoid losing one of two corrections
    on an existing row. Returning refreshes any already-cached ORM identity.
    """
    dialect = db.get_bind().dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise ValueError("Capture memory requires PostgreSQL or SQLite")
    statement = (
        pg_insert(CaptureFieldMemory)
        if dialect == "postgresql"
        else sqlite_insert(CaptureFieldMemory)
    )
    statement = statement.values(
        org_id=org_id,
        vendor_name=vendor_name,
        field=field,
        observed_count=observed,
        corrected_count=corrected,
        last_original_value=original,
        last_corrected_value=value,
    )
    updates = {
        "observed_count": CaptureFieldMemory.observed_count + observed,
        "corrected_count": CaptureFieldMemory.corrected_count + corrected,
        "updated_at": func.now(),
    }
    if corrected:
        updates["last_original_value"] = statement.excluded.last_original_value
        updates["last_corrected_value"] = statement.excluded.last_corrected_value
    returning_statement = (
        statement.on_conflict_do_update(
            index_elements=["org_id", "vendor_name", "field"],
            set_=updates,
        )
        .returning(CaptureFieldMemory)
        .execution_options(populate_existing=True)
    )
    (await db.scalars(returning_statement)).all()


@dataclass
class FieldHint:
    """One ADVISORY suggestion for a field, derived from past corrections for
    this vendor. Never applied automatically — see the module docstring."""

    field: str
    suggested_value: str
    observed_count: int
    corrected_count: int


async def hints_for(
    db: AsyncSession, org_id: str, vendor_name: str | None, fields: list[str]
) -> dict[str, FieldHint]:
    """ADVISORY hints for a capture: the most recent human correction on
    record for each of `fields`, keyed by field name — a SUGGESTION, never
    applied. Empty when the vendor has no correction history yet, or no
    vendor name is known (a capture whose vendor_name field itself is
    missing)."""
    key = normalize_vendor(vendor_name)
    names = sorted(set(fields) - {"supplier_iban"})
    if key is None or not names:
        return {}
    rows = await db.scalars(
        select(CaptureFieldMemory).where(
            CaptureFieldMemory.org_id == org_id,
            CaptureFieldMemory.vendor_name == key,
            CaptureFieldMemory.field.in_(names),
            CaptureFieldMemory.corrected_count > 0,
        )
    )
    return {
        r.field: FieldHint(
            field=r.field,
            suggested_value=r.last_corrected_value,
            observed_count=r.observed_count,
            corrected_count=r.corrected_count,
        )
        for r in rows
        if r.last_corrected_value
    }
