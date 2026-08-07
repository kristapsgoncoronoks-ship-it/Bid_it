"""Receipt control — "did we receive every invoice the supplier issued?"
(G3.5; `BA_fleet_fuel.md` §3.J, §2.3 — no dedicated R-number).

§2.3, verbatim: *"A missing invoice is un-recoverable VAT. Cadence ×
activity is the only way to know what SHOULD have arrived."* Every gate
built before this module validates what DID arrive (R10 documents, R15
waivers, the G2.10 checklist); this module is the expectation model that
sees what NEVER arrived, while chasing it is still possible.

THE HARVESTED MECHANISM (§3.J, cited verbatim in
`app/models/transport/receipt_control.py`'s docstring)
------------------------------------------------------
1. Each supplier has an invoicing CADENCE (three values, `CADENCES`).
2. EXPECTATION = cadence × ACTIVITY — an invoice is expected for a slot
   only if transactions exist in it; *"No activity → 'NO ACTIVITY', no
   invoice expected — OK."*
3. CROSS-CONTROL statuses `received_doc` / `received_no_doc` / `missing`
   (*"activity but no invoice registered → chase the supplier"*), plus
   the ORPHAN check — *"every transaction must be covered by a registered
   invoice"* (`orphan_transactions`, transaction-grain, read-only).
4. Results PERSIST (`vat_receipt_controls`); *"manual overrides
   (waived / note) survive re-runs"* — `run_receipt_control` never writes
   the override columns; `set_control_override` is their only writer.

WHERE CADENCE COMES FROM, AND WHY DEFAULTS ARE SAFE TO SHIP ON
--------------------------------------------------------------
`cadence_for` resolves: admin row (`VatSupplierCadence`, `set_cadence`) →
harvested per-network default (`DEFAULT_CADENCES`, §3.J item 1 / §5.1's
network table, keyed by the G3.2 parser `network` codes, matched
case-insensitively) → `None`. A supplier resolving to `None` produces NO
expectations and is only COUNTED in the run summary
(`unmanaged_suppliers`) — the R61/WO-66 opt-in-by-absence discipline: an
ungoverned supplier behaves exactly as before. Shipping the harvested
defaults ON is safe because every output of this module is ADVISORY
(below): the worst a wrong default produces is a chase-list row an admin
can waive or fix with `set_cadence`. Eurowag is deliberately absent from
the defaults — §5.1 tags its cadence "—" (parser only).

ADVISORY THROUGHOUT (master-context §4.19 discipline)
-----------------------------------------------------
§3.J's verbs are "answers", "cross-control", "persist", "chase" —
monitoring verbs, in deliberate contrast to R25's two regimes ("halts the
close", "blocks registration") and the G2.6 gates ("cannot be filed"). A
`missing` slot or an orphan transaction therefore NEVER gates a claim, a
submission, an ingest or the close; the blocking side of receipt control
stays exactly where WO-58/WO-60 put it (R15 waivers + the claim-level
checklist item). `close.run_close` RUNS this control as its `run_control`
stage (§3.H H4's harvested stage list) and completes regardless of what
the control FINDS — an EXCEPTION inside the stage still halts the close
via the existing job rollback (R31's "halts on the first failure" is
about infrastructure failures, not findings).

ONE COVERAGE PREDICATE, NEVER TWO
---------------------------------
"Covered by a registered invoice" is `invoice_match.resolve_invoice_ref`
— C3's ONE resolution order, the same function `claim_lines`/`checklist`
already call (*"so the two can never drift"*). This module adds NO second
matching heuristic; it only batches calls per DISTINCT
(supplier, country, invoice_ref) key within a run. The "+ DOC" half is
WO-58's `extraction.invoice_ids_with_documents` batch seam — one query
per run, never per slot (the N+1 `docs_index()` lesson).

MONEY IS UNTOUCHED — this module reads and counts `FuelTransaction` rows;
it sums nothing, converts nothing, stores no amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.receipt_control import (
    CADENCES,
    VatReceiptControl,
    VatSupplierCadence,
)
from app.services import audit, modules
from app.services import extraction as extraction_svc
from app.services.transport import queries
from app.services.transport.invoice_match import MatchedInvoice, resolve_invoice_ref

# §3.J item 1 / §5.1 — the harvested per-network cadence assignments, keyed
# by the G3.2 parser `network` codes (upper-cased for case-insensitive
# lookup; `PORTONE` has no parser yet — G4.2 — but §3.J names it monthly).
# Eurowag is deliberately absent: §5.1 tags its cadence "—" (parser only).
DEFAULT_CADENCES: dict[str, str] = {
    "E100": "semi-monthly",
    "DKV": "semi-monthly",
    "MOEVE": "monthly",
    "BP": "monthly",
    "TFC": "monthly",
    "PORTONE": "monthly",
    "Q8": "monthly-per-country",
}

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# The last day of the first half-month slot — "one invoice per half-month"
# (§3.J item 1) read as the natural calendar split days 1-15 / 16-end
# (E100's own two invoices per month). Documented interpretation, see the
# model docstring.
_H1_LAST_DAY = 15


async def _require_module(db: AsyncSession, org_id: str) -> None:
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def _validate_period(period: str) -> None:
    if not _PERIOD_RE.match(period):
        raise ValidationError(
            f"'{period}' is not a valid period — use YYYY-MM", code="invalid_period"
        )


# --------------------------------------------------------------------------- #
# Cadence configuration
# --------------------------------------------------------------------------- #


async def set_cadence(
    db: AsyncSession, org_id: str, *, supplier: str, cadence: str
) -> VatSupplierCadence:
    """Create or update the admin cadence assignment for one supplier —
    the ONLY writer of `VatSupplierCadence`; beats `DEFAULT_CADENCES` in
    `cadence_for`. Idempotent on the (org, supplier) key: a repeat call
    with the same value returns the existing row and audits nothing (the
    `checklist.set_active` no-op convention); a changed value audits
    old→new. Refuses (422, `code="invalid_cadence"`) a value outside the
    harvested three (§3.J item 1's closed set)."""
    await _require_module(db, org_id)
    if cadence not in CADENCES:
        raise ValidationError(
            f"'{cadence}' is not a valid cadence — use one of: " + ", ".join(CADENCES),
            code="invalid_cadence",
        )

    existing = await db.scalar(
        select(VatSupplierCadence).where(
            VatSupplierCadence.org_id == org_id, VatSupplierCadence.supplier == supplier
        )
    )
    if existing is not None:
        if existing.cadence == cadence:
            return existing
        old = existing.cadence
        existing.cadence = cadence
        await db.flush()
        await audit.record(
            db,
            audit.A.TRANSPORT_SUPPLIER_CADENCE_SET,
            target_type="vat_supplier_cadence",
            target_id=existing.id,
            meta={"supplier": supplier, "old_cadence": old, "new_cadence": cadence},
            org_id=org_id,
        )
        return existing

    row = VatSupplierCadence(org_id=org_id, supplier=supplier, cadence=cadence)
    db.add(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_SUPPLIER_CADENCE_SET,
        target_type="vat_supplier_cadence",
        target_id=row.id,
        meta={"supplier": supplier, "old_cadence": None, "new_cadence": cadence},
        org_id=org_id,
    )
    return row


async def remove_cadence(db: AsyncSession, org_id: str, *, supplier: str) -> bool:
    """Delete the admin assignment (the supplier falls back to the
    harvested default, or to no cadence at all). Returns False when there
    was nothing to remove — a no-op audits nothing."""
    await _require_module(db, org_id)
    existing = await db.scalar(
        select(VatSupplierCadence).where(
            VatSupplierCadence.org_id == org_id, VatSupplierCadence.supplier == supplier
        )
    )
    if existing is None:
        return False
    old = existing.cadence
    row_id = existing.id
    await db.delete(existing)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_SUPPLIER_CADENCE_REMOVE,
        target_type="vat_supplier_cadence",
        target_id=row_id,
        meta={"supplier": supplier, "old_cadence": old},
        org_id=org_id,
    )
    return True


