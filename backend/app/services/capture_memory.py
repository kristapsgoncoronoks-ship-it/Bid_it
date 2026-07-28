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

from sqlalchemy import select
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
    db: AsyncSession, org_id: str, vendor_name: str | None, fields: list[str]
) -> None:
    """Bump `observed_count` once per DISTINCT field name for a freshly-parsed
    run — the accuracy denominator. Per-(vendor x field-name), not per line
    occurrence: `category`/`quantity`/etc. appear on every line item, but a
    3-line invoice one month and a 7-line invoice the next share no line
    positions, so counting per-name is what actually accumulates history.

    Best-effort: a capture with no resolvable vendor name yet is skipped, never
    blocking the parse. Commits (mirrors `extraction.record_fields`, which this
    is always called alongside)."""
    key = normalize_vendor(vendor_name)
    names = sorted(set(fields))
    if key is None or not names:
        return
    existing = {
        row.field: row
        for row in await db.scalars(
            select(CaptureFieldMemory).where(
                CaptureFieldMemory.org_id == org_id,
                CaptureFieldMemory.vendor_name == key,
                CaptureFieldMemory.field.in_(names),
            )
        )
    }
    for name in names:
        row = existing.get(name)
        if row is None:
            # NOTE: the model's `default=0` is a Python-side INSERT default —
            # it only applies at flush, so a freshly-constructed row's counter
            # is `None` in memory until then. Seed it explicitly here so the
            # `+= 1` below never hits `None`.
            row = CaptureFieldMemory(
                org_id=org_id, vendor_name=key, field=name, observed_count=0, corrected_count=0
            )
            db.add(row)
            existing[name] = row
        row.observed_count += 1
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
    key = normalize_vendor(vendor_name)
    if key is None:
        return  # no vendor identity yet — nothing to learn against
    row = await db.scalar(
        select(CaptureFieldMemory).where(
            CaptureFieldMemory.org_id == org_id,
            CaptureFieldMemory.vendor_name == key,
            CaptureFieldMemory.field == field,
        )
    )
    if row is None:
        # Same INSERT-default caveat as `observe_fields` — seed both counters
        # explicitly so the `+= 1` below never hits a `None`.
        row = CaptureFieldMemory(
            org_id=org_id, vendor_name=key, field=field, observed_count=0, corrected_count=0
        )
        db.add(row)
    row.corrected_count += 1
    row.last_original_value = (original_value or "")[:_MAX_VALUE_LEN] or None
    row.last_corrected_value = corrected_value[:_MAX_VALUE_LEN]


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
    names = sorted(set(fields))
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
