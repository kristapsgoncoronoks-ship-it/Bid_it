# WO-81 — the cash-recovery analytics service + its read route (G4.3 / R38)

> The first M5 row, and the first transport surface that answers a question
> about the PORTFOLIO rather than about one claim. Every figure is derived
> strictly from the canonical claim services WO-49…WO-80 already shipped —
> `claim.list_claims`, `status.derive_stage`, `freeze.preview_vat_base`,
> `minimum.below_minimum`, `deadline.deadline_status`. **Nothing forks a
> query, nothing is invented, nothing is written.**

**WORK ORDER 81 — the cash-recovery analytics service (`app/services/transport/
recovery.py`) + its read route (`app/api/routes/transport/recovery.py`) on the
existing `TRANSPORT_READ` permission. Effort M 3–5d. Priority P0. Milestone M5
(board G4.3, rule R38). Depends on: WO-49 (the claim grain), WO-54 (the frozen
VAT base + `preview_vat_base`), WO-56 (the deadline scanner + the Art. 17
minimum), WO-59/WO-60 (`derive_stage` + the checklist evaluator), WO-76/WO-79
(the route + accessor pattern this mirrors).**

### Objective and business value

The gap, with verified evidence. `docs/plan/plan-a/ARCH_plan.md` line 121 lists
`recovery.py` in the `app/api/routes/transport/` file set; `backend/app/api/
routes/transport/__init__.py` names it in its own docstring as a future
includer ("Future slices (`fuel.py`, `recovery.py`, `excise.py`,
`overcharges.py` — the ARCH_plan file list) include themselves HERE"); and
`backend/app/api/routes/transport/fuel.py` (WO-79) records the permission
decision that reserves `TRANSPORT_READ` for exactly this module: *"`TRANSPORT_READ`
stays reserved for the derived analytics/excise slices (`recovery.py`/`excise.py`/
`overcharges.py`), which have no backing service yet."* `TODO.md`'s own M3 row
says the same in its closing sentence: the analytics half of the transport route
batch has **no backing service**. Today an operator can open one claim and read
one claim's stage — there is no surface anywhere that answers *"how much money
can we still recover this year, and what is stopping each euro of it?"*.

Who stops losing money: the FINANCE_MANAGER who files. The harvested spec's own
risk register scores the missed 30-September filing deadline **L-1 = 6, the
maximum** (`ARCH_plan.md` line 1289), because CJEU C-294/11 *Elsacom* makes it a
permanent forfeiture — and `BA_fleet_fuel.md` A4 states the north-star KPI in one
line: **"deadline misses = 0."** A per-claim view cannot produce that KPI. A
portfolio view that counts the claims inside the 60-day risk window, and totals
the euros sitting behind each blocking reason, is the only artifact that turns
the time-bar from a thing you discover into a thing you manage. The same view
carries the two figures the commercial model runs on — € recovered and € still
claimable.

### Scope

**In scope:**
- `backend/app/services/transport/recovery.py` (**new**) — `recovery_dashboard(db,
  org_id, year)`: buckets every claim of the refund year into the six harvested
  readiness states, totals the north-star euros, counts deadline risk, and
  computes the median days-to-refund. READ-ONLY: no write, no audit event, no
  commit, never raises on missing data.
- `backend/app/services/transport/claim.py` — `list_claims` gains an additive
  `year: int | None = None` keyword (a `ref_period LIKE 'YYYY-%'` filter). The
  ONE claim-listing query stays the one claim-listing query; existing callers are
  byte-identical at the default.
- `backend/app/schemas/transport_recovery.py` (**new**) — `RecoveryDashboardOut`
  and its parts, every amount `Decimal` (strings on the wire, §4.9).
- `backend/app/api/routes/transport/recovery.py` (**new**) — one thin controller,
  `GET /api/v1/transport/recovery-dashboard`, router-level `TRANSPORT_READ`.
- `backend/app/api/routes/transport/__init__.py` — the aggregator includes it.
- `backend/tests/transport/test_wo81_recovery.py` (**new**) — the analytics
  correctness matrix (see Testing requirements).
- `docs/DECISIONS-NEEDED.md` — a new section for the claim-line **supplier
  attribution** question WO-79 and WO-80 both left open. Recorded, not decided.