async def list_cadences(db: AsyncSession, org_id: str) -> list[VatSupplierCadence]:
    await _require_module(db, org_id)
    return list(
        await db.scalars(
            select(VatSupplierCadence)
            .where(VatSupplierCadence.org_id == org_id)
            .order_by(VatSupplierCadence.supplier)
        )
    )


async def cadence_for(db: AsyncSession, org_id: str, supplier: str) -> str | None:
    """Admin row → harvested per-network default (case-insensitive on the
    supplier code) → None (no expectation — opt-in by absence). Pure read,
    no module gate: called inside `run_receipt_control`'s own gated loop
    and independently harmless (mirrors `claim_gates.is_synthetic`'s
    nothing-to-gate rationale)."""
    row = await db.scalar(
        select(VatSupplierCadence).where(
            VatSupplierCadence.org_id == org_id, VatSupplierCadence.supplier == supplier
        )
    )
    if row is not None:
        return row.cadence
    return DEFAULT_CADENCES.get(supplier.upper())


# --------------------------------------------------------------------------- #
# The control run
# --------------------------------------------------------------------------- #


def _slot_of(cadence: str, txn: FuelTransaction) -> tuple[str, str]:
    """(slot, country) key for one transaction under a cadence."""
    if cadence == "semi-monthly":
        return ("H1" if txn.txn_date.day <= _H1_LAST_DAY else "H2", "")
    if cadence == "monthly-per-country":
        return ("M", txn.country.upper())
    return ("M", "")


