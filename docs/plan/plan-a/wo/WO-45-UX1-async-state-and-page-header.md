# WORK ORDER 45 — Async-state truth + consistent page headers (board UX1)

<!-- ═══════════════ COPY FROM HERE: WO-45 ═══════════════ -->

**WORK ORDER 45 — Async-state truth on the money-bearing pages, consistent page headers, and a focus ring for the legacy button utility (board UX1). Effort M 3–5d. Priority P0. Milestone M1. Depends on: nothing.**

Baseline: **frontend** at `af78f38` — `cd frontend && npm run build` → **exit 0** (`tsc --noEmit` + `vite build` both clean), verified by the auditor immediately before writing this order. **Backend** — the last figure recorded in a work order is *1048 passed, 4 skipped* (WO-21, `WO-21-I15.md`), but WO-22…WO-44 added tests without restating it, so **that number is stale and must not be quoted as this order's baseline.** Per the process rules, the implementer runs `cd backend && . .venv/bin/activate && python -m pytest -q` **before** touching anything and records the exact figure here. This order changes no backend file, so whatever that number is, it must be **byte-identical** afterwards.

Source: `docs/design/UX-AUDIT.md` §2 (P1, P4, P8), §7. This is the first slice of the new **UX epic**, which complements the existing `I` board rather than replacing any of its rows.

## Objective and business value

**The defect.** 39 of the SPA's pages call `useQuery`; **26 of them never reference `isError`, `ErrorState` or `QueryState`**. The canonical case is `frontend/src/pages/Invoices.tsx:43` — `const { data, isLoading } = useQuery<InvoiceList>({…})`, where `isError` is simply not destructured. When `GET /invoices` returns 500, 403 or times out, `data` stays `undefined`, `isLoading` goes false, and lines 171-175 render a `<td>` reading **"No invoices found."** A failed request is therefore pixel-identical to an empty accounts-payable ledger, with no toast and no retry. The same shape is in `Expenses.tsx`, `PaymentRuns.tsx`, `InvoiceDetail.tsx`, `CashPosition.tsx`, `IssuedReports.tsx`, `Receipts.tsx` and `Review.tsx`. Separately, `frontend/src/components/ui/QueryState.tsx:26-34` — the primitive that is supposed to fix this — renders its **error** branch as an `EmptyState`, which carries no `role="alert"`, so even the 7 pages that adopted it do not announce failure to assistive tech; the correct `ErrorState` component (`ui/ErrorState.tsx`, which does set `role="alert"`) exists and is unused by it. Two further verified defects ride in the same files: page titles use **four different class strings** (27× `text-2xl font-semibold tracking-tight`, 16× `text-xl font-semibold`, 9× `text-lg font-semibold`, 1× `mb-1 text-lg font-semibold`), with `Expenses.tsx` rendering `<h1>Expenses</h1>` twice (lines 47 and 58); and the `btn` `@utility` in `frontend/src/index.css:33-35` defines **no focus ring** (`inline-flex … transition disabled:opacity-50`), so the 86 `btn-primary`/`btn-ghost` call sites fall back to the user-agent outline while `ui/Button`'s 63 call sites show a brand ring — inconsistent focus within a single page.

**Who pays.** The master context states the product's promises and adds: *"Every one of those promises dies if a number is wrong or a tenant sees another tenant's data."* A silently-empty AP ledger is that failure in its most expensive form — a finance lead who opens `/invoices` during a partial outage sees nothing to pay and reasonably concludes there is nothing to pay, and the product has actively misinformed them about money owed. This is also a standing violation of the project's own Definition of Done §7.2 (*"Loading, empty and error states on every new screen"*) on 26 screens, i.e. the codebase currently ships a rule it does not keep. Fixing the eight money-bearing pages removes the entire class of silent-wrong-state incidents where cash is at stake, and the accompanying `PageHeader` and focus-ring changes buy the largest visual-coherence and accessibility gain per hour available in the frontend (audit §2 P4, P8) at effectively zero risk.

## Scope

**In scope:**
- `frontend/src/components/ui/QueryState.tsx` — error branch renders `ErrorState` (which has `role="alert"`) instead of `EmptyState`; keep the existing `errorTitle` prop and the `refetch` retry wiring.
- `frontend/src/index.css` — add a focus ring to the `btn` `@utility`.
- Adopt `QueryState` (or an explicit `ErrorState` branch where a page runs several independent queries) and `ui/PageHeader` on these **eight** pages, chosen because every one of them displays money and none currently handles an error:
  `Invoices.tsx` · `Expenses.tsx` · `PaymentRuns.tsx` · `InvoiceDetail.tsx` · `CashPosition.tsx` · `IssuedReports.tsx` · `Receipts.tsx` · `Review.tsx`