- Boards: `TODO.md`, `docs/transport/rules.md` (R38 + R9's new consumer),
  `README.md` scale line.

**Out of scope:**
- **The overcharge euro** (`BA_fleet_fuel.md` R38 lists "overcharges" among the
  north-star figures). It is board **G4.5 / R41** (`overcharge.py` +
  `contract_audit`), and no such service exists in this codebase —
  `grep -rn "contract_audit\|overcharge" backend/app` returns only docstring
  prose. Emitting a zero labelled "overcharges" would read as *"we found no
  supplier breaches"*, which is a different and false statement. The field is
  **omitted**, and the omission is documented in the module docstring and
  reported. (§10 — no invented functionality.)
- `/excise` (G4.6), `/value`, `/claim-status` (G4.4), the refund-estimate funnel
  (G4.8), the overpay/benchmark grains (G4.7). Each is its own board row.
- **G2.9 fee freezing** — decision-gated, `docs/DECISIONS-NEEDED.md` §10. No fee
  figure appears on this dashboard.
- Any SPA screen. This order ships the service + route only; the analytics UI is
  the remaining transport UI slice.
- Populating `submitted_date`/`paid_date`/`paid_amount`. Nothing writes them
  today (verified: `grep -rn "paid_date\|submitted_date" backend/app/services/
  transport` matches only `claim_pack.py` READING them) — that is G2.9/G4.4
  territory. This order READS them and reports its sample size honestly.

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/recovery.py` | **new** — the analytics service |
| `backend/app/services/transport/claim.py` | additive `year` keyword on `list_claims` |
| `backend/app/schemas/transport_recovery.py` | **new** — the Decimal-typed response |
| `backend/app/api/routes/transport/recovery.py` | **new** — one GET controller |
| `backend/app/api/routes/transport/__init__.py` | include the new router |
| `backend/tests/transport/test_wo81_recovery.py` | **new** — the correctness matrix |
| `docs/DECISIONS-NEEDED.md` | new section — claim-line supplier attribution |
| `docs/transport/rules.md` | R38 row (new); R9 gains its first consumer |
| `TODO.md` | WO-81 row, M3/M5 cells, suite line |
| `README.md` | scale line (verified counts) |

### The harvested definitions — every figure, with its citation

Everything below is quoted or derived from `docs/plan/shared/specs/BA_fleet_fuel.md`.
No Fleet Fuel code or data is read or reproduced; the spec text is the source.

**The six readiness states** — `BA_fleet_fuel.md` §2.4 (line 218), verbatim:
> *"Six readiness states: `ready · deadline · missing · below · submitted · paid`.
> North-star €: recovered, awaiting, claimable, overcharges, **median
> days-to-refund**. Deadline risk = within **60 days** of 30-Sep."*

and R38 (line 1410), verbatim:
> *"Cash-recovery dashboard bucketing every claim into six readiness states with
> north-star euros (recovered, awaiting, claimable, overcharges, median
> days-to-refund, deadline-risk count) — built on the **canonical** claims and
> recovery queries, **never a forked query**."*
> Acceptance: *"The dashboard totals reconcile exactly with the underlying claim
> reports."*

The spec names the six states but does not define their precedence, so the
mapping onto THIS codebase's actual lifecycle columns is stated here and
mirrored verbatim in the module docstring. It is built entirely on the two
harvested lifecycle layers of §3.D — the coarse ENGINE status and the
system-derived AUTO stage codes 1A/1B/1C/1E:

| Bucket | This codebase's condition | Spec anchor |
|---|---|---|
| `paid` | `claim.status == "paid"` | §3.D `3A` "Money received" → engine `paid` |
| `submitted` | `claim.status in {"submitted", "approved"}` | §3.D `2`/`3` → engine `submitted`/`approved` |
| `missing` | draft, `derive_stage` → `"1A"` | §3.D `1A` "Missing documents" |
| `below` | draft, `derive_stage` → `"1C"` **and** `minimum.below_minimum` is true | §3.A A3 (Art. 17); §3.D D3's "caveat" |
| `deadline` | draft, otherwise fileable, `deadline_status != "ok"` | §3.A A4, `DEADLINE_RISK_DAYS = 60` |
| `ready` | every other draft (stages `1B`, `1E`, and a waiver-only `1C`) | §3.D `1E` "Ready to submit" |

Four documented interpretations, each stated because the spec does not settle it:

1. **`1B` (period not ended) lands in `ready`.** The spec gives six states and no
   seventh; a `1B` claim has nothing an operator must fix — it becomes fileable
   when its own period closes. Folding it into `missing` would report a data gap
   that does not exist. **No seventh bucket is invented** (§10).
2. **A waiver-only `1C` lands in `ready`, not `below`.** `status.derive_stage`
   emits `1C` for EITHER of two causes (below-minimum, or an active
   receipt-control waiver — that module's own docstring). Only the first is a
   THRESHOLD problem, so `below` re-asks `minimum.below_minimum` (the same
   canonical predicate, via the same `freeze.preview_vat_base`) rather than
   trusting the shared code.
3. **`deadline` outranks nothing.** It applies only to a claim that is otherwise
   fileable — the most actionable possible meaning ("this one is ready and the
   clock is running"). A `missing`/`below` claim inside the window keeps its
   blocking bucket (the bucket says WHAT TO DO) and is still counted in
   `deadline_risk_claims` (the separate count says HOW URGENT). This is exactly
   why R38 lists the deadline-risk COUNT separately from the buckets.
4. **`withdrawn`/`rejected` claims match none of the six states** and are
   reported in an explicit `excluded` block with their reason, never silently
   dropped: `Σ bucket claims + Σ excluded claims == total_claims` is asserted by
   a test. Adding them as a seventh readiness state would be invented
   vocabulary; dropping them silently would make the row count lie.

**The north-star euros.** Basis: **NET EUR** — every figure is `vat_eur`, the
column `BA_fleet_fuel.md` line 998 calls *"Reclaimable VAT in EUR — the
VAT-refund north star"*.

| Figure | Definition | Source |
|---|---|---|
| `recovered_eur` | Σ `claim.vat_eur` over the `paid` bucket | the FROZEN claim figure (R13/C10) |
| `awaiting_eur` | Σ `claim.vat_eur` over the `submitted` bucket | same |
| `claimable_eur` | Σ previewed `vat_eur` over the four DRAFT buckets | `freeze.preview_vat_base` — the canonical preview `lock.submit_claim` and `status.derive_stage` already use |
| `median_days_to_refund` | median of `(paid_date − submitted_date).days` over paid claims where BOTH dates exist | §4.3's claim aggregate columns |
| `deadline_risk_claims` | count of UNFILED claims with `deadline_status != "ok"` | A4 / `DEADLINE_RISK_DAYS = 60` |

`recovered + awaiting + claimable` equals the total VAT across every bucketed
claim — the reconciliation R38's acceptance line demands, asserted directly.

A draft claim's `vat_eur` column is NULL by construction (WO-49: it is frozen
only at submission), which is exactly why `claimable_eur` must go through
`preview_vat_base` and not the column. Using the column would silently report
€0 claimable for every claim not yet filed — the single most dangerous possible
error on this surface.

**Why `recovered_eur` reads `vat_eur` and not `paid_amount`.** `VatRefundClaim.
paid_amount` carries no currency of its own: the claim's `currency` column is the
refund state's LOCAL currency (frozen from the lines by `freeze._sum_lines`), so
`paid_amount` is currency-ambiguous in the current model, and nothing writes it.
Summing it would be a §4.14 violation dressed as a total. `vat_eur` is
unambiguously EUR, frozen over exactly the locked claim set. Recorded as a
deviation; when G2.9 lands a settlement writer with an explicit currency, this
figure should be revisited.

### Invariants this order must preserve

- **§4.14 — no aggregate sums across currencies.** Two mechanisms. (a) Every
  euro on this surface is `vat_eur`, a single-currency EUR column; `vat_local` is
  never summed and the response carries `currency: "EUR"` explicitly. (b)
  `preview_vat_base` REFUSES (`claim_currency_mismatch`) a draft claim whose lines
  span currencies. The dashboard catches that refusal PER CLAIM: the claim is
  bucketed `missing` (it is missing a filable single-currency VAT base — an
  operator must fix the data), contributes **zero** euros, and is counted in
  `currency_mismatch_claims` so the shortfall is visible rather than silent. One
  bad claim never blanks a whole year, and no foreign amount is ever labelled EUR.
- **§4.9 — Decimal, ROUND_HALF_UP, never float.** Every sum is `Decimal`,
  quantized through `app.core.money.q2`. The median days-to-refund is not money
  but is computed as an exact `Decimal` (`money.q` at 0.1) precisely so no
  `statistics.median` float enters the module. `grep -n "float" ` over the new
  files returns nothing.
- **§4.10 — the server recomputes every total.** No client input feeds any
  figure; `year` selects the set and nothing else.
- **§4.19 — advisory never blocks or mutates.** This module holds no writable
  intent at all: no `db.add`, no attribute assignment on a model, no
  `audit.record`, and the route issues no `db.commit()`. The stage evaluator it
  calls seeds default checklist rules idempotently (flushed, never committed) —
  the same read-only posture `GET /transport/claims/{id}/checklist` already has
  (WO-76's own docstring).
- **§4.4 — opaque 404** and **§4.1 tenancy**: every read is org-scoped through
  `claim.list_claims(db, org_id, …)`; the route exposes no object id, so there is
  no by-id surface to leak one.
- **ADR-P3 rule 3 — module entitlement enforced INSIDE the service**
  (`module_not_enabled`, fail CLOSED) before any query, like every transport
  entry point since WO-49.
- **§4.20 additive**: one additive keyword on `list_claims` (default preserves
  behaviour exactly); three new files; no migration; no schema change; no new
  permission member.

### Database / migration impact

**None.** No table, no column, no index, no RLS policy. The service reads
`vat_refund_claims`, `vat_claim_lines`, `vat_checklist_rules` and
`vat_receipt_waivers` — all four already tenant-registered, RLS-policied and
covered by a `tests/test_tenancy_parity.py` HTTP probe.

**Tenancy parity: no exemption changes.** The dashboard returns AGGREGATES only —
counts and sums, no row and no object id — so it makes no currently-EXEMPT table
route-readable. The three remaining transport exemptions
(`vat_claimed_invoices`, `fuel_extraction_baselines`, `supplier_vat_registrations`)
stay exempt for exactly the reasons they state; their "after WO-77/WO-79" reason
text is trued up to "after WO-77/WO-79/WO-81".

### Permission decision (justified, not invented)

**`TRANSPORT_READ`**, router-level. WO-79 chose `VAT_READ` for the fuel-transaction
route and recorded WHY the two differ: *"this surface is not analytics: these rows
ARE the evidence base of a claim… `TRANSPORT_READ` stays reserved for the derived
analytics/excise slices (`recovery.py`/`excise.py`/`overcharges.py`)."* This module
IS that derived analytics slice — it returns no claim row, no evidence, no object
id, only portfolio aggregates. Honouring the reservation is what makes the two
permissions mean something.

The choice changes no effective access today: `app/core/authz.py::ROLE_PERMISSIONS`
grants `VAT_READ` and `TRANSPORT_READ` to exactly the same six roles and denies both
to APPROVER and EMPLOYEE — pinned by
`test_wo79_vat_read_and_transport_read_have_identical_role_coverage`, which now
guards two routes' assumptions instead of one. **No permission member is added**
(§10).

### Testing requirements

`backend/tests/transport/test_wo81_recovery.py`, fixture strategy exactly
`test_wo79_fuel_routes.py`'s (real HTTP register flow for orgs/tokens; direct
service setup for transport enablement; claims and lines seeded through the real
services; every assertion about the API goes through the API).

Bucket coverage — one claim constructed in each state:
- `test_wo81_a_draft_with_a_failing_checklist_item_buckets_missing`
- `test_wo81_a_below_minimum_draft_buckets_below`
- `test_wo81_a_fileable_draft_inside_the_risk_window_buckets_deadline`
- `test_wo81_a_fileable_draft_outside_the_window_buckets_ready`
- `test_wo81_a_period_not_ended_draft_buckets_ready_not_missing`
- `test_wo81_a_submitted_claim_buckets_submitted_and_a_paid_one_paid`
- `test_wo81_withdrawn_and_rejected_claims_are_excluded_with_a_reason`
- `test_wo81_every_claim_is_accounted_for_exactly_once`

Totals and boundaries:
- `test_wo81_totals_match_hand_computed_decimals`
- `test_wo81_recovered_plus_awaiting_plus_claimable_reconciles`
- `test_wo81_deadline_risk_boundary_is_inclusive_at_exactly_60_days` — 60 days out
  is at risk, 61 days out is not (both sides of the window edge).
- `test_wo81_median_days_to_refund_over_an_even_sample`
- `test_wo81_median_is_null_when_no_paid_claim_carries_both_dates`
- `test_wo81_an_empty_year_returns_zeroes_not_an_error`

§4.14 and §4.9:
- `test_wo81_a_cross_currency_draft_is_excluded_from_the_euros_and_counted`
- `test_wo81_amounts_cross_the_wire_as_decimal_strings`

Route matrix:
- `test_wo81_employee_is_denied_and_accountant_is_granted`
- `test_wo81_module_disabled_refuses_403_module_not_enabled`
- `test_wo81_the_dashboard_is_org_scoped_with_overlapping_data`
- `test_wo81_a_cross_tenant_orgs_claims_are_invisible` (opaque — no id surface)
- `test_wo81_an_invalid_year_is_refused_422`

### Acceptance criteria (verifiable checklist)

- [ ] `GET /api/v1/transport/recovery-dashboard?year=2026` returns 200 with six
      buckets named exactly `ready`, `deadline`, `missing`, `below`, `submitted`,
      `paid`, in that order.
- [ ] A claim seeded in each of the six states appears in exactly that bucket.
- [ ] `sum(bucket.claims) + sum(excluded.claims) == total_claims` for a mixed
      portfolio including a `withdrawn` and a `rejected` claim.
- [ ] `recovered_eur + awaiting_eur + claimable_eur` equals the hand-computed
      Decimal total of every bucketed claim's VAT.
- [ ] A claim whose deadline is exactly 60 days away is counted in
      `deadline_risk_claims`; one 61 days away is not.
- [ ] A draft claim with lines in two currencies returns 200 (never 409),
      contributes `0.00` to every euro figure, and increments
      `currency_mismatch_claims`.
- [ ] Every amount is a JSON **string**; `grep -n "float" ` over the three new
      backend files returns nothing.
- [ ] `role_client("user")` (EMPLOYEE) gets 403; an ACCOUNTANT gets 200.
- [ ] An org without the `transport` module gets 403 `module_not_enabled`.
- [ ] A year with no claims returns 200 with all counts 0 and all euros `"0.00"`
      — never a 404 and never an exception.
- [ ] `python -m pytest -q` is green at 1865 + the new tests, 10 skipped, zero
      pre-existing tests modified.

### Rollback strategy

Code revert only — three new files, one additive keyword, one aggregator line.
No migration, so nothing to downgrade and no data to lose. Narrower mitigation:
remove the single `router.include_router(recovery.router)` line from
`app/api/routes/transport/__init__.py` and the surface disappears while the
service stays importable. Nothing this order ships can change a stored figure, a
claim status, a lock or an audit chain, so a revert is total by construction.

### Documentation to update

- `docs/transport/rules.md` — a new **R38** row; **R9** gains its first real
  consumer (its own text says the deadline functions exist "ready for a future
  dashboard (G4.3) to consume" — that future is this order).
- `TODO.md` — WO-81 row, the M3/M5 milestone cells, the test-suite line.
- `README.md` — the scale line (counts verified, not assumed).
- `docs/DECISIONS-NEEDED.md` — the supplier-attribution question (recorded only).
- No ADR is contradicted. ADR-P3's rule set is followed as-is.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo81_recovery.py -q
python -m pytest tests/test_tenancy_parity.py tests/test_authz_coverage.py tests/test_boundaries.py -q
python -m pytest -q                                   # full baseline: 1865 -> 1865+N
python -c "
import app.services.transport.recovery as r, inspect
src = inspect.getsource(r)
assert 'float' not in src, 'float in a money path'
assert 'db.add' not in src and 'audit.record' not in src, 'this module must write nothing'
print('recovery.py is read-only and float-free')
"
grep -n "recovery" ../backend/app/api/routes/transport/__init__.py
python ../scripts/pii_scan.py --tree
```
