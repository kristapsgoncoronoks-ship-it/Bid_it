# ADR-0029 — `reclaimable_tax` is wired; the "Reclaimable VAT" figure excludes drafts and rejections (C1.8, WO-43)

**Status:** Accepted — implemented (WO-43, board C1.8).

## Context

`ExpenseItem.reclaimable_tax` (`app/models/expense.py`) has existed since the
expense module's data-model slice: a per-line boolean, default `True`,
documented as "whether the tax on this item is reclaimable (some
categories/countries aren't)". It was captured on every write
(`app/services/expenses.py::_build_item`, later `build_items`) and read by
**nothing** — `ARCH_plan.md` C1.8 named this verbatim.

Meanwhile two user-facing surfaces both label a figure "Reclaimable VAT" and
both get it wrong the same way:

- `GET /expenses/summary` (`app/api/routes/expenses.py`) computed
  `reclaimable_vat = SUM(ExpenseReport.vat_total)` over **every** report
  belonging to the caller — including `draft` (not yet reviewed by the
  claimant themselves, figures can still change) and `rejected` (never
  approved — the tax was never going to be reclaimed on that spend).
- The expense-report PDF export (`build_pdf`) printed a `"Reclaimable VAT"`
  row that was actually just `report.vat_total` — the **total** tax paid on
  the report, not the reclaimable subset.

Both bugs share one root cause: `vat_total` (total tax on the report) was
being used as a stand-in for "reclaimable VAT" everywhere, and
`reclaimable_tax` — the one field that actually distinguishes the two — was
never consulted. A finance lead reading either the dashboard KPI or the PDF
handed to their bookkeeper was shown a bigger number than they can actually
claim back, on every tenant with even one non-reclaimable line (e.g. client
entertainment, VAT-exempt categories, a country that disallows the
deduction) or one draft/rejected report sitting in the list.

## Decision

**Wire the field** (ARCH_plan's own stated preference: "Either wire the
field or delete it — and fix the figure to exclude drafts and rejected
reports either way. ... wire it (preferable — they are the natural capture
states)" is the C1.9 phrasing for a sibling case; the same reasoning applies
here — the field already exists, is already captured on every write, and
maps to a real accounting distinction a claimant can toggle per line).

1. **New service functions** (`app/services/expenses.py`), not a route-level
   query — keeps the business rule in one place, callable from both the
   summary endpoint and the PDF export:
   - `reclaimable_vat_of(items)` — pure, in-memory: sums `vat_amount` over
     items where `reclaimable_tax` is true. Used by `build_pdf`, which
     already holds `report.items` in memory.
   - `reclaimable_vat_total(db, org_id, employee_id)` — the query form for
     `/expenses/summary`: joins `ExpenseItem` → `ExpenseReport`, filters to
     the org, the employee, `reclaimable_tax = true`, and
     `status NOT IN ('draft', 'rejected')`.
2. **`vat_total` keeps its existing meaning** — total tax paid on the
   report, independent of reclaimability. It is still what the PDF's line
   items show per-row and what `compute_totals` returns; nothing about it
   changes. A **new**, separate `"VAT"` row is added above `"Reclaimable
   VAT"` in the PDF so both figures are visible and distinguishable, rather
   than silently replacing one meaning with another under the same label.
3. **The exclusion rule mirrors AR's own precedent** (`vat_refund`-style
   readiness gates elsewhere in the codebase, and simply the plain English
   of "reclaimable"): a `draft` hasn't been reviewed yet — its items can
   still be edited or deleted — and a `rejected` report was explicitly
   refused reimbursement, so its VAT was never going to be reclaimed via
   this expense at all. Every other status (`submitted`,
   `partially_approved`, `approved`, `returned`, `marked_for_reimbursement`,
   `reimbursed`) counts — including ones still awaiting a decision, since
   the tax was genuinely paid and remains presumptively reclaimable while a
   decision is pending or once granted.

**Rejected alternative — delete the field.** Deleting `reclaimable_tax`
would require a migration, would drop real signal a claimant already
enters, and would leave the codebase unable to distinguish "VAT I paid" from
"VAT I can claim back" at all — the exact distinction the KPI's own label
promises. Wiring is strictly cheaper (no migration — the column and its
capture path already exist) and strictly more correct.

## Consequences

- **No migration.** `reclaimable_tax` was already a NOT NULL column with a
  `True` default on every existing row — every pre-existing item is treated
  as reclaimable by default, matching its prior (accidental) behaviour
  exactly. Only items a claimant has explicitly marked non-reclaimable
  change the total.
- **`GET /expenses/summary`'s wire shape is unchanged** — `reclaimable_vat`
  is still a `Decimal` on `ExpenseSummary`; only its computation changed.
  No SPA change required.
- **The PDF gains one row** (`"VAT"`, the pre-existing total) alongside the
  corrected `"Reclaimable VAT"` row — additive, not a shape change (the PDF
  has no machine-readable schema to break).
- **`tests/test_expenses.py::test_foreign_currency_eur_and_summary`'s
  existing assertion (`reclaimable_vat == "7.50"`) is unaffected**: its one
  report is `submitted` (not draft/rejected) and both its items default
  `reclaimable_tax=True`, so the corrected computation yields the same
  figure as the old (buggy) one for that exact fixture — proof the fix is
  behaviour-preserving on the happy path and only changes the cases it was
  meant to (a non-reclaimable item, or a draft/rejected report in the mix).
- **New regression coverage** (`tests/test_expenses.py`): a report with one
  reclaimable and one non-reclaimable item asserts the summary and the PDF
  total exclude the non-reclaimable line; a draft and a rejected report in
  the same employee's history are asserted **absent** from the summary
  figure entirely (not even at zero — their VAT never enters the sum).

## Revisit when

A jurisdiction-specific partial-reclaim rate (e.g. "50% of meal VAT is
reclaimable") is wanted — today `reclaimable_tax` is all-or-nothing per
line, matching the existing boolean column; a percentage would need a new
column and migration, deliberately out of scope here.