- `Invoices.tsx` only — change the table container's `overflow-hidden` to `overflow-x-auto` (audit §2 P5; a one-line clipping fix, taken here because the file is already open and the money column is currently unreachable below ~700px).
- New `frontend/e2e/error-states.spec.ts` — failure-injection tests using the established `page.route` mocking pattern.
- Extend `frontend/e2e/nav.spec.ts` (or add assertions to the existing live-app specs) for the single-`<h1>` guarantee.
- `docs/DESIGN_SYSTEM.md` — record that `QueryState`'s error branch is `ErrorState`, and that `PageHeader` is the only sanctioned page-title mechanism.

**Out of scope (anti-scope-creep):**
- The remaining ~18 `useQuery` pages without an error state and the remaining ~31 hand-rolled `<h1>`s — **WO-50-UX6**. Do not widen the page list; eight is the reviewable unit.
- Any `DataTable` migration — **WO-49-UX5**. These pages keep their hand-rolled `<table>` markup in this order, apart from the single `Invoices.tsx` overflow one-liner named above. Converting a table here would make the diff unreviewable and collide with WO-49.
- Any form/label/`FormField` work — **WO-46-UX2**. Do not touch `className="label"` or `className="input"` in these files.
- Building `Tooltip` or moving `Toast` — **WO-47-UX3**.
- Design tokens, semantic colours, the `slate-400` contrast fix — **WO-48-UX4**. Do not change any colour value in this order.
- Pagination and search debounce — **WO-51-UX7**. `Invoices.tsx`'s hand-rolled prev/next stays exactly as it is.
- Bulk actions / row selection — **WO-53-UX9**.
- Deleting the legacy `card`/`input`/`label`/`badge`/`btn*` `@utility` classes — **WO-55-UX11**. This order *adds* a focus ring to `btn`; it removes nothing.
- **Any backend file, schema, route, service or migration.** This order is frontend-only. If a page appears to need a backend change to render an error correctly, stop and report — do not change the wire (§4.20).

### Files to touch

| File | Change |
|---|---|
| `frontend/src/components/ui/QueryState.tsx` | Error branch → `ErrorState` (`role="alert"`, `onRetry={query.refetch}`); import `ErrorState`, drop the now-unused `Button` import if nothing else needs it |
| `frontend/src/index.css` | `btn` `@utility` gains `focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-brand-400` |
| `frontend/src/pages/Invoices.tsx` | `PageHeader`; `QueryState` around the table; `overflow-hidden` → `overflow-x-auto` (L129); distinct empty vs error copy |
| `frontend/src/pages/Expenses.tsx` | `PageHeader` (collapses the duplicate `<h1>` at L47 and L58 into one); `QueryState` on the `pending` and `mine` queries |
| `frontend/src/pages/PaymentRuns.tsx` | `PageHeader`; `QueryState` on the run list and the payable-invoice list |
| `frontend/src/pages/InvoiceDetail.tsx` | `PageHeader` (title = invoice number); `QueryState` on the detail query |
| `frontend/src/pages/CashPosition.tsx` | `PageHeader` — **preserve WO-18's honesty copy verbatim** as `description`; `QueryState` per card |
| `frontend/src/pages/IssuedReports.tsx` | `PageHeader` (collapses its two `<h1>`s); `QueryState`; keep the existing `Tabs` usage untouched |
| `frontend/src/pages/Receipts.tsx` | `PageHeader`; `QueryState` |
| `frontend/src/pages/Review.tsx` | `PageHeader`; `QueryState` |
| `frontend/e2e/error-states.spec.ts` | **new** — failure-injection specs |
| `frontend/e2e/nav.spec.ts` | Extend with the single-`<h1>` assertion |
| `docs/DESIGN_SYSTEM.md` | Document the `QueryState`→`ErrorState` contract and `PageHeader` as the sole title mechanism |
| `docs/design/UX-AUDIT.md` | Tick WO-45 in the §7 table when complete |

> Every path above exists at `af78f38` except `frontend/e2e/error-states.spec.ts`, marked **new**. Verified.

## Implementation guidance

