# Project profitability — design

**Status:** design for review, no code yet (owner chose "design doc first",
2026-08-16). Decisions already made by the owner, recorded up front because they
shape everything below: **salary enters as a manual cost line** (no payroll);
**costs allocate at line level and by percentage** (whole-invoice linking is the
degenerate case, not the model); **the P&L freezes at close**.

---

## 1. The idea, in the owner's scenario

A transport company wins a cargo contract. In the system they:

1. **open a project** for the contract and attach the signed contract document;
2. **issue their sales invoice(s)** to their customer under the project — the
   revenue side;
3. **receive supplier invoices** (fuel card, tolls, repairs) whose lines are
   allocated to the project — the cost side;
4. add **driver expense reports** and a **wage cost line** for the job;
5. **close the project** and read its profitability: revenue − costs = the
   number that tells them whether the contract was worth having.

That last number is the point. Every existing feature (capture, VAT recovery,
expense reports) feeds costs or recovers cash; this is the screen where a client
finally sees per-contract profit, which is the question a transport operator
actually runs their business on. It is also retention moat: the system that
holds three years of per-contract profitability history is the system nobody
churns off.

## 2. What already exists (verified against the tree, not recalled)

| Piece | State |
|---|---|
| `Project` master (`projects`, code+name, **active→closed→archived** lifecycle, optimistic concurrency, archive-never-delete) | exists — `app/services/costing.py` |
| Received invoices → project (`invoices.project_id`, composite tenant FK) | exists |
| Expense items → project (`expense_items.project_id`, same FK shape) | exists |
| Issued invoices → project | **missing** — the revenue side has no project column at all |
| Contract document on a project | missing (but the vault + versioning + e-sign seams exist to hang it on) |
| Salary/wage cost | missing entirely — and stays out of scope as payroll |
| Line-level / % cost allocation | missing — `project_id` is whole-document today |
| The P&L rollup + freeze | missing |

So this is the completion of a half-wired subsystem, not a new one.

## 3. Data model

### 3.1 Revenue link (phase 1)

`issued_invoices.project_id` — GUID, nullable, composite tenant FK
`(org_id, project_id) → projects(org_id, id)`, exactly the shape `invoices`
and `expense_items` already use (three-layer tenancy: column FK + ORM guard +
RLS — no new table, so no RLS work). Chosen on the issue screen next to the
issuer picker; editable while the project is open.

A credit note inherits its parent invoice's project — revenue reversals must
land where the revenue did, or every credited project overstates.

### 3.2 Contract attachment (phase 1)

Reuse `issued_invoice_attachments`' pattern: `project_documents` (org_id,
project_id, sha256, filename, kind='contract'|'other', uploaded_by). Bytes in
the existing content-addressed store. E-sign integration is a later phase — the
attachment slot is the seam it will plug into.

### 3.3 Manual cost lines — salary and anything else uninvoiced (phase 1)

`project_cost_entries`: org_id, project_id, label, category
('wages'|'per_diem'|'other'), amount, currency (EUR-normalised via the existing
FX provenance rules), entry_date, created_by, note. Audited like every
mutation.

**Explicitly not payroll.** No employee master, no tax, no social
contributions, no net/gross. A wage cost line makes the P&L honest — driver
wages are a top-3 transport cost — without entering a regulated domain this
product has no business in. The docstring will say so as loudly as this doc.

### 3.4 Line-level + percentage allocation (phase 2)

The transport reality: one Eurowag invoice covers ten deliveries. Whole-invoice
linking books it all on one project and the P&L is wrong precisely for the
target client.

- `line_items.project_id` (nullable, same composite FK) — a line's explicit
  project wins over the invoice's.
- `invoice_project_splits` (org_id, invoice_id, project_id, percent) for the
  by-% case, `SUM(percent) = 100` enforced; applies to whatever amount is not
  already line-allocated.
- Precedence, checked in one place: **line > split > whole-invoice
  `project_id`**. Money math via the existing money rules (Decimal,
  ROUND_HALF_UP); rounding residue from a % split lands on the largest share so
  the parts always sum to the invoice.

Same three shapes for expense items (line level already exists there as
`expense_items.project_id` — only the % split is new).

### 3.5 The P&L, and the freeze at close (phase 1 live, phase 2 frozen)

