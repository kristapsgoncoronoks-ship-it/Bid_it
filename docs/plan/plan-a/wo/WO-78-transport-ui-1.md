# WO-78 — transport UI slice 1: the VAT claims workspace

> The head of the M3 **UI batch**. WO-76 shipped the claim lifecycle over
> HTTP and WO-77 the admin/config surfaces + filing artifacts; the TODO M3
> cell's own remaining list still reads "… and the UI surface". This slice
> ships the SPA workspace for the CLAIM LIFECYCLE — a claims list, a claim
> detail with lines/checklist/stage/totals, the permission-gated lifecycle
> actions, the D5 submit refusal vocabulary rendered as human sentences,
> and the two filing-artifact downloads — and nothing else of the M3 UI.

**WORK ORDER 78 — transport UI slice 1: the VAT claims workspace
(`frontend/src/pages/VatClaims.tsx` + `VatClaimDetail.tsx`) over the
WO-76/WO-77 routes, permission-gated in the UI exactly as the API gates
them (VAT_READ / VAT_WRITE / VAT_SUBMIT), with the D5 refusal codes mapped
to actionable human messages and the workbook/evidence downloads wired.
Effort M 3–5d. Priority P1. Milestone M3. Depends on: WO-76 (the claim
lifecycle routes), WO-77 (waivers, status codes, artifacts), WO-49 (the
VAT permission vocabulary), I1.2 (the live nav IA / `AppShell`).**

### Objective and business value

The gap, with verified evidence: `frontend/src/App.tsx` routes 45 pages and
`frontend/src/lib/nav.ts` groups them Overview / Payables / Receivables /
Insights / Workspace — `grep -rn "transport\|vat" frontend/src` returns
nothing outside the FX/tax-code surfaces. Meanwhile
`backend/app/api/routes/transport/` now exposes 36 routes: the nine
claim-lifecycle endpoints of WO-76 and the 27 admin/artifact endpoints of
WO-77, every one of them structurally gated and tested. The claim
lifecycle is reachable only with `curl`. WO-76's own objective paragraph
states the standard this order meets — "a VAT refund claim that can only be
driven from a Python shell is not a product" — one layer higher: an
operator cannot see a claim, cannot read why a submit was refused, and
cannot download the two artifacts that ARE the deliverable to the refunding
member state.

Who stops losing money: the FINANCE_MANAGER who files. The whole D5 gate
chain is fail-closed by design and its value is entirely in the OPERATOR
seeing the refusal in time — `period_not_ended`, `below_minimum`,
`customer_not_active`, `country_not_activated`, `unresolved_invoice_refs`,
`invoice_document_missing`, `duplicate_invoice_lock`. A 409 with a raw slug
in a network tab is not a product either; each refusal must say what is
wrong AND what to do next, or the claim silently misses the 30 September
Art. 15 deadline and the cash is forfeited. The ACCOUNTANT must be able to
prepare a claim without seeing a Submit button they will only ever be
refused (the WO-49 VAT_WRITE/VAT_SUBMIT split, made visible).

### Scope

**In scope:**
- `frontend/src/pages/VatClaims.tsx` (**new**) — the claims list: entity,
  refund country, reference period, status + status code, frozen VAT total;
  loading / empty / error states; a create-claim form (VAT_WRITE).
- `frontend/src/pages/VatClaimDetail.tsx` (**new**) — header (the R1 grain
  + status), the materialized lines table, the advisory checklist panel,
  the stage/status-code ladder, server-returned totals, the lifecycle
  actions, the submit dialog, and the two artifact downloads.
- `frontend/src/lib/transportClaims.ts` (**new**) — PURE helpers: the
  refusal-code → human-message map (`claimRefusal`), the stage ladder
  (`stageLadder`), and the synthetic-ref predicate mirror
  (`isSyntheticRef`) used to pre-seed the submit dialog. No React, no
  network — so the mapping is unit-testable through the page.
- `frontend/src/lib/format.ts` — additive `decimalMoney`: formats a wire
  Decimal STRING by string surgery (grouping + the currency symbol), never
  `Number()` (master-context §4.9 — the UI must not round-trip money
  through a float even to display it).
- `frontend/src/lib/roles.ts` — additive `VAT_PERMISSIONS` mirror +
  `hasVatPerm(user, perm)`, a cosmetic mirror of
  `backend/app/core/authz.py::ROLE_PERMISSIONS` (the same discipline the
  existing `ROLE_RANK` mirror already documents).
- `frontend/src/lib/nav.ts` — a "Transport" nav group with the VAT claims
  destination, gated `module: "transport"` **and** a new `perm` flag.
