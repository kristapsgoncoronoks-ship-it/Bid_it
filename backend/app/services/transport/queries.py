"""The canonical query registry for transport (G4.1; R51; ADR-0023).

WHAT THE HARVESTED SPEC ACTUALLY ASKS FOR
-------------------------------------------
`BA_fleet_fuel.md` R51, verbatim: *"**One canonical query layer.** Every report,
export, dashboard and materialized metric derives from it; nothing forks the
math."* Its verification line is unusually concrete — *"Rename a canonical
function => every consumer breaks; no duplicate implementation exists."*
`ARCH_plan.md` G4.1 repeats it and adds the acceptance *"renaming a canonical
function breaks every consumer; no duplicate implementation exists"*, and
ADR-0023 states it as a standing projection rule: *"every figure derives from
one canonical query registry — no surface may fork the math"*, with a Risks
line that this module exists to retire for the transport half: *"The projection
rule is discipline until the canonical query registry is complete — drift is
possible in the interim."*

R38 is where that clause is already load-bearing (*"built on the CANONICAL
claims and recovery queries, NEVER a forked query"*), and WO-81 honoured it by
hand. This module makes it structural.

WHAT A "CANONICAL QUERY" IS HERE — THE CHARTER
------------------------------------------------
**Every query whose WHERE clause decides which rows a money figure is computed
over, on the two money-bearing transport tables**: `fuel_transactions` (the
source of every EUR/L, every claim line and every north-star euro) and
`vat_claim_lines` (the claim's own money grain).

That boundary is deliberate and it is where the danger actually lives. A forked
row-selection does not raise, does not fail a test and does not look wrong on a
screen — it silently returns a different set of rows, and every euro downstream
is computed over the wrong denominator. The two shapes this module was written
to de-duplicate are exactly that:

* `claim_lines.build_claim_lines` (what a claim's lines are BUILT from) and
  `checklist._unresolved_suppliers` (what the checklist REPORTS as blocking that
  same claim) carried the same four predicates, written out twice. If one drifts,
  the checklist reports a clean claim whose lines silently omit a supplier — or
  chases a supplier whose transactions were never in scope.
* `freeze._unfrozen_lines`, `claim_gates.unfrozen_synthetic_refs` and
  `document_gate._resolved_invoice_ids` each re-typed "the claim's currently
  unfrozen lines" — the set `lock.submit_claim` is about to freeze. `claim_gates`'
  own docstring already named the hazard ("never two independent line-scans that
  could drift"); it mirrored the shape by hand.

THE CONVENTION — WHO OWNS WHAT
--------------------------------
**The registry owns WHICH ROWS (the WHERE clause). The consumer owns HOW THEY
ARE READ** — projection (`.with_only_columns(...)`), ordering, grouping,
pagination. Ordering is presentation, not math: `contract_audit`'s
`(supplier, txn_date, line_seq)` and `fuel`'s `(period, supplier, txn_date,
line_seq)` stay at their call sites, where their own docstrings explain them.

Consequences of that split, each deliberate:

1. **Pure functions, no I/O.** No `AsyncSession` parameter, no `await`, no
   `db.scalars`. A builder returns a `Select`; the calling service executes it.
2. **No money arithmetic, ever.** No `Decimal`, no `q2`, no summing. Every euro
   keeps being computed by the service that already computes it — this module
   changes where a predicate is written, never what it means.
3. **No module-entitlement check.** The calling service gates first (ADR-P3
   rule 3, the transport-wide entry-point pattern); a builder is reached only
   after that gate has passed and must not imply a second one.
4. **`org_id` is the first positional parameter of every function.** A builder
   that could omit the tenant filter must not exist — that is the point of
   collapsing fourteen hand-typed `org_id ==` terms into six. Master-context
   §4.1 makes the filter mandatory and §4.4 makes its absence invisible (another
   tenant's rows come back with a 200, not a 403).

`None` MEANS "EVERY", AND WHY NOTHING IS NORMALISED HERE
----------------------------------------------------------
An optional dimension filters only when it is `is not None`. The tree's existing
call sites were not consistent about this — `contract_audit`/`fuel` used
`if supplier:` (so `""` meant "every supplier") while `rebate` used
`if supplier is not None:` (so `""` meant "the supplier named `''`"). Unifying
them on one truthiness rule would have silently changed one of the two, which is
the exact class of change this programme forbids. So the registry filters on
`is not None` and the falsy-tolerant call sites pass `supplier or None`, which
maps falsy -> None exactly and preserves both behaviours.

For the same reason the registry does NOT normalise a value. `fuel._filtered`
upper-cased `country` before comparing; that stays at the call site
(`country.upper() if country else None`). A registry that upper-cased would
change what every OTHER caller matches — `claim_scope_transactions` included —
for any value that was not already upper-cased.

NAMING — WHAT IS HARVESTED AND WHAT IS INTERPRETED
----------------------------------------------------
The module NAME is the spec's own vocabulary: `BA_fleet_fuel.md` refers to
`queries.q_savings` (§2.4) and `queries.q_ledger` (§N10) as members of the
canonical layer. `q_savings` — the same-day overpay grain — has since LANDED as
`price_comparison_transactions` (G4.7/WO-87), exactly as this paragraph
predicted it would when its board arrived. `q_ledger` is the export hub's
ledger source and is still not in the tree; inventing its grain here would be
inventing functionality (§10), so it belongs in this module when its board lands.

The `q_` prefix is a Fleet Fuel stylistic detail rather than a rule; this
codebase's own convention governs the function names (master-context §6:
"services expose verbs"), so each builder is named for the table it reads and
the cut it takes. Recorded as an interpretation, not presented as harvested.

PARTIAL HARVEST — the materialised-metric half of R51 has no subject yet
-------------------------------------------------------------------------
R51's second sentence — *"Materialized metrics have a drift check that
recomputes through the same code path, and an un-materialized period still
renders via a live fallback"* — is deliberately NOT implemented, because this
codebase materialises no transport metric: all seventeen `app/models/transport/*`
tables are sources of record, none is a rollup. A drift check over nothing would
be invented functionality. The trigger for that slice is explicit: **the first
transport rollup table**, whose recompute path must be this registry.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, func, select

from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.vat_claim import VatRefundClaimLine

# The tables this registry is the single row-selection layer for. Exported so
# the structural anti-forking test names them from one place rather than
# re-listing them (a scanner whose target list can drift from the registry it
# guards is the same defect one level up).
CANONICAL_MODELS: tuple[type[FuelTransaction] | type[VatRefundClaimLine], ...] = (
    FuelTransaction,
    VatRefundClaimLine,
)

# The cross-entity duplicate scan (R26, `capture_checks`) compares invoice
# references NORMALISED — uppercased with spaces stripped, separators KEPT.
# The expression lives here because it appears three times in one statement
# (the projection, the WHERE and the GROUP BY) and must be the same expression
# in all three; a second spelling of it would silently group differently from
# the way it filters.
NORMALIZED_INVOICE_REF: ColumnElement[str] = func.upper(
    func.replace(FuelTransaction.invoice_ref, " ", "")
)

# `BA_fleet_fuel.md` §2.5's "Avoidable overpay (same-day)" row, verbatim:
# **"diesel only"**. It is a ROW-SELECTION predicate, so it lives here and is
# written exactly once — the value is one of `PRODUCT_GROUPS`, asserted by
# `tests/transport/test_wo87_savings.py` so a rename of the group cannot leave
# this string silently matching nothing (a cut that matches nothing reads as
# "no supplier ever overcharged you", the most dangerous wrong answer this
# family can give).
DIESEL = "Diesel"


def fuel_transactions(
    org_id: str,
    *,
    entity_id: str | None = None,
    supplier: str | None = None,
    country: str | None = None,
    period: str | None = None,
    months: Sequence[str] | None = None,
    currency: str | None = None,
    product_group: str | None = None,
    ids: Sequence[str] | None = None,
) -> Select[tuple[FuelTransaction]]:
    """THE row-selection predicate over `fuel_transactions`.

    Every cut this platform takes of the validated fuel line-item store is this
    function with a different set of dimensions bound. Each optional dimension
    filters only when it is `is not None` (see the module docstring on why the
    call sites, not this function, normalise a falsy value).

    `product_group` (G4.7/WO-87) is the seventh dimension and the one that
    decides whether a €/L comparison is comparing like with like. It filters on
    the exact `PRODUCT_GROUPS` value `product_group.derive_product_group`
    assigned — never a pattern, never a case-fold — for the same reason nothing
    else here normalises: a registry that normalised would change what every
    other caller matches.

    `period` and `months` are the two period shapes the tree actually uses —
    `period == "YYYY-MM"` (the accounting month, half the WO-50 natural key) and
    `period.in_(months)` (a claim reference period expanded through
    `claim_lines.period_months`). Passing BOTH is a programming error, not user
    input, so it raises `ValueError` rather than an `AppError` — the same
    posture `fuel_card_parser.select` takes for an unroutable file.

    Returns an unordered `Select` on purpose: ordering is the consumer's, and
    two consumers reading the same rows in different orders are not forking
    anything.
    """
    if period is not None and months is not None:
        raise ValueError("pass period or months, not both — they are the same dimension")

    stmt = select(FuelTransaction).where(FuelTransaction.org_id == org_id)
    if entity_id is not None:
        stmt = stmt.where(FuelTransaction.entity_id == entity_id)
    if supplier is not None:
        stmt = stmt.where(FuelTransaction.supplier == supplier)
    if country is not None:
        stmt = stmt.where(FuelTransaction.country == country)
    if period is not None:
        stmt = stmt.where(FuelTransaction.period == period)
    if months is not None:
        stmt = stmt.where(FuelTransaction.period.in_(list(months)))
    if currency is not None:
        stmt = stmt.where(FuelTransaction.currency == currency)
    if product_group is not None:
        stmt = stmt.where(FuelTransaction.product_group == product_group)
    if ids is not None:
        stmt = stmt.where(FuelTransaction.id.in_(list(ids)))
    return stmt


def claim_scope_transactions(
    org_id: str,
    *,
    entity_id: str,
    refund_country: str,
    months: Sequence[str],
) -> Select[tuple[FuelTransaction]]:
    """The transactions in ONE claim's scope — `(entity, refund_country,
    reference period)`, R2's own grain.

    The single most important entry in this registry: `claim_lines.
    build_claim_lines` turns exactly these rows into claimable euros, and
    `checklist._unresolved_suppliers` tells an operator which suppliers among
    exactly these rows are blocking the claim. Before this function the two
    carried the same four predicates written out twice, and nothing in the tree
    stopped one of them from acquiring a fifth.

    `months` is the caller's `claim_lines.period_months(claim.ref_period)` — the
    ONE quarter/year expansion, not re-expressed here (this module must not
    import the service layer it is read by).
    """
    return fuel_transactions(org_id, entity_id=entity_id, country=refund_country, months=months)


def price_comparison_transactions(
    org_id: str,
    *,
    period: str,
    country: str | None = None,
) -> Select[tuple[FuelTransaction]]:
    """The rows BOTH overpay grains are computed over — `BA_fleet_fuel.md`
    §2.4 names this cut `queries.q_savings` (G4.7).

    §2.5's "Avoidable overpay (same-day)" row states the predicate in three
    words that all live here: *"diesel only"*, one accounting month, and a
    comparison that never leaves the **country of supply**. The country term is
    optional because a same-day/same-country comparison is already PARTITIONED
    on country by the consumer — narrowing the scope to one country cannot
    change any group's contents, so it is a scope filter, not a maths change.

    There is deliberately NO `supplier` parameter. Filtering the comparison set
    by supplier would remove the very rows that decide who the *cheapest rival*
    was, so the same query would silently answer a different question — the
    exact failure mode this registry exists to make impossible. A consumer that
    wants one supplier's findings filters the FINDINGS, never the rows.

    Named for the cut it takes rather than harvested as `q_savings`: the `q_`
    prefix is a Fleet Fuel stylistic detail and master-context §6 governs the
    function names (the interpretation this module's docstring already records).
    """
    return fuel_transactions(org_id, period=period, country=country, product_group=DIESEL)


def fuel_transaction_by_natural_key(
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    period: str,
    line_seq: int,
) -> Select[tuple[FuelTransaction]]:
    """The WO-50 structural natural key `(org, entity, supplier, period,
    line_seq)` — what makes ingestion insert-or-no-op rather than
    DELETE-by-period (G1.2).

    Registered here even though its consumer is a WRITE path: it still decides
    WHICH ROW an amount lands on, and a fork of it would re-insert a
    transaction that already exists (double-counting every euro of that line in
    every figure downstream) or silently update the wrong one.
    """
    return fuel_transactions(org_id, entity_id=entity_id, supplier=supplier, period=period).where(
        FuelTransaction.line_seq == line_seq
    )


def fuel_transactions_by_normalized_invoice_ref(
    org_id: str,
    normalized_refs: Sequence[str],
) -> Select[tuple[FuelTransaction]]:
    """Registered transactions whose invoice reference normalises into
    `normalized_refs` — the cross-entity duplicate scan of R26 (`capture_checks`),
    org-wide across every entity by design.

    Only rows that HAVE a reference participate: a NULL `invoice_ref` cannot
    duplicate anything, and letting it through would make every un-referenced
    line normalise to the same empty string. The consumer projects and groups
    this to invoice grain using `NORMALIZED_INVOICE_REF`, the same expression
    the filter is built from.
    """
    return select(FuelTransaction).where(
        FuelTransaction.org_id == org_id,
        FuelTransaction.invoice_ref.is_not(None),
        NORMALIZED_INVOICE_REF.in_(list(normalized_refs)),
    )


def vat_claim_line_criteria(
    org_id: str,
    claim_id: str,
    *,
    frozen: bool | None = None,
) -> tuple[ColumnElement[bool], ...]:
    """The claim-line cut as bare WHERE criteria, for the ONE statement that is
    not a SELECT: `claim_lines.build_claim_lines` DELETEs the unfrozen lines
    before rebuilding them (R2's live rebuild).

    That delete is the most dangerous expression of this predicate in the tree —
    a drift there removes the wrong rows rather than merely reporting the wrong
    ones — so it must be the SAME cut the gates scan, not a hand-written twin.
    `vat_claim_lines` below is this tuple wrapped in a `select`; nothing else
    should need the criteria directly.
    """
    criteria: list[ColumnElement[bool]] = [
        VatRefundClaimLine.org_id == org_id,
        VatRefundClaimLine.claim_id == claim_id,
    ]
    if frozen is True:
        criteria.append(VatRefundClaimLine.frozen_at.is_not(None))
    elif frozen is False:
        criteria.append(VatRefundClaimLine.frozen_at.is_(None))
    return tuple(criteria)


def vat_claim_lines(
    org_id: str,
    claim_id: str,
    *,
    frozen: bool | None = None,
) -> Select[tuple[VatRefundClaimLine]]:
    """A claim's materialized lines. `frozen=None` is every line, `False` the
    currently-UNFROZEN set, `True` the FROZEN set.

    The three cuts are the three questions this codebase asks of a claim's
    money grain, and they were three separate predicates before this function:

    * `frozen=None`  -> `claim_lines.list_claim_lines`, the route-facing read.
    * `frozen=False` -> the set `lock.submit_claim` is about to freeze, scanned
      by the R3 synthetic-ref gate (`claim_gates`), the R10 document gate
      (`document_gate`) and the G2.5 freeze/preview (`freeze`). These three MUST
      scan identical rows: a gate that passes on a set the freeze does not
      freeze is a gate that proved nothing.
    * `frozen=True`  -> `claim_pack`, the filing artifacts — frozen lines only,
      because an artifact rendered from live rows could differ from what was
      actually filed (G2.12/ADR-P3).
    """
    return select(VatRefundClaimLine).where(
        *vat_claim_line_criteria(org_id, claim_id, frozen=frozen)
    )


def resolved_vat_claim_lines(
    org_id: str,
    claim_id: str,
) -> Select[tuple[VatRefundClaimLine]]:
    """The claim's currently-unfrozen lines that RESOLVED to a real registered
    invoice — R10's own set (`document_gate`).

    A narrower named cut rather than a boolean on `vat_claim_lines`, because it
    answers a different question: an `UNMATCHED` line's `invoice_id` is always
    NULL, so "resolved" and "unfrozen" are not two flags on one query but the
    document gate's whole subject. R3 refuses the unresolved ones separately and
    earlier (`lock.submit_claim`'s gate order), so this cut must not silently
    absorb that refusal.
    """
    return vat_claim_lines(org_id, claim_id, frozen=False).where(
        VatRefundClaimLine.invoice_id.is_not(None)
    )


__all__ = [
    "CANONICAL_MODELS",
    "DIESEL",
    "NORMALIZED_INVOICE_REF",
    "claim_scope_transactions",
    "fuel_transaction_by_natural_key",
    "fuel_transactions",
    "fuel_transactions_by_normalized_invoice_ref",
    "price_comparison_transactions",
    "resolved_vat_claim_lines",
    "vat_claim_line_criteria",
    "vat_claim_lines",
]
