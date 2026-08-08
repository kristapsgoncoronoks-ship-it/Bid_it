# WO-90 — transport UI slice 4: the savings / negotiation-evidence workspace

> WO-86 shipped the third UI slice and closed the analytics surface "for every
> built transport route" — a statement that was true on the day it was written
> and stopped being true the moment WO-87 landed. WO-87 added three
> `TRANSPORT_READ` GETs and, in its own scope section, recorded why it shipped no
> pixel: *"Any UI. Every analytics board in this programme ships its SPA slice
> separately (WO-78/WO-80/WO-86); this order changes no pixel."* This is that
> slice.

**WORK ORDER 90 — transport UI slice 4: the savings / negotiation-evidence
workspace (`frontend/src/pages/Savings.tsx` over the three WO-87 routes
`GET /transport/savings/same-day`, `/internal-benchmark`, `/expected-rebate`),
carrying R53's SECOND framing into the UI structurally — no claim-back
vocabulary, no send verb, no path from an overpay euro into the claim-back flow —
with R52's two non-reconciling grains explained as page text rather than left to
be discovered as a contradiction. Effort M 3–5d. Priority P1. Milestone M5.
Depends on: WO-87 (the three services, their schemas, their routes and their
constants), WO-86 (the SPA conventions: `lib/types.ts`, `decimalMoney`,
`hasVatPerm`, `claimRefusal`, `RefusalNotice`, the nav `perm` flag, the
`page.route`-mocked Playwright harness).**

### Objective and business value

The gap, with verified evidence. `grep -rn "transport/savings" frontend/src`
returns **nothing**: the three routes `backend/app/api/routes/transport/savings.py`
declares (`/same-day`, `/internal-benchmark`, `/expected-rebate`, all on
router-level `TRANSPORT_READ`) are reachable today only with `curl`. WO-87's own
acceptance criteria are all backend-side, and the analyses it built are the ones
whose whole value is being *shown to a human before a supplier meeting*: the
same-day grain exists to produce the sentence *"on 2026-05-14 you charged us
€0.0420/L more than the cheapest network we were already using in LV, on 12,400
L"*. A figure that never leaves a JSON response has never once been said out loud
at a negotiating table.

Who stops losing money: the finance lead who negotiates the fuel bill — a
haulier's second-largest cost line, and one that is negotiated rather than
posted. Three concrete failures the missing screen causes. (a) **The evidence is
never used.** `avoidable_eur` and `benchmark_gap_eur` are computed per period and
consumed by nobody. (b) **The rebate that quietly stopped arriving is never
seen.** `expected_rebate` learns a pair's typical €/L from all history and flags
the line that lost it — WO-84 recorded this as the half it deliberately did not
harvest, and WO-87 built it; with no screen, the one line that lost its rebate is
still invisible. (c) **The two grains become a support ticket.** R52's *"they
will not reconcile — that is correct"* is stated in the service docstrings and
nowhere a user can read it; the first operator to open both figures will assume
one is broken. This order makes the non-reconciliation a sentence on the page.

And the risk this order is really about: **R53's second framing**. WO-83 made the
FIRST framing client-reachable as a formal PDF letter with a 30-day
credit/refund demand. WO-87 then built the second analysis family and kept the
two apart structurally — different constants, no shared field vocabulary, no
import path in either direction, no write verb. **A UI is exactly where that
separation gets flattened**: one tempting "send this to the supplier" button, one
"recoverable" column heading, one link that carries an overpay euro into the
claim-back flow, and the platform is asserting a debt that nobody agreed to, on
our client's letterhead. This order therefore treats the framing as the
governing constraint and proves its preservation the same way WO-87 did — by
asserted absence, with a seeded-violation self-test.

### Scope