- `frontend/src/components/Layout.tsx` — honour the new `perm` flag in
  `filterNav` (one predicate, same shape as `module`/`admin`/`owner`).
- `frontend/src/lib/types.ts` — the transport wire types, field-for-field
  from `app/schemas/transport_claim.py` / `transport_admin.py`.
- `frontend/src/App.tsx` — `/vat-claims` and `/vat-claims/:id` routes
  (lazy, like every other page behind the shell).
- `frontend/e2e/vat-claims.spec.ts` (**new**) — the matrix below, in the
  established `page.route`-mocked live-app pattern of
  `e2e/error-states.spec.ts`.
- `frontend/package.json` — add the new spec to the `test:e2e` script (the
  list CI's `frontend-e2e` job runs).
- Boards: `TODO.md` (WO-78 row + M3 cell + suite line), `README.md` (SPA
  page count 45 → 47, machine-checked by
  `backend/tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`),
  `docs/transport/rules.md` (R-rows gaining a UI consumer).

**Out of scope (the anti-scope-creep clause):**
- Any SPA surface for the WO-77 **admin/config** routes — checklist-rule
  admin, cadences, the receipt-control grid, note→invoice overrides,
  tie-out expectations, the customer-lifecycle ladder. They are a second UI
  slice (they carry their own screens and their own refusal vocabulary);
  this order consumes only `GET /transport/status-codes` from `admin.py`,
  for the ladder vocabulary.
- Any analytics surface (`fuel.py`/`recovery.py`/`excise.py`/
  `overcharges.py` — no backing service or route exists).
- Any BACKEND change. No new route, no new schema field, no new
  permission. If the UI wants a field the wire does not carry, the field is
  reported as a gap, never invented (§9/§10).
- A new UI library, state manager, styling system or test framework. The
  page composes `src/components/ui` primitives, TanStack Query, Tailwind
  and Playwright exactly as the existing 45 pages do.
- Visual-regression snapshots (`npm run test:vr` is a documented LOCAL
  gate; new baselines are environment-specific).

### Files to touch

| File | Change |
|---|---|
| `frontend/src/pages/VatClaims.tsx` | **new** — the claims list + create form |
| `frontend/src/pages/VatClaimDetail.tsx` | **new** — the claim workspace |
| `frontend/src/lib/transportClaims.ts` | **new** — refusal map + stage ladder + synthetic-ref mirror |
| `frontend/src/lib/format.ts` | additive `decimalMoney` (string-exact) |
| `frontend/src/lib/roles.ts` | additive `VAT_PERMISSIONS` + `hasVatPerm` |
| `frontend/src/lib/types.ts` | additive transport wire types |
| `frontend/src/lib/nav.ts` | "Transport" group + the `perm` gating flag |
| `frontend/src/components/Layout.tsx` | `filterNav` honours `perm` |
| `frontend/src/App.tsx` | two lazy routes |
| `frontend/e2e/vat-claims.spec.ts` | **new** — the test matrix |
| `frontend/package.json` | the new spec joins `test:e2e` |
| `TODO.md`, `README.md`, `docs/transport/rules.md` | boards (final commit) |

### Implementation guidance

1. **Verify every field against the backend first.** `ClaimOut` /
   `ClaimLineOut` / `ChecklistItemOut` / `StageOut` in
   `app/schemas/transport_claim.py`; `StatusCodesOut` in
   `app/schemas/transport_admin.py`. Type the SPA models field-for-field —
   money fields as `string` (pydantic v2 serializes `Decimal` as a JSON
   string) and nullable exactly where the schema says `| None`.
2. **Money: display-only, string-exact.** `decimalMoney(value, currency)`
   splits the wire string on `"."`, groups the integer part with a regex
   over the digits, and pads/truncates nothing — no `Number()`, no
   `parseFloat`, no arithmetic. A `null` money field renders `"—"` (NOT
   `0.00`: `vat_eur === null` means "not yet frozen", per the schema's own
   comment). The UI computes NO total: the frozen `vat_eur` on the claim is
   what is shown (§4.10 — the server recomputes every total; the client
   never does).
3. **Permission gating is cosmetic and additive.** `hasVatPerm` mirrors
   `ROLE_PERMISSIONS`; an action the user lacks is NOT RENDERED (no dead
   button), and the page still handles a 403 from the server as the real
   control (master-context §6 — a hidden nav item is never a security
   boundary). `is_platform_admin` short-circuits to all three, matching
   `authz.has_permission`.
4. **Refusal mapping is a pure function.** `claimRefusal(code, detail)`
   returns `{ title, next }` — the title says WHAT IS WRONG in the
   operator's words, `next` says WHAT TO DO NEXT. The server's own `detail`
   (which names the offending suppliers / invoice refs / period) is
   rendered VERBATIM underneath as the specifics: the map adds the action,
   it never replaces the server's facts and never shows the raw slug. An
   unmapped code falls back to the server `detail` alone — fail-OPEN on
   *presentation* (showing the server's sentence is strictly better than
   swallowing it), which is safe because presentation gates nothing.
5. **`below_minimum` exposes the override.** The refusal card for that one
   code renders a "Submit anyway (below the Art. 17 minimum)" action that
   re-posts the same body with `override_minimum: true` — the flag the
   `ClaimSubmitIn` schema already carries. It is shown only to a
   VAT_SUBMIT holder (the same permission that gates submit itself; WO-76
   recorded that no harvested rule assigns it a stricter one) and states
   plainly that the override is recorded on the claim.
6. **The submit dialog mirrors the service contract exactly.**
   `POST .../submit` takes `invoices: [{supplier, invoice_ref,
   fuel_transaction_id}]` (min 1). No route enumerates fuel transactions
   (`fuel.py` does not exist — out of scope in both WO-76 and WO-77), so
   the operator supplies the rows. The dialog PRE-SEEDS one row per
   materialized line whose `invoice_ref` is not synthetic (the
   `is_synthetic` mirror: contains `INPUT`, starts with `ALL:`, or equals
   `UNMATCHED`) with the ref filled in, and leaves supplier +
   fuel-transaction id to be typed. This is a recorded LIMITATION of the
   current wire, not an invented endpoint.
7. **Stage vs status code.** `GET .../stage` is meaningful only on a DRAFT
   claim (the service refuses `claim_not_draft` otherwise) — the page
   requests it only while `status === "draft"` and treats a refusal as "not
   applicable", never as an error banner. Past submission the ladder
   position is the claim's own `status_code`. The ladder itself is built
   from `GET /transport/status-codes` (`auto` then `manual`) — this
   codebase deliberately has NO label mapping (WO-77 decision 5), so the
   codes are rendered as the codes they are; the page adds no invented
   label vocabulary.
8. **Advisory never blocks (§4.19).** The checklist panel is presented as
   advisory: a failing item shows its `reason` and NEVER disables the
   Submit button. The hard gate is the server's; the UI must not pre-empt
   it (pre-empting would make the UI a second, drifting gate).
9. **Downloads reuse `downloadFile`** from `src/lib/api.ts` — it already
   re-inflates a blob error body so `apiErrorCode` sees the `{detail,code}`
   shape. `claim_not_frozen`, `synthetic_line_in_pack`,
   `claim_totals_drift`, `claim_currency_mismatch` and
   `evidence_document_unavailable` all flow through the SAME
   `claimRefusal` map.
10. **Module gate.** Both pages render `ModuleInactive` when the
    `transport` module is off (the `Expenses`/`Issue` precedent), and the
    nav item is hidden — while the server keeps returning 403
    `module_not_enabled` regardless.

### Invariants this order must preserve

- **§4.9 (Decimal, never float):** every money value crosses the wire as a
  string and is formatted by `decimalMoney` without `Number()`/
  `parseFloat`/arithmetic. Proven by a test that renders a value with more
  significant digits than an IEEE-754 double can hold and asserts the exact
  digits appear.
- **§4.10 (the server recomputes every total):** the detail page displays
  the claim's own frozen `vat_eur`/`vat_local`; it never sums the lines to
  produce a header figure. Proven by a test whose mocked lines deliberately
  do NOT sum to the header and asserts the HEADER value is what renders.
- **§4.19 (advisory never blocks):** a failing checklist item never
  disables Submit; the checklist/stage GETs are reads only.
- **§4.20 (additive):** no existing route, schema, error shape or page is
  changed in behaviour. `nav.ts`/`Layout.tsx` gain one optional flag whose
  absence preserves the current filtering exactly.
- **§6 (frontend gating is cosmetic):** the pages hide actions the role
  lacks AND still render the server's refusal if one arrives; no UI check
  is treated as the control.
- **§10 (no invented functionality / zero Fleet Fuel bytes):** no status
  LABEL vocabulary is invented (the codes are the vocabulary); no field
  absent from the wire is displayed; all fixtures are synthetic.

### Database / migration impact

None. No backend file is touched.

### Testing requirements

`frontend/e2e/vat-claims.spec.ts`, in the `page.route`-mocked live-app
pattern of `e2e/error-states.spec.ts` (synthetic fixtures only):
- `list: renders the claims with their grain, status and frozen total`
- `list: the loading state renders before the API resolves`
- `list: an empty result shows the empty copy, never an alert`
- `list: a 500 shows the error state, never the empty copy`
- `list: the module being off shows the module notice, not a table`
- `nav: the VAT claims entry appears for a VAT_READ role and is absent for
  an employee` (and absent when the module is off)
- `detail: renders the grain, the lines and the server's own totals`
- `detail: the header total is the server's, not a sum of the lines`
- `detail: money renders exactly from the wire string (no float
  round-trip)`
- `detail: the advisory checklist shows a failing item and Submit stays
  enabled`
- `detail: the stage ladder marks the derived stage on a draft claim`
- `permissions: an accountant sees Build lines but no Submit/Withdraw`
- `permissions: a read-only user sees no mutating action at all`
- one test per submit refusal code rendering its human message and NOT the
  raw slug: `period_not_ended`, `below_minimum`, `customer_not_active`,
  `country_not_activated`, `unresolved_invoice_refs`,
  `invoice_document_missing`, `duplicate_invoice_lock`, `claim_not_draft`,
  `empty_claim_set`
- `submit: below_minimum offers the override and re-posts with
  override_minimum=true`
- `artifacts: a workbook refusal (claim_not_frozen) renders its human
  message`
- `artifacts: the evidence pack downloads on a frozen claim`

### Acceptance criteria (verifiable checklist)

- [ ] `/vat-claims` lists mocked claims showing entity, refund country,
      reference period, status + status code and the frozen VAT total
      rendered from the wire string.
- [ ] A claim detail whose mocked lines sum to `"999.99"` while the header
      says `"1234.56"` renders **1,234.56** in the header — the UI computes
      no total.
- [ ] A money string of `"12345678901234.57"` renders with those exact
      digits (a `Number()` round-trip would print `…34.56`).
- [ ] A submit returning 409 `below_minimum` renders a sentence naming the
      Art. 17 minimum and an explicit override action; clicking it re-posts
      with `override_minimum: true` (asserted on the captured request body).
- [ ] A submit returning 409 `unresolved_invoice_refs` renders an
      actionable sentence and the raw slug `unresolved_invoice_refs` appears
      NOWHERE on the page.
- [ ] With `role: "accountant"` the detail page renders "Build lines" and
      renders NO "Submit"/"Withdraw"/"Set status code" control.
- [ ] With `role: "user"` (EMPLOYEE) the nav has no VAT claims entry.
- [ ] With the `transport` module disabled, both pages render the
      module-inactive notice and no table.
- [ ] `GET .../workbook` returning 409 `claim_not_frozen` renders the
      human message, not a slug.
- [ ] `npm run build` (tsc --noEmit + vite build) and `npm run test:e2e`
      (including the new spec) are green; `python scripts/pii_scan.py
      --tree` is clean; the backend suite is unchanged at 1853 passed / 10
      skipped.
- [ ] `README.md`'s scale line says 47 SPA pages and
      `test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`
      passes.

### Rollback strategy

Pure frontend revert: two new pages, one new lib module, three additive
lib/nav/Layout edits, two route lines, one new spec. No migration, no data
effect, no backend change. Narrow mitigation without a full revert: drop
the "Transport" group from `LIVE_NAV` — the routes become unreachable from
the shell while the backend keeps its own gates.

### Documentation to update

- `TODO.md` — WO-78 row + M3 cell + suite line.
- `README.md` — the SPA page count in the scale line (45 → 47).
- `docs/transport/rules.md` — the R-rows that gain a **UI** consumer (R1
  create/list, R2 the lines table, R3/R6/R7/R8/R10/R44 the submit refusal
  surface, R5 withdraw, R12/R17 the stage + status-code ladder, R45 the
  advisory checklist panel, G2.12 the artifact downloads).
- No ADR contradicted: ADR-0024's structural authz is unchanged (the SPA
  mirror is explicitly cosmetic); ADR-P3's UI batch is being started, not
  altered.

### Self-verification block

```bash
cd /home/user/Bid_it/frontend
npm run build                       # tsc --noEmit + vite build
npx playwright test e2e/vat-claims.spec.ts
npm run test:e2e                    # the full CI e2e list, incl. the new spec
# the demonstration — the workspace exists, is permission-mirrored, and no raw
# refusal slug is ever rendered:
grep -n "vat-claims" src/App.tsx src/lib/nav.ts
grep -c "" src/lib/transportClaims.ts
grep -rn "Number(\|parseFloat" src/pages/VatClaim*.tsx || echo "no float money path"
cd ../backend && .venv/bin/python -m pytest -q      # 1853 passed / 10 skipped, unchanged
cd .. && python scripts/pii_scan.py --tree
```
