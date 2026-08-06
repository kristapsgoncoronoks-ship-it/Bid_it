"""The shared claim-gate predicates (R3) — load-bearing, deliberately centralized.

`is_synthetic()` is THE single predicate for "this claim line is not tied to
one real, registered invoice". Fleet Fuel's own BA is explicit about why it
must be exactly one function (`docs/plan/shared/specs/BA_fleet_fuel.md` C2):

    "A pack containing ANY synthetic line CANNOT be filed. The same predicate
    is used by: the lock gate ... the checklist gate ... the readiness check
    ... the workbook builder. This centralization is deliberate: so they all
    block the same set of synthetic refs."

This module shipped (WO-49) BEFORE any consumer existed, because the
INTERFACE is the load-bearing artifact: every gate must import THIS function
rather than re-deriving the pattern, or the drift R3 exists to prevent creeps
back in on day one. The named consumers are now real:

- the WORKBOOK BUILDER (WO-74): `claim_pack._load_pack` refuses any FROZEN
  synthetic line (`synthetic_line_in_pack`) — its scan stays inline there
  because it operates on line rows already loaded for rendering;
- the LOCK GATE (WO-75): `lock.submit_claim` calls this module's
  `enforce_no_synthetic_lines()` at the head of D5's engine-gate group
  (`BA_fleet_fuel.md` C9: the `bad` gate runs before `claim_set`/locks/
  doc-gate/freeze) — a claim with an unresolved line never freezes or locks;
- the CHECKLIST / STAGE view (WO-59/WO-60): `checklist.submission_checklist`'s
  `unresolved_refs` item (consumed by `status.derive_stage` -> `1A`) — its
  scan deliberately stays at the TRANSACTION level because the advisory
  surface must NAME the offending suppliers, which the collapsed `UNMATCHED`
  line cannot yield (see `checklist.py`'s own docstring); the set of blocked
  claims is still defined by this one predicate, reached through
  `invoice_match.resolve_invoice_ref`'s own short-circuit on it.

WHY THIS FAILS TOWARD BLOCKING (fail-closed), not open
---------------------------------------------------------
A synthetic ref means "we cannot prove which real invoice this euro amount
belongs to". Filing it anyway risks the WHOLE claim on a probabilistic guess;
CJEU case law and the Fleet Fuel BA agree the correct failure mode is to
refuse the line (and by extension the pack) rather than invent a match. Every
future consumer of `is_synthetic()` must preserve this: a bug that makes the
predicate return `False` too often is a silent forfeiture-of-money bug, not a
convenience.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.transport.vat_claim import VatRefundClaimLine


def is_synthetic(ref: str, vat_id: str | None = None) -> bool:
    """R3 — Art. 9/15 Dir. 2008/9/EC (a claim line must be tied to a real,
    registered invoice); harvested verbatim from `BA_fleet_fuel.md` C2 /
    the R3 row of its requirements table:

        is_synthetic(ref, vat_id) ==
            "INPUT" in ref or ref.startswith("ALL:") or ref == "UNMATCHED"
            or "INPUT" in str(vat_id)

    `ref` is the claim line's `invoice_ref` (never the buyer-supplied
    aggregate, never the resolved invoice number placeholder). `INPUT` in
    either the ref or the vat_id marks a hand-entered placeholder value (the
    Fleet Fuel capture UI's own convention for "we don't have this yet" —
    R45 reuses the identical substring check for `_field_ok`). `ALL:` marks a
    country-level aggregate a period once collapsed unresolved transactions
    into (never a real invoice). `UNMATCHED` is the terminal state of
    `_resolve_inv`'s note-matching chain when nothing else resolved it.

    A pack containing ANY line for which this returns `True` cannot be filed
    — see the module docstring for which future gates enforce that.
    """
    return (
        ("INPUT" in ref)
        or ref.startswith("ALL:")
        or (ref == "UNMATCHED")
        or ("INPUT" in str(vat_id))
    )


async def unfrozen_synthetic_refs(db: AsyncSession, org_id: str, claim_id: str) -> list[str]:
    """The non-raising half of the R3 LOCK GATE (WO-75): the sorted, distinct
    synthetic `invoice_ref`s among a claim's currently-UNFROZEN materialized
    lines — exactly the rows `freeze.freeze_claim_lines` is about to stamp,
    which is why the scan reads the lines table and never `submit_claim`'s
    caller-supplied `invoices` tuples (the same validates-what-gets-frozen
    reasoning `document_gate.py` records for R10). Empty means every line is
    tied to a real invoice — or there are no materialized lines at all, which
    passes trivially (again the R10 semantics: nothing to check; the Art. 17
    minimum gate separately refuses a zero-base claim).

    Mirrors `document_gate.missing_document_invoice_ids`'s shape: one
    org-scoped query, shared by the blocking gate below and any future
    read-only preview — never two independent line-scans that could drift.
    """
    rows = await db.execute(
        select(VatRefundClaimLine.invoice_ref, VatRefundClaimLine.vat_id).where(
            VatRefundClaimLine.org_id == org_id,
            VatRefundClaimLine.claim_id == claim_id,
            VatRefundClaimLine.frozen_at.is_(None),
        )
    )
    return sorted({ref for ref, vat_id in rows if is_synthetic(ref, vat_id)})


async def enforce_no_synthetic_lines(db: AsyncSession, org_id: str, claim_id: str) -> None:
    """R3 as a submission-blocking LOCK-GATE consumer (WO-75) — the harvested
    lock gate `bad = [... if _synthetic(r)]` -> "BLOCKED - unresolved invoice
    refs" (`BA_fleet_fuel.md` C2/C9), raised here as 409
    `code="unresolved_invoice_refs"` naming every offender so the operator
    fixes them all in one pass (the same message discipline R6/R10 use).

    Fails CLOSED, deliberately: a synthetic ref means "we cannot prove which
    real invoice this euro amount belongs to", and freezing/locking it anyway
    would park the claim — and its invoice locks (R4/R5) — on a figure the
    filing artifacts (`claim_pack`) will refuse to render later. Refusing at
    submit keeps the claim in `draft`, where the fix (note-matching, an
    admin override, or an R15 waiver for a genuinely-uninvoiced supplier)
    is still cheap. A waived supplier never trips this gate because its
    transactions never became lines at all (`claim_lines.build_claim_lines`,
    WO-58's "excluded by construction").
    """
    offenders = await unfrozen_synthetic_refs(db, org_id, claim_id)
    if offenders:
        raise ConflictError(
            f"Unresolved invoice ref(s) in claim: {', '.join(offenders)} — every claim "
            "line must be tied to a real, registered invoice before submission",
            code="unresolved_invoice_refs",
        )