**In scope:**
- `frontend/src/pages/Savings.tsx` (**new**) — `/savings`, three tabs over the
  three WO-87 GETs, each rendering the response field-for-field:
  1. **Same-day overpay** (`GET /transport/savings/same-day?period=&country=`) —
     `findings` (country, date, supplier, `litres`, `eur_l_eff`,
     `cheapest_rival_supplier`, `cheapest_rival_eur_l_eff`, `delta_eur_l`,
     `avoidable_eur`, `suppliers_that_day`, `lines`), the `by_supplier`
     attribution, the total `avoidable_eur`, and the four counters
     (`lines_compared`, `days_compared`, `days_without_a_rival`,
     `lines_skipped_zero_qty`). `days_without_a_rival` is rendered as a **named
     fact, never as a zero finding**: a day with one supplier had no rival to
     compare against, which is a different statement from "you were competitive
     that day". The absence of a `supplier` filter is stated with WO-87's own
     reason (filtering the rows would change who the cheapest rival was).
  2. **Internal benchmark** (`/internal-benchmark?period=&country=`) — `rows`
     (country, supplier, `litres`, `eur_l_eff`, `best_supplier`,
     `best_eur_l_eff`, `gap_eur_l`, `benchmark_gap_eur`, `lines`), the total
     `benchmark_gap_eur` and its three counters.
  3. **Expected rebate** (`/expected-rebate?period=&supplier=`) — `expectations`
     (`typical_eur_l` with `learned_from_lines` beside it, because an
     expectation learned from two lines deserves less trust than one learned
     from two hundred), `findings` (both prices — `eur_l_doc` and `eur_l_eff` —
     plus `applied_eur_l`, `typical_eur_l`, `expected_rebate_eur`), the
     `tolerance_eur_l` the flag fires under, and the counters including
     `lines_without_an_expectation`.
- **R52 made legible.** Each overpay panel renders the response's own `grain`
  string, and a shared page-level note states in words that the two figures use
  different grains and are not expected to match — with the reason (different
  denominator, different comparison), so an operator who opens both is told why
  rather than left to assume one is wrong. Neither total is ever added to the
  other, and no element renders both euro fields as one figure.
- **R53's second framing, carried structurally into the UI:**
  - `savings.LEGAL_FRAMING` is rendered **verbatim from the wire** on all three
    panels (never restated, never paraphrased — the WO-86 `Overcharges.tsx`
    precedent for `price_basis`/`legal_framing`), together with `price_basis`
    (§3.G G1: the basis must be stated on any new report surface).
  - **No claim-back vocabulary anywhere in the new modules.** No `recover*`,
    `owed`, `owes`, `claim*`, `demand*`, `due`, `debt*`, `payable` in any
    identifier, string, comment or class name of `Savings.tsx`,
    `lib/transportSavings.ts` or the new wire interfaces in `lib/types.ts`.
  - **No send verb, no mutating control of any kind** — the routes are GET-only
    and the page mirrors that: no button that transmits anything to a supplier,
    no download, no form.
  - **No path into the claim-back flow.** The page links to `/overcharges`
    nowhere, so there is no route by which an overpay euro reaches the screen
    that opens a frozen demand.
  - A plainly-worded framing block on the page states what the figures are for
    (a negotiation) and what they are not, so an operator cannot mistake an
    overpay figure for money a supplier is obliged to pay.
- `frontend/src/lib/transportSavings.ts` (**new**) — PURE helpers, no React, no
  network, no arithmetic: the tab definitions, the R52 non-reconciliation copy,
  the "no rival that day is not a zero" copy, the advisory copy, and
  `isCountryShape` (shape only — `savings._validate_country` owns the refusal).
  `isPeriodShape` is IMPORTED from `transportRecovery.ts` rather than
  re-declared: one shape check, one source.
- `frontend/src/lib/types.ts` — additive wire types, field-for-field from
  `app/schemas/transport_savings.py`: `SameDayOverpayLine`, `SupplierOverpayTotal`,
  `SameDayOverpay`, `InternalBenchmarkRow`, `InternalBenchmark`, `LearnedRebate`,
  `MissingRebateLine`, `ExpectedRebate`. Every money field and every €/L is
  `string`; `litres` is a `string` too (it is a €/L denominator on the wire and a
  float round-trip of it would move the prices computed from it).
- `frontend/src/lib/nav.ts` — one destination in the existing Transport group,
  gated `module: "transport"` + `perm: "transport.read"`, exactly as WO-86's
  three are. Labelled for what the surface is, in the service's own words, and
  deliberately not with any word from the claim-back family.
- `frontend/src/App.tsx` — `/savings` (lazy), beside the other transport routes.
- `frontend/e2e/savings.spec.ts` (**new**) — the matrix below, in the
  `page.route`-mocked live-app pattern of `recovery.spec.ts`.
- `frontend/package.json` — the new spec joins the `test:e2e` list CI runs.
- Boards: `README.md` (SPA page count 52 → 53, machine-checked by
  `backend/tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`,
  moved in the SAME commit that adds the page), `TODO.md` (WO-90 row + M5 cell +
  suite line), `docs/transport/rules.md` (R53's second framing and R52 gain their
  UI consumer).

**Out of scope (the anti-scope-creep clause):**
- **Any BACKEND change.** No route, schema field, permission member, constant or
  error code. If the screen wants a figure the wire does not carry, it is
  reported as a gap and left unbuilt (§10). Specifically: no supplier filter on
  the same-day grain (WO-87 states why the route does not take one), no
  cross-period trend, no export/download endpoint (WO-87 shipped none), no
  "send" of any kind.