def _expected_slots(
    cadence: str, by_slot: dict[tuple[str, str], list[FuelTransaction]]
) -> list[tuple[str, str]]:
    """Every slot to persist for this (entity, supplier, period).

    Semi-monthly ALWAYS emits both halves — a half with no transactions
    becomes `no_activity` (*"no invoice expected — OK"*), which is exactly
    the row the acceptance test reads ("a supplier with no activity in a
    slot is never chased"). Monthly emits its one slot. Per-country emits
    only countries WITH activity (§3.J item 1 verbatim — an inactive
    country is structurally unenumerable)."""
    if cadence == "semi-monthly":
        return [("H1", ""), ("H2", "")]
    if cadence == "monthly-per-country":
        return sorted(by_slot.keys())
    return [("M", "")]


async def _resolve_cached(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    country: str,
    invoice_ref: str | None,
    cache: dict[tuple[str, str, str, str | None], MatchedInvoice | None],
) -> MatchedInvoice | None:
    """One `resolve_invoice_ref` call per DISTINCT (entity, supplier,
    country, invoice_ref) key within a run — a batching of the ONE
    resolution implementation (C3), never a second one. `entity_id` is in
    the key because R16's override table is scoped per entity."""
    key = (entity_id, supplier, country, invoice_ref)
    if key not in cache:
        cache[key] = await resolve_invoice_ref(
            db,
            org_id,
            entity_id=entity_id,
            supplier=supplier,
            refund_country=country,
            invoice_ref=invoice_ref,
        )
    return cache[key]


