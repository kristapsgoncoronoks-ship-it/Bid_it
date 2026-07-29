# ADR-0023 — Platform evolution: 8 bounded contexts, 2 projection layers, and the transport-vertical seam

**Status:** Accepted (contexts + projection rules are in effect today; the transport
vertical itself is future work — this ADR fixes its binding rules *before* it is built).
Extends ADR-0001 (modular monolith), ADR-0004 (tenant isolation).

**Implementation status (WO-49, M3 opener):** rules 1-5 are now CI-enforced, not
just documented. `app/models/transport/vat_claim.py` (`VatRefundClaim`/
`VatRefundClaimLine`, the `(org, entity, refund_country, ref_period)` grain,
R1), `app/services/transport/claim_gates.py::is_synthetic()` (R3, the single
predicate every future gate must call), the `transport` module entitlement
(default off, `app/services/modules.py`), and the four new `Permission`
members (rule 5) have landed. `tests/test_boundaries.py::
test_transport_services_do_not_import_other_domain_models` makes rule 2 an
enforced CI assertion, not a promise. Still future work (tracked as ADR-P3's
G2.2 onward in `docs/plan/plan-a/ARCH_plan.md`): the lock table
(`vat_claimed_invoices`, R4/R5), the checklist/period-end/minimum/deadline
gate stack, fee freezing, status derivation, and every `api/routes/transport/*`
route — none of rules 1-5 above required them to exist first.

