# ADR-0023 — Platform evolution: 8 bounded contexts, 2 projection layers, and the transport-vertical seam

**Status:** Accepted (contexts + projection rules are in effect today; the transport
vertical itself is future work — this ADR fixes its binding rules *before* it is built).
Extends ADR-0001 (modular monolith), ADR-0004 (tenant isolation).

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
(`org_modules`, `plans`, `usage_counters`, `role_policies`) and Subscription Billing
(`billing_*` behind `BillingProvider`).

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