async def run_receipt_control(db: AsyncSession, org_id: str, period: str) -> dict:
    """The §3.J engine — computes and PERSISTS the period's control grid.

    Enumerates (entity, supplier) pairs FROM THE PERIOD'S TRANSACTIONS
    (expectation = cadence × ACTIVITY — a configured supplier with zero
    transactions anywhere in the period has nothing expected; an
    all-`no_activity` grid would be noise). Skips — and counts — suppliers
    with no cadence. Upserts each slot on its natural key, writing ONLY
    the computed columns (`status`, `txn_count`); the override columns
    (`waived`, `note`) are NEVER touched here, which is the whole §3.J
    item 4 "overrides survive re-runs" contract. Rows are never deleted by
    a re-run — a slot whose activity vanished on re-ingest recomputes to
    `no_activity`, keeping its override history.

    Findings never raise: a `missing` slot is a persisted worklist row,
    not a gate (§4.19 — see the module docstring). Raises only on real
    validation/infrastructure failures (bad period, module off), which —
    inside the close — correctly halt per R31."""
    await _require_module(db, org_id)
    _validate_period(period)

    txns = list(await db.scalars(queries.fuel_transactions(org_id, period=period)))

    pairs: dict[tuple[str, str], list[FuelTransaction]] = {}
    for txn in txns:
        pairs.setdefault((txn.entity_id, txn.supplier), []).append(txn)

    resolve_cache: dict[tuple[str, str, str, str | None], MatchedInvoice | None] = {}
    # (entity_id, supplier, slot, country) -> (txns, matched invoice ids)
    computed: dict[tuple[str, str, str, str], tuple[int, set[str]]] = {}
    unmanaged: set[str] = set()

    for (entity_id, supplier), pair_txns in sorted(pairs.items()):
        cadence = await cadence_for(db, org_id, supplier)
        if cadence is None:
            unmanaged.add(supplier)
            continue

        by_slot: dict[tuple[str, str], list[FuelTransaction]] = {}
        for txn in pair_txns:
            by_slot.setdefault(_slot_of(cadence, txn), []).append(txn)

        for slot, country in _expected_slots(cadence, by_slot):
            slot_txns = by_slot.get((slot, country), [])
            matched_ids: set[str] = set()
            for txn in slot_txns:
                matched = await _resolve_cached(
                    db,
                    org_id,
                    entity_id=entity_id,
                    supplier=supplier,
                    country=txn.country.upper(),
                    invoice_ref=txn.invoice_ref,
                    cache=resolve_cache,
                )
                if matched is not None:
                    matched_ids.add(matched.invoice_id)
            computed[(entity_id, supplier, slot, country)] = (len(slot_txns), matched_ids)

    # ONE documents query for the whole run (never per slot — the N+1
    # docs_index() lesson, WO-58's batch seam).
    all_matched = sorted({i for _, ids in computed.values() for i in ids})
    documented = await extraction_svc.invoice_ids_with_documents(db, org_id, all_matched)

    counts = {"no_activity": 0, "received_doc": 0, "received_no_doc": 0, "missing": 0}
    for (entity_id, supplier, slot, country), (txn_count, matched_ids) in computed.items():
        if txn_count == 0:
            status = "no_activity"
        elif not matched_ids:
            status = "missing"
        elif matched_ids & documented:
            status = "received_doc"
        else:
            status = "received_no_doc"
        counts[status] += 1

        existing = await db.scalar(
            select(VatReceiptControl).where(
                VatReceiptControl.org_id == org_id,
                VatReceiptControl.entity_id == entity_id,
                VatReceiptControl.supplier == supplier,
                VatReceiptControl.period == period,
                VatReceiptControl.slot == slot,
                VatReceiptControl.country == country,
            )
        )
        if existing is not None:
            # Computed columns ONLY — `waived`/`note` pass through
            # untouched (the §3.J item 4 contract).
            existing.status = status
            existing.txn_count = txn_count
        else:
            db.add(
                VatReceiptControl(
                    org_id=org_id,
                    entity_id=entity_id,
                    supplier=supplier,
                    period=period,
                    slot=slot,
                    country=country,
                    status=status,
                    txn_count=txn_count,
                    waived=False,
                    note=None,
                )
            )
    await db.flush()

    summary = {
        "period": period,
        "rows": len(computed),
        **counts,
        "unmanaged_suppliers": sorted(unmanaged),
    }
    await audit.record(
        db,
        audit.A.TRANSPORT_RECEIPT_CONTROL_RUN,
        # No single target row — a whole-org batch summary, the
        # TRANSPORT_CLOSE_RUN pattern (org_id + meta, no target_type/id).
        meta=summary,
        org_id=org_id,  # explicit — callable from the close job worker.
    )
    return summary