1. **Characterise first.** Before editing any page, add to `frontend/e2e/error-states.spec.ts` one passing test per target page that mocks a **200 with data** and asserts the current happy-path rendering (the table/cards appear, the title text is present). Run them green. These are the regression net proving steps 3-5 changed only the failure path and the title markup — not the data path. Follow the `page.route("**/api/v1/**", …)` mocking convention already established in `frontend/e2e/billing-downgrade-confirm.spec.ts:90-112` and `frontend/e2e/cash-position.spec.ts`; do not invent a new fixture mechanism.

2. **Fix the primitive before the pages.** In `QueryState.tsx`, replace the error branch's `EmptyState` with `ErrorState`:
   - keep the `errorTitle` prop name and default (`"Couldn’t load this"`) so the 7 existing call sites — including `Dashboard.tsx:39` and `Vendors.tsx` — compile and behave identically apart from gaining `role="alert"` and the error iconography;
   - pass `onRetry={query.refetch}` so `ErrorState` renders its own retry button; delete the hand-built `<Button>` currently inside `QueryState`;
   - keep `description` at `ErrorState`'s default unless a call site overrides it.
   This is the highest-leverage line in the order: it upgrades every current and future `QueryState` consumer at once.

3. **`btn` focus ring.** One utility, one line. Because `btn-primary` and `btn-ghost` both `@apply btn`, all 86 legacy call sites inherit it. Use `ring-brand-400` to match `ui/Button`'s primary variant (`ui/Button.tsx:22`) so legacy and kit buttons are visually identical when focused. **Change nothing else in `index.css`** — no colour values, no new tokens (that is WO-48).