**Implementation status (WO-50, G1.2):** the typed `fuel_transactions` model
has landed — `app/models/transport/fuel_transaction.py` (`FuelTransaction`,
natural key `(org, entity, supplier, period, line_seq)` so ingestion is
insert-or-no-op, never Fleet Fuel's DELETE-by-period; the overloaded `note`
column split into `invoice_ref`/`provenance_note`), `app/services/transport/
product_group.py::derive_product_group()` (the centralized PROMO → HVO →
{AdBlue,Parking,Toll/Fees} → Diesel → Service/Other precedence, mirroring
`is_synthetic()`'s centralization for the same drift-prevention reason),
`app/services/transport/fuel_ingest.py::ingest_transaction()` (module-gated,
`q2`-quantized money, `qty` deliberately unquantized). Rule 1's nullable FK
from transport into the AP invoice table is now proven twice
(`vat_claim_lines.invoice_id` from WO-49, `fuel_transactions.invoice_id` from
this order) — both target `invoices`, never each other, so the two transport
tables "relate" only through the shared AP invoice, never a new transport-
internal cross-reference. Still future work: the lock table
(`vat_claimed_invoices`, R4/R5, G2.2), claim-line materialization from
transactions (G2.4/G2.5), the monthly close (G1.3/G1.4), and the goods-code
mapping table itself (G2.8 — `product_group` is derived now; the
`product_group -> goods_code` lookup is not).
**Implementation status (WO-51, G2.2):** the one-invoice-one-submission lock
has landed — `app/models/transport/lock.py` (`VatClaimedInvoice`,
`UNIQUE(org_id, entity_id, refund_country, supplier, invoice_ref)` IS the
lock, R4; `entity_id`/`refund_country` are denormalized so the constraint
spans every claim, not just the one that currently holds a row), `app/
services/transport/lock.py::submit_claim` (a minimal stub `draft`->
`submitted` transition that acquires one lock row per invoice via a plain
ORM INSERT in the SAME flush as the claim's status mutation — a lost race
raises `IntegrityError` and rolls back the whole transition, proven on real
Postgres with two genuinely concurrent submissions racing the same invoice
key) and `withdraw_claim` (R5 — the ONLY function that deletes a lock row,
proven both structurally, via a grep-based test, and behaviorally, via a
test that directly mutates a claim's `status` and asserts no lock release
cascades). The composite RESTRICT FK from `vat_claimed_invoices` into
`fuel_transactions` is now proven end to end (one representative transaction
row per lock; protecting every row sharing an `invoice_ref` is a future
close/re-close guard's job, not this FK's). Still future work: the
checklist/period-end/minimum/deadline gate stack (G2.6), wiring
`is_synthetic()` into the lock path (G2.3's consumer side), claim-line
materialization/freezing (G2.4/G2.5), fee freezing and status derivation
(G2.9/G2.7), and every `api/routes/transport/*` route.
**Implementation status (WO-52, G2.4):** claim-line construction + note→
invoice resolution has landed. `app/services/transport/invoice_match.py`
implements C3's ONE resolution order (`resolve_invoice_ref`): two note-
matching heuristics (prefix / stem-contained, a documented interpretation of
an underspecified BA phrase — see the module docstring), then the admin-
curated override (`app/models/transport/note_override.py`'s
`VatNoteInvoiceOverride`, C4/R16 — never displaces a successful heuristic
match, `ondelete=CASCADE` on the target FK rather than `SET NULL` because a
composite-FK `SET NULL` would try to null the NOT-NULL `org_id` column too,
caught live while writing this order's own de-registration test), then the
sole-registered fallback, else UNMATCHED. `app/services/transport/
claim_lines.py::build_claim_lines` MATERIALIZES the live (unfrozen)
`VatRefundClaimLine` rows a `draft` claim's underlying `fuel_transactions`
resolve to (R2 — one row per (invoice, product_group), never an `ALL:`
aggregate) — rebuildable, refuses a non-draft claim, and only ever touches
`frozen_at IS NULL` rows (future-proofing for G2.5's freeze). Two new
read-only AP-domain seams landed alongside it, filling the `invoice_service`
gap this ADR's rule 2 always named but the codebase didn't yet have:
`app.services.invoices` (`list_by_vendor`, `get_by_id`) and
`app.services.vendors.get_by_name` — so `services/transport/*` never has to
import `app.models.invoice`/`app.models.vendor` directly (rule 2 stays CI-
enforced, `test_transport_services_do_not_import_other_domain_models`).
Still future work: the checklist/period-end/minimum/deadline gate stack
(G2.6), wiring `is_synthetic()` into the lock path (G2.3's consumer side),
freezing claim lines at submission (G2.5), fee freezing and status
derivation (G2.9/G2.7), the goods-code mapping table (G2.8), and every
`api/routes/transport/*` route.
The Insight projection rule has its first composed endpoint: the home dashboard
(`GET /dashboard`, `services/dashboard.py`, WO-16 / I1.1) — it consumes only
canonical services (`approval_policy.waiting_for`, `expense_approval.pending_report_count`,
`payment_run.runs_awaiting_check`, `vendors.pending_change_count`,
`extraction.review_queue_summary`, `ap_aging`, `issued_reports`, `cash_position`),
owns no tables and adds no arithmetic on amounts.

## Context

The product charter names ten "core modules". Modelling all ten as bounded contexts
would repeat two known failure modes: (a) "Dashboard" and "Reports" own no domain
concept, no lifecycle and no writes — treating projections as modules is exactly how
math gets forked (the codebase has already exhibited the seed of this: the Explore
pivot engine and the fixed by-dimension report carry different dimension registries —
see ADR-0026); and (b) a transport vertical (EU VAT refunds under Dir. 2008/9/EC,
fuel/toll line-item analytics, excise) is being added, harvested as *specification*
from the retired Fleet Fuel system, and its intensely specific domain (litres,
`net_eur_eff`, per-country seller entities, Art. 9 goods codes) must not leak into
`invoices`, `line_items` or `vendors`, or every non-transport tenant pays the
complexity tax forever.

## Selected approach

### 1. Eight domain contexts, two projection layers

Domain contexts (own tables, own lifecycle, own invariants): **Intake & Capture**,
**AP Record**, **AR / Issuing**, **Settlement & Banking**, **Expenses**, **Money &
Compliance kernel** (pure), **Organization & Identity**, and (future) **Transport**.

Cross-cutting projection layers (own **no** domain tables; may own recomputable read
models): **Insight** (analytics/explore/benchmark/budget/cash/dashboards) and
**Export & Reporting** (CSV/Excel/PDF/SAF-T/ERP/e-invoice/audit export). Projection
rules: every figure derives from one canonical query registry — no surface may fork
the math; a materialised rollup must be recomputable through the same code path and
drift-checked; exports are read-only, formula-injection-safe, never invent a figure
and never sum across currencies.

**Integrations is a register of adapters, not a context.** Each adapter is owned by
the context it feeds, behind an existing Protocol seam (`ExtractionProvider`,
`BillingProvider`, the storage backend Protocol, the email-intake payload contract,
the ERP exporter registry, `sso_connections`). New integrations add an adapter,
never a module. **SaaS Administration** splits into Entitlements & Metering
(`org_modules`, `plans`, `usage_counters`, `plan_policies` — WO-47: quota keyed by
the org's plan) and Subscription Billing (`billing_*` behind `BillingProvider`).

### 2. The transport seam — six binding rules

1. **Transport owns only transport tables.** It never adds a column to `invoices`,
   `line_items` or `vendors`; fuel line detail lives in its own tables with a
   nullable FK to the AP invoice it was captured from.
2. **Transport reads the core through services, never through joins.** A boundary
   test (extending `tests/test_boundaries.py`) will assert no transport service
   imports another domain package's models.
3. **Transport is an entitlement** — an `org_modules` key `transport`, default
   **off**, plan-gated exactly like `issuing`/`expenses`. A tenant that does not buy
   it sees no nav, no routes, and pays no query cost.
4. **Transport reuses the platform floor unchanged** — `core/money` (Decimal
   ROUND_HALF_UP), the hash-chained audit, tenancy (`org_id` + composite FK + RLS),
   the durable jobs queue, `filesec` at the single upload choke point, `documents`
   for the vault, `keyvault` for any stored credential.
5. **Transport adds permissions, not roles** — new `Permission` members join
   `app/core/authz.py` with rows for all 8 business roles; no new role tier.
6. **Transport never gates a core figure and the core never gates a claim.** The
   advisory covenant is preserved: excise, overcharge, benchmark and any AI seam are
   advisory and cannot mutate a legal figure.

### 3. The one Fleet Fuel invariant that is *translated*, not copied

Fleet Fuel kept its VAT-claim data in a **physically separate database** so a
monthly reload (DELETE-by-period + INSERT) could never corrupt filed claims — a
SQLite-shaped solution to a real problem. Copied into Postgres it would fight the
tenancy model, RLS and the single-transaction audit commit. The translation:

- A claim **materialises and freezes its own lines at submission** (alongside the
  frozen VAT amounts and fee terms). Fleet Fuel derived claim lines live at read
  time, which is precisely why it needed the separate database.
- Once frozen, a re-close of the period **cannot change what was filed** — nothing
  reads through to the transaction rows any more.
- Transaction rows locked into a submitted claim are protected **at the database
  level**: the period-scoped delete in the close excludes locked rows, and a
  `RESTRICT` FK from the claim-lock table makes accidental deletion an error rather
  than silent data loss.
- The close runs as a **durable idempotent job** (`jobs` kind, tenant-scoped,
  idempotent by `(org, period)`) on the existing queue.

This is **strictly stronger** than the original: Fleet Fuel's separation protected
the claim *store* but still recomputed claim lines from live transaction data on
every read — a reload changed what a claim *showed* even if not what it stored.
Freezing at submission protects the *content*; the FK + delete-guard protect the
*inputs*; the idempotent job protects the *process* — and all three live inside the
same transactional, RLS-guarded, audit-chained database instead of beside it.

## Alternatives considered

- **Ten modules as ten contexts** — rejected: projections-as-modules fork math;
  Integrations-as-a-context centralises what belongs at each context's edge.
- **Transport columns on the AP tables** (a `fuel_*` column family on
  `invoices`/`line_items`) — rejected: every tenant pays; the AP record is the most
  reusable asset in the platform.
- **A separate transport database / read-only replica** (copying Fleet Fuel §3.H
  literally) — rejected: fights RLS, composite-FK tenancy and the atomic
  audit-commit; the freeze-at-submission translation is stronger (see above).
- **A separate transport service** — rejected per ADR-0001; one deployable, one
  regression net.

## Why appropriate

It converts "build 10 modules" into "close gaps in 8 contexts that mostly exist and
build 1 new one", keeps the most valuable asset (the AP/AR record) vertical-neutral,
and fixes the hardest structural decision — how a legally-sensitive vertical plugs in
— before any of its code exists, when the rules are cheapest to enforce.

## Risks

- The projection rule is discipline until the canonical query registry is complete —
  drift is possible in the interim (tracked as C1.6/C1.7 in ADR-0026).
- The transport rules are asserted here before the code exists; the boundary test in
  rule 2 must land in the same PR as the first transport module, or the seam is
  fiction.

## Revisit when

The transport vertical's first implementation PR opens (the six rules become CI
assertions); a second vertical arrives (the seam pattern generalises); or a
projection needs its own writes (it is then a context and must say so).