`project_pnl(org_id, project_id)` computes, NET EUR, FX-normalised:

```
revenue    = Σ issued invoice (lines) on the project  − credits
costs      = Σ received-invoice allocation + Σ expense allocation + Σ cost entries
profit     = revenue − costs;  margin = profit / revenue
```

**Close freezes it.** `costing`'s existing `active→closed` transition gains a
side effect: compute the P&L and store a snapshot (`projects.closed_pnl_json` +
`closed_at`, audited). After close:

- the stored figure is what every screen shows — a late fuel invoice cannot
  silently change a number the client already reported to their customer or
  bank;
- documents can still be allocated to the closed project (they genuinely
  belong to it), but they accumulate in a visible **"arrived after close"**
  adjustment line shown next to the frozen figure — the drift is displayed,
  never silent;
- `closed→active` (reopen) is the existing transition, audited; reopening
  discards the snapshot and says so.

This is the same philosophy as the invoice locks and the archive: a number
someone acted on must not move behind their back.

### 3.6 Deletion-chain interaction (must not be forgotten)

A binned invoice's allocation vanishes from the P&L with it (the soft-delete
guard already does this); restore brings it back. An invoice that reaches the
**platform archive** while its project is still open leaves the P&L — correct,
it left the books — but the close snapshot happens at close time, so a closed
project's frozen figure is immune. The archive's `line_items_json` keeps the
allocation columns so a three-year-old P&L question remains answerable.

## 4. Screens

1. **Project page** (extends the existing Cost objects master): header
   (code/name/dates/status/contract), the P&L card (live while open, frozen +
   adjustments after close), and three tabs listing the linked issued invoices,
   supplier invoice allocations, and expenses + cost entries. Add-cost-line
   form inline.
2. **Issue screen**: a project picker next to the existing issuer picker.
3. **Invoice review/detail**: per-line project picker + a "split by %" dialog
   (phase 2).
4. **Projects list**: code, name, status, revenue, costs, profit, margin —
   sortable by margin, because "which contracts lose money" is the question.

## 5. Phases

- **Phase 1 — the loop closes:** `issued_invoices.project_id` + migration ·
  contract attachment · `project_cost_entries` · `project_pnl` (live) · the
  project page + pickers. A client can run the owner's scenario end to end;
  allocation is whole-document.
- **Phase 2 — the numbers become right:** line-level + % allocation with the
  precedence rule · freeze-at-close + after-close adjustments · margin column
  on the list.
- **Phase 3 — depth, each its own decision:** e-sign the contract in-app ·
  budget-vs-actual per project (extend `budget.py` beyond monthly categories) ·
  per-km / per-delivery unit economics (needs quantity capture) · a
  profitability report export.

Phase 1 and 2 are each roughly the size of the recycle-bin build. Nothing here
blocks, or is blocked by, the remaining approved backlog (bin for other
entities, VAT-claim link).

## 6. Invariants (the test list, in prose)

1. Revenue and costs only ever roll up **within one org** (composite FKs +
   guard + RLS — and the P&L query re-asserts org anyway, belt-and-braces).
2. An allocation's parts always sum to the document: % splits total 100, line
   precedence never double-counts, rounding residue is deterministic.
3. A **closed** project's stored P&L never changes; post-close arrivals are
   visible adjustments; reopen is explicit and audited.
4. A credit note's project follows its parent.
5. Binned documents leave the live P&L; restored ones return; the close
   snapshot is immune to both.
6. Cost entries are audited with who/when/what (and now, from where — the
   audit trail carries IP).
7. No payroll semantics anywhere: a wage line is a labelled amount, nothing
   more.

## 7. Open questions for the owner (none block phase 1)

1. Should a **closed** project refuse new allocations outright instead of
   accumulating adjustments? (Doc assumes: allow + display, because the fuel
   invoice for the job's last week genuinely arrives after the job ends.)
2. Who may close/reopen a project — admin/owner only, or any processor?
   (Doc assumes admin/owner, matching restore's narrower-than-delete pattern.)
3. Should the VAT actually **recovered** on a project's fuel/toll invoices
   show as a P&L line (recovered cash is part of the contract's true economics,
   and it is this product's signature move)? Doc assumes yes, as a separate
   visible line, phase 2.