4. **`PageHeader` on the eight pages.** Import from `../components/ui`. Map the existing markup:
   - the current `<h1>` text → `title`;
   - any subtitle paragraph → `description`;
   - the existing action row (e.g. `Invoices.tsx:60-77`'s export buttons and Upload link) → `actions`, unchanged in behaviour and order;
   - counts/badges → `meta`.
   **Hoist `PageHeader` above the loading branch** so the title is stable while data loads and exactly one `<h1>` exists per render. For `Expenses.tsx` and `IssuedReports.tsx` this is what collapses their duplicate `<h1>`s — verify by reading both branches, not by deleting one blindly.
   **`CashPosition.tsx` requires care:** WO-18 (I1.3) deliberately worded that page so the UI never implies a bank balance. Move its copy into `PageHeader`'s `description` **verbatim**. Do not shorten, summarise or "tidy" any of it, and do not move any of it into a `title` attribute. If the copy does not fit the `description` slot, keep it as a paragraph below the header rather than trimming it. `e2e/cash-position.spec.ts` must pass unmodified.

5. **`QueryState` on the eight pages.** Wrap the primary data render. Give the empty and error paths **different words** — the whole point of the order:
   - empty → `"No invoices match these filters"` (a fact about the data);
   - error → `errorTitle="Couldn’t load invoices"` (a fact about the request).
   Never reuse one string for both. Where a page runs several independent queries (`Expenses.tsx` has 9, `Receipts.tsx` 6, `Reconciliation`-style multi-card layouts), wrap **each independently-failing region** in its own `QueryState` so one failed panel does not blank the page. Preserve every existing loading affordance; where a page currently shows a bare `Loading…` cell, `QueryState`'s `loading` prop may take a `Skeleton` — that is an improvement, not a scope breach.

6. **`Invoices.tsx` overflow.** Line 129, `className="card overflow-hidden p-0"` → `className="card overflow-x-auto p-0"`. `overflow-hidden` currently clips the right-aligned Total column below roughly 700px with no way to scroll to it. This is the only table-markup change permitted in this order.

7. **Money and currency are untouched.** No `money()` call, no currency prop, no total, and no per-currency grouping may change in any file. If wrapping a render in `QueryState` appears to require restructuring a currency-grouped list, stop and report rather than restructuring — §4.14 (no aggregate sums across currencies) must remain trivially true because no summing logic is edited.

8. **Fail-open vs fail-closed.** This order introduces no gate. It changes only how an *already-failed* request is presented: previously fail-**silent** (rendered as empty), now fail-**visible** (rendered as an error with a retry). Nothing becomes more permissive; a 403 that previously showed an empty table now shows an error state, and the server remains the sole authority on access (§6 — frontend permission rendering is cosmetic). State this reasoning in the `QueryState` docstring.

## Invariants this order must preserve

- **§4.20 (frozen wire contract).** No request or response shape changes. `QueryState` reads only TanStack's client-side `isError`/`isLoading`/`data`; the `{"detail","code"}` body and `X-Request-ID` header are untouched. Proven by: no file under `backend/` is modified, and the backend baseline is byte-identical.
- **§6 (frontend gating is cosmetic).** No permission logic is added, cached or moved. A 403 renders an error state exactly like a 500; the UI draws no authorization conclusion and hides no control based on one. `Layout.tsx::filterNav` is not touched.
- **§4.9/§4.14/§4.15 (money, no cross-currency aggregation, one FX convention).** Trivially preserved — no arithmetic, no `money()` call and no currency handling is edited in any file. Proven by a diff review showing zero changes inside any `money(`/`q2`/currency expression.
- **§4.19 (AI advisory).** No AI seam is touched. `CaptureReview.tsx`/`CaptureQueue.tsx` are not in scope, and no advisory badge or confirm gate is altered.
- **DoD §7.2 (loading, empty and error states).** This order is the invariant being *restored* on eight screens; WO-50 completes the rest.

## Database / migration impact

**None.** This order touches no backend file, no model, no schema and no migration. `alembic heads` must still return a single head, unchanged.

## Testing requirements

The standard authorization / cross-tenant / financial-correctness / concurrency cases in the master context §8 attach to **backend** work orders. This order modifies **no backend file and no money path**, so those categories have no applicable case here — stated explicitly rather than silently skipped, per the reporting rules. The equivalent adversarial coverage for a presentation-layer order is failure injection, which is mandatory below.

**`frontend/e2e/error-states.spec.ts` (new)** — using the `page.route` mocking convention from `billing-downgrade-confirm.spec.ts:90-112`:

- `test_invoices_500_shows_error_not_empty` — mock `GET /invoices` → 500; assert an element with `role="alert"` is visible, that it contains "Couldn't load", that a "Try again" button is present, and — **critically** — that the text "No invoices found" is **absent**.
- `test_invoices_empty_shows_empty_not_error` — mock `GET /invoices` → 200 `{items: [], total: 0}`; assert the empty copy is visible and **no** `role="alert"` element exists. (The both-sides pair for the same gate.)
- `test_invoices_403_shows_error_state` — mock 403 with `{"detail": "...", "code": "forbidden"}`; assert the same error state renders (the UI draws no authorization conclusion).
- `test_error_state_retry_refetches` — mock 500 on first call and 200 on the second; click "Try again"; assert the table renders.
- `test_review_500_shows_error` and `test_payment_runs_500_shows_error` — the same first assertion for the approval queue and the payment-run list, the two other pages where a silent empty state is most costly.
- `test_partial_failure_does_not_blank_the_page` — on `Expenses.tsx`, mock the "awaiting my approval" query → 500 and "my reports" → 200; assert one region shows `role="alert"` **and** the other still renders its rows.

**Extend `frontend/e2e/nav.spec.ts`:**
- `test_every_audited_page_has_exactly_one_h1` — for each of the eight routes, assert `page.getByRole("heading", { level: 1 })` resolves to exactly one element (`toHaveCount(1)`), covering the `Expenses`/`IssuedReports` duplicate-`<h1>` fix.

**Must pass unmodified** (behaviour-preserving proof, per template rule 4): `frontend/e2e/cash-position.spec.ts`, `frontend/e2e/dashboard.spec.ts`, `frontend/e2e/smoke.spec.ts`, `frontend/e2e/masters.spec.ts`, `frontend/e2e/upload-duplicate.spec.ts`. If any of these needs editing, the change went further than the order allows — stop and report.

**Visual regression:** `npm run test:vr` targets `/design` only. The `QueryState` and `btn` changes reach `/design`, so **snapshots are expected to change** in exactly two ways: focus rings on legacy buttons, and the error-state rendering in any fixture that shows one. Review each changed snapshot by eye and state in the report which ones changed and why before running `test:vr:update`. An unexplained snapshot diff is a defect, not a rebaseline.

## Acceptance criteria (verifiable checklist)

- [ ] `grep -n "EmptyState" frontend/src/components/ui/QueryState.tsx` shows `EmptyState` used **only** in the empty branch; the error branch renders `ErrorState`.
- [ ] `grep -c "focus-visible" frontend/src/index.css` ≥ 1 and the `btn` utility contains `focus-visible:ring-2`.
- [ ] For each of the eight files, `grep -c "<h1" <file>` returns **0** (the `<h1>` now comes from `PageHeader`) and `grep -c "PageHeader" <file>` returns ≥ 1.
- [ ] `grep -c "overflow-hidden" frontend/src/pages/Invoices.tsx` returns **0**.
- [ ] With `GET /invoices` mocked to 500, `/invoices` renders an element with `role="alert"` containing "Couldn't load invoices", and the string "No invoices found" appears **nowhere** in the DOM.
- [ ] With `GET /invoices` mocked to 200 `{items:[],total:0}`, `/invoices` renders the empty copy and **no** element with `role="alert"`.
- [ ] Clicking "Try again" after a mocked 500→200 sequence renders the invoice rows.
- [ ] On `/expenses` with one of two queries mocked to 500, both a `role="alert"` region and a populated region are simultaneously visible.
- [ ] `page.getByRole("heading", { level: 1 })` has count exactly 1 on each of `/invoices`, `/expenses`, `/payment-runs`, `/cash-position`, `/issue/reports`, `/receipts`, `/review`, and an invoice detail route.
- [ ] `frontend/e2e/cash-position.spec.ts` passes **unmodified**, and the WO-18 honesty copy is present in `CashPosition.tsx` character-for-character (`git diff` shows the strings moved, not edited).
- [ ] `cd frontend && npm run build` → exit 0.
- [ ] `cd backend && python -m pytest -q` returns the **exact** figure recorded at the top of this order before any edit, and `git diff --name-only` lists **no** path under `backend/`.

## Rollback strategy

Pure code revert — `git revert` of the single commit restores the previous behaviour exactly. **No migration, no data change, no one-way effect**: nothing is written, revoked or corrected, so there is no state to unwind and no downgrade to test.

Narrow mitigations if only part regresses:
- If the `btn` focus ring causes an unforeseen layout shift (it should not — `ring` does not affect layout box size), revert that single line in `index.css` and keep everything else.
- If one page's `QueryState` wrapping proves wrong, revert that one file; the pages are independent and the `QueryState` primitive fix stands alone and benefits the other seven.
- If the `QueryState`→`ErrorState` change surprises an existing consumer, the `errorTitle` prop is unchanged, so reverting only `QueryState.tsx` restores prior rendering while leaving the eight pages correctly wired.

## Documentation to update

- `docs/DESIGN_SYSTEM.md` — §"Overlays & feedback": state that `QueryState`'s error branch renders `ErrorState` (with `role="alert"`), that `EmptyState` means "no data, that's fine" and `ErrorState` means "the request failed", and that these must never share copy. §"Structure & navigation": state that `PageHeader` is the **only** sanctioned mechanism for a page title and that a page has exactly one `<h1>`.
- `docs/design/UX-AUDIT.md` — tick WO-45 in the §7 table.
- **No ADR is contradicted.** This order restores an existing rule (DoD §7.2); it does not change one. No ADR needs amendment.

## Self-verification block

```bash
# Frontend — the only tree this order touches
cd /home/user/Bid_it/frontend
npm run build                                   # tsc --noEmit + vite build, must exit 0
npx playwright test e2e/error-states.spec.ts e2e/nav.spec.ts
npx playwright test e2e/cash-position.spec.ts e2e/dashboard.spec.ts \
                   e2e/smoke.spec.ts e2e/masters.spec.ts e2e/upload-duplicate.spec.ts
npm run test:vr                                 # review each /design diff BEFORE any rebaseline

# DEMONSTRATE the fix, don't just assert tests pass:
# 1. the error branch no longer renders an EmptyState
grep -n "EmptyState\|ErrorState" src/components/ui/QueryState.tsx
# 2. the legacy button utility now has a focus ring
grep -n -A3 "@utility btn " src/index.css
# 3. zero hand-rolled <h1> remains in the eight audited pages (expect no output)
grep -n "<h1" src/pages/{Invoices,Expenses,PaymentRuns,InvoiceDetail,CashPosition,IssuedReports,Receipts,Review}.tsx
# 4. all eight now use PageHeader (expect 8 lines)
grep -l "PageHeader" src/pages/{Invoices,Expenses,PaymentRuns,InvoiceDetail,CashPosition,IssuedReports,Receipts,Review}.tsx
# 5. the clipping container is gone (expect no output)
grep -n "overflow-hidden" src/pages/Invoices.tsx
# 6. WO-18's honesty copy survived verbatim — strings moved, not reworded
git diff src/pages/CashPosition.tsx | grep '^[-+].*bank'

# Backend — proves this order did not touch it
cd /home/user/Bid_it/backend && . .venv/bin/activate
python -m pytest -q                             # must equal the baseline recorded at the top of this order
cd /home/user/Bid_it && git diff --name-only | grep '^backend/' || echo "OK: no backend file touched"
```

<!-- ═══════════════ END: WO-45 ═══════════════ -->