async def list_controls(db: AsyncSession, org_id: str, period: str) -> list[VatReceiptControl]:
    """The persisted grid for one period — the §3.J board, read-only."""
    await _require_module(db, org_id)
    _validate_period(period)
    return list(
        await db.scalars(
            select(VatReceiptControl)
            .where(VatReceiptControl.org_id == org_id, VatReceiptControl.period == period)
            .order_by(
                VatReceiptControl.supplier,
                VatReceiptControl.slot,
                VatReceiptControl.country,
            )
        )
    )


async def set_control_override(
    db: AsyncSession,
    org_id: str,
    control_id: str,
    *,
    waived: bool | None = None,
    note: str | None = None,
) -> VatReceiptControl:
    """The ONLY writer of the override columns (`waived`, `note`) — §3.J
    item 4's "manual overrides". `waived=True` means "stop chasing this
    slot": a worklist mute, NOT WO-58's claim-level legal waiver — it has
    zero effect on any claim, line, lock, freeze, checklist or ingest
    (proven end-to-end by `tests/transport/test_g3_5_receipt_control.py`).
    A `None` argument leaves that column unchanged; a no-op call (nothing
    changes) audits nothing. Org-scoped lookup — a cross-tenant id is
    indistinguishable from an unknown one (§4.4: opaque 404, never 403)."""
    await _require_module(db, org_id)
    row = await db.scalar(
        select(VatReceiptControl).where(
            VatReceiptControl.id == control_id, VatReceiptControl.org_id == org_id
        )
    )
    if row is None:
        raise NotFoundError("Receipt control row not found", code="receipt_control_not_found")

    changes: dict[str, object] = {}
    if waived is not None and waived != row.waived:
        changes["old_waived"], changes["new_waived"] = row.waived, waived
        row.waived = waived
    if note is not None and note != row.note:
        changes["old_note"], changes["new_note"] = row.note, note
        row.note = note
    if not changes:
        return row

    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_RECEIPT_CONTROL_OVERRIDE,
        target_type="vat_receipt_control",
        target_id=row.id,
        meta={"supplier": row.supplier, "period": row.period, "slot": row.slot, **changes},
        org_id=org_id,
    )
    return row


# --------------------------------------------------------------------------- #
# The orphan check
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OrphanTransaction:
    """One §3.J orphan — a transaction not covered by a registered
    invoice. A plain transport-local value type, never the ORM row
    (the `MatchedInvoice` discipline)."""

    transaction_id: str
    entity_id: str
    supplier: str
    country: str
    invoice_ref: str | None


async def orphan_transactions(
    db: AsyncSession, org_id: str, period: str
) -> list[OrphanTransaction]:
    """§3.J item 3, verbatim: *"every transaction must be covered by a
    registered invoice."* Returns every transaction in the period whose
    `invoice_ref` does NOT resolve via the ONE resolution order — which
    includes every synthetic `INPUT`/`ALL:` ref (nothing can cover those)
    and every reference no heuristic/override/sole-fallback lands. Pure
    READ: persists nothing, mutates nothing (a report function, §4.19).
    The transaction-grain complement of the slot-grain `missing` status —
    a `waived` slot does NOT suppress its orphans (different grains,
    different questions)."""
    await _require_module(db, org_id)
    _validate_period(period)

    txns = list(await db.scalars(queries.fuel_transactions(org_id, period=period)))

    cache: dict[tuple[str, str, str, str | None], MatchedInvoice | None] = {}
    orphans: list[OrphanTransaction] = []
    for txn in txns:
        matched = await _resolve_cached(
            db,
            org_id,
            entity_id=txn.entity_id,
            supplier=txn.supplier,
            country=txn.country.upper(),
            invoice_ref=txn.invoice_ref,
            cache=cache,
        )
        if matched is None:
            orphans.append(
                OrphanTransaction(
                    transaction_id=txn.id,
                    entity_id=txn.entity_id,
                    supplier=txn.supplier,
                    country=txn.country,
                    invoice_ref=txn.invoice_ref,
                )
            )
    return orphans
