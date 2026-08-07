"""Frozen claim lines + frozen VAT base at submission (G2.5; `BA_fleet_fuel.md`
C10, ADR-P3 — strengthens R30).

WHY THIS IS THE LINCHPIN (`ARCH_plan.md`'s own word for G2.5)
------------------------------------------------------------------
Everything downstream — the checklist/period-end/minimum/deadline gate stack
(G2.6), fee freezing (G2.9), status derivation (G2.7) — assumes a SUBMITTED
claim's figures are immutable: "after submission, editing or re-closing the
underlying period changes nothing about the filed claim." Without this order,
`app.services.transport.close.run_close` could rebuild a SUBMITTED claim's
lines out from under it the next month (it can't, because `build_claim_lines`
already refuses a non-`draft` claim — but nothing YET stamps `frozen_at` or
computes the claim's own `vat_eur`/`vat_local`/`currency`, so there is no
frozen figure for a future fee/gate to read in the first place).

WHAT "FROZEN" MEANS HERE, VS FLEET FUEL
-------------------------------------------
Fleet Fuel froze `vat_eur`/`vat_local` "computed over exactly the locked
`claim_set` via `invoice_lines()` — not a raw `SUM(vat_eur)` over the period,
which would wrongly include period invoices not in this claim" (C10). In this
codebase, `build_claim_lines` (G2.4) already scopes a claim's
`VatRefundClaimLine` rows to exactly that claim's own
`(entity, refund_country, period)` — a claim's own lines ARE its claim_set by
construction, so summing them directly (never a raw `FuelTransaction` period
query) is the faithful translation of C10, with no extra "exclude what's not
in this claim" step needed the way Fleet Fuel's live `invoice_lines()` query
needed one.

WHY A MIXED-CURRENCY CLAIM REFUSES TO FREEZE
-------------------------------------------------
Each EU member state charges VAT in its own single national currency, so a
`refund_country`'s invoices are, in the overwhelming case, all in ONE
currency — but nothing in this codebase enforces that at ingestion. If a
claim's lines ever DO carry more than one distinct local `currency` (data
error, a misconfigured supplier feed, or a genuinely multi-currency edge
case Directive 2008/9/EC does not anticipate), summing raw `vat_local`
figures across them would violate master-context invariant §4.14 ("no
aggregate sums across currencies without a recorded conversion... it never
labels a foreign amount EUR"). `freeze_claim_lines` refuses
(`code="claim_currency_mismatch"`) rather than guess — the same fail-toward-
blocking discipline `claim_gates.is_synthetic()`'s docstring states
explicitly.

WHY THIS RUNS INSIDE `lock.submit_claim`, NOT AS A SEPARATE CALL
---------------------------------------------------------------------
"Frozen claim lines" only means something if freezing and lock acquisition
are ATOMIC: a lost lock race must leave the claim's lines unfrozen too (the
whole transition rolls back together, WO-51's own guarantee), and a
successful submission must never leave locks acquired with an unfrozen VAT
base (or vice versa). `lock.submit_claim` calls `freeze_claim_lines` in the
SAME flush as the lock inserts + the claim's status flip.

WHY `preview_vat_base` EXISTS SEPARATELY FROM `freeze_claim_lines` (WO-56,
G2.6 slice)
------------------------------------------------------------------------------
Fleet Fuel's own submission gate order (D5) checks the Art. 17 minimum-
amount gate (R8) BEFORE the claim's figures are frozen/locked — a below-
minimum claim must never freeze or lock at all. That means the minimum gate
needs to know what the claim's `vat_eur`/`vat_local` WOULD BE, without yet
committing to freezing them. `preview_vat_base` is the read-only half of
`freeze_claim_lines`'s own computation (same currency-mismatch check, same
summation), used by `app.services.transport.minimum.below_minimum` via
`lock.submit_claim` BEFORE calling `freeze_claim_lines` for real. Both
functions share the exact same arithmetic (`_sum_lines`) so the previewed
figure and the frozen figure can never drift apart — the same drift-
prevention discipline `is_synthetic()`'s centralization exists for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.money import q2
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import queries


def _sum_lines(lines: list[VatRefundClaimLine]) -> tuple[Decimal, Decimal, str | None]:
    """The shared arithmetic: (vat_eur, vat_local, currency) summed over
    `lines`, refusing (`code="claim_currency_mismatch"`) if they span more
    than one distinct local currency (master-context §4.14) — see module
    docstring "WHY A MIXED-CURRENCY CLAIM REFUSES TO FREEZE"."""
    currencies = {ln.currency for ln in lines if ln.currency is not None}
    if len(currencies) > 1:
        raise ConflictError(
            f"Claim lines span multiple currencies {sorted(currencies)} — "
            "refusing to freeze a single VAT base across them",
            code="claim_currency_mismatch",
        )

    vat_eur = Decimal("0")
    vat_local = Decimal("0")
    for ln in lines:
        vat_eur += ln.vat_eur
        if ln.vat_local is not None:
            vat_local += ln.vat_local

    return q2(vat_eur), q2(vat_local), next(iter(currencies), None)


async def _unfrozen_lines(db: AsyncSession, org_id: str, claim_id: str) -> list[VatRefundClaimLine]:
    return list(await db.scalars(queries.vat_claim_lines(org_id, claim_id, frozen=False)))


async def preview_vat_base(
    db: AsyncSession, org_id: str, claim_id: str
) -> tuple[Decimal, Decimal, str | None]:
    """Read-only: what `claim.vat_eur`/`vat_local`/`currency` WOULD be if
    frozen right now, without mutating anything — see module docstring "WHY
    `preview_vat_base` EXISTS SEPARATELY...". Raises the SAME
    `claim_currency_mismatch` `freeze_claim_lines` would raise, so a caller
    never sees a gate pass on a figure freezing would then refuse."""
    lines = await _unfrozen_lines(db, org_id, claim_id)
    return _sum_lines(lines)


async def freeze_claim_lines(db: AsyncSession, org_id: str, claim: VatRefundClaim) -> int:
    """Freeze every currently-unfrozen `VatRefundClaimLine` for `claim` and
    stamp the claim's own `vat_eur`/`vat_local`/`currency` from EXACTLY
    those lines' totals (C10) — see module docstring. Returns the number of
    lines frozen (0 for a claim with no lines yet — an empty claim set is a
    future G2.6 gate's concern, not this function's).

    Does NOT flush or commit — the caller (`lock.submit_claim`) does both,
    in the same flush as its own lock-acquisition + status-flip writes, so
    the whole submission is one atomic unit (module docstring).
    """
    lines = await _unfrozen_lines(db, org_id, claim.id)
    vat_eur, vat_local, currency = _sum_lines(lines)

    now = datetime.now(UTC)
    for ln in lines:
        ln.frozen_at = now

    claim.vat_eur = vat_eur
    claim.vat_local = vat_local
    claim.currency = currency

    return len(lines)
