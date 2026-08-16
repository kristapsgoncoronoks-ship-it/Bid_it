# Project profitability — design

**Status:** design for review, no code yet (owner chose "design doc first",
2026-08-16). Decisions already made by the owner, recorded up front because they
shape everything below: **salary enters as a manual cost line** (no payroll);
**costs allocate at line level and by percentage** (whole-invoice linking is the
degenerate case, not the model); **the P&L freezes at close**.

---

## 1. The idea — industry-neutral by design (owner requirement, 2026-08-16)

**This is a GENERAL feature for every kind of customer, not a transport
feature.** The owner's explicit direction: a builder, a landscaping company, a
consultancy, a transport operator — anyone whose work arrives as jobs/contracts
must be able to run the same loop. Nothing in the model may assume an industry;
industry words may appear in EXAMPLES only, never in schema, code, copy or
tests' subjects. (The concrete guard: no field, enum value, label or route in
this feature may name a vehicle, cargo, fuel, site, crew or any other
industry noun — `docs/design` reviews check this list.)

The loop, generically:

1. **open a project** for a won contract/job and attach the signed contract;
2. **issue sales invoice(s)** to the customer under the project — revenue;
3. **allocate received supplier and SUBCONTRACTOR invoices** to it — costs
   (a subcontractor's invoice is just a received invoice allocated to the
   project; it needs no special machinery, and that is a feature);
4. add **employee expense reports** and **manual cost lines** (wages, per
   diems, equipment hire — whatever the industry calls its uninvoiced costs);
5. **close the project** and read revenue − costs.

The same five steps, three industries:

| | Transport operator | Builder | Landscaping company |
|---|---|---|---|
| Project | cargo contract | house renovation | seasonal grounds contract |
| Revenue | delivery invoices | stage invoices to the client | monthly service invoices |
| Allocated costs | fuel card, tolls | materials, subcontractors | fuel, materials, equipment hire |
| Expense reports | driver travel | site crew | field crew |
| Cost lines | driver wages | crew wages | crew wages |

That final number is the point: per-contract profit is the question every
project-shaped business runs on, whatever the industry. It is also retention
moat — the system holding three years of per-project profitability history is
the system nobody churns off.

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
contributions, no net/gross. A wage cost line makes the P&L honest — in most
project businesses wages are a top-3 cost — without entering a regulated domain this
product has no business in. The docstring will say so as loudly as this doc.

### 3.4 Line-level + percentage allocation (phase 2)

The shared-cost reality, in every industry: one fuel-card invoice covers ten
deliveries; one builders-merchant invoice covers three sites; one equipment
lease covers every job that month. Whole-invoice linking books it all on one
project and the P&L is wrong for exactly the clients who need it most.

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

- the stored figure is what every screen shows — a late supplier invoice cannot
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
  per-unit economics (needs quantity capture) · a profitability report export.

Phase 1 and 2 are each roughly the size of the recycle-bin build. Nothing here
blocks, or is blocked by, the remaining approved backlog (bin for other
entities, VAT-claim link).

## 5a. The FULL project lifecycle — owner vision, recorded 2026-08-16

The owner's complete picture, stated so it is not lost while phases 1–3 ship.
Phase 1 built the middle of this arc; the vision is the WHOLE arc, and every
stage is a document the client already produces somewhere else today:

> **open project → offer/estimate → contract → invoicing according to the
> contract → project costs (wages, materials, consumables, transport, and
> others) → acceptance & handover → final invoicing**

Mapped against what exists:

| Stage | State today | What it becomes |
|---|---|---|
| Open project | ✅ phase 1 (`projects` master) | unchanged |
| **Offer / estimate** | ❌ nothing | a priced offer document issued FROM the project before any contract — versionable (offers get revised), convertible: an accepted offer seeds the contract's scope and the invoicing plan. The project's first artifact, and the first number later compared against the final P&L (estimated vs. actual margin — the single most instructive figure a project business can see). |
| Contract | ✅ phase 1 (attachment) · phase 3 e-sign | plus **standardized templates** (below) |
| Invoicing per contract | ✅ phase 1 (project picker) | plus an **invoicing plan**: the contract's agreed schedule (stage/advance/interim amounts) tracked against what was actually issued — "contracted 3 × 10,000, issued 2" is a live receivable the client can see instead of remember |
| Project costs | ✅ phase 1 (invoices, expenses, manual lines incl. wages) · phase 2 allocation | generic cost categories already cover materials/consumables/transport via allocation + labelled manual lines — deliberately no industry taxonomy |
| **Acceptance & handover** | ❌ nothing | a project state between "work done" and "closed": an acceptance document (act of acceptance/handover — standard commercial practice in this product's markets) generated from a template, countersigned by the customer (e-sign seam), stored on the project like the contract. Acceptance is what makes the final invoice UNARGUABLE — the customer has signed that the work is done. |
| **Final invoicing** | ❌ as a concept | the closing invoice tied to acceptance. **ADJUSTABLE/DYNAMIC (owner decision 2026-08-16):** the invoicing plan's computed remainder is a starting point, and unexpected costs, damages and claims — either party's — are added as explicit, labelled adjustment lines, so the invoice reconciles as contracted sum ± named adjustments = final total and the P&L explains the difference instead of hiding it. A negative adjustment that flips the sign becomes a credit note through the existing machinery, never a negative invoice. Linked to acceptance by default, gate as a per-org toggle. Feeds phase 2's close-freeze naturally: acceptance → final invoice → close → frozen P&L. |

**Standardized document templates (owner direction, lawyer committed
2026-08-16):** the platform provides contract and acceptance-document templates
a customer can use — prefilled from the project (parties from the issuer
registry + customer master, scope, amounts, dates), stored per-org so a
business can adapt its own wording, and rendered through the existing PDF
path. Same industry-neutral rule: templates carry placeholders, never industry
nouns. **The owner's lawyer will produce the standardized base texts**; until
they land, phase 5 builds and ships the template MACHINERY against per-org
custom templates — it is identical either way, so legal is off the critical
path. **Offer numbering is client-configurable** (per-org prefix/pattern/
counter; the platform enforces only per-org uniqueness).

Sequencing note: offer/estimate and the invoicing plan are the natural
**phase 4** (they extend the revenue side phase 1 built); acceptance +
templates + final invoicing are **phase 5** (they extend the close that
phase 2 builds). Nothing in phases 1–3 needs rework to add them — the stages
slot in front of and behind the existing loop, which is the test that the
lifecycle model is right.

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
   accumulating adjustments? (Doc assumes: allow + display, because the supplier
   invoice for the job's last week genuinely arrives after the job ends.)
2. Who may close/reopen a project — admin/owner only, or any processor?
   (Doc assumes admin/owner, matching restore's narrower-than-delete pattern.)
3. Should VAT actually **recovered** on a project's invoices show as a P&L
   line (recovered cash is part of the contract's true economics, and it is
   this product's signature move)? Doc assumes yes, as a separate visible line,
   phase 2 — and CONDITIONAL: it renders only for tenants with the VAT module
   active, so the generic P&L stays generic and the line is an overlay, never
   a dependency. A builder who never touches VAT recovery sees a P&L with no
   empty slot where a transport feature would have been.