- **Any link, button or handoff into the claim-back flow** (`/overcharges`,
  `overcharge.open_claim`, the WO-83 artifacts). That is not an omission, it is
  the deliverable — R53.
- **The four G4.7 follow-up slices** that have no service: the peer benchmark
  (R55, needs a cross-entity cohort policy decision), the margin report (needs
  §3.H H5's `my_prices`/`wholesale_prices` tables), supplier reliability (needs
  an append-only `advertised_prices` table) and the six anomaly rules (R54).
  Each is named with its blocker in `docs/transport/rules.md` and none is
  stubbed here.
- **`/excise`, `/value`, `/claim-status`, the refund-estimate funnel** (G4.4 /
  G4.6 / G4.8) — no route exists for any of them.
- **Charting.** No new dependency and no chart: these are tables of euros beside
  the litres and the two prices that produced them, and a chart would add pixels
  without adding an answer (WO-86's own decision, unchanged).
- Reworking `RecoveryDashboard.tsx` / `Overcharges.tsx` / `Rebates.tsx` or any
  existing lib entry. This order adds; it does not refactor.

### Files to touch

| File | Change |
|---|---|
| `frontend/src/pages/Savings.tsx` | **new** — the three-panel workspace |
| `frontend/src/lib/transportSavings.ts` | **new** — pure copy + shape helpers |
| `frontend/src/lib/types.ts` | 8 additive wire interfaces |
| `frontend/src/lib/nav.ts` | one Transport-group destination |
| `frontend/src/App.tsx` | one lazy route |
| `frontend/e2e/savings.spec.ts` | **new** — the spec matrix |
| `frontend/package.json` | the spec joins `test:e2e` |
| `README.md` | scale line: SPA pages 52 → 53 |
| `TODO.md` | WO-90 row, M5 cell, suite line |
| `docs/transport/rules.md` | R53 (second framing) + R52/G4.7 gain a UI consumer |

### Implementation guidance

1. **Read the wire off `app/schemas/transport_savings.py`, never off a work
   order.** Every field a panel renders must appear in that module. A field that
   does not exist is not rendered and not invented (§10).
2. **Money never becomes a number** (§4.9). Every amount, every €/L and `litres`
   arrive as decimal STRINGS and are rendered through `decimalMoney` (money) or
   as-is (rates and litres, which are not currency). The page performs NO
   arithmetic — not a sum, not a difference, not a percentage. The server has
   already totalled and quantized everything (§4.10); a UI-side total would be a
   second, forkable source of truth. Grep-provable, and the suite greps.
3. **R52 is presentational as well as structural.** Render each response's own
   `grain`; state the non-reconciliation in words on the page; never put the two
   euro totals in one row, one tile group or one sentence that implies a sum.
4. **"No rival that day" is a fact, not a zero.** `days_without_a_rival` gets its
   own named counter and a sentence; the findings table for such a day is simply
   empty. The string "€0.00" must not be used to describe a day with no rival.
5. **R53 — the four absences, mirrored from WO-87 and proven, not promised.**
   (a) the framing string comes off the wire and is rendered verbatim; (b) the
   forbidden vocabulary is absent from the new modules — enforced by a
   source-level scan in the spec with a seeded-violation self-test, so the scan
   itself cannot silently stop working; (c) no link or route into the claim-back
   flow; (d) no mutating control at all. Every one of these is an assertion about
   tomorrow's file, not a description of today's.
6. **Advisory means advisory** (§4.19). All three analyses gate nothing, mutate
   nothing and persist nothing; the copy says so and does not describe any of
   them as blocking. Conversely `fx_rate_unavailable` is a REFUSAL — the analysis
   was not run — and the copy must not imply a partial figure was produced.
7. **Loading / empty / error on every panel** via `QueryState` / `EmptyState` /
   `Skeleton`, module gating via `useModules` + `ModuleInactive`, refusals via
   `RefusalNotice`, exactly as the three WO-86 pages do.
8. **Permission mirroring is cosmetic** (master-context §6). The nav entry and
   the page read on `transport.read`; there is no write control to gate. A 403
   from the server still renders through the refusal path.
9. **The refusal map is not edited.** All four codes these routes raise
   (`module_not_enabled`, `invalid_period`, `invalid_country`,
   `fx_rate_unavailable`) already have entries in
   `lib/transportClaims.ts`. Adding none keeps this order additive; the one
   imprecision (`invalid_period`'s sentence is written for the claim's
   quarter/year shape, while these routes take `YYYY-MM`) is reported rather
   than fixed by editing a shared entry other pages depend on.

### Invariants this order must preserve

- **§4.9 — money is a string end to end.** No `Number()`, `parseFloat`,
  `toFixed`, `Math.` or arithmetic operator touches an amount, a €/L or a litre
  figure on the new page or in the new helper module. Proven by a source grep in
  the spec and by a fixture carrying `99999999999999.99`, which an IEEE-754
  round-trip would destroy.
- **§4.10 — the server computes every total.** `avoidable_eur`,
  `benchmark_gap_eur` and `expected_rebate_eur` are rendered exactly as
  received; no panel re-derives a total, a per-supplier subtotal or a delta.
- **§4.14 — no cross-currency sum is ever presented.** Each response's own
  `currency` ("EUR") is rendered beside its figures, and the page states the
  basis; nothing on the screen mixes currencies, because the service refuses
  rather than comparing across an unestablished EUR basis.
- **§4.19 — an advisory surface must not imply it gates.** All three analyses
  are described as read-only evidence that changes nothing; `expected_rebate` in
  particular is a document to go and find, never an entitlement.
- **§4.20 — additive.** One new page, one new pure-helper module, additive types,
  one nav row, one route, one spec file. No existing page, type, refusal entry,
  helper or permission value is changed or removed.
- **R52** — two grains, each labelled with the service's own `grain` string,
  never summed, non-reconciliation stated as page text.
- **R53 (second framing)** — see §5 above; the four absences are the deliverable.
- **R49 / §3.G G1** — `price_basis` rendered verbatim on every panel.
- **§9/§10 — actual vocabulary, nothing invented.** `grain`, `legal_framing`,
  `price_basis`, the field names and the counters are the server's own; no label
  is invented for a state the server does not have.

### Database / migration impact

**None.** This order touches no backend file. No table, no column, no migration,
no RLS policy, no permission member.

### Testing requirements

`frontend/e2e/savings.spec.ts`, the `page.route`-mocked live-app harness of
`recovery.spec.ts` (synthetic fixtures — fictional supplier codes, stations and
figures; no Fleet Fuel bytes, no literal shaped like a VAT id, IBAN or
registration number).

Same-day overpay:
- `same-day: each finding renders its litres, both effective prices, the delta and the avoidable euro`
- `same-day: the per-supplier attribution renders with its days count`
- `same-day: the total renders exactly as the wire string`
- `same-day: a day with no rival is reported as a named count, never as a €0.00 finding`
- `same-day: a month with no comparable day renders the zero-state, not an error`
- `same-day: the response's own grain string is rendered`
- `same-day: the page states why there is no supplier filter`
- `same-day: a loading state renders before the API resolves`
- `same-day: a 500 renders the error state`
- `same-day: fx_rate_unavailable renders its sentence and says no comparison was run`
- `same-day: invalid_country renders its sentence, not the slug`

Internal benchmark:
- `benchmark: each row renders its supplier price, the best supplier and the gap`
- `benchmark: the best supplier appears with a zero gap rather than being hidden`
- `benchmark: the total renders exactly as the wire string`
- `benchmark: an empty month renders the zero-state`

R52:
- `r52: both grains are labelled with the service's own grain string`
- `r52: the page states that the two grains do not reconcile and why`
- `r52: the two euro totals never appear as one figure`

Expected rebate:
- `rebate: an expectation renders its typical €/L with the sample it was learned from`
- `rebate: a flagged line renders both prices, what was applied and the advisory magnitude`
- `rebate: the tolerance the flag fires under is rendered`
- `rebate: lines without an expectation are counted, not warned about`
- `rebate: an empty result explains that no pair has a learned rebate yet`

R53 — the framing separation (the governing constraint):
- `r53: the legal framing is rendered verbatim from the wire on all three panels`
- `r53: the price basis is rendered verbatim on all three panels`
- `r53: the page states plainly that these figures are not a debt`
- `r53: no forbidden claim-back vocabulary appears in the new modules` (source
  scan over `pages/Savings.tsx`, `lib/transportSavings.ts` and the new wire
  interfaces in `lib/types.ts`)
- `r53: the vocabulary scan detects a seeded violation` (self-test — the scan
  must be able to fail)
- `r53: the page carries no link or route into the claim-back flow` (source scan
  + a DOM assertion inside `<main>`)
- `r53: the page exposes no mutating control` (no button/form that posts)

Permission, module and money:
- `perm: a read-only role sees every figure` (granted)
- `perm: a role without transport.read sees no nav entry for this surface` (denied)
- `perm: a server 403 renders through the refusal path`
- `module: transport off renders the module notice`
- `money: every amount renders from the wire string with no float round-trip`
  (a `99999999999999.99` fixture asserted character-for-character)
- `money: the new modules perform no float arithmetic` (source grep for
  `parseFloat`, `Number(`, `toFixed`, `Math.`)

### Acceptance criteria (verifiable checklist)

- [ ] `/savings` renders three tabs and each one renders its response's own
      `legal_framing` and `price_basis` strings verbatim.
- [ ] With `days_without_a_rival: 4` and an empty `findings` list, the page shows
      the count of 4 with a sentence saying no rival traded those days, and the
      string `€0.00` does not describe any of them.
- [ ] Both overpay panels render their `grain` string, and the page contains a
      sentence stating the two grains do not reconcile, with the reason.
- [ ] `grep -nE "recover|owed|owes|claim|demand|debt|payable" frontend/src/pages/Savings.tsx
      frontend/src/lib/transportSavings.ts` returns nothing (word-boundary
      matched, per the spec's scan), and the spec's seeded-violation self-test
      proves the scan can fail.
- [ ] `grep -n "/overcharges" frontend/src/pages/Savings.tsx` returns nothing, and
      no link inside `<main>` on `/savings` points at the claim-back flow.
- [ ] `/savings` contains no `<button>` or `<form>` that issues a non-GET request.
- [ ] A `user_free` (READ_ONLY) session sees every figure; a `user` (EMPLOYEE)
      session sees no nav entry for the surface.
- [ ] `GET /transport/savings/same-day` refused with `fx_rate_unavailable`
      renders a sentence and states that no comparison was produced.
- [ ] `grep -nE "parseFloat|Number\(|toFixed|Math\." frontend/src/pages/Savings.tsx
      frontend/src/lib/transportSavings.ts` returns nothing.
- [ ] `npm run build` (tsc + vite) clean; `npm run test:e2e` green at 171 + the
      new specs with zero pre-existing specs modified.
- [ ] `python -m pytest -q` unchanged from the WO-89 baseline (frontend-only
      order), with `README.md`'s SPA page count moved 52 → 53 in the SAME commit
      that adds the page.

### Rollback strategy

Code revert only — one new page, one new helper module, additive types, one nav
row, one App route, one spec file. No backend file, no migration, no data.
Narrower mitigation: remove the one entry from `frontend/src/lib/nav.ts` and the
destination disappears from the IA while the route stays reachable by URL; remove
the `<Route>` line and it is gone entirely. Nothing this order ships can change a
stored figure, a status, a lock or an audit chain — it issues three GETs.

### Documentation to update

- `README.md` — the scale line, SPA pages 52 → 53 (machine-checked).
- `TODO.md` — the WO-90 row, the M5 cell, the suite line.
- `docs/transport/rules.md` — R53's second framing and R52/G4.7 gain their UI
  consumer, with the four absences named as what preserves the separation.
- No ADR is contradicted. ADR-0024's structural authorization is mirrored, not
  re-decided; the frontend mirror stays cosmetic (master-context §6).

### Self-verification block

```bash
cd /home/user/Bid_it/frontend
npm run build                       # tsc --noEmit + vite build
npx playwright test e2e/savings.spec.ts
npm run test:e2e                    # 171 -> 171 + N, zero pre-existing specs touched

# the money-discipline proof (must print nothing)
grep -nE "parseFloat|Number\(|toFixed|Math\." src/pages/Savings.tsx src/lib/transportSavings.ts

# the R53 proofs (must print nothing)
grep -nEi "\brecover|\bowed\b|\bowes\b|\bclaim|\bdemand|\bdebt|\bpayable\b" \
  src/pages/Savings.tsx src/lib/transportSavings.ts
grep -n "/overcharges" src/pages/Savings.tsx

# every field the page reads must exist on the wire schema
cd /home/user/Bid_it/backend && . .venv/bin/activate
python -c "
from app.schemas.transport_savings import SameDayOverpayOut, InternalBenchmarkOut, ExpectedRebateOut
for m in (SameDayOverpayOut, InternalBenchmarkOut, ExpectedRebateOut):
    print(m.__name__, sorted(m.model_fields))
"
python -c "
from app.services.transport import contract_audit, savings
assert contract_audit.LEGAL_FRAMING != savings.LEGAL_FRAMING
print(savings.LEGAL_FRAMING)
"
python -m pytest -q                 # frontend-only order: unchanged baseline
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```
