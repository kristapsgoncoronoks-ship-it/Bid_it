# TODO — Task Board

Live board per the founder's charter (statuses: `Backlog` · `Planned` · `In Progress` · `Blocked` ·
`Testing` · `Review` · `Completed`). Supersedes the 2026-07-27 audit snapshot preserved at the bottom
of this file — every P0/P1/P2 item it listed as `Approved`/`Backlog` is now closed. This file is the
current source of truth; `docs/M0-exit-gate.md` and `docs/plan/plan-a/wo/` (WO-1…47) are the detailed
per-order record.

**Plan:** Plan A (evolve Bid_it) — decided 2026-07-25, reaffirmed 2026-07-28. See
`docs/plan/PLAN_A_vs_PLAN_B.md`. Plan B (`docs/plan/plan-b/GREENFIELD_plan.md`) was considered and set
aside; not executed.

---

## Milestone status

| Milestone | Theme | Status |
|---|---|---|
| **M0** | Security/correctness debt sprint | ✅ **Completed** — WO-1…11 (incl. B1.5). All 12 exit-gate criteria met. See `docs/M0-exit-gate.md`. |
| **M1** | Feature completion + independent audit | ✅ **Completed** — WO-12…46. Every named epic shipped; 18-item audit (R1–R19) closed except two decision-gated/backlog items (below). |
| **M2** | "We can take money" — billing go-live | 🔶 **In Progress** — WO-47 (quota model) + WO-48 (dogfood billing fallback) shipped. Three items still owner-blocked (below). |
| **M3** | Transport vertical phase 1 — VAT refund claim engine | 🔶 **In Progress** — WO-49 (foundation: claim grain, `is_synthetic()`, module entitlement) + WO-50 (`fuel_transactions`: typed model, idempotent ingestion, `product_group` derivation) + WO-51 (`vat_claimed_invoices`: the one-invoice-one-submission lock, R4/R5) + WO-52 (claim-line construction + note→invoice resolution, R2/R16) + WO-53 (monthly close as a durable job + locked-line protection, R31/R60/R30) + WO-54 (frozen claim lines + frozen VAT base at submission, G2.5 "the linchpin") + WO-55 (Art. 9 goods-code mapping, G2.8, R11) + WO-56 (G2.6 slice 1: period-end + Art. 17 minimum + deadline scanner, R7/R8/R9) + WO-57 (G2.6 slice 2: annual mop-up + quarterly duplicate-block, R6) + WO-58 (G2.6 slice 3: document-presence gate + receipt-control waivers, R10/R15) + WO-59 (G2.7: status lifecycle 1A→5, R12/R17) + WO-60 (G2.10 slice 1: the adjustable checklist engine, R45) + WO-61 (G3.1 slice 1: per-country supplier legal-entity registry, R21/R22) + WO-62 (G3.2 slice 1: the fuel-card parser registry + the Eurowag parser, R20) + WO-63 (G3.2 slice 2: the E100 fuel-card parser, R20's second worked example) + WO-64 (G3.2 slice 3: the Q8/Kuwait Petroleum fuel-card parser — the first multi-country/multi-currency-in-one-statement proof, `net_eur_eff`/Port-One-rebate merge explicitly deferred to the future G4.2) + WO-65 (G3.2 slice 4: the DKV fuel-card parser — the supplier-STATED-EUR money model, `fx_source="stated"` reachable from transport for the first time) + WO-66 (G3.3: the two independent validation regimes, R25 — the nine-rule capture review gate blocking registration at `statement_ingest`, and the human-typed engine tie-out halting `run_close`) + WO-67 (G3.2 slice 5: the TFC by Moya fuel-card parser — the DERIVED-NET hub-only discount money model, parser-local arithmetic over the unchanged shared contract, the first network onboarded against WO-66's live gate) + WO-68 (G3.2 slice 6: the Moeve fuel-card parser — the per-line-IVA VAT-inclusive money model at the harvested 6-dp internal precision, the cash-at-pump settlement flag as provenance-only, rate policy reused from the WO-66 gate's harvested ES (21, 10) dual entry) + WO-69 (G3.2 slice 7: the BP/Aral fuel-card parser — the PLN independently-given money model on the existing dated-ECB-rate branch, the MPP split-payment annotation as settlement-side advisory, the ORS fee line as an ordinary VAT-bearing line; **G3.2 CLOSED — all seven §5.1 networks parse deterministically**) + WO-70 (G3.3 slice 2: the anti-drift extraction baseline + `regression_check` — the harvested "flags a drift when a re-extraction moves net or vat by more than 0.02", advisory per §4.19, keyed (statement-SHA-256 × currency), recorded at first successful registration; **G3.3 CLOSED** — both R25 regimes AND the anti-drift paragraph are real) + WO-71 (G3.4: deterministic advisory post-capture checks, R26 — IBAN MOD-97 via `core/bank_id`, per-country VIES-format VAT-ID structure, the no-I/O `vies_check` stub, and the cross-entity invoice duplicate scan; every finding advisory per §4.19 — an error-severity finding never blocks, proven end to end; **G3.4 CLOSED**) + WO-72 (G3.5: receipt control — cadence × activity expectation over the harvested three-cadence set + §5.1 per-network defaults, the persisted `vat_receipt_controls` slot grid with overrides that survive re-runs, the orphan check, and the `run_control` close stage; ADVISORY per §4.19 — a `missing` slot never gates a claim/close, the blocking side stays with WO-58/WO-60; **G3.5 CLOSED**) + WO-73 (G2.11: customer lifecycle + per-country activation gates, R44 — `vat_customer_lifecycles` (prospect→pending→active→inactive) + `vat_country_activations` ((none)→requested→active), the fail-CLOSED `enforce_activation` gate in `submit_claim` after R8/before R6 per D5+§3.E, preparation surfaces deliberately ungated; F3's `country_requirements`/`country_ready_to_activate` deferred to the customer-document-store slice; **G2.11 CLOSED (core)**) + WO-74 (G2.12: evidence pack + claim workbook — `claim_pack.py`: the two filing artifacts of a FROZEN claim rendered from ONE loaded pack (workbook = Claim header + R2-grain Lines + TOTAL row equal to the frozen VAT base; evidence pack = the §3.K K6 ZIP filing bundle under the §3.M M1 vault tree with every vaulted invoice document + a SHA-256 `manifest.csv`), the identical-lines-and-totals acceptance proven structurally AND cell-for-cell; R3's FIRST blocking consumer — any synthetic frozen line refuses both artifacts (`synthetic_line_in_pack`); frozen-lines-only (`claim_not_frozen`), totals-drift refusal (`claim_totals_drift`, §4.10), missing-document-bytes refusal (`evidence_document_unavailable`); read-only, nothing persisted, no migration; **G2.12 CLOSED — the last non-decision-gated M3 service row**) + WO-75 (the R3 LOCK GATE: `claim_gates.enforce_no_synthetic_lines` — the submit-side consumer of THE one `is_synthetic()` predicate, wired into `lock.submit_claim` at the head of D5's engine-gate group per C9's `bad`-gate-first order (after R44 activation, before the R6 duplicate machinery): a claim whose materialized unfrozen lines include an UNMATCHED/INPUT/aggregate ref refuses 409 `unresolved_invoice_refs` with NOTHING mutated — no freeze, no locks, status stays `draft` — closing WO-74 design decision 8's recorded gap; **R3's full consumer set is now wired**: lock gate (hard), workbook/evidence builders (hard), checklist/stage view (advisory)) + WO-76 (transport routes slice 1 — `api/routes/transport/claims.py`, the claim lifecycle over HTTP: create/list/detail, line materialization+read, advisory checklist/stage reads, the D5 submit chain and R5 withdraw as nine thin controllers on the EXISTING WO-49 VAT_READ/VAT_WRITE/VAT_SUBMIT structural permissions; every refusal code is the service's own; tenancy parity now PROBES `vat_refund_claims`/`vat_claim_lines` over the real routes) + WO-77 (transport routes slice 2 — the ADMIN/CONFIG surfaces + the FILING ARTIFACTS: `api/routes/transport/admin.py` (status-code vocabulary, checklist-rule admin R45, cadences + the persisted receipt-control grid and its override G3.5, note→invoice-ref overrides R16, tie-out expectations R25 regime 2) + `customers.py` (the R44 lifecycle + per-country activation ladder, closing the "no route exists yet" gap that module's own docstring recorded) + six claim-scoped additions to `claims.py` (waivers R15, the manual status code R17/R12, and `GET /{id}/workbook` + `GET /{id}/evidence` — the G2.12 filing artifacts finally served, with the downloaded bytes really parsed by openpyxl/zipfile and the manifest SHA-256s re-hashed); 27 routes on the SAME existing VAT_READ/VAT_WRITE/VAT_SUBMIT permissions, every refusal the service's own code, EIGHT more tenancy-parity EXEMPT rows converted to real HTTP probes) + WO-78 (transport UI slice 1 — the VAT claims WORKSPACE: `frontend/src/pages/VatClaims.tsx` + `VatClaimDetail.tsx` over the WO-76/WO-77 routes; the D5 refusal vocabulary rendered as actionable human sentences with the raw slug shown nowhere, `below_minimum`'s override surfaced, the ADVISORY checklist that never disables Submit, the status-code ladder built from the service's own vocabulary, string-exact Decimal money with no float round-trip, the UI computing no total, and the G2.12 workbook/evidence downloads; permission-mirrored (VAT_READ/WRITE/SUBMIT) and module-gated nav; 29 Playwright specs, ZERO backend change) + WO-79 (the fuel-transaction READ surface — `services/transport/fuel.py`/`schemas/transport_fuel.py`/`api/routes/transport/fuel.py`: the FIRST code path that returns a `fuel_transactions` row, on the existing VAT_READ (TRANSPORT_READ left reserved for the derived analytics slices; identical role coverage pinned by a test), entity+period required with optional supplier/country, `period` accepting a claim reference period via the SHARED `claim_lines.period_months`, Decimal-as-string incl. `qty` and the FX quadruple, module-gate → opaque-404 entity fetch → query; `fuel_transactions` converted from a tenancy-parity EXEMPT row to a real HTTP probe; and the SUBMIT PICK-LIST it unlocks in the SPA — the `(supplier, invoice_ref, fuel_transaction_id)` lock tuple selected off the claim's own transactions instead of typed, with the typed path preserved as the fallback; WO-78 deviation 1 (a supplier column on the claim-line grain) deliberately NOT built because no unambiguous link exists on the wire) + WO-80 (transport UI slice 2 — the ADMIN/CONFIG WORKSPACE: `frontend/src/pages/VatAdmin.tsx`, one tabbed page over `admin.py` (checklist-rule admin R45 incl. its COMMITTING seed, the persisted receipt-control slot grid + override G3.5, cadences, note→invoice-ref mappings R16, tie-out expectations R25 regime 2, and the R17 status-code vocabulary as a reference panel that links to the claim rather than duplicating the VAT_SUBMIT action) + `VatCustomers.tsx` (the R44 lifecycle + per-country activation ladder — the screen the claim workspace's own `customer_not_active`/`country_not_activated` sentences finally send an operator to) + the claim-scoped R15 waiver panel on the claim detail; the advisory/gate distinction carried in the COPY and asserted as TEXT (the receipt-control board "blocks no claim, halts no close and changes no figure"; a typed tie-out expectation "stops the monthly close"); nothing the backend lacks was invented — no note-override delete (asserted by absence), no control run, no status labels; 47 new Playwright specs, ZERO backend change) shipped. G2.6 is fully closed (R6-R10, R15 all real gates); R20 is closed for Eurowag AND E100 only (Q8/DKV/TFC/Moeve/BP carry no R20 claim of their own); R25 is closed (both regimes real gates); **G3.2 is closed** (Eurowag, E100, Q8, DKV, TFC, Moeve, BP — no fuel-card network remains); **G3.3 is closed** (WO-66 + WO-70); **G3.5 is closed** (WO-72); **R44 is closed** (WO-73 — the F3 informational helper rides the future customer-document-store slice). Remaining in M3: G2.9 (decision-gated — the ONLY service row left), the rest of the ANALYTICS half of the `api/routes/transport/*` batch (`excise.py` still has no backing service; **`recovery.py` and `overcharges.py` now EXIST with one** — WO-81 and WO-82 respectively — WO-81, an M5/G4.3 row delivered against this batch's own file list; `fuel.py` EXISTS but as a raw-row READ surface for the claim workspace, not the €/L analytics slice ADR-P3 names, and WO-76/WO-77/WO-79/WO-81 together leave every BUILT transport service route-reachable) and the REST of the UI surface (WO-78 shipped the claim-lifecycle workspace, WO-79 its submit pick-list and WO-80 the ADMIN/CONFIG screens — checklist rules, cadences, the receipt-control grid, note overrides, tie-out expectations, the claim-scoped waivers and the customer-lifecycle ladder — so EVERY built transport route now has a screen; what remains of the UI batch is the ANALYTICS surface, which has no backing service yet). Carried forward from WO-79/WO-80 and now FORMALLY RECORDED as `docs/DECISIONS-NEEDED.md` §11: the claim-line SUPPLIER attribution needs a BACKEND slice (`build_claim_lines` recording the supplier, and a decision on what an `UNMATCHED` multi-supplier bucket carries — it aggregates several suppliers, and a claim line's `invoice_ref` is the RESOLVED AP invoice number while a fuel transaction's is the RAW statement note, so the two cannot simply be joined) before any UI can show it honestly. 70-100 day milestone; remaining slices tracked below. |
| M4 | Payments & cash depth | `Planned` |
| **M5** | Transport vertical phase 2 — recovery intelligence | 🔶 **In Progress** — WO-81 (G4.3/R38: the cash-recovery analytics service + its read route — every claim of a refund year bucketed into the six harvested readiness states with the north-star euros, built strictly on the canonical claim services and NEVER a forked query; `TRANSPORT_READ`, the permission WO-79 reserved for exactly this derived-analytics slice; read-only, no migration, no new permission) shipped — the first M5 row, and the first backing service the analytics half of the transport route batch has ever had. WO-82 (G4.5/R41: supplier overcharge detection + claim-back — the harvested contract audit (§2.5's two term types, the two flag strings, TOLERANCE 0.005 €/L, `recover_eur = gap × litres` dropped if ≤ 0) over the validated fuel lines on the NET EUR/L final basis, plus §4.5's `detected → packaged → claimed → recovered \| rejected \| written_off` claim-back lifecycle and §2.4's booked-cash `recovered_total` — which CLOSES WO-81's deviation 1: the dashboard's `overcharges_eur` is now real, obtained by CALLING the new service rather than forking a query; two tenant tables with FORCE RLS in the same migration, both straight into real tenancy-parity probes; `TRANSPORT_READ` reads / `VAT_WRITE` writes, no permission member invented) shipped. WO-83 (G4.5/R41 + R53: R41's two send-ready ARTIFACTS — the Excel evidence packet and the formal PDF claim letter with §2.4's 30-day credit/refund demand — rendered from the ONE line source `contract_audit.audit()` already was; *"both artifacts show identical lines and totals"* proven STRUCTURALLY (one loader, one column spec, two sync renderers that cannot query) and CELL-FOR-CELL over the parsed bytes; R53's framing and §3.G G1's basis PRINTED on both from the same constants the API returns; four fail-CLOSED refusals rather than a misleading document; two downloads on the existing `TRANSPORT_READ`, no table, no migration, no new permission, no new dependency) shipped — **G4.5 is CLOSED and so is R41**. WO-84 (G4.2/R50: the off-invoice rebate merge into `net_eur_eff` + the source guard — the recon finding that `net_eur_eff` was an exact COPY of `net_eur` on every production row, so `contract_audit`'s `applied` term was identically zero and its short-discount flag over-claimed by the whole contracted rebate in a letter WO-83 had just made client-reachable; §4.2's two-tier model implemented with a litres-pro-rata allocation stated as a documented interpretation and a cumulative-then-quantize walk that loses no cent, idempotent because it always recomputes from the as-invoiced net, written by the ENGINE as the first close stage and never by a web request (§3.H), with R50's guard in both halves — an unsourced rebate refused fail-CLOSED and a DISAPPEARED rebate layer warned loudly from §2.5's history-learned 'Expected rebate' expectation; §4.15 refusing a non-EUR rebate rather than guessing a rate; one tenant table with FORCE RLS in the same migration and a real tenancy probe in the same commit) shipped — **G4.2 is CLOSED and so are R50 and R49**. WO-85 (G4.1/R51: the canonical query registry — `app/services/transport/queries.py`, six named pure org-scoped Select builders that 18 call sites over `fuel_transactions`/`vat_claim_lines` now go through, closing ONE byte-identical fork in the claim-building path (`build_claim_lines` vs `checklist._unresolved_suppliers`) and the triple re-typing of the unfrozen-claim-line cut behind the R3 gate / R10 gate / G2.5 freeze — plus its DELETE, the fourth writer; the deliverable is the STRUCTURAL guarantee: an AST scan over the transport service AND route packages refusing any rival `select()` or `<Model>.org_id` filter, with a seeded-violation self-test, demonstrated by re-introducing the real fork; R51's materialised-metric half is a documented PARTIAL HARVEST — no transport rollup table exists to drift-check; no table, no migration, no route, no SPA change, zero pre-existing tests edited) shipped — **G4.1 is CLOSED and so is R51's query-layer half**. Remaining: G4.4 (client claim-status portal), G4.6 (diesel excise), G4.7 (overpay + benchmark — which also owns R53's SECOND framing, the *"negotiation evidence, NOT a contractual claim-back"* notice), G4.8 (refund-estimate funnel), and the analytics UI. |
| M6 | Integrations & enterprise go-live | `Planned` |

**Test suite:** 761 → 1169 → 1216 → 1247 → 1259 → 1290 → 1303 → 1309 → 1322 → 1352 → 1357 → 1369 → 1384 →
1393 → 1402 → 1435 → 1497 → 1527 → 1578 → 1610 → 1645 → 1679 → 1699 passed (+938 total, +20 this
session; the 1679 baseline was measured at WO-69's close-out), 10 skipped (pg-only, verified
separately on real Postgres), 0 known regressions, as of WO-70. WO-71: 1699 → **1718 passed, 10 skipped** (+19 new R26 tests; full suite re-run green at the WO-71 commit). WO-72: 1718 → **1744 passed, 10 skipped** (+26 new G3.5 tests; full suite re-run green at the WO-72 commit). WO-73: 1744 → **1764 passed, 10 skipped** (+20 new R44 tests; 21 pre-existing tests raised in fixture privilege via `activate_entity`, 0 assertions weakened; full suite re-run green at the WO-73 commit). WO-74: 1764 → **1785 passed, 10 skipped** (+21 new G2.12 tests; 0 pre-existing tests touched, 0 assertions weakened; full suite re-run green at the WO-74 commit). WO-75: 1785 → **1795 passed, 10 skipped** (+10 new R3 lock-gate tests; 9 pre-existing successful-submit tests raised in fixture privilege via the shared `register_documented_invoice` conftest helper (resolved+documented lines) and the G2.12 frozen-synthetic seeding switched to a direct row tamper (its own `vat_id` precedent) because the legal path now correctly refuses, 0 assertions weakened; full suite re-run green at the WO-75 commit). WO-76: 1795 → **1817 passed, 10 skipped** (+22: 20 new claim-route tests + 2 new tenancy-parity probe params (`vat_refund_claims`/`vat_claim_lines` EXEMPT→probe); 0 pre-existing tests touched, 0 assertions weakened; full suite re-run green at the WO-76 commit). WO-77: 1817 → **1853 passed, 10 skipped** (+36: 26 new admin/artifact route tests + 8 new tenancy-parity probe params + 2 new authz-coverage regression tests (the transport package's routes were outside the CI coverage net — FastAPI's lazy `include_router`; fixed and pinned) (`vat_receipt_waivers`/`vat_checklist_rules`/`vat_supplier_cadences`/`vat_receipt_controls`/`vat_note_invoice_overrides`/`fuel_tieout_expectations`/`vat_customer_lifecycles`/`vat_country_activations` EXEMPT→probe); 0 pre-existing tests touched, 0 assertions weakened; full suite re-run green at the WO-77 commit). WO-78: **1853 passed, 10 skipped — UNCHANGED** (a frontend-only order: zero backend files touched, so the backend regression net is the flat line it should be; the new coverage is 29 Playwright specs in `frontend/e2e/vat-claims.spec.ts`, taking the `npm run test:e2e` list CI runs from 31 to 60 passing). WO-79: 1853 → **1865 passed, 10 skipped** (+12: 11 new fuel-route tests + 1 new tenancy-parity probe param (`fuel_transactions` EXEMPT→probe); 0 pre-existing tests touched, 0 assertions weakened; full suite re-run green at the WO-79 commit) plus 10 new Playwright specs taking the `npm run test:e2e` list from 60 to 70 passing. WO-80: **1865 passed, 10 skipped — UNCHANGED** (a frontend-only order: zero backend files touched, so the backend regression net is again the flat line it should be; the new coverage is 47 Playwright specs — 42 in the new `frontend/e2e/vat-admin.spec.ts` plus five waiver specs on the claims spec — taking the `npm run test:e2e` list CI runs from 70 to 117 passing). WO-81: 1865 → **1900 passed, 10 skipped** (+35 new recovery-analytics tests — the six readiness buckets each proven with a claim genuinely constructed in that state, the 60-day deadline window asserted on both sides of its edge one day apart, the §4.14 cross-currency draft, the euro reconciliation against hand-computed Decimals, and the route matrix; 0 pre-existing tests touched, 0 assertions weakened; full suite re-run green at the WO-81 commit, `1900 passed, 10 skipped in 1983.85s`). Frontend untouched — no e2e run needed, the Playwright list stays at 117. WO-82: 1900 → **1971 passed, 10 skipped** (+71: 25 new contract-audit detection tests + 24 new claim-back lifecycle tests + 20 new overcharge-route tests + 2 new tenancy-parity probe params (`vat_supplier_contract_terms`/`vat_overcharge_claims`, classified as real HTTP probes in the same commit that created them — never EXEMPT); 1 pre-existing assertion RE-PINNED (the hard-coded table count in `tests/test_docs_truth.py`, 79 → 81, because this order adds two tables — a truth-up, not a weakening), 0 assertions weakened; full suite re-run green at the WO-82 commit — 1971 passed, 10 skipped in 2201s. (The first full run reported one failure, `test_readme_scale_numbers_match_the_live_tree`, purely because the README's scale line was edited WHILE that 37-minute run was in flight: pytest had already compiled the old `== 79` constant at collection and read the new README at run time. The file passes in isolation and in the clean re-run.) Postgres 16 gate re-verified on a fresh scratch cluster: `test_rls.py` (incl. the RLS/model set-equality check over the two new tables) + `test_numbering_concurrency.py` + `test_transport_lock_concurrency.py` = 6 passed on a NOSUPERUSER role, `alembic upgrade/downgrade/upgrade` clean, both tables confirmed `relrowsecurity`+`relforcerowsecurity`.) Frontend untouched — the Playwright list stays at 117. **WO-83 did not extend this line**; the tree collected 2017 tests at its commit `03c9326` (README still claimed 1981 — the collected-test figure is deliberately NOT asserted by `tests/test_docs_truth.py`, so CI was never red), so the WO-83 baseline is **2007 passed, 10 skipped** and WO-84 re-pins both figures. WO-84: 2007 → **2053 passed, 10 skipped** (+46: 31 new merge/guard tests + 14 new rebate-route tests + 1 new tenancy-parity probe param (`vat_off_invoice_rebates`, classified as a real HTTP probe in the same commit that created the table — never EXEMPT); 1 pre-existing assertion RE-PINNED (the hard-coded table count in `tests/test_docs_truth.py`, 81 → 82, because this order adds one table — a truth-up, not a weakening), 0 pre-existing tests touched, 0 assertions weakened; full suite re-run green at the WO-84 commit — `2053 passed, 10 skipped in 2085.84s`. Postgres 16 gate re-verified on a fresh scratch cluster: `test_rls.py` (incl. the RLS/model set-equality check over the new table) + `test_numbering_concurrency.py` + `test_transport_lock_concurrency.py` = 6 passed on a NOSUPERUSER role, `alembic upgrade/downgrade/upgrade` clean and `alembic check` reporting no drift, `vat_off_invoice_rebates` confirmed `relrowsecurity`+`relforcerowsecurity`.) Frontend untouched — the Playwright list stays at 117. WO-85: 2053 → **2073 passed, 10 skipped** (+20 new registry/anti-forking/equivalence tests; 0 pre-existing tests touched, 0 assertions weakened, 0 pinned docs-truth numbers moved — the order adds no table, no migration and no module to `app/services/*.py`, so `tests/test_docs_truth.py`'s counts are untouched and only README's deliberately un-asserted collected-test figure is re-pinned 2063 → 2083; full suite re-run green at the WO-85 commit — `2073 passed, 10 skipped in 1882.51s`. The transport subset alone was run green twice mid-order, 886 passed. No Postgres gate re-run was needed: this order creates no table, alters no migration and takes no lock.) Frontend untouched — the Playwright list stays at 117.

---

## M3 — In Progress

- [x] **WO-49** — `Completed` — M3 opener: the transport-vertical foundation. `app/models/transport/
  vat_claim.py` (`VatRefundClaim`/`VatRefundClaimLine`, the `(org, entity, refund_country, ref_period)`
  claim grain, R1 — `entity_id` reuses the existing `issuer_profiles` registry rather than a new
  legal-entity table); `app/services/transport/claim_gates.py::is_synthetic()` (R3, the ONE predicate
  every future lock/checklist/readiness/workbook gate must import — harvested verbatim from
  `BA_fleet_fuel.md` C2); `app/services/transport/claim.py::get_or_create_claim` (idempotent on the
  grain, R1's acceptance test verbatim); the `transport` module entitlement (default OFF, absent from
  every plan's module set — pricing is owner-blocked, `docs/DECISIONS-NEEDED.md` §10); 4 new
  permissions (`vat.read/write/submit`, `transport.read`) in all 8 `ROLE_PERMISSIONS` rows ahead of any
  route (ADR-P3 rule 5); migration `02d418169f97` (2 new tenant tables, RLS in the same migration,
  defense-in-depth CHECK constraints for the period shape and R11's "goods code 9 never" rule); the
  `test_boundaries.py` cross-domain-import CI assertion ADR-0023 promised. Explicitly NOT built (future
  M3 work orders, `ARCH_plan.md` G2.2 onward): the lock table, any gate (period-end/deadline/minimum/
  checklist/receipt-waiver), fee freezing, status derivation, goods-code mapping, `fuel_transactions`,
  the monthly close job, capture/entity-resolution, and every `api/routes/transport/*` route. Detail:
  `docs/plan/plan-a/wo/WO-49-G1.1-G2.1-G2.3.md`.

- [x] **WO-50** — `Completed` — G1.2: the typed `fuel_transactions` model
  (`app/models/transport/fuel_transaction.py`) per `BA_fleet_fuel.md` section 4.2 (the canonical
  transaction schema) + section 8.1 items 4-6 (no duplicated positional schema; split the overloaded
  `note` into `invoice_ref`+`provenance_note`; a real natural key). Note: `ARCH_plan.md` tags this task
  R29/R30, but those R-numbers are actually about engine read-only ownership and the separate claims
  store — not the transaction schema; corrected in `docs/plan/plan-a/wo/WO-50-G1.2.md`. Natural key
  `(org, entity, supplier, period, line_seq)` — caller-assigned, deterministic line position — makes
  ingestion insert-or-no-op, never Fleet Fuel's DELETE-by-period; `app/services/transport/product_group.py
  ::derive_product_group()` centralizes the PROMO → HVO → {AdBlue,Parking,Toll/Fees} → Diesel →
  Service/Other precedence the same way `is_synthetic()` is centralized; `app/services/transport/
  fuel_ingest.py::ingest_transaction()` gates on the module entitlement first, resolves the entity via
  `issuer.get_by_id` (opaque 404), `q2`-quantizes every monetary column while leaving `qty` deliberately
  unrounded, audits exactly once per real insert. `invoice_id` is a nullable FK into `invoices` (ADR-P3
  rule 1) — the same table `vat_claim_lines.invoice_id` (WO-49) points at, so the two transport tables
  relate only through the shared AP invoice, never a new transport-internal cross-reference. Migration
  `fc45baaf3283` (1 table, RLS in the same migration); RLS proven on real Postgres (cross-tenant SELECT
  returns zero rows, cross-tenant INSERT blocked by `WITH CHECK`). 68 tables, 74 revisions. Detail:
  `docs/plan/plan-a/wo/WO-50-G1.2.md`.

- [x] **WO-51** — `Completed` — G2.2: the one-invoice-one-submission lock table. `app/models/transport/
  lock.py` (`VatClaimedInvoice`, `UNIQUE(org_id, entity_id, refund_country, supplier, invoice_ref)` IS
  the lock, R4 — `entity_id`/`refund_country` denormalized so the constraint spans EVERY claim, not just
  the one that currently holds a row, and widened with `org_id` per this codebase's standing convention
  over the harvested BA text, which predates multi-tenancy); `app/services/transport/lock.py::
  submit_claim` (a minimal stub `draft`→`submitted` transition — acquires one lock row per invoice via a
  plain ORM INSERT, never an upsert, in the SAME flush as the claim's status mutation, so a lost race
  raises `IntegrityError` and rolls back the whole transition, nothing partially applied) and
  `withdraw_claim` (R5 — the ONLY function that deletes a lock row, proven both structurally via a
  grep-based test and behaviorally via a test that directly mutates a claim's `status` and asserts no
  lock release cascades). Three composite FKs off the lock row: `(org_id, claim_id)` CASCADE into
  `vat_refund_claims`, `(org_id, entity_id)` RESTRICT into `issuer_profiles`, `(org_id,
  fuel_transaction_id)` RESTRICT into `fuel_transactions` (WO-50's composite unique constraint existed
  specifically for this FK target — one representative transaction row per lock; protecting every row
  sharing an `invoice_ref` is a future close/re-close guard's job, flagged explicitly as NOT solved by
  this FK alone). Migration `dea0a87e6b0d` (1 table, RLS in the same migration). The headline proof: a
  real-Postgres concurrency test (`tests/test_transport_lock_concurrency.py`, added to the CI `postgres`
  job) fires two DIFFERENT claims racing `asyncio.gather` over the SAME invoice key — exactly one wins,
  the loser's status reads back its unchanged pre-submission value from a fresh query, proving the whole
  transaction rolled back, not just the lock insert; a second test proves `withdraw_claim` then frees the
  key for a third claim. All 6 pre-existing pg-only files (`test_rls.py`, `test_rls_connection_reuse.py`,
  `test_numbering_concurrency.py`, `test_payment_run_pay_concurrency.py`,
  `test_credit_note_lock_concurrency.py`, `test_expense_decision_concurrency.py`) re-verified green on
  the same scratch cluster. 69 tables, 75 revisions. Detail: `docs/plan/plan-a/wo/WO-51-G2.2.md`.

- [x] **WO-52** — `Completed` — G2.4: claim-line construction + note→invoice resolution.
  `app/services/transport/invoice_match.py::resolve_invoice_ref` — the ONE C3 resolution order (two
  note-matching heuristics — prefix / stem-contained, a documented interpretation of an underspecified
  BA phrase — then the admin-curated override, only consulted once both heuristics fail to resolve
  uniquely and NEVER displacing a heuristic match, then the sole-registered-invoice fallback, else
  UNMATCHED); `app/models/transport/note_override.py::VatNoteInvoiceOverride` (R16's admin override
  table, `ondelete=CASCADE` on the target FK — a real defect caught live: a composite-FK `SET NULL`
  would try to null the NOT-NULL `org_id` column too, so CASCADE deletes the dead override row instead);
  `app/services/transport/claim_lines.py::build_claim_lines` (materializes the LIVE, unfrozen
  `VatRefundClaimLine` rows for a `draft` claim from its in-scope `fuel_transactions` — R2, one row per
  (invoice, product_group), never an `ALL:` aggregate; refuses a non-draft claim; only ever touches
  `frozen_at IS NULL` rows, future-proofing G2.5's freeze). Two new read-only AP-domain seams
  (`app.services.invoices`, `app.services.vendors.get_by_name`) fill the `invoice_service` gap ADR-0023
  always named, so `services/transport/*` never imports `app.models.invoice`/`app.models.vendor`
  directly (`test_transport_services_do_not_import_other_domain_models` stays green). Migration
  `4cb7fca7e508` (1 table, RLS in the same migration). `tests/test_tenancy_parity.py`'s exemption
  registry gained a `vat_note_invoice_overrides` row (no route yet to drive an HTTP-level probe through).
  70 tables, 76 revisions, 83 service modules. Detail: `docs/plan/plan-a/wo/WO-52-G2.4.md`.

- [x] **WO-53** — `Completed` — G1.3/G1.4: the monthly close as a durable job + locked-line
  protection. `app/services/transport/close.py::run_close` (re)builds live claim lines (G2.4) for
  every `draft` claim in scope for a closed `"YYYY-MM"` period, on the PRE-EXISTING
  `app.services.jobs` durable-job framework — no new mechanism, since that framework already
  provides idempotent-by-key enqueue, dead-letter + manual retry, and rollback-then-fail on any
  handler exception (R31/R60 verbatim, with zero new infrastructure). `enqueue_close` keys the job
  on `idempotency_key=f"transport.close:{period}"`; registered in `app.services.job_handlers`
  (`transport.close`) so the close only ever runs through `jobs.run_once`, never inline in a web
  request (no `api/routes/transport/*` route exists to call it synchronously either). Fleet Fuel's
  own `consolidate→build_master→history→run_control→backup` ETL pipeline is deliberately NOT ported
  — there is nothing to consolidate FROM, since `fuel_transactions` ingestion (G1.2) is already
  insert-or-no-op, not Fleet Fuel's DELETE-by-period-then-reinsert. G1.4 ("locked lines are
  protected from a re-close") turned out to be already STRUCTURALLY true from G2.2/G2.4 alone —
  `run_close` only ever queries `status == "draft"` claims (a submitted claim's lines are invisible
  to it) and `vat_claimed_invoices`' pre-existing `RESTRICT` FK into `fuel_transactions` (WO-51)
  independently refuses a raw delete of a locked transaction at the database level — proven directly
  for the FIRST time by this order's own test, since no prior order had exercised that FK's
  delete-time behavior. No migration (no new model/table). Detail: `docs/plan/plan-a/wo/WO-53-G1.3-G1.4.md`.

- [x] **WO-54** — `Completed` — G2.5 ("the linchpin", `ARCH_plan.md`'s own word): frozen claim
  lines + frozen VAT base at submission. `app/services/transport/freeze.py::freeze_claim_lines`
  stamps `frozen_at` on every currently-unfrozen `VatRefundClaimLine` and sets `claim.vat_eur`/
  `vat_local`/`currency` from EXACTLY that claim's own lines (C10 — never a raw period `SUM`);
  `lock.py::submit_claim` calls it in the SAME flush as lock acquisition + the status flip, so a
  lost lock race rolls back the freeze too (proven by a same-session backstop test mirroring
  WO-51's own). Refuses (`claim_currency_mismatch`/`claim_line_mixed_currency`) rather than sum raw
  local-currency amounts across more than one currency, at both the per-line-bucket level
  (`build_claim_lines`, extended) and the whole-claim level (`freeze_claim_lines`) — master-context
  §4.14. `vat_claim_lines` gained 3 additive nullable columns (`net_local`/`vat_local`/`currency`,
  migration `bc783e1ec7c2`, no RLS change — existing table) so `build_claim_lines` (G2.4) captures
  the local-currency figure per line. Fee freezing (R13/G2.9) stays explicit future work. 70 tables,
  77 revisions. Detail: `docs/plan/plan-a/wo/WO-54-G2.5.md`.

- [x] **WO-55** — `Completed` — G2.8: the Art. 9 goods-code mapping, independent of the
  G2.2-G2.7 critical-path chain (deps G1.2 only). `app/services/transport/goods_code.py::GOODS_CODE`
  is harvested VERBATIM from `BA_fleet_fuel.md` A6 (Diesel/HVO/Promo adj → "1", Toll/Fees → "4",
  AdBlue/Parking/Service/Other → "10"); `derive_goods_code()` defaults an unrecognised
  `product_group` to "10", never "9" (R11), on top of the pre-existing DB CHECK constraint from
  WO-49 — two independent layers. `build_claim_lines` (G2.4) now populates every line's
  `goods_code` at construction time (additive change to an already-shipped function; the full
  pre-existing transport suite passed unmodified). No migration (the column has been nullable
  since WO-49). Detail: `docs/plan/plan-a/wo/WO-55-G2.8.md`.

- [x] **WO-56** — `Completed` — G2.6 slice 1: the hard period-end gate (R7) + the Art. 17
  minimum-amount gate (R8) + the 30-Sep deadline risk scanner (R9) — closing `ARCH_plan.md`'s
  own highest-scored risk item (L-1, score 6: the fatal 30-Sep time-bar). `app/services/transport/
  deadline.py`/`minimum.py` (new, pure functions, no DB access); `lock.py::submit_claim` gains the
  period-end gate (409 `period_not_ended`) and the minimum gate (409 `below_minimum`, an
  `override_minimum` param that records the exact comparison in `status_note`), previewed via a new
  `freeze.preview_vat_base` BEFORE the freeze/lock machinery — matching Fleet Fuel's own D5 gate
  order, so a below-minimum or not-yet-ended claim never mutates anything. Sweden/Denmark compare
  `vat_local` against their fixed statutory amounts (harvested verbatim); every other country
  (including Poland, deliberately) compares `vat_eur` against €400/€50. Every pre-existing
  `submit_claim` test call site (WO-51/54/55 + the real-Postgres concurrency test) was re-audited
  and given `override_minimum=True` where its own purpose was unrelated to R8 — one fixture bug
  (a not-yet-ended test period) caught and fixed while verifying on a scratch Postgres cluster
  (7 pg-only RLS/concurrency files all green together on a fresh DB). G2.9 (fee freezing) explicitly
  NOT attempted — `docs/DECISIONS-NEEDED.md` §10 updated: no established "customer"/fee-rate concept
  to build against without guessing the commercial model. No migration. Detail:
  `docs/plan/plan-a/wo/WO-56-G2.6-slice1.md`.

- [x] **WO-57** — `Completed` — G2.6 slice 2: the annual claim mop-up + quarterly overlap
  duplicate-block (R6). `lock.py::_apply_annual_mop_up_or_duplicate_block` runs after the R7/R8
  gates, before the freeze: an ANNUAL claim silently excludes an invoice already locked by a
  QUARTERLY claim (the mop-up, not a conflict) but still locks a genuinely new one; a QUARTERLY
  claim treats ANY existing-lock overlap as a duplicate and blocks the WHOLE submission before any
  mutation; an annual claim with nothing left after exclusion is refused ("nothing to claim
  annually"). Two annual claims overlapping the same invoice is a fail-closed blocking duplicate
  (an interpretation beyond the harvested text). One pre-existing WO-51 test now expects the new,
  cleaner `ConflictError`/`duplicate_invoice_lock` instead of a raw DB `IntegrityError` — a real
  improvement (catches an already-known duplicate before any mutation), not a weakened assertion;
  the DB constraint and the genuine-concurrent-race case stay independently proven, re-verified on
  a fresh scratch Postgres cluster after this order's `lock.py` changes. No migration. Detail:
  `docs/plan/plan-a/wo/WO-57-G2.6-slice2.md`.

- [x] **WO-58** — `Completed` — G2.6 slice 3: the document-presence gate (R10) + receipt-control
  waivers (R15). `app/services/transport/document_gate.py::enforce_document_presence` checks every
  real, RESOLVED `vat_claim_lines` row (never an `UNMATCHED` one) has >=1 captured document with real
  stored bytes (`ExtractionRun.source_sha256 IS NOT NULL`) — reads the claim's own MATERIALIZED lines,
  not `submit_claim`'s still-caller-supplied `invoices` tuple, since the lines are what actually gets
  frozen; a new batch seam `app/services/extraction.py::invoice_ids_with_documents` (one query, no N+1)
  is the AP-domain read this needed. A new tenant table `vat_receipt_waivers` (grain `(org, claim,
  supplier)`) backs `app/services/transport/waiver.py` (`set_waiver`/`remove_waiver`/
  `waived_suppliers`): `set_waiver` refuses (422 `waiver_supplier_has_invoices`) a supplier with any
  registered invoice for the claim's refund country (reusing `invoice_match.registered_invoices`, never
  a second implementation), otherwise records the waiver idempotently on a `draft` claim;
  `claim_lines.build_claim_lines` excludes a waived supplier's transactions from grouping BEFORE the
  resolution step (C9's "excluded from the claim by construction" — never even an `UNMATCHED` line);
  `lock.submit_claim` stamps every active waiver into `status_note` at submission. Both gates wired
  into `submit_claim`'s D5 order after the R6 mop-up/duplicate-block gate and before the freeze.
  Migration `312f33068c4b` (1 new tenant table, RLS in the same migration; up/down/up clean on real
  Postgres). README's "Scale of the codebase" truth-up sentence and `test_docs_truth.py`'s hard-coded
  table count updated (70→71 tables, 77→78 revisions, 1332→1379 collected tests). Explicitly NOT
  attempted: wiring `is_synthetic()` as an actual submission-blocking gate over a remaining, un-waived
  `UNMATCHED` line (a real, pre-existing gap — flagged, not solved); G2.9 (fee freezing, still
  decision-gated); G2.7/G2.10; any `api/routes/transport/*` route. Detail:
  `docs/plan/plan-a/wo/WO-58-G2.6-slice3.md`.

- [x] **WO-59** — `Completed` — G2.7: the status lifecycle 1A→5 (R12/R17), narrowly scoped.
  `app/services/transport/status.py::derive_stage` computes the system-derived `AUTO_CODES` value
  (`1A`/`1B`/`1C`/`1E`) for a `draft` claim in D3's literal order — an unresolved `UNMATCHED` line or a
  resolved invoice missing its document → `1A`; period not ended → `1B`; a "verdict caveat" (a
  DOCUMENTED INTERPRETATION: below the Art. 17 minimum, or an active receipt-control waiver) → `1C`;
  else `1E` — reusing WO-58's own `claim_gates.is_synthetic`/`document_gate` rather than re-deriving
  either check (a new non-raising twin, `document_gate.missing_document_invoice_ids`, keeps the blocking
  gate and this read-only preview on ONE query). `status.set_status_code` is the ONE writer of
  `status_code`: refuses every `AUTO_CODES` value ("system-controlled") and refuses every `MANUAL_CODES`
  value — INCLUDING `"2"` itself — while the claim is still `draft` (`claim_not_submitted`), matching
  R17's own acceptance tests verbatim; on an already-locked claim it stamps the workflow-code LABEL (+
  `action_deadline` for `2B`/`3D`'s soft reminder, R12) WITHOUT ever touching the coarse engine `status`
  column — deliberately, since the real `ENGINE_OF` engine-state transitions for 3/3A/3B/3C/3D/4/4A/5
  collide with G2.9's decision-gated fee engine. `lock.submit_claim` additively stamps
  `status_code = "2"` in the same flush as its existing writes. No migration (`status_code`/
  `action_deadline` are pre-existing nullable columns from WO-49). Detail: `docs/plan/plan-a/wo/WO-59-G2.7.md`.

- [x] **WO-60** — `Completed` — G2.10 slice 1: the adjustable submission checklist as DATA (R45),
  a documented PARTIAL harvest. New tenant table `vat_checklist_rules` (key/label/scope/check_type/
  reference/active/sort) backs `app/services/transport/checklist.py`: `seed_default_rules` (idempotent),
  `set_active` (the ONLY writer of `active` — "deactivate a rule ⇒ it disappears from the gate," proven
  verbatim), `submission_checklist` (the evaluator). Only `customer_data`/`bank_account`
  (`check_type="data"`, `scope="customer"`, evaluated against the claimant `IssuerProfile`'s
  registration_number/vat_number/address_line1/iban — no new customer concept, ADR-P3 rule 2) are
  seeded/evaluable — `contract`/`nace`/`trade_register`/`power_of_attorney` (needing a document-
  requirements-with-expiry concept this codebase doesn't have yet, or a new `nace_code` column) are
  explicitly deferred, not silently skipped. The four claim-level items (receipt control, unresolved
  refs, documents attached, period ended) reuse WO-56/58's own pure checks — since a materialized
  `vat_claim_lines` row collapses every unresolved transaction under one `"UNMATCHED"` ref with no
  supplier retained, naming "the missing supplier" re-queries `fuel_transactions` directly (one
  duplicated SELECT, zero duplicated resolution/waivability logic). `status.derive_stage` (G2.7) now
  consults this evaluator, replacing WO-59's own two-check proxy exactly as that order's docstring
  anticipated — `tests/transport/conftest.py::make_entity` gained synthetic registration_number/
  address_line1/iban defaults so every pre-existing "clean claim" test fixture stays clean under the
  new checks (raising fixture completeness, not weakening an assertion). Migration `920cbde1e481`
  (1 new tenant table, RLS in the same migration; up/down/up clean on real Postgres, 11 pg-only
  RLS/concurrency tests re-verified on a fresh scratch cluster). README/`test_docs_truth.py` truth-up
  (71→72 tables, 78→79 revisions, 1394→1403 collected tests). Detail: `docs/plan/plan-a/wo/WO-60-G2.10-slice1.md`.

- [x] **WO-61** — `Completed` — G3.1 slice 1: the per-country supplier legal-entity registry (R21/R22),
  a documented partial harvest of G3.1. New tenant table `supplier_vat_registrations`
  (`(org, supplier, country)` → `vat_number`/`entity_name`/`source`) backs
  `app/services/transport/supplier_entity.py`: `get_registration` (a single exact-key SELECT, R21 —
  marker-only, no fuzzy matching anywhere), `set_registration` (the only admin-curated writer, ALWAYS
  wins over a learned row), `learn_registration` (R22 — seeds a NEW `"capture"` row only when none
  exists; never overwrites an existing row of either source, never touches a `Vendor`/group-primary row,
  never queues a pending-change request — a deliberate contrast with A2.3's vendor-bank-detail dual
  control since this is diagnostic/filing metadata, not a payment-redirection vector). **R20** (capture
  actually reading the seller off a real invoice document — the Eurowag per-country footer, the E100
  anchor) is **explicitly NOT closed** by this slice — that is text/PDF extraction, `G3.2` (the fuel-card
  parser registry, a separate XL-effort, 7-network build, the natural next slice); `learn_registration`
  has no real caller yet, proven correct at the function level with a synthetic "just-captured" input,
  mirroring `is_synthetic()`'s own WO-49 debut with zero consumers wired in. Migration `4ae197627e35`
  (1 new tenant table, RLS in the same migration; up/down/up clean on real Postgres, 11 pg-only
  RLS/concurrency tests re-verified on a fresh scratch cluster). README/`test_docs_truth.py` truth-up
  (72→73 tables, 79→80 revisions, 1403→1412 collected tests). Detail:
  `docs/plan/plan-a/wo/WO-61-G3.1-slice1.md`.

- [x] **WO-62** — `Completed` — G3.2 slice 1: the fuel-card parser registry (R20), scoped down from
  `ARCH_plan.md`'s XL/7-network G3.2 to shared infrastructure + ONE fully-specified network, the same
  slicing discipline WO-56/57/58 used for G2.6. `app/services/transport/fuel_card_parser.py` is the
  deterministic-first registry (`FuelCardParser` ABC, `register`/`select`/`run`, fail-closed — raises
  rather than guessing which network a file belongs to) mirroring the AP-domain `extraction_provider.py`
  PATTERN over a fuel-transaction-shaped output, not its invoice-shaped type; `app/services/transport/
  parsers/eurowag.py` (`EurowagParser`) is the first network — Eurowag, because it is R20's own worked
  example (`BA_fleet_fuel.md` §3.B1): anchors ONLY to lines carrying the literal `"Pārdevējs / Verkoper:"`
  label against the harvested legal-form token set, so the Czech "W.A.G. Issuing Services, a.s." factoring
  entity is structurally unreachable even when an unrelated disclosure sentence naming it sits elsewhere in
  the same document. `app/services/transport/statement_ingest.py::ingest_statement` is the REAL caller
  `fuel_ingest.ingest_transaction` (WO-50) and `supplier_entity.learn_registration` (WO-61) were built for
  but never had: gates on the `transport` entitlement first, resolves EVERY line's EUR figure via
  `app.services.fx.to_eur` in a first pass BEFORE writing anything (a statement with one unconvertible
  line writes ZERO rows, not "all but one" — mirrors `expenses.build_items`'s existing all-or-nothing FX
  precedent), reads the network off the PARSED statement rather than a caller-supplied string (a
  mislabeled upload can't be miscategorized), and a malformed row aborts the whole statement at parse time
  rather than being silently dropped. R22 proven END TO END for the first time through a real caller
  (an admin-curated registration survives an ingest that would otherwise seed a conflicting one). No
  migration — pure service/parser composition over WO-50/WO-61's existing tables. Explicitly NOT
  attempted (named future slices, priority-ordered in the work order): the remaining six networks — E100
  (VAT-inclusive gross, the buyer-VAT-annexe hazard, the OTHER R20 worked example), Q8/Port One (the
  off-invoice rebate that is the entire reason `net_eur_eff` exists as its own column), DKV (5.63% service
  fee), TFC by Moya (hub-only discount), Moeve (6-dp VAT-inclusive maths), BP/Aral (Polish split-payment);
  G3.3 (the two independent validation regimes — line-count tie-out + capture review gate — explicitly
  DEPENDS on G3.2 per `ARCH_plan.md`); a persisted statement review-queue (this slice's review surface is
  the returned `warnings` list only); any `api/routes/transport/*` route. 73 tables, 80 revisions (both
  unchanged — no migration), 1412→1445 collected tests. Detail: `docs/plan/plan-a/wo/WO-62-G3.2-slice1.md`.

- [x] **WO-63** — `Completed` — G3.2 slice 2: the E100 fuel-card parser (R20's second worked example),
  the second network registered into WO-62's `fuel_card_parser` registry. `app/services/transport/
  parsers/e100.py` (`E100Parser`) anchors ONLY to lines carrying the literal `"E100 International Trade"`
  marker string (verbatim, `BA_fleet_fuel.md` §3.B1) — a co-present, unrelated buyer-VAT annexe line
  ("repeats on every annexe page") is never inspected, so it can never enter `parsed.entities` (the direct
  proof this order exists for). This is also the first parser to encounter a structurally DIFFERENT money
  model: E100 supplies only VAT-inclusive `gross_local` + a per-line `vat_rate`, never independently-given
  `net_local`/`vat_local` the way Eurowag's CSV does — `net_local`/`vat_local` are DERIVED by the reverse
  calculation (`net_local = gross_local / (1 + vat_rate/100)`, `vat_local = gross_local - net_local`),
  entirely in `Decimal`, proven numerically (`net_local + vat_local == gross_local`, pre-rounding); a
  `vat_rate` outside `[0, 100]` raises `ValueError` (never silently clamped), and a malformed row (bad
  decimal / out-of-range rate / bad country) aborts the WHOLE statement, unchanged discipline from
  `eurowag.py`. `fuel_card_parser._default_parsers()` gains one line (`E100Parser()`, appended after
  `EurowagParser()`); a registry-level test dispatches a well-formed file of EACH network to the correct
  parser in the same test, proving they coexist without collision. `statement_ingest.ingest_statement`
  needed ZERO changes — proven by re-running WO-62's own `test_g3_2_fuel_card_parser.py` and
  `test_g3_2_statement_ingest.py` byte-for-byte unmodified, and by the new DB-touching suite re-proving R22
  (learning never clobbers a curated registration), the two-phase FX guarantee, module-off inertness, and
  cross-tenant isolation end to end for E100 specifically. No migration — pure parser addition over
  WO-50/WO-61/WO-62's existing tables/services. Explicitly NOT attempted (named future slices, priority
  order unchanged from WO-62's own list, now with E100 struck off): Q8/Port One (the off-invoice
  `net_eur_eff` rebate), DKV (5.63% service fee), TFC by Moya (hub-only discount), Moeve (6-dp VAT-inclusive
  maths, per-line IVA rate), BP/Aral (Polish split-payment); station-colour discount-tier modelling
  (already folded into E100's given gross, per `BA_fleet_fuel.md` §4.2 — not a separate rebate layer);
  semi-monthly cadence enforcement (G3.3); G3.3 itself; a persisted statement review-queue; any
  `api/routes/transport/*` route. 73 tables, 80 revisions (both unchanged — no migration), 1445→1480
  collected tests. Detail: `docs/plan/plan-a/wo/WO-63-G3.2-slice2.md`.

- [x] **WO-64** — `Completed` — G3.2 slice 3: the Q8/Kuwait Petroleum fuel-card parser, the third
  network registered into WO-62's `fuel_card_parser` registry. `app/services/transport/parsers/q8.py`
  (`Q8Parser`) reuses Eurowag's straightforward money model (independently-given `net_local`/`vat_local`/
  `gross_local` — Q8 invoices at LIST price, never VAT-inclusive gross the way E100's is), so no new
  arithmetic was needed; what this order proves for the FIRST time in this codebase is that a single
  statement can legitimately carry lines for more than one country and currency in the same upload
  (`BA_fleet_fuel.md` §5.1's own "per-line country + currency" quirk for Q8 — neither Eurowag's nor E100's
  fixtures ever exercised this before). `Q8Parser` deliberately attempts NO seller-entity detection at all
  — `entities` is unconditionally `[]` with one explanatory warning distinguishing "never attempted" from
  `eurowag.py`/`e100.py`'s "attempted, none found" — because `BA_fleet_fuel.md` gives no footer label or
  anchor marker for Q8 the way it does for Eurowag/E100; an adversarial test plants a "Kuwait Petroleum ...
  VAT: ..."-shaped decoy line in the raw file and confirms it is never picked up (no scan exists to
  accidentally match it). **R20 stays CLOSED at exactly Eurowag and E100** — Q8 is G3.2 progress, not an
  R20 claim. Q8's `net_eur_eff` (the Port One off-invoice-rebate figure) is likewise deliberately left at
  `ingest_transaction`'s existing default (`= net_eur`) and proven so by an explicit test — reconciling
  Q8's list-price statement against a SEPARATE Port One rebate export is a cross-statement merge with no
  worked column layout given anywhere in the harvested spec, and `ARCH_plan.md` already scopes it as its
  own, later board item (G4.2, M5, R49/R50), not a G3.2 slice. `fuel_card_parser._default_parsers()` gains
  one line (`Q8Parser()`, appended third); a registry-level test dispatches a well-formed file of EACH of
  the three networks to the correct parser in the same test. `statement_ingest.ingest_statement` needed
  ZERO changes — proven by re-running WO-62/WO-63's own suites byte-for-byte unmodified, and by the new
  DB-touching suite re-proving the two-phase FX guarantee (including the multi-currency case), module-off
  inertness, and cross-tenant isolation end to end for Q8 specifically. No migration — pure parser addition
  over WO-50/WO-62's existing tables/services. Explicitly NOT attempted (named future slices, priority
  order unchanged from WO-62/WO-63's own list, now with Q8 struck off): DKV (5.63% service fee), TFC by
  Moya (hub-only discount), Moeve (6-dp VAT-inclusive maths, per-line IVA rate), BP/Aral (Polish
  split-payment); the Port One rebate merge itself (G4.2/M5); monthly-per-country cadence enforcement
  (G3.5/G3.3); G3.3 itself; a persisted statement review-queue; any `api/routes/transport/*` route. 73
  tables, 80 revisions (both unchanged — no migration), 1480→1501 collected tests. Detail:
  `docs/plan/plan-a/wo/WO-64-G3.2-slice3.md`.

- [x] **WO-65** — `Completed` — G3.2 slice 4: the DKV Euro Service fuel-card parser + the
  supplier-STATED-EUR money model, the fourth network registered into WO-62's `fuel_card_parser`
  registry and the FIRST (and, per the harvested quirks, last) network to require a deliberate
  extension of the shared parser/ingestion contract. `BA_fleet_fuel.md` §5.1's DKV row — *"SEK/EUR;
  semi-monthly; flat 1.30 SEK/L diesel discount; 5.63% service fee on parking/services; trusts the
  supplier's per-line EUR and pro-rates"* — is a THIRD distinct money model (after Eurowag/Q8's
  independently-given figures and E100's VAT-inclusive reverse-calc): the statement's own per-line
  EUR figure is the conversion's source of truth. `ParsedFuelLine` gained one additive field
  (`stated_net_eur`, default `None` — every prior parser untouched by construction) and
  `statement_ingest._resolve_line` gained the third branch between EUR-identity and cached-ECB:
  `net_eur` = the supplier's figure AS GIVEN (never an ECB recomputation, even when a cached rate
  exists — proven by a test whose seeded fallback SEK rate would have produced a DIFFERENT figure;
  the invoice is what a refund state audits against), `vat_eur` PRO-RATED at the same implied basis
  (`vat_local * stated_net_eur / net_local` — the harvested "and pro-rates" clause), `fx_rate` = the
  implied applied rate (`net_local / stated_net_eur`, 6 dp), `fx_ecb_rate`/`fx_ecb_date` = the
  OFFICIAL reference frozen BEST-EFFORT (NULL when uncached — a stated line NEVER fails on missing
  ECB coverage, the observable difference from the ECB branch, proven with an uncached-currency
  test), `fx_source="stated"` — the platform's pre-existing ADR-0010 enum value
  (`expenses.apply_item_fx` precedent; WO-50's `FX_SOURCE_CHECK` always allowed it, nothing could
  write it until now). Fail-CLOSED: a stated pair whose implied rate is not strictly positive
  (zero/sign-flipped) refuses the WHOLE statement (`fx_stated_inconsistent`) before any write,
  under the unchanged two-phase guarantee; an EUR-currency DKV row must state `net_eur == net_local`
  (a mismatch is a malformed row → whole statement aborts) and rides the untouched identity branch.
  The on-invoice 1.30 SEK/L discount + 5.63% service fee are ALREADY INSIDE the given figures
  (`BA_fleet_fuel.md` §4.2 verbatim) — read, never recomputed (contract-term verification is
  G3.4/M5 territory). Like Q8, `DKVParser` attempts NO seller-entity detection (`entities`
  unconditionally `[]`, the "never attempted" warning; no R20 worked marker exists for DKV) —
  **R20 stays CLOSED at exactly Eurowag and E100**. ONE changed assertion, aligned not weakened:
  WO-64's `test_g3_2_registry_ships_eurowag_e100_and_q8` asserted the exact three-parser list
  (encoding "no fourth network exists") and now asserts membership + relative order, WO-62/WO-63's
  own future-proof registry-test style; the exact four-parser list is asserted by the new DKV suite.
  No migration — pure code addition over existing tables/columns (`fx_source='stated'` was always
  CHECK-allowed). Explicitly NOT attempted (named future slices, priority order unchanged, now with
  DKV struck off): TFC by Moya (hub-only discount), Moeve (6-dp VAT-inclusive maths, per-line IVA
  rate), BP/Aral (Polish split-payment); semi-monthly cadence enforcement (G3.5/G3.3); G3.3 itself
  (now the recommended NEXT M3 item — DKV was the last network whose model touches the shared
  contract, so the two validation regimes can be built against a stable `ParsedFuelLine`/
  `statement_ingest` shape and proven over THREE distinct money models); a persisted statement
  review-queue; any `api/routes/transport/*` route. 73 tables, 80 revisions (both unchanged),
  1507→1537 collected tests (+30: 19 parser + 11 ingest; both endpoints measured live —
  1497→1527 passed + 10 pg-only skips). Detail:
  `docs/plan/plan-a/wo/WO-65-G3.2-slice4.md`.

- [x] **WO-66** — `Completed` — G3.3: the TWO INDEPENDENT VALIDATION REGIMES (R25, P0/L — the
  standing recommendation from WO-65's own report, taken now that the parser/ingestion contract is
  stable over three distinct money models; deps G3.2-partial + C1.1/WO-7 both satisfied). They answer
  different questions and stay structurally separate (`BA_fleet_fuel.md` §2.1a: "Two separate
  regimes"). **Regime 1 — the capture review gate** (`app/services/transport/capture_review.py`,
  pure functions): the harvested nine-rule verdict lattice `ok < warn < error` — invoice number
  present (error) / date `YYYY-MM-DD`, empty passes (warn) / country in the 23-country set (warn) /
  net+vat parse as numbers (error, EARLY RETURN) / `net > 0` (error) / `vat >= 0` (error) /
  `vat <= net` (error) / `net <= 5,000,000` (warn) / VAT-rate coherence within ±0.5pp of a known
  rate, `vat == 0` and unknown countries deliberately SKIPPED (warn); the batch tie-out
  `abs(q2(Σ net+vat) − q2(coversheet_total)) <= 0.02` COMPARED ON DECIMALS (a boundary diff never
  flips on binary-float noise — proven by a test whose float sum WOULD flip); the harvested commit
  gate `can_commit = (errors == 0) and (tie is None or tie.ok)`; WARNINGS NEVER BLOCK. `CapturedLine`
  carries RAW captured strings so rule 4 is real, not vacuously typed away; `review_statement()`
  adapts a typed `ParsedStatement` — ONE rule set for the future review screen and today's wiring
  (the C1.1 lesson; the AP `validation.py` engine is deliberately NOT reused — ADR-P3). Wired into
  `statement_ingest.ingest_statement` (new optional `coversheet_total`) after parse and BEFORE FX: a
  blocked batch raises `capture_review_blocked` naming EVERY error at once, zero rows written; warn
  findings surface in `result.warnings`. `VAT_RATES`: the 23-country count, ±0.5pp window, skip
  rules and the four dual entries (PL 23/8, ES 21/10, EE 22/20, FI 24/25.5) are harvested; remaining
  membership/rates are a DOCUMENTED RECONSTRUCTION from public VAT law (the source table may not be
  copied — master-context §10). **Regime 2 — the engine tie-out** (`FuelTieOutExpectation` /
  `fuel_tieout_expectations`, migration `b3f1c6d2a904` with FORCE RLS in the SAME migration +
  composite `(org_id, entity_id)` FK; service `app/services/transport/tie_out.py`): the
  per-(entity, supplier, period, currency) figures a HUMAN types FROM THE INVOICE PDF (§5.1's
  "fill `expected` from the invoice → PASS = trained" onboarding contract) — `lines` tolerance 0
  (EXACT, always), `gross_local` within a per-row CHECK-bounded [0.02, 0.05] tolerance (default
  0.02), `net_eur`/`gross_eur` 0.05, `diesel_litres` (Diesel product-group only) 0.05 (interpreted —
  no harvested litres tolerance). `close.run_close` evaluates EVERY expectation row FIRST and raises
  ONE `tie_out_failed` naming ALL failures ("the operator sees all failures at once"); the job
  framework's whole-session rollback makes "the hand-off artifact is not written" literal — proven
  through the REAL `enqueue_close` + `jobs.run_once` path (no claim-line rebuild survives).
  Fail-OPEN by absence (an untyped supplier is not checked — the expectation IS the opt-in training
  target), fail-CLOSED once typed; `remove_expectation` is the audited narrow un-halt.
  `set_expectation` upserts (a typo is corrected by retyping; audit keeps old→new), quantizes at the
  service (q2 money / 3-dp litres), and is tenant-opaque (foreign entity → 404-shaped
  `entity_not_found`). The expectation grain's `currency` column is a DOCUMENTED INTERPRETATION
  (WO-64 proved multi-currency statements; §4.14 forbids cross-currency `gross_local` sums — a
  single-currency supplier types one row, byte-equivalent to the harvested behaviour). Aggregation
  in Python over Decimals (never a dialect-coerced SQL SUM). NINE aligned test changes, none
  weakened (each encoded the pre-gate absence): 8 fixture rows across the Eurowag/E100/Q8 ingest
  suites gained a synthetic `invoice_ref` (their subjects — ECB conversion, two-phase FX refusal —
  untouched); the DKV zero-`net_local` test now expects `capture_review_blocked` (rule 5 refuses
  EARLIER than the FX branch's zero-guard, whose ingest coverage moved to the new
  `test_g3_3_stated_zero_eur_still_hits_the_fx_guard_behind_the_gate`); `test_docs_truth`'s table
  count 73→74 (README truth-up: 74 tables, 81 revisions, 1588 collected). Explicitly NOT attempted
  (named board owners): the anti-drift extraction baseline / regression check (>0.02 re-extraction
  drift — G3.3 slice 2, needs a baseline store + a re-extraction caller that does not exist);
  TFC by Moya, Moeve, BP/Aral (G3.2's remaining slices, priority order unchanged); cadence
  enforcement (G3.5); post-capture IBAN/VAT-ID/duplicate checks (G3.4); a persisted statement
  review-queue; any `api/routes/transport/*` route. Postgres 16 gate re-verified on a fresh scratch
  cluster (NOSUPERUSER `appuser`, FORCE RLS + `tenant_isolation` policy live on the new table,
  up/down/up clean, 6/6 pg-only RLS/concurrency tests; cluster torn down). 74 tables, 81 revisions,
  1537→1588 collected tests (+51: 24 pure gate + 7 gate-through-ingest + 14 tie-out + 6 close-halt).
  Detail: `docs/plan/plan-a/wo/WO-66-G3.3.md`.

- [x] **WO-67** — `Completed` — G3.2 slice 5: the TFC BY MOYA fuel-card parser
  (`app/services/transport/parsers/tfc.py`, `TFCParser`), the FIFTH network in WO-62's registry and
  the FOURTH distinct money model — the DERIVED-NET hub-only discount (`BA_fleet_fuel.md` §5.1:
  "−0.205/L only at TFC hubs (Meer −0.19); third-party stations undiscounted. Flat 21% VAT"). The
  statement prints litres, a LIST unit price and a per-line `station_class`; the parser DERIVES
  `net_local = qty × (list_price_eur_l − tier)` (harvested tiers 0.205/`hub`, 0.19/`meer`,
  0/`third_party`), `vat_local` at the flat 21% and `gross_local = net + vat`, pure unrounded
  Decimal, derivation basis in `provenance_note` — the "station-classification concept" WO-62's
  slice list named. Deliberately parser-LOCAL: `ParsedFuelLine`/`statement_ingest`/`capture_review`/
  `tie_out` byte-identical to WO-66's tree (WO-65's contract-stability argument holds); every TFC
  line is EUR on the untouched identity branch; the on-invoice discount enters the figures BY the
  derivation (`net_eur_eff` = default; off-invoice stays G4.2). Fail-closed row refusals (each
  aborts the whole statement): unknown `station_class` (a guessed tier is an invented discount),
  non-EUR currency (the tier constants are EUR/L — the one parser where the currency check is
  load-bearing), non-positive discounted unit price (mis-keyed list price named at the parser).
  FIRST network onboarded against the LIVE G3.3 regimes: the ingest suite proves the
  €0.02-allowed/€0.03-blocked coversheet tie-out over the parser's DERIVED figures (761.695 → q2
  761.70 vs 761.72/761.73) through `ingest_statement` — §5.1's "PASS = trained" against the real
  WO-66 gate, plus the assert-the-absence coherence proof (flat 21% in BE/NL emits no
  `vat_rate_incoherent` warn). Like Q8/DKV: NO entity detection ever (`entities == []`, "never
  attempted" warning, decoy-line adversarial test); R20 stays CLOSED at exactly Eurowag+E100. ONE
  existing assertion aligned, none weakened: the DKV suite's exact-four-list registry test (encoding
  "no fifth network exists") now asserts membership + relative order — the same fix WO-65 applied to
  WO-64's exact-three-list test; the exact five-list moved to the TFC suite. No migration, no
  schema/RLS impact. Explicitly NOT attempted (named owners): Moeve + BP/Aral (G3.2's remaining
  slices, priority order unchanged — Moeve next), G3.3 slice 2 (anti-drift regression check),
  cadence enforcement (G3.5), tier-grant/contract verification (G3.4/M5 contract-audit), a
  persisted review-queue, any `api/routes/transport/*` route. 74 tables, 81 revisions, 1588→1620
  collected tests (+32: 22 parser + 10 ingest). Detail: `docs/plan/plan-a/wo/WO-67-G3.2-slice5.md`.

- [x] **WO-68** — `Completed` — G3.2 slice 6: the MOEVE (ex-Cepsa) fuel-card parser
  (`app/services/transport/parsers/moeve.py`, `MoeveParser`), the SIXTH network in WO-62's registry —
  the per-line-IVA VAT-INCLUSIVE money model (`BA_fleet_fuel.md` §5.1: "ALL amounts VAT-inclusive;
  per-line IVA rate (10% gasoleo / 21% EcoBlue); cash-at-pump nets against transfer; 6-dp internal
  calc"), E100's reverse calculation varied three harvested ways. (1) The IVA rate genuinely varies
  LINE-BY-LINE within one statement — each line is reverse-calculated at ITS OWN printed rate, never
  a statement-wide constant. (2) The harvested 6-dp internal calc: `net_local = q(gross/(1+rate/100),
  6dp)` ROUND_HALF_UP with `vat_local = gross − net` EXACT, so `net+vat==gross` holds identically and
  the implied rate stays inside rule 9's ±0.5pp window; `q2` remains at the ingest boundary ONLY
  (E100's unrounded derivation untouched — the 6-dp step is Moeve's quirk, not a shared convention).
  (3) THE CASH-AT-PUMP DECISION (documented in the WO): a flagged row IS a `ParsedFuelLine` —
  invoiced, VAT-bearing fuel whose VAT is exactly as reclaimable — because "nets against transfer" is
  SETTLEMENT (M4/G3.5 territory, never capture): the per-line `payment` channel
  (`transfer`/`cash_at_pump`, fail-closed on any other value — a settlement channel is never guessed)
  rides `provenance_note` only, NEVER mutates a figure, the batch tie-out ties the INVOICE total (all
  lines, cash included), and the parse surfaces ONE advisory count+gross-sum warning. Rate policy
  provably lives in the WO-66 gate, not the parser: `iva_rate` bounded [0,100] (E100's defensive
  bound), NO {10,21} whitelist, NO product→rate mapping — rule 9's HARVESTED ES dual entry `(21, 10)`
  exists for exactly this network and is REUSED (ingest proves both ways: 10/21 ES lines emit no
  `vat_rate_incoherent`; a 15% ES line warns and still registers — warnings never block).
  Parser-LOCAL by design: `ParsedFuelLine`/`statement_ingest`/`capture_review`/`tie_out`
  byte-identical to WO-67's tree; every fixture line is EUR on the untouched identity branch;
  `net_eur_eff` = default (the ON-INVOICE "PRN off pump PVP" discount is inside the printed gross;
  verifying it is G3.4/M5). Live-gate onboarding pair over the derived figures (Σ gross = 1001.00
  exactly, cash lines included: 1001.02 registers / 1001.03 blocked, zero rows). Like Q8/DKV/TFC: NO
  entity detection ever (`entities == []`, "never attempted" warning, ES all-nines decoy adversarial
  test, allow-list entry landed in the same commit — the WO-67 lesson); R20 stays CLOSED at exactly
  Eurowag+E100. ONE existing assertion aligned, none weakened: the TFC suite's exact-five-list
  registry test (encoding "no sixth network exists") now asserts membership + relative order — the
  WO-65/WO-67 precedent; the exact six-list moved to the Moeve suite. No migration, no schema/RLS
  impact. Explicitly NOT attempted (named owners): BP/Aral (G3.2's last slice — PLN, Polish
  split-payment MPP, ORS toll-fee lines; next), G3.3 slice 2 (anti-drift regression check),
  settlement/transfer modelling (M4), cadence enforcement (G3.5), PRN-discount/product→rate
  verification (G3.4/M5), a persisted review-queue, any `api/routes/transport/*` route. 74 tables,
  81 revisions, 1620→1655 collected tests (+35: 24 parser + 11 ingest). Detail:
  `docs/plan/plan-a/wo/WO-68-G3.2-slice6.md`.

- [x] **WO-69** — `Completed` — G3.2 slice 7: the BP/ARAL (B2Mobility) fuel-card parser
  (`app/services/transport/parsers/bp.py`, `BPParser`, network code `"BP"` per §4.2's supplier
  column), the SEVENTH and LAST network in WO-62's registry — **G3.2 IS CLOSED**: all seven
  `BA_fleet_fuel.md` §5.1 networks (Eurowag, E100, Q8, DKV, TFC, Moeve, BP) now parse
  deterministically into the claim engine. The money model is deliberately the SIMPLE one
  (independently-given net/vat/gross read VERBATIM — Eurowag/Q8's shape; §5.1 gives BP no derivation
  quirk); the network's quirks live elsewhere: (1) the first ALL-PLN network — ingestion's EXISTING
  dated-ECB-rate branch (`fx.to_eur` at each line's own txn_date, `fx_source="ecb"`) is its DEFAULT
  conversion path, and the harvested `month_config.FX` static fallback is deliberately NOT
  reproduced (master-context §4.15 refuse-never-guess wins: `fx_rate_unavailable`, zero rows, the
  two-phase guarantee — proven with a clean EUR line + a zero-coverage ZAR probe line, NEITHER
  written; PLN itself carries a bundled dated snapshot in the platform's rate store). (2) THE
  ORS-FEE-LINE DECISION (documented in the WO): an ORS fee line IS a `ParsedFuelLine` — an ordinary
  VAT-bearing statement line, never a coversheet adjustment, never merged into its toll line (§5.1
  says fee LINES; §4.2 models non-fuel lines; DKV's 5.63% fee is the ON-INVOICE precedent): its 23%
  VAT is exactly as reclaimable, the tie-out ties the INVOICE total fee lines included, and the
  Art. 9 goods-code path stays honest (toll → Toll/Fees → 4, ORS → Service/Other → 10, fuel →
  Diesel → 1, asserted on stored `product_group`); the contracted ~2.5% ratio is NEVER verified at
  capture (G3.4/M5) — ONE advisory warning carries count + per-currency ORS and toll gross sums.
  (3) THE MPP DECISION (documented in the WO): Polish split-payment is SETTLEMENT (the WO-68
  cash-at-pump boundary) — the statutory `MECHANIZM PODZIELONEJ PŁATNOŚCI` literal is scanned at
  statement level; present ⇒ ONE advisory with the per-currency VAT total ("settlement-side only"),
  absent ⇒ ONE fail-OPEN mandatory-network advisory (an absent settlement annotation corrupts no
  captured figure — blocking would invent policy); no figure ever changes, no per-line flag, no
  contract widening. Fail-closed: an unknown per-line `line_type` (`fuel`/`toll`/`ors_fee`) aborts
  the whole statement (a guessed line kind mis-maps the goods code — TFC `station_class` / Moeve
  `payment` discipline). Rule 9's HARVESTED PL dual entry `(23, 8)` coheres the 23/8/23 mix with no
  parser-side rate table (no `vat_rate_incoherent` on the default fixture — the reuse proof).
  Live-gate onboarding pair over DOCUMENT-currency totals (Σ net+vat = 1452.15 PLN exactly, fee
  lines included: 1452.17 registers / 1452.18 blocked, zero rows). Parser-LOCAL by design:
  `ParsedFuelLine`/`statement_ingest`/`capture_review`/`tie_out` byte-identical to WO-68's tree.
  Like Q8/DKV/TFC/Moeve: NO entity detection ever (`entities == []`, "never attempted" warning, PL
  all-nines decoy adversarial test, allow-list entry landed in the same commit — the WO-67 lesson);
  R20 stays CLOSED at exactly Eurowag+E100. ONE existing assertion aligned, none weakened: the
  Moeve suite's exact-six-list registry test (encoding "no seventh network exists") now asserts
  membership + relative order — the WO-65/67/68 precedent; the exact seven-list moved to the BP
  suite. No migration, no schema/RLS impact. Explicitly NOT attempted (named owners): G3.3 slice 2
  (anti-drift regression check — the priority head now G3.2 is closed), split-payment settlement
  modelling (M4), ORS contract verification (G3.4/M5), a `month_config.FX`-style fallback table
  (refused per §4.15), cadence enforcement (G3.5), a persisted review-queue, any
  `api/routes/transport/*` route. 74 tables, 81 revisions, 1655→1689 collected tests (+34: 24
  parser + 10 ingest). Detail: `docs/plan/plan-a/wo/WO-69-G3.2-slice7.md`.

- [x] **WO-70** — `Completed` — G3.3 slice 2: the anti-drift extraction baseline + `regression_check`
  — **G3.3 IS CLOSED** (slice 1 = WO-66's two R25 regimes; this slice = the harvested "Anti-drift"
  paragraph: *"`extraction_baseline` records a confirmed extraction as known-good; `regression_check`
  flags a drift when a re-extraction moves net or vat by more than 0.02"*, `BA_fleet_fuel.md` §2.1a /
  Appendix B). `app/models/transport/extraction_baseline.py` (`FuelExtractionBaseline`,
  `fuel_extraction_baselines`: the known-good per-(statement-SHA-256 × currency) parsed aggregates —
  exact `line_count`, q2'd LOCAL-currency `net_total`/`vat_total`; migration `c7d2e9a41f58`, RLS in
  the same migration, tenancy-parity EXEMPT entry in the SAME commit — the WO-66 CI lesson applied);
  `app/services/transport/extraction_baseline.py` (`DRIFT_TOLERANCE=0.02`; pure `aggregate`/`compare`;
  confirm-time `record`; the explicit re-extraction caller `regression_check` — read-only,
  module-gated, fail-OPEN on a never-baselined digest; and the audited human acts `rebaseline` —
  re-parses, never re-types, the deliberate inversion of the tie-out's human-typed independence —
  and `remove_baseline`, both opaque-404 across tenants). Wired into
  `statement_ingest.ingest_statement` AFTER phase 2 + entity learning (only a CONFIRMED extraction is
  known-good — a capture-gate-blocked or FX-failed statement leaves no baseline): first sight of the
  digest RECORDS; a re-seen digest COMPARES and appends "extraction drift: ..." WARNINGS. Three
  documented design decisions off the harvested spec (recorded in the WO): (1) drift is ADVISORY —
  the harvested verb is "flags" vs the two R25 regimes' block/halt, and WO-66 records anti-drift as
  outside R25's acceptance, so §4.19 semantics apply (never blocks, nothing self-adjusts; doubly
  load-bearing because `ingest_transaction`'s insert-or-no-op makes a drifted re-parse otherwise
  INVISIBLE — the warning is the only surface of the divergence); (2) the threshold is STRICTLY
  `> 0.02` on q2'd Decimals per currency ("moves … by MORE than 0.02"; exactly-0.02 proven NOT
  flagged); (3) the baselined figures are LOCAL-currency per-currency (FX is downstream of extraction
  — an EUR basis would flag rate-cache changes as parser drift; §4.14 forbids the cross-currency sum)
  plus `line_count` as a third EXACT metric (a split/merged line can drift compensatingly with equal
  totals — T-6). Audit: `TRANSPORT_EXTRACTION_BASELINE_SET`/`_REMOVE`, one event per statement,
  old→new. Proven: 20 new tests (13 service + 7 ingest-wiring: the strict 0.02/0.03 boundary,
  per-currency isolation over a two-currency Q8 fixture, currency-set change in both directions,
  cross-tenant independence + opacity, drifted-reingest-succeeds with `fuel_transactions`
  byte-unchanged and the baseline NOT self-adjusted, drift-after-review warning ordering); Postgres 16
  gate green (up/down/up on `c7d2e9a41f58`, FORCE RLS + `tenant_isolation` policy verified on the new
  table, `test_rls.py` set-equality + the concurrency files 8 passed on a NOSUPERUSER role); zero
  existing assertions changed or weakened. Explicitly NOT attempted (named owners): a persisted
  drift/review queue or any `api/routes/transport/*` route (future transport surface), statement-byte
  vaulting so re-extraction can run without the caller's file (capture-automation epic), G3.4
  post-capture checks, G3.5 cadence. 75 tables, 82 revisions, 1689→1709 collected tests (+20).
  Detail: `docs/plan/plan-a/wo/WO-70-G3.3-slice2.md`.

- [x] **WO-71** — `Completed` — G3.4: deterministic advisory post-capture checks (R26), closing the
  board row entirely. `app/services/transport/capture_checks.py` — the harvested module by name
  (`BA_fleet_fuel.md` §3.I7): IBAN ISO 13616 + MOD-97 (severity error) composing `core/bank_id`
  with ONE documented divergence (unknown country prefix = uncheckable = NO finding; the SEPA
  path's fail-closed gate untouched); per-country VAT-ID STRUCTURAL check from the public VIES
  format table (EU-27, EL→GR; allocation ranges deliberately absent — "fail toward not crying
  wolf"); `vies_check` performing NO I/O at all (always `not_checked` — the live lookup is
  deliberately not inline, proven with socket creation monkeypatched to explode); and the
  cross-entity duplicate scan (normalized ref = uppercase+strip-spaces with separators KEPT;
  exact q2-amount + same-currency equality; prior cross-entity registration = error, in-batch
  normalization collision = warn; the current (entity, supplier, period) group excluded so
  insert-or-no-op replays never self-flag; cross-tenant identical invoices never flag — proven).
  `CheckFinding` is deliberately a DIFFERENT type from `capture_review`'s lattice so an
  error-severity ADVISORY finding structurally cannot block — the §4.19 heart, proven end to end
  (entity B ingesting entity A's registered invoice gets the `post-capture check (error)` warning
  AND its rows are written). Wired into `ingest_statement` pre-write; warnings ordering extends
  WO-70's stable contract (parser → review → post-capture → drift, asserted end-to-end). No
  migration, no new table (findings are ephemeral review output — the WO's decision 7); the
  G3.3 guarded suites pass byte-for-byte unmodified. 1699→1718 passed (+19). Detail:
  `docs/plan/plan-a/wo/WO-71-G3.4.md`.

- [x] **WO-72** — `Completed` — G3.5: receipt control (cadence × activity, orphan check, overrides
  survive), closing the board row entirely — the harvested `invoice_control.py` (`BA_fleet_fuel.md`
  §3.J; §2.3: "a missing invoice is un-recoverable VAT — cadence × activity is the only way to know
  what SHOULD have arrived"). `app/models/transport/receipt_control.py` — `VatSupplierCadence` (the
  admin cadence per free-text supplier code, the WO-61 registry precedent, three harvested values
  only) + `VatReceiptControl` (the persisted §3.J grid, natural key org×entity×supplier×period×slot
  ×country with `country=""` never NULL); `app/services/transport/receipt_control.py` —
  `DEFAULT_CADENCES` (§5.1 verbatim: E100/DKV semi-monthly, MOEVE/BP/TFC/PORTONE monthly, Q8
  monthly-per-country; Eurowag deliberately absent), `cadence_for` (admin row → harvested default →
  None = NO expectation, opt-in by absence per the R61/WO-66 discipline), `run_receipt_control`
  (expectation = cadence × ACTIVITY: monthly `M`; semi-monthly `H1`/`H2` split days 1-15/16-end, a
  documented interpretation; per-country one slot per country WITH activity; a slot with no
  transactions is `no_activity` — never chased; cross-control `received_doc`/`received_no_doc`/
  `missing` via the ONE `invoice_match.resolve_invoice_ref` + WO-58's `invoice_ids_with_documents`
  batch seam, one docs query per run), `orphan_transactions` (§3.J item 3 verbatim — transaction-
  grain, read-only, persists nothing), and `set_control_override` — the ONLY writer of `waived`/
  `note` (grep-proven, the R5 structural pattern), so the re-run upsert preserves overrides by NEVER
  writing them ("manual overrides survive re-runs", §3.J item 4, proven through two re-runs incl.
  one that recomputes the status underneath). ADVISORY THROUGHOUT (§4.19 — §3.J's verbs are
  "answers"/"persist"/"chase", never "block"/"halt"): a `missing` slot / a waived slot changes NO
  claim submission outcome (proven end-to-end); a G3.5 slot waiver is a worklist mute, structurally
  distinct from WO-58's claim-level legal waiver (R15) — the boundary is documented on both sides.
  `close.run_close` gained the `run_control` stage (§3.H H4's harvested stage list) after the
  claim-line rebuild: findings never fail the close, the R25 tie-out still halts BEFORE the stage
  (proven: a failed tie-out persists zero control rows). Migration `d8e4f2a61b37` (2 tenant tables,
  FORCE RLS in the same migration, up/down/up verified on scratch Postgres 16 with the 7 pg-only
  files green on a NOSUPERUSER role); tenancy-parity EXEMPT classified in the same commit. 77
  tables, 83 revisions. 1718→1744 passed (+26). Detail: `docs/plan/plan-a/wo/WO-72-G3.5.md`.

- [x] **WO-73** — `Completed` — G2.11: customer lifecycle + per-country activation gates (R44),
  closing the board row's shippable core — the first gate that asks WHO the claim is for
  (`BA_fleet_fuel.md` §3.F F1/F3, §3.E "Activation gates layered on top", §4's state tables; a
  claim is filed in the client's name under a power of attorney — filing for an unactivated
  customer is an unauthorised legal act, not a data bug). `app/models/transport/
  customer_lifecycle.py` — `VatCustomerLifecycle` (F1's `prospect → pending → active → inactive`,
  CHECK-constrained) + `VatCountryActivation` (`(none) → requested → active` per refund country),
  BOTH transport-local tables keyed to `issuer_profiles` via the composite RESTRICT FK (the
  WO-61/WO-72 registry precedent — never lifecycle columns on the shared AP/AR issuer model; the
  harvested `customers.db` was itself a separate store, §4.1). `app/services/transport/
  customer_lifecycle.py` — EXACTLY the harvested edges and nothing else (§10 no-invention):
  `add_prospect` (idempotent, NEVER downgrades a real client of any status — the `company_name`
  idempotency key translated to this codebase's customer identity, the entity row, documented),
  `promote_prospect` (the onboarding handoff), `set_activation` (F1's pending↔active toggle),
  `set_inactive` (§4's churn edge; `inactive` TERMINAL in this slice — no re-onboarding edge is
  harvested, recorded rather than invented), `request_country`/`set_country_activation` (the
  linear country ladder — activating a never-requested country refused); every real transition
  audited old→new (§4.16), idempotent no-ops audit nothing. `enforce_activation` — the ONE gate
  predicate (the `is_synthetic()` centralization discipline), FAIL-CLOSED on absence in both
  halves (an unonboarded entity never out-privileges an onboarded pending one) — wired into
  `lock.submit_claim` AFTER the R8 minimum and BEFORE the R6 duplicate block (D5's engine-gate
  group head per §3.E's "layered on top"); a refused claim never freezes or locks (proven), and
  the gate is UNCONDITIONAL (Fleet Fuel's `gate_activation=False` escape existed for a second
  path whose checklist superseded the coarse flags; here the checklist checks data, never the
  status flag, and R44's acceptance names the ACTIVATION message). R44's acceptance verbatim:
  a prospect's claim → 409 `customer_not_active` with the activation message; pending/inactive/
  no-row refused identically; active + country merely `requested` → `country_not_activated`
  (PoA wording); active + country active submits through the full gate stack. Preparation
  surfaces stay deliberately UNGATED (claim creation, `build_claim_lines`, the checklist,
  `run_close` — F1 scopes the rule to legal/claim GATES; the 1A stage exists to describe an
  incomplete customer's draft claim; run_close proven to rebuild a pending entity's lines).
  F3's `country_requirements` + `country_ready_to_activate` (informational, "not a gate")
  deferred to the customer-document-store slice WO-60 already named — deferred NOT silently
  skipped (the R45 partial-harvest precedent). Migration `e5b9c3d71a24` (2 tenant tables, FORCE
  RLS in the same migration, up/down/up verified on scratch Postgres 16 with the 7 pg-only files
  green on a NOSUPERUSER role); tenancy-parity EXEMPT classified in the same commit. Existing
  submit-path suites raised via the new `conftest.activate_entity` helper (the WO-60
  `make_entity` precedent) — 21 tests across 10 files gained the fixture (+ the pg concurrency
  seed), ZERO assertions weakened, and every pre-R44 gate test (module-off/not-draft/empty-set/
  `period_not_ended`/`below_minimum`) passes byte-unmodified as the gate-order proof. 79 tables,
  84 revisions. 1744→1764 passed (+20). Detail: `docs/plan/plan-a/wo/WO-73-G2.11.md`.

- [x] **WO-74** — `Completed` — G2.12: evidence pack + claim workbook, the two filing
  artifacts of a frozen VAT refund claim and the LAST non-decision-gated M3 service row
  (`BA_fleet_fuel.md` §2.2 line 204 — "the Excel claim workbook (`build_workbook`), the
  evidence pack"; §3.K K6 — the filing bundle under the human-navigable vault tree; §3.M M1 —
  every path segment Windows/SharePoint/FTP-sanitized, `period_label` `Qn`/`Annual`).
  `app/services/transport/claim_pack.py`: ONE loader (`_load_pack`) feeds BOTH renderers —
  the evidence pack embeds a workbook rendered from the SAME loaded pack, so ARCH_plan's
  G2.12 acceptance ("the workbook and the evidence pack agree line-for-line") is structural,
  and a test also proves it cell-for-cell (Lines sheet + TOTAL row) against the bundle-embedded
  copy. Workbook: `Claim` header sheet + `Lines` at the R2 grain (one row per (invoice,
  product code), Art. 9 goods code per line) + a bold TOTAL row equal to the frozen
  `vat_eur`/`vat_local`. Evidence pack: ZIP with the workbook, every vaulted invoice document
  (SHA-256-deduped) and a `manifest.csv` of per-file SHA-256s (the §3.K K4 manifest
  discipline). Refusals, all fail-CLOSED: `claim_not_frozen` (frozen lines only — the
  G2.5/ADR-P3 translation of Fleet Fuel's live-derived lines + separate legal DB),
  `synthetic_line_in_pack` via THE R3 predicate (`claim_gates.is_synthetic` — the workbook
  builder is C2's fourth named consumer and the FIRST blocking consumer wired anywhere;
  grep-test proves no rival predicate), `claim_totals_drift` (the renderer recomputes `q2`
  sums and refuses a frozen header its own lines cannot reproduce, §4.10),
  `claim_currency_mismatch` (defensive §4.14 twin of `freeze._sum_lines`),
  `evidence_document_unavailable` (dangling object-store reference — never an incomplete
  filing bundle). Free text through the ONE shared `core.csv_safety.sanitize_cell`
  (supplier names, invoice numbers, manifest filenames); money cells raw Decimal (a leading
  `-` is a negative amount). Read-only end to end: persists nothing, mutates nothing, audits
  nothing (grep-test), NO new table/migration (the WO-71 decision-7 precedent). Service-only;
  the `api/routes/transport/*` batch serves + permission-gates the bytes later. Follow-up
  recorded (WO-74 design decision 8): `lock.submit_claim` still lacks the R3 synthetic lock
  gate — a synthetic line can freeze but can never produce filing artifacts. 21 new tests
  (refusals incl. cross-tenant 404 + module gate, frozen-figure correctness, deterministic
  order, injection safety, vault-tree shape + segment sanitization, manifest hashes, dedup,
  withdrawn-claim reproducibility, the line-for-line identity proof). 79 tables, 84 revisions
  (unchanged). 1764→1785 passed (+21). Detail: `docs/plan/plan-a/wo/WO-74-G2.12.md`.

- [x] **WO-75** — `Completed` — the R3 synthetic-line refusal as a LOCK-GATE consumer in
  `lock.submit_claim` — the small, precisely-scoped gap WO-74's design decision 8 recorded
  (`BA_fleet_fuel.md` C2 — "A pack containing ANY synthetic line CANNOT be filed. The same
  predicate is used by: the lock gate (`bad = [... if _synthetic(r)]` → 'BLOCKED - unresolved
  invoice refs') ..."; R3 row line 1360). `claim_gates.py` gains the DB-backed pair mirroring
  `document_gate`'s shape: `unfrozen_synthetic_refs` (non-raising, sorted distinct offenders
  among the claim's materialized UNFROZEN lines — exactly the rows the G2.5 freeze would stamp,
  the same validates-what-gets-frozen reasoning as R10) + `enforce_no_synthetic_lines` (409
  `unresolved_invoice_refs` naming every offender, fail-CLOSED). Wired into `submit_claim`
  between R44 (activation) and R6 (duplicate machinery) — the harvested position: D5 names
  "synthetic/duplicate/document" in that order and C9's internal `set_status` sequence puts the
  `bad` gate before `claim_set`/locks/doc-gate/freeze. A refused submit mutates NOTHING (status
  `draft`, no `status_code`, no frozen lines, no `vat_eur`, zero lock rows — the D5 proof
  pattern, asserted). R15 interaction proven: a waived supplier's synthetic `INPUT` transactions
  never became lines (WO-58 "excluded by construction"), so waiving stays the legitimate path
  past this gate; a claim with NO materialized lines passes trivially (R10 semantics). 10 new
  tests (`test_r3_lock_gate.py`: refusal + nothing-mutated, INPUT-vat_id variant, position vs
  R6 and vs R10, waiver interaction, trivial pass, sorted/distinct/org-scoped scan,
  all-offenders-named, structural no-rival-predicate + gate-order). Fixture raises only, zero
  assertions weakened: new shared `register_documented_invoice` conftest helper; 9 pre-existing
  successful-submit tests raised to resolved+documented lines (g1_4 ×1, g2_11 ×2, g2_5 ×2,
  g2_6 submission-gates ×2, g2_6 annual-mop-up ×5 call sites across 5 tests); the g2_12
  frozen-synthetic test now seeds by direct row tamper (defence-in-depth: the pack still blocks
  a corrupted frozen set). No route, no table, no migration. 79 tables, 84 revisions
  (unchanged). 1785→1795 passed (+10). Detail: `docs/plan/plan-a/wo/WO-75-R3-lock-gate.md`.

- [x] **WO-76** — `Completed` — transport routes slice 1: the claim lifecycle over HTTP — the
  head of the M3 route batch (ADR-P3's `api/routes/transport/` file list; this slice ships
  `claims.py` only). NEW package `app/api/routes/transport/` (an aggregating `__init__.router`
  so `tests/test_authz_coverage.py`'s `pkgutil` enumeration keeps the package inside the
  structural-coverage net) with nine thin controllers: `GET/POST /transport/claims` (list +
  R1 idempotent get-or-create — deliberately 200 both calls, the service cannot lie a 201),
  `GET /{id}`, `GET/POST /{id}/lines` (R2 materialization + read), `GET /{id}/checklist` +
  `GET /{id}/stage` (advisory reads — NO commit, so even the evaluator's idempotent
  default-rule seeding is discarded with the request session; reading changes NOTHING, §4.19),
  `POST /{id}/submit` (the D5 chain R7→R8→R44→R3→R6→R15-stamp→R10→freeze→lock→"2") and
  `POST /{id}/withdraw` (R5). Structural authz on the EXISTING WO-49 permissions (no invented
  vocabulary): router-level VAT_READ, per-route VAT_WRITE (create/build-lines), VAT_SUBMIT
  (submit/withdraw — the ACCOUNTANT-books-but-cannot-submit split proven live on the wire).
  Every refusal is the SERVICE's own `AppError` code rendered by the one `app.main` handler
  (`module_not_enabled`, `claim_not_found`, `period_not_ended`, `below_minimum`,
  `customer_not_active`, `unresolved_invoice_refs`, `duplicate_invoice_lock`,
  `invoice_document_missing`, `claim_not_locked`, …) — routes map nothing, §4.20 additive-only;
  a 409 submit provably mutates nothing over HTTP (draft status, zero locks, zero frozen
  lines). `lock.submit_claim`'s `today` test seam deliberately NOT exposed (a client-supplied
  clock could bypass R7). Additive service read accessors (`claim.list_claims`/`get_claim`,
  `claim_lines.list_claim_lines`) keep the module gate + opaque-404 in the service layer —
  zero business logic in controllers. Money rides the wire as pydantic-serialized Decimal
  strings (§4.9, asserted: `"2000.00"`, never a float). Tenancy parity: `vat_refund_claims` +
  `vat_claim_lines` EXEMPT→real HTTP probes (overlapping bodies, opaque 404 both ways);
  remaining transport exemption reasons trued up. 20 new route tests + 2 new parity params;
  0 assertions weakened, 0 pre-existing tests touched. No table, no migration, no new
  permission, no SPA change. 79 tables, 84 revisions (unchanged). 1795→1817 passed (+22).
  Detail: `docs/plan/plan-a/wo/WO-76-transport-routes-1.md`.

- [x] **WO-77** — `Completed` — transport routes slice 2: the admin/config surfaces + the
  filing artifacts over HTTP. 27 more thin controllers on the EXISTING WO-49 permissions (no
  invented vocabulary), all aggregated through the same package `__init__.router`:
  **`claims.py` (+6, claim-scoped)** — waiver `GET/POST/DELETE` (R15; VAT_WRITE, because a
  waiver configures how lines are BUILT and flips no status), `POST /{id}/status-code`
  (R17/R12; VAT_SUBMIT — it is the claim-STATUS surface) and the two G2.12 FILING ARTIFACTS
  `GET /{id}/workbook` (xlsx) + `GET /{id}/evidence` (ZIP), closing `claim_pack.py`'s own
  recorded "no route exists yet"; **`admin.py` (new, org-level)** — the status-code
  VOCABULARY read, checklist-rule list/seed/`set_active` (R45), cadences `GET/PUT/DELETE` +
  the persisted receipt-control grid and its override (G3.5), note→invoice-ref overrides
  `GET/PUT` (R16), tie-out expectations `GET/PUT/DELETE` (R25 regime 2); **`customers.py`
  (new)** — the R44 lifecycle + per-country activation ladder, closing the gap
  `customer_lifecycle.py`'s docstring recorded ("no `api/routes/transport/*` route exists yet,
  so the admin-click requirement lands as the documented intent for that route's permission").
  Five additive service read accessors (`waiver.list_waivers`, `tie_out.list_expectations`,
  `invoice_match.list_note_overrides`, `customer_lifecycle.lifecycle_overview`,
  `status.list_status_codes`) each carry the module gate + opaque-404 fetch, and
  `checklist.list_rules` gains the gate now that it is route-facing (behaviour-preserving —
  its internal callers already gate first). Every refusal on the wire is the SERVICE's own
  code (§4.20 — routes map nothing): `waiver_supplier_has_invoices`, `claim_not_draft`,
  `checklist_rule_not_found`, `status_code_system_controlled`, `unknown_status_code`,
  `claim_not_submitted`, `invalid_cadence`, `invalid_period`/`invalid_currency`/
  `invalid_expected_lines`/`invalid_gross_tolerance`, `tieout_expectation_not_found`,
  `receipt_control_not_found`, `override_note_is_synthetic`/`override_target_not_registered`,
  `not_a_prospect`/`lifecycle_transition_invalid`/`invalid_country`/`country_not_requested`/
  `country_transition_invalid`, `claim_not_frozen`/`synthetic_line_in_pack`/
  `claim_totals_drift`/`evidence_document_unavailable`. THREE recorded judgment calls: the
  artifact downloads are VAT_READ (a read-only rendering of data `/lines` already serves —
  `EXPORT_RUN` guards the different accounting-ledger hub); `seed_default_rules` needed its
  own COMMITTING route precisely because the claim checklist GET never commits; and the
  status-code surface is the one VAT_SUBMIT verb here. TWO scopes deliberately NOT built and
  recorded rather than invented (§9/§10): no note-override DELETE (no removal FUNCTION was
  harvested — R16's lifecycle is CASCADE-on-de-registration) and no `STATUS_LABELS` read
  (Fleet Fuel's label map was never harvested as data — the route returns the ACTUAL
  `AUTO_CODES`/`MANUAL_CODES` vocabulary); `run_receipt_control`/`orphan_transactions` stay
  unrouted (R60 — the close never runs inline in a request). Artifact tests parse the REAL
  bytes: openpyxl asserts the workbook TOTAL row equals the frozen `vat_eur`, zipfile
  re-hashes every bundle entry against its `manifest.csv` SHA-256. Tenancy parity: EIGHT
  EXEMPT rows converted to real HTTP probes (`vat_receipt_waivers`, `vat_checklist_rules`,
  `vat_supplier_cadences`, `vat_receipt_controls`, `vat_note_invoice_overrides`,
  `fuel_tieout_expectations`, `vat_customer_lifecycles`, `vat_country_activations`); the four
  remaining transport exemption reasons trued up. 26 new route tests + 8 new parity params;
  0 assertions weakened, 0 pre-existing tests touched. No table, no migration, no new
  permission, no SPA change. 79 tables, 84 revisions (unchanged). 1817→1853 passed (+36).
  Detail: `docs/plan/plan-a/wo/WO-77-transport-routes-2.md`.

- [x] **WO-78** — `Completed` — transport UI slice 1: the VAT claims workspace. The M3 **UI
  batch opener** — the WO-76 claim lifecycle and WO-77 filing artifacts, until now reachable
  only with `curl`, become two SPA pages composed from the EXISTING ui primitives, TanStack
  Query and Tailwind (no new library, state manager, styling system or test framework).
  **`pages/VatClaims.tsx`** — the claims list (entity · refunding country · reference period ·
  status + workflow code · frozen VAT total) with real loading / empty / error states via
  `QueryState`, and a VAT_WRITE-gated create form on the R1 grain whose button reads "Open or
  create" because `get_or_create_claim` is idempotent. **`pages/VatClaimDetail.tsx`** — the
  filing workspace: the grain + status header, the R2-grain lines table (unresolved refs
  badged), the ADVISORY checklist panel (a failing item NEVER disables Submit — §4.19), the
  stage/status-code ladder built from `GET /transport/status-codes` (the service's OWN
  vocabulary — no label map is invented, WO-77 decision 5 respected), the totals exactly as
  the service froze them, the lifecycle actions and the two artifact downloads.
  **THE SUBMIT UX IS THE POINT:** every D5 refusal code is mapped in a pure, testable module
  (`lib/transportClaims.ts::claimRefusal`, 23 codes read off the services) to a sentence that
  says what is wrong AND what to do next, with the server's own `detail` (which names the
  suppliers / refs / period) rendered underneath as the specifics — the raw slug reaches the
  screen NOWHERE, asserted per code. `below_minimum` additionally surfaces the
  `override_minimum` path the `ClaimSubmitIn` schema already carries, VAT_SUBMIT-gated like
  the submit itself and stated to be recorded on the claim. **Money never round-trips through
  a float:** `format.decimalMoney` formats the wire Decimal STRING by string surgery (no
  `Number()`, no arithmetic, no rounding); a null total renders as an em dash, never `0.00`
  ("not frozen yet" ≠ zero). **The UI computes no total** (§4.10) — proven by a fixture whose
  lines deliberately do not sum to the frozen header. Permission gating mirrors
  `authz.ROLE_PERMISSIONS` cosmetically (`roles.hasVatPerm`) so no dead button is rendered —
  explicitly NOT a boundary, the server still refuses. Nav: a "Transport" group gated on the
  `transport` module AND a new optional `perm` flag on `LiveNavItem` (its absence leaves every
  existing item's filtering byte-identical). TWO limitations recorded rather than invented
  around (§9/§10): the claim-line grain carries NO supplier column, and no route enumerates
  fuel transactions (`fuel.py` does not exist), so the submit dialog's
  `(supplier, invoice_ref, fuel_transaction_id)` set is operator-supplied, pre-seeded from the
  non-synthetic line refs. 29 new Playwright specs in the repo's existing `page.route`-mocked
  live-app harness, added to the `test:e2e` script CI runs: 31→60 frontend e2e passing.
  ZERO backend change — no route, no schema, no permission, no migration; the backend suite is
  unchanged at 1853 passed / 10 skipped. 45→47 SPA pages.
  Detail: `docs/plan/plan-a/wo/WO-78-transport-ui-1.md`.

- [x] **WO-79** — `Completed` — the fuel-transaction READ surface + the submit pick-list it
  unlocks. Closes both limitations WO-78 recorded, and one the tenancy suite had carried since
  WO-50. **The gap:** `fuel_transactions` — the typed line item every claim is built from —
  had existed since WO-50 with `fuel_ingest.ingest_transaction` as its only writer and NO code
  path that returned one (`grep -rn "FuelTransaction" app/api` was empty). Consequences:
  `test_tenancy_parity.py` carried it as an EXEMPT row whose own reason read "Still no route
  RETURNS these rows"; and the WO-78 submit dialog had to ask an operator to TYPE a supplier
  and a fuel-transaction UUID, because the `(supplier, invoice_ref, fuel_transaction_id)`
  tuples `lock.submit_claim` keys its locks on had no enumerable source.
  **`services/transport/fuel.py::list_fuel_transactions`** — the additive read accessor in the
  transport-standard entry-point order, failing CLOSED: module entitlement → org-scoped
  `issuer.get_by_id` (opaque 404 `entity_not_found`, §4.4) BEFORE any transaction row is
  touched → period resolution. Required `entity_id`+`period`, optional `supplier`/`country`
  (country matters: `build_claim_lines` scopes a claim's transactions by country of supply, so
  a pick-list ignoring it would offer rows the claim provably excludes). `period` takes the
  model's own `YYYY-MM` **or** a claim reference period, expanded through the EXISTING shared
  `claim_lines.period_months` — guarded by `claim.validate_ref_period` first, because that
  helper is deliberately trusting and would otherwise read `"2026-13"` as Q3. Pagination
  reuses the `InvoiceListOut` envelope with the large-list bounds of expenses/email (100/500);
  `total` and the page come from the SAME filter set. **`schemas/transport_fuel.py`** — every
  amount and `qty` typed `Decimal`, so they cross the wire as exact strings (§4.9), with the
  FX provenance quadruple carried verbatim (§4.15 is worthless without it).
  **`api/routes/transport/fuel.py`** — one thin controller, router-level VAT_READ (ADR-0024
  structural). The permission is a RECORDED judgment: `TRANSPORT_READ` exists and stays
  reserved for the derived analytics/excise slices, while these rows ARE the claim's evidence
  base — the same set `/claims/{id}/lines` already serves aggregated under VAT_READ; both
  permissions have byte-identical role coverage, pinned by a test so a future matrix split has
  to revisit this gate deliberately. No permission member added. Read-only by construction: no
  POST/PATCH/DELETE, so no audit event and no SoD question. **Tenancy:** `fuel_transactions`
  EXEMPT → a real HTTP probe over identical overlapping data in both orgs; the three remaining
  transport exemptions' reason text trued up to state what WO-79 did NOT route.
  **The pick-list (WO-78 deviation 2, closed):** the submit dialog reads
  `GET /transport/fuel-transactions` at the CLAIM'S OWN grain and each ticked row contributes
  its own tuple — supplier and UUID never typed again; the invoice reference stays editable
  because that column is nullable by design while `SubmitInvoiceIn` requires a non-empty one.
  The typed path survives as "Add a row manually" and is the fallback (pre-seeded exactly as
  WO-78 did) when the period is empty or the fuel read fails — derived at render, not pushed
  by an effect. Money/litres still render with no `Number()`/`parseFloat`; the pick-list adds
  no gate and never disables File claim (§4.19); the page still computes no total (§4.10).
  **WO-78 deviation 1 (a supplier column on the claim-line table) is deliberately NOT built**
  and the page docstring + the WO doc record why: a claim line carries the RESOLVED AP invoice
  number while a transaction carries the RAW statement note (`invoice_match` exists precisely
  because those differ), `FuelTransaction.invoice_id` is never populated by any service, and
  an `UNMATCHED` line aggregates every unresolved supplier — there is no unambiguous link on
  the wire, and a guessed supplier on a filing surface is worse than an absent one. The honest
  fix is a backend one (carry the supplier through `build_claim_lines`), proposed as the next
  slice. 11 new route tests + 1 new tenancy-parity probe param; 10 new Playwright specs
  (60→70 frontend e2e passing). No migration, no model change, no new SPA page.
  Detail: `docs/plan/plan-a/wo/WO-79-fuel-read-surface.md`.

- [x] **WO-80** — `Completed` — transport UI slice 2: the admin/config workspace. Closes the
  gap WO-78's own anti-scope clause named: `api/routes/transport/admin.py` (14 routes) and
  `customers.py` (7 routes) were live, gated and tested, and `grep -rn "checklist-rules|
  cadences|receipt-controls|note-overrides|tie-out-expectations|transport/customers"
  frontend/src` returned NOTHING — WO-78 consumed exactly one of them (`GET
  /transport/status-codes`). Two of the claim workspace's own refusal sentences
  (`customer_not_active`, `country_not_activated`) told an operator to do something that had
  no screen. **`pages/VatAdmin.tsx`** — one tabbed page (`/vat-admin`, the existing
  `Tabs`/`TabPanel` primitives, no new navigation pattern) with six panels, each reading only
  its own route when its tab is open: checklist rules (list · the COMMITTING seed · per-key
  active toggle), the persisted receipt-control slot grid + its mute/annotate override,
  cadences (assign from the harvested closed set · drop back to the default), note→invoice-ref
  mappings (list · set), tie-out expectations (list · upsert · delete by natural key) and the
  status-code vocabulary as a REFERENCE panel that links to the claim rather than duplicating
  the VAT_SUBMIT action. **`pages/VatCustomers.tsx`** — `/vat-customers`: the R44 lifecycle
  ladder and every per-country activation row, the state named in words (a `null` status reads
  "Never onboarded" — the real state the gate refuses, never blank and never an error), and
  only the transitions legal from that state offered. **`pages/VatClaimDetail.tsx`** gains the
  claim-scoped R15 waiver panel — the one WO-77 surface that is claim-scoped, so it belongs on
  the claim, not on the configuration page. **The advisory/gate distinction is carried in the
  COPY and asserted as text** (§4.19): the receipt-control board states it "blocks no claim,
  halts no close and changes no figure" and names its mute as a worklist mute rather than
  R15's legal waiver, while the tie-out panel states the opposite — "These stop the monthly
  close" — because that one really does (R25 regime 2). **Nothing the backend lacks was
  invented** (§10): no note-override DELETE (no service function — WO-77 decision 6, the
  absence is asserted by a test), no receipt-control RUN trigger (R60), no orphan view, no
  status labels, no entity name on a transport surface (the issuer registry is a convenience
  on a DIFFERENT permission that degrades to a typed id). Fifteen WO-77 refusal codes joined
  the ONE refusal map, each read off the service that raises it, so `RefusalNotice` renders
  the new screens unchanged; every mutating control is hidden without the mirrored VAT_WRITE
  and the server's 403 still renders. No figure is parsed, summed or re-formatted: a 16-digit
  tie-out gross renders and re-posts with its exact digits, untyped optional figures post as
  `null` (not zero), and the single integer field is a line COUNT. 47 new Playwright specs
  (70→117 frontend e2e passing) across the new `e2e/vat-admin.spec.ts` and the claims spec;
  two pre-existing pick-list selectors SCOPED to the submit dialog (a strengthening — no
  assertion weakened). ZERO backend change: 1865 passed / 10 skipped, unchanged.
  Detail: `docs/plan/plan-a/wo/WO-80-transport-ui-2.md`.

---

## M5 — In Progress

- [x] **WO-81** — `Completed` — G4.3/R38: the cash-recovery analytics service + its read
  route. The first transport surface that answers a question about the PORTFOLIO rather than
  about one claim — *"how much money can we still recover this refund year, and what is
  stopping each euro of it?"* `app/services/transport/recovery.py::recovery_dashboard` buckets
  every claim of a year into the six harvested readiness states (`ready · deadline · missing ·
  below · submitted · paid`, `BA_fleet_fuel.md` §2.4) and totals the north-star euros:
  recovered, awaiting, claimable, the deadline-risk count and the median days-to-refund.
  **R38's binding constraint is honoured literally — nothing computes a figure of its own:**
  the claim set comes from `claim.list_claims` (which gains an ADDITIVE `year` keyword, so the
  ONE listing query stays one), a draft's stage from `status.derive_stage`, its VAT base from
  `freeze.preview_vat_base` (the same preview `submit_claim` gates R8 on), the Art. 17
  threshold from `minimum.below_minimum` and the time-bar from `deadline.deadline_status` —
  the pure functions `deadline.py`'s own docstring said "a future dashboard (G4.3)" would
  consume. FOUR interpretations the spec does not settle are documented in the module and the
  rule row: stage `1B` folds into `ready` (no seventh bucket is invented); a waiver-only `1C`
  is `ready`, not `below` (only a threshold caveat is a threshold problem); `deadline` means
  "otherwise fileable with the clock running", so a blocked claim keeps its BLOCKING bucket and
  is still counted in the SEPARATE `deadline_risk_claims`; and `withdrawn`/`rejected` are
  reported in an `excluded` block by reason, with `Σ buckets + Σ excluded == total_claims`
  asserted. **§4.14 over multi-country claims:** every euro is the single-currency `vat_eur` —
  `vat_local` and the currency-ambiguous `paid_amount` are never read (an AST assertion pins
  that absence) — and a draft whose lines span currencies is caught PER CLAIM: it buckets
  `missing`, contributes `0.00`, and increments `currency_mismatch_claims`, so one bad claim
  never blanks a year and no foreign amount is ever labelled EUR. §4.9 throughout, including an
  exact-`Decimal` median so no float can arrive via `statistics`. `GET /api/v1/transport/
  recovery-dashboard?year=` is one thin controller on `TRANSPORT_READ` — the permission WO-79
  explicitly RESERVED for this derived-analytics slice — with Decimal-as-string on the wire,
  200-with-zeroes on an empty year and 422 `invalid_year` otherwise. Read-only: no write, no
  audit row, no commit, no migration, no new permission member, and no tenancy-parity exemption
  changes (the response is aggregates only — no row, no object id). TWO deviations recorded
  rather than faked: the `overcharges` euro needs G4.5's absent `contract_audit`/`overcharge`
  service, so the field is OMITTED (a zero would read as "we found no supplier breaches"); and
  nothing writes `submitted_date`/`paid_date` yet (G2.9, decision-gated), so the median ships
  with a `days_to_refund_sample` beside it and `null` reads as "no claim has both dates". Also
  records `docs/DECISIONS-NEEDED.md` §11 (claim-line supplier attribution) — stated, not
  decided. Detail: `docs/plan/plan-a/wo/WO-81-recovery-analytics.md`.

- [x] **WO-82** — `Completed` — G4.5/R41: supplier overcharge detection + the claim-back
  lifecycle, and the north star it feeds. The order that CLOSES WO-81's deviation 1 — the
  dashboard's `overcharges` euro, which that order deliberately OMITTED rather than emit as a
  misleading zero ("we found no supplier breaches" is a different and false statement).
  **(1) DETECTION** (`app/services/transport/contract_audit.py`, READ-ONLY over
  `fuel_transactions`): the harvested contract audit, `BA_fleet_fuel.md` §2.5 verbatim — the
  *"two term types only"* (`expected_discount_eur_l`, a rebate in €/L; `max_net_eur_l`, a
  contracted NET price ceiling in €/L) stored on §4.4's own `supplier_discounts` grain
  (`supplier × country × station_like × product_group`, `active`); the two flag strings verbatim
  (`"short discount"` when `applied < expected − tol`, `"over ceiling"` when `eff_l > max + tol`)
  at the harvested **`TOLERANCE = 0.005 €/L`**; `recover_eur = gap × litres`, *"dropped if ≤ 0"*.
  §2.5's closing sentence is honoured as a CONSTRAINT — **no volume-tier / stepped-rebate /
  annual-bonus / card-fee modelling** — so a term the model cannot express is stated, never
  approximated. BASIS **NET EUR/L, final — VAT excluded, rebates applied** (§3.G G1 / R49,
  verified against the spec rather than assumed): the discount ACTUALLY applied is
  `net_eur/qty − net_eur_eff/qty`, which is §4.2's own two-tier discount model read as an
  identity; the basis string AND R53's *"Money the supplier owes"* framing ride the result so no
  surface can render the euro without them. §4.9: comparisons on UNROUNDED `Decimal` quotients so
  a boundary can never flip on a rounding — the EXACT-boundary case (applied == expected,
  `eur_l_eff` == max) yields NOTHING, proven on both sides one 0.0001 step apart — with the gap
  quantized once to 4 dp and the euro once through `money.q2`. §4.14: EUR-only with no coercion
  anywhere (both price columns are EUR by construction, so a supplier month spanning EUR and PLN
  totals natively in EUR); the three document-currency amount columns are NEVER read (asserted
  structurally by name) and a line's own currency rides each finding as provenance that is never
  totalled. Three fail-safe readings, documented: exactly ONE term applies per line (most
  specific wins — two overlapping terms would DOUBLE-COUNT real money); **no configured term ⇒ no
  finding at all**, counted as `lines_without_terms` (never a false positive); a `qty <= 0` promo
  line is skipped, never divided by. Detection writes nothing — proven structurally and by a
  column-for-column before/after comparison of every fuel transaction. **(2) THE CLAIM-BACK**
  (`app/services/transport/overcharge.py`): §4.5's chain drawn LITERALLY — `detected → packaged →
  claimed → recovered | rejected | written_off`, the three outcomes terminal, no shortcut edge
  invented (the WO-73 `inactive`-is-terminal precedent). An illegal edge is 409
  `overcharge_transition_invalid` with NOTHING mutated (asserted column-for-column); every real
  move is audited old→new in the same transaction (§4.16). The euro is FROZEN at `open_claim`
  (the G2.5 reasoning applied to a demand letter), `open_claim` is idempotent on
  `(org, supplier, period)` and refuses 422 `no_overcharge_detected` when nothing was found, and
  `recovered_eur` is bounded to `(0, detected_eur]` (§4 invariant 13 — a north-star total
  unbounded by its own evidence is not a measurement). **(3) THE NORTH STAR**:
  `recovery.recovery_dashboard` now carries `overcharges_eur`, obtained by CALLING
  `overcharge.recovered_total(year=…)` — R38's never-a-forked-query clause honoured; it is §2.4's
  BOOKED-CASH reading (*"`recovered_total()` = the booked-cash north star"*), not the €-exposure
  detected, and it sits deliberately OUTSIDE the `recovered + awaiting + claimable` VAT
  reconciliation, which is unchanged. **(4) THE ROUTE**:
  `app/api/routes/transport/overcharges.py` — the term admin, the audit read, the worklist, the
  total and the two lifecycle verbs on router-level `TRANSPORT_READ` (the reservation `fuel.py`
  recorded names this module by name) with writes overriding to `VAT_WRITE` (the `admin.py`
  `_WRITE` pattern; `VAT_SUBMIT` stays reserved for lock-acquiring VAT-claim actions). No
  permission member invented, and the split is load-bearing: an AUDITOR reads the worklist and
  cannot drive it. Migration `a7c2e9f14b58` (2 tenant tables, FORCE RLS in the same migration,
  up/down/up verified on a scratch Postgres 16 with the pg-only files green on a NOSUPERUSER
  role); both tables classified as REAL tenancy-parity HTTP probes in the same commit that
  created them — never EXEMPT. 81 tables, 85 revisions. **DEFERRED here, SHIPPED in WO-83:** R41's two
  send-ready ARTIFACTS (the Excel evidence packet and the formal PDF claim letter with a 30-day
  credit/refund demand, *"built from the SAME line source"*) were a named follow-up slice of
  G4.5 — `contract_audit.audit()` already IS that single line source, which is exactly what made
  the *"identical lines and totals"* acceptance structural rather than coincidental (the G2.12
  `claim_pack` precedent). Detail: `docs/plan/plan-a/wo/WO-82-overcharge-claimback.md`.

- [x] **WO-83** — `Completed` — G4.5/R41 + R53: R41's two send-ready ARTIFACTS, off the ONE line
  source. The follow-up slice WO-82 named in its own out-of-scope section and this board's M5
  cell recorded as the recommended next one — **R41 is now CLOSED**.
  `app/services/transport/overcharge_pack.py` renders the Excel EVIDENCE PACKET
  (`build_evidence_packet`) and the formal PDF CLAIM LETTER (`build_claim_letter`) from
  `contract_audit.audit()` — the single line source R41 names, and the same call
  `overcharge.open_claim` snapshots. **R41's acceptance is proven twice.** STRUCTURALLY, three
  times over: one `_load_packet()` (loaded once, rendered twice), one `_COLUMNS` spec both
  renderers project through the same accessors over the same `Breach` objects, and two SYNC
  renderers that take no `AsyncSession` and therefore cannot reach a second source even by
  accident. Then CELL-FOR-CELL: the generated bytes are really parsed (openpyxl over the
  `Evidence` sheet, pypdf over the letter text) and both compared against the SAME hand-computed
  Decimals — the G2.12/`claim_pack` precedent applied to the surface `claim_pack`'s own docstring
  said it was standing in for. **The packet**: the breach lines with flag, agreed/actual €/L,
  gap, litres and recoverable EUR, a bold TOTAL row, and workbook-only LINEAGE columns (claimant
  entity, invoiced/effective €/L, document currency, transaction id) that a one-page A4 letter has
  no room for. **The letter**: our letterhead from the existing `IssuerProfile` registry (resolved
  READ-ONLY — `list_issuers`/`get_by_id`, never `get_or_create`, which COMMITS a row and would make
  a read-only artifact a writer), the supplier's own per-country legal entity (R20/R21, marker-only
  via `supplier_entity.get_registration`; an unregistered country prints the supplier code and
  invents NOTHING), the SAME table, and §2.4's demand verbatim — a **credit note or refund within
  30 days** (`DEMAND_DAYS = 30`, harvested twice at §2.4 lines 221 and 236), with the due date
  printed as letter date + 30. **R53 carried, not paraphrased**: both artifacts PRINT
  `contract_audit.LEGAL_FRAMING` and `PRICE_BASIS` — the same constants the JSON API returns — so
  a framing cannot drift from surface to surface; R53 gets its first real consumer and its own
  ledger row. **FOUR fail-CLOSED refusals, each because the alternative is a MISLEADING document**:
  `no_overcharge_detected` (422 — an empty packet reads as *"we found nothing owed"*);
  `overcharge_evidence_drift` (409 — the live line source no longer reproduces the FROZEN
  `detected_eur`, so the demand and its own enclosure would disagree: the
  `claim_pack.claim_totals_drift` twin, §4.10, with the missing re-snapshot edge RECORDED in
  `docs/DECISIONS-NEEDED.md` §13 rather than invented); `overcharge_claim_closed` (409, LETTER
  ONLY — a live 30-day demand must not assert a debt the ledger records as
  `recovered`/`rejected`/`written_off`, while the PACKET stays reproducible in every state per
  WO-74's *"reproducing what WAS filed is audit-trail behaviour"*); and
  `issuer_profile_incomplete` (409, letter only, naming the missing Art. 226 fields). **The
  routes**: `GET /transport/overcharges/{id}/packet` and `.../letter` on the SAME router-level
  `TRANSPORT_READ` — generation is a READ (nothing persisted, mutated, audited or committed),
  which is why an AUDITOR downloads both and still cannot advance the worklist; correct media
  types, `content_disposition` filename and `nosniff`, the WO-77 artifact-download precedent.
  **NO new table, NO migration, NO new permission, NO new dependency** (openpyxl and reportlab
  are already pinned; degradation mirrors `invoice_pdf.PdfUnavailable` as a 503
  `pdf_renderer_unavailable` `AppError` so the route stays a thin controller). Building both
  artifacts leaves the claim-back and every fuel-transaction column byte-identical and writes no
  audit row — asserted, not promised. Detail:
  `docs/plan/plan-a/wo/WO-83-overcharge-artifacts.md`.

- [x] **WO-84** — `Completed` — G4.2/R50: the OFF-INVOICE rebate merge into `net_eur_eff` and its
  SOURCE GUARD. **The recon this order opened with is the finding:** `net_eur_eff` had exactly ONE
  writer (`fuel_ingest.ingest_transaction`'s `= net_eur` default) and no production caller ever
  passed a value — `statement_ingest` omits the argument and all seven G3.2 parsers say so in as
  many words — so it was an exact COPY of `net_eur` on every stored row, while
  `contract_audit._breach_for` already read it as though a merge had happened. Its
  `applied = eur_l_doc − eur_l_eff` term was therefore identically `0.0000`, and every line
  governed by an `expected_discount_eur_l` term flagged `short discount` for the FULL contracted
  rebate — a euro `overcharge.open_claim` FREEZES and WO-83's claim letter PRINTS in a 30-day
  payment demand. The platform was demanding money that a supplier paying off-invoice had already
  paid, which is exactly why this order had to follow WO-82/WO-83 rather than precede them: the
  wrong number had become client-reachable. **The merge** (`app/services/transport/rebate.py` +
  `vat_off_invoice_rebates`, grain `(org, supplier, country, period, source_ref)`) implements §4.2's
  two-tier discount model verbatim — an on-invoice discount is *"already inside `net_eur`"*, an
  off-invoice rebate *"lands ONLY in `net_eur_eff`"* — with the ONE thing the spec does not supply,
  the allocation formula, stated as a DOCUMENTED INTERPRETATION rather than guessed (the
  `contract_audit._specificity` precedent): **pro-rata by litres**, because §5.1's rebate vocabulary
  is uniformly €/L, because it makes `applied` a CONSTANT €/L (the shape a €/L term is compared
  against), and because a value-weighted alternative would manufacture findings on the expensive
  lines. Rounding is cumulative-then-quantize so the shares sum EXACTLY to the recorded euro — the
  10.00-over-three-lines case (3.33/3.34/3.33) is asserted, where independent quantizing would have
  silently lost a cent onto a price surface. **Idempotent by construction**: the effective net is
  always recomputed from the AS-INVOICED `net_eur`, never from the column's current value, so a
  re-close writes nothing and audits nothing (asserted as an ABSENCE — no second audit row) and a
  corrected rebate document self-heals instead of compounding a delta. **The ENGINE owns the write
  (§3.H)**: `record_rebate` is web-reachable and touches no transaction (also asserted as an
  absence, over HTTP); the merge is the FIRST `close.run_close` stage, every changed figure audited
  old→new (§4.16), a refusal riding `jobs.run_once`'s whole-session rollback. **THE SOURCE GUARD,
  BOTH HALVES OF §4.2's HAZARD** (*"swapping in the raw file silently loses the rebate layer"*):
  (1) a rebate is never INFERRED — `merge_period` reads recorded rows and has no derivation path,
  and `record_rebate` refuses a blank or WHITESPACE `source_ref`/`source_party` fail-CLOSED before
  any query (`rebate_source_required`), repeated as database `CHECK`s so no raw path bypasses it;
  (2) a rebate layer never silently DISAPPEARS — a question about a period with NO row, answered by
  `missing_source_warnings`, which learns the expectation from the registry's own HISTORY exactly as
  §2.5's "Expected rebate" prescribes (that row's rationale names this very case) and rides
  `ContractAuditResult.source_warnings` additively (§4.20), ADVISORY per §4.19 — R50's acceptance is
  *"fails **or** warns loudly"*, so this WARNS at the analytics surface and FAILS wherever a wrong
  figure would be written (`rebate_has_no_transactions`, `rebate_no_litres_to_allocate`,
  `rebate_exceeds_net`, each two-phase so a refusal leaves ZERO rows changed). It is deliberately
  SILENT for a supplier that never had a layer — proven in both directions. **§4.15 is live**: a
  non-EUR rebate converts through `fx.to_eur` at the document's own date (ECB units per 1 EUR, so
  converting DIVIDES) and is REFUSED with no row when the cache has no rate; there is deliberately
  no `stated` branch, because a rebate invoice states an amount and never a rate. The merge sums
  `amount_eur` only, so §4.14 holds structurally. **Consumer impact proven, not asserted**: the same
  term over the same line yields a €50.00 short-discount demand BEFORE the merge and NO finding
  after it, while a genuinely short rebate still reaches WO-82's own €150.00; `contract_audit`'s
  arithmetic is BYTE-UNCHANGED and both WO-83 artifacts quote the post-merge figure by construction
  (one line source, never forked). ONE new tenant table with FORCE RLS in the same migration and a
  real HTTP tenancy probe in the same commit that created it; `TRANSPORT_READ` reads /`VAT_WRITE`
  writes, NO permission member invented, NO SPA change. 82 tables, 86 revisions. Detail:
  `docs/plan/plan-a/wo/WO-84-net-eur-eff-merge.md`.

- [x] **WO-85** — `Completed` — G4.1/R51: **the canonical query registry**
  (`app/services/transport/queries.py`) and the structural proof that a future consumer cannot
  silently fork one. **The recon is again the finding**: R38's *"never a forked query"* clause had
  been honoured by HAND since WO-81, and hand-discipline had already slipped once. Eighteen call
  sites built a row-selection predicate over the two money-bearing transport tables, and among them
  sat ONE byte-identical fork in the claim-building path — `claim_lines.build_claim_lines` (what a
  claim's lines are BUILT from) and `checklist._unresolved_suppliers` (what the checklist REPORTS as
  blocking that same claim) carried the same four predicates written out twice, with nothing
  stopping one from acquiring a fifth: a drift there files a claim short (VAT forfeited at the
  30-Sep time-bar) or chases a supplier who was never in scope. A second shape was written three
  times — "the claim's currently-unfrozen lines", the set `lock.submit_claim` is about to freeze,
  re-typed by R3's synthetic-ref gate, R10's document gate and G2.5's freeze/preview; `claim_gates`'
  own docstring named the hazard (*"never two independent line-scans that could drift"*) and then
  mirrored the shape by hand. A FOURTH writer of that same cut was the `DELETE` inside
  `build_claim_lines` — the dangerous one, because a drift there removes rows rather than merely
  misreporting them, so the registry exposes it as bare criteria (`vat_claim_line_criteria`) rather
  than leaving it outside. Plus fourteen hand-typed `org_id ==` filters, each individually correct
  and each an independent chance to omit the one thing whose absence returns another tenant's rows
  with a **200** (§4.1/§4.4). **The registry**: six named, pure, org-scoped `Select` builders — no
  `AsyncSession`, no `await`, no `Decimal`, asserted structurally — under a stated charter *"the
  registry owns WHICH ROWS (the WHERE clause); the consumer owns HOW THEY ARE READ"*, so projection
  (`with_only_columns`), ordering, grouping and pagination stay at the call sites where their own
  docstrings already explain them. The module NAME is the spec's own vocabulary (`queries.q_savings`
  §2.4, `queries.q_ledger` §N10); the `q_` prefix is a Fleet Fuel stylistic detail and this
  codebase's naming convention governs instead — recorded as an interpretation, the WO-82/WO-84
  tie-break precedent. **Behaviour preserved where the tree was inconsistent, and reported rather
  than "cleaned up"**: three call sites treated a falsy `supplier`/`country` as "every"
  (`if supplier:`) and one treated it as a literal (`is not None`), so the registry filters on
  `is not None` and the falsy-tolerant sites bridge with `or None` — unifying them on one truthiness
  rule would have silently changed one of them, and `""` reaching `missing_source_warnings` is the
  one place where a migration WOULD have moved a figure. `fuel`'s `country.upper()` likewise stays
  at its call site: a registry that normalised would change what every other consumer matches.
  **The real deliverable is the guarantee, not the file**: an AST scan over
  `app/services/transport/` **and** `app/api/routes/transport/` refuses any `select()` over
  `FuelTransaction`/`VatRefundClaimLine` and any `<Model>.org_id` filter outside the registry, ships
  a seeded-violation self-test (template rule 6 — a scan that cannot fail proves nothing), and was
  demonstrated against the real tree by re-introducing the `checklist.py` fork and watching it named
  by file and line. R51's own acceptance is asserted both ways: *"no duplicate implementation
  exists"* by the scan, *"rename a canonical function ⇒ every consumer breaks"* by a no-orphan check
  over every registry entry. **PARTIAL HARVEST, documented, not silently skipped**: R51's
  materialised-metric half (*"a drift check that recomputes through the same code path… an
  un-materialized period still renders via a live fallback"*) has NO subject — all seventeen
  `app/models/transport/*` tables are sources of record, none a rollup — so building a drift check
  over nothing would be invented functionality (§10); its trigger is named (the first transport
  rollup table). `queries.q_savings`/`q_ledger` themselves belong to G4.7 and the export hub and are
  deliberately not invented here. Read-only refactor: NO table, NO migration, NO route, NO SPA
  change, NO permission, and not one pre-existing test file edited. 82 tables, 86 revisions. Detail:
  `docs/plan/plan-a/wo/WO-85-canonical-query-registry.md`.

---

## M2 — In Progress

- [x] **WO-47** — `Completed` — Quota/usage-limit model now keys off the org's `plan`
  (`app.services.plans.PLANS`), not the acting user's role (the plan flagged this as must-fix
  "before the first invoice, not after") — every member of an org shares one org-wide cap;
  `role_policies`→`plan_policies`. Preserves the never-lose-a-document-on-limit guardrail (every
  quota check still runs before anything is persisted; block-at-the-cap, since auto-charge overage
  needs live billing, still owner-blocked). Detail: `docs/plan/plan-a/wo/WO-47-H13.md`.
- [x] **WO-48** — `Completed` — Dogfood fallback (H1.6): `app.services.platform_billing` invoices
  InvoiceIQ's own paying tenants through the platform's own AR module (issuer registry, gap-free
  numbering, PDF/XML, send, dunning — all pre-existing, zero new delivery code) once per calendar
  month, whenever no live billing provider is configured. Off by default (`platform_org_id` unset);
  applies a 0% VAT placeholder pending the seller-of-record decision (§2/§2b). Revenue is no longer
  blocked on Stripe/EveryPay credentials landing. Detail: `docs/plan/plan-a/wo/WO-48-H16.md`.

### M2 — Blocked on owner/business decisions (not code-blocked; tracked in `docs/DECISIONS-NEEDED.md`)

- [ ] **Stripe live** — Checkout, Billing Portal, signed webhook, Billing Meter. Needs production
  Stripe credentials from the owner. **Fallback shipped (WO-48):** AR-module dogfood invoicing —
  revenue is not blocked on this; activation is an operational config step, `docs/DECISIONS-NEEDED.md`
  §2b.
- [ ] **Seller-of-record VAT process** — Stripe Tax vs. an explicit alternative; a finance decision.
- [ ] **Plan ladder reconciliation (H1.2)** — code implements trial/starter/pro/enterprise
  (€0/€29/€99/custom); the pricing hypothesis doc proposes a different Free/€39/€99/€249/Enterprise +
  Practice ladder. They conflict; the owner must pick one. WO-47's quota fix already uses whichever
  ladder is live in code today (indicative defaults, sysadmin-overridable) — this decision changes
  only `plans.py::PLANS`, not the enforcement mechanism. `docs/DECISIONS-NEEDED.md` §2a.

---

## M1 — Completed (WO-12…46)

Epics F1.1 (master-data/document screens) · C1.5–C1.9 (currency/dimension registries, multi-currency
reporting, dead-state removal, reclaimable-VAT fix) · I1.1–I1.3/I1.5 (composed dashboard, grouped nav
IA, honest cash-position label, report writers) · A1.5 (8 business roles reachable) · E1.1–E1.7
(capture-review UI, line-item provenance, hash re-upload detection, Mailgun adapter, extraction
learning loop) — all shipped, tested, documented. Full list: `docs/plan/plan-a/wo/WO-12*.md` through
`WO-46*.md`.

### 4-agent SaaS review-board audit (R1–R19) — Completed except 2 open

| Item | Status | Closed by |
|---|---|---|
| R1 — CSV formula-injection across 3 exports | ✅ Completed | WO-29 |
| R2 — Credit-note creation had no row lock (over-crediting race) | ✅ Completed | WO-26 |
| R3 — Seed data self-contradicted (Cash Position vs Invoices) | ✅ Completed | WO-28 |
| R4 — Expense-decision had no concurrency guard | ✅ Completed | WO-30 |
| R5(b) — Enterprise self-upgrade-for-free billing bypass | ✅ Completed | WO-31 |
| R5(a) — Self-serve billing collects zero real payment | 🔶 **Owner-blocked** | → M2 (Stripe live) |
| R6 — Reimbursement payout had no maker≠checker SoD | ✅ Completed | WO-32 |
| R7 — ClamAV fail-closed branch had no test coverage | ✅ Completed | WO-33 |
| R8 — OIDC discover/JWKS had no SSRF guard | ✅ Completed | WO-37 |
| R9 — Duplicate CSV-sanitization helper (3×) | ✅ Completed | WO-36 |
| R10 — `LocalStorage._path` bare-`startswith` containment | ✅ Completed | WO-42 |
| R11 — Stale SSO `client_secret` TODO comment | ✅ Completed | WO-41 |
| R12 — Stale README/ARCHITECTURE.md counts | ✅ Completed | WO-10 + ongoing truth-up (verified count now in README) |
| R13 — `test_refresh_owner_only_and_graceful` didn't test "owner only" | ✅ Completed | WO-38 |
| **R14** — No application-owned backup/restore tooling | 🔴 **Decision-gated** | Needs an owner/ops decision (infra-level DR runbook vs. app-level capability) before any code — see `docs/audit/remediation-roadmap.md` R14 detail |
| R16 — AR Void/Write-off had no confirmation dialog | ✅ Completed | WO-34 |
| R17 — Payment-run Cancel had no confirmation | ✅ Completed | WO-39 |
| R18 — Billing downgrade silently disabled modules | ✅ Completed | WO-35 |
| **R15** — No load/concurrency/large-dataset perf harness | ⚪ **Backlog (P3)** | Standalone build, not started — larger effort, own future work order |
| **R19** — No guided onboarding/setup-wizard checklist | ⚪ **Backlog (P3)** | Standalone build, not started — larger effort, own future work order |

### UX/UI redesign audit — Phase 1 complete, implementation started

- [x] **Audit** — `Completed` — `docs/design/UX-AUDIT.md`. Verdict: the WO-17 grouped sidebar nav is
  already good, do not rebuild; two CRITICAL findings (silent-failure async states on 26 pages;
  unlabeled form controls, WCAG 1.3.1/4.1.2) and one HIGH (design system built but ~unused —
  migration, not new-build).
- [x] **WO-45-UX1** — `Completed` — `QueryState`'s error branch now renders `ErrorState`
  (`role="alert"`, retry via `onRetry`) instead of `EmptyState` — the fix that upgrades every
  current and future `QueryState` consumer at once. Adopted `QueryState`+`PageHeader` on all eight
  money-bearing pages (`Invoices`, `Expenses`, `PaymentRuns`, `InvoiceDetail`, `CashPosition`,
  `IssuedReports`, `Receipts`, `Review`) — each independently-failing region gets its own
  `QueryState` (e.g. `Expenses.tsx`'s "awaiting my approval" vs "my reports" panels; `IssuedReports`'
  4 tabs); `PageHeader` hoisted above the loading/error branch on every page, collapsing
  `Expenses.tsx`/`IssuedReports.tsx`'s duplicate `<h1>`s. `CashPosition.tsx`'s WO-18 honesty copy
  moved verbatim into `PageHeader`'s `description` — `e2e/cash-position.spec.ts` passes unmodified.
  Unclipped the Invoices table (`overflow-hidden` → `overflow-x-auto`, Invoices.tsx only, per scope).
  Added a focus ring to the legacy `btn` `@utility` — using `ring-brand-500` (the WO's `ring-brand-400`
  does not exist as a theme token; adding one was explicitly out of scope, so this order used the
  nearest real token rather than inventing one, flagged in the commit). New
  `frontend/e2e/error-states.spec.ts` (7 tests: 500/empty/403/retry on Invoices, partial-failure on
  Expenses, single-region failures on Review/PaymentRuns) + `nav.spec.ts` extended with a single-`<h1>`
  guarantee across all eight routes + an invoice-detail route. All 5 "must pass unmodified" specs
  (`cash-position`, `dashboard`, `smoke`, `masters`, `upload-duplicate`) pass byte-identical; visual
  regression (`test:vr`) shows **zero** snapshot diffs (none of the `/design` showcase fixtures
  exercise a focused button or a query-error state, so this is the correct, verified outcome, not a
  missed change). Zero backend files touched. Detail:
  `docs/plan/plan-a/wo/WO-45-UX1-async-state-and-page-header.md`.
- [ ] Further UX slices (design-system migration across remaining pages, form-label pass, nav collapse
  rail/group-collapse/breadcrumbs, orphaned-route wiring for `/issuer` and `/reimbursements`) —
  `Backlog`, scoped in `docs/design/UX-AUDIT.md`'s phased plan, to be written up as WO-48+ in turn.

---

## M0 — Completed (WO-1…11)

Structural authorization + CI coverage gate · vendor bank-detail dual control (the IBAN fraud vector)
· partners router lockdown · per-request org-suspension + session revocation · mandatory inbound-email
secret · PII quarantine (tree + full-history CI scan) · one validation engine · one FX convention (the
SEPA wrong-currency bug) · payment-run maker≠checker/export-once/MsgId · docs truth-up + ADRs + the
tenancy-parity probe · `users.org_id`→memberships (B1.5). Full detail: `docs/M0-exit-gate.md`.

---

## Historical note

Everything below this line is the original 2026-07-27 audit snapshot, preserved for traceability. Every
item on it is now closed or reclassified above — do not treat it as current; the tables above supersede
it entirely. The original per-item detail sections it links to remain live in `docs/audit/`.

<details>
<summary>Original 2026-07-27 board (click to expand — superseded)</summary>

This board is seeded from the Phase 1-11 independent 4-agent SaaS review board audit (see
`docs/audit/`, run on branch `claude/bidit-invoice-data-analytics`, 2026-07-27). It reflects the
**debate-adjusted** priorities in `docs/audit/remediation-roadmap.md`. Every P0/P1 item starts as
**Approved** (ready for a work order); every P2 starts as **Backlog**; P3/P4 items are also
**Backlog** (lower priority, schedule opportunistically). No disputed/rejected findings occurred in
this audit round — see `docs/audit/agent-debate.md`'s "Disputed / rejected findings" section.

Statuses: `Backlog` · `Approved` · `In Progress` · `Blocked` · `Testing` · `Review` · `Completed` · `Rejected`

Implementation of these items is explicitly **out of scope for this pass** — each item becomes its own
future work order (WO-26+), reviewed one change at a time, matching the WO-1..WO-25 pattern already used
in this repo (see `git log` / `docs/plan/`).

### P0 — blocks any pilot (Approved)

- [x] **R2** — Credit-note creation has no row lock, violating the codebase's own non-negotiable
  invariant on over-crediting (reproduced live). Evidence: `docs/audit/functional-audit.md` §2.1,
  `docs/audit/agent-debate.md` §1.
- [x] **R3** — Demo/seed data self-contradicts: Cash Position/Payment Runs show €0 owed while
  Invoices lists >€1M. Evidence: `docs/audit/commercial-readiness.md` §3, `docs/audit/agent-debate.md` §8.

### P1 — blocks general (self-serve) release (Approved)

- [x] **R1** — CSV formula-injection sanitization inconsistently applied across 3 financial exports.
  Evidence: `docs/audit/functional-audit.md` §3.1, `docs/audit/security-findings.md` §2.4.
- [x] **R4** — Expense-approval decision has no optimistic-concurrency guard or row lock.
  Evidence: `docs/audit/test-baseline.md` finding #2.
- [ ] **R5** — Self-serve billing collects zero real payment today; Enterprise tier is self-upgradable
  for free even with billing wired (`price_eur=None` bypass). Split: the free-upgrade bypass closed
  (WO-31); the "collects zero real payment" half is owner-blocked, tracked under M2 above.
  Evidence: `docs/audit/commercial-readiness.md` §7.

### P2 — should fix before general release (Backlog)

- [x] **R6** — Reimbursement payout had no maker≠checker (SoD) control.
- [x] **R7** — ClamAV fail-closed malware-scan branch had zero test coverage.
- [ ] **R14** — No application-owned backup/restore tooling exists; decision-gated. Still open —
  tracked above.
- [x] **R16** — AR "Issue" screen: destructive actions had no confirmation dialog.
- [x] **R18** — Billing downgrade silently disabled modules with no confirmation.

### P3 — backlog / hardening (Backlog)

- [x] **R8** — OIDC `discover()`/`fetch_jwks()` had no SSRF guard.
- [x] **R9** — Duplicate CSV-sanitization helper implemented 3x.
- [x] **R13** — `test_fx.py::test_refresh_owner_only_and_graceful` didn't actually test "owner only."
- [ ] **R15** — No load/concurrency/large-dataset performance testing harness. Still open — tracked above.
- [x] **R17** — Payment-run "Cancel" button fired with no confirmation.
- [ ] **R19** — No guided onboarding/setup-wizard checklist. Still open — tracked above.

### P4 — informational / doc hygiene (Backlog)

- [x] **R10** — `LocalStorage._path` containment check used bare `startswith`.
- [x] **R11** — Stale `# TODO: secret store` comment on `SsoConnection.client_secret`.
- [x] **R12** — Root `README.md`/`ARCHITECTURE.md` were stale.

### Verified controls — no action required (not tasks; recorded for traceability)

These were raised during the audit (some at P1) but resolved through adversarial debate as
**confirmations of existing strength**, not defects — see `docs/audit/agent-debate.md` and
`docs/audit/remediation-roadmap.md`'s "Verified controls" section:

- Tenant isolation (query filter + ORM guard + Postgres RLS with FORCE) — independently reproduced
  live against a real Postgres cluster twice.
- Structural, CI-gated route authorization (`test_authz_coverage.py`) — recommend keeping this a
  required, unfiltered CI gate.
- Upload/attachment security gate (`filesec.py`) — covers all 8 intake paths, live exploit tests pass.
- `docs/plan/plan-a/ARCH_plan.md`'s prior risk claims (vendors/partners authz, route-level
  `_reconcile`, hardcoded EUR) were stale/false against source at audit time — since superseded by the
  banner fix (2026-07-28); the document is current again.

*Backlog items predating this audit, if any existed at the repo root, are preserved in
`docs/BACKLOG.md` (pre-existing) — this file is the audit-derived task board and does not duplicate or
supersede that doc; see `docs/BACKLOG.md` for other in-flight work not covered by this audit round.*

</details>
