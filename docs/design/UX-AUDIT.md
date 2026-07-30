# InvoiceIQ — UI/UX audit and redesign plan (Phase 1)

**Status:** Phase 1 — audit and plan only. No application code was changed by this document.
**Branch audited:** `claude/bidit-invoice-data-analytics` at `af78f38` (WO-44/E1.4).
**Baseline verified:** `cd frontend && npm run build` → exit 0 (`tsc --noEmit` + `vite build` both clean). Every claim below was read out of a file in the tree at that commit; no figure is estimated.
**Scope:** `frontend/` only. Backend, wire contract, tenancy, authz and the AI-advisory invariants are untouched and constrain every recommendation (see §0).

---

## 0. Constraints this plan is written inside

Read `docs/plan/shared/00_MASTER_CONTEXT.md` §4 and §6 before implementing any slice. Three of its rules shape the whole plan and are repeated here so no slice can drift:

| Invariant | What it forbids in UX work |
|---|---|
| §6 — *"the frontend's permission-aware rendering is **cosmetic only**. The server is the control. Never treat a hidden nav item as a security boundary."* | No proposal here may move an authorization decision into the SPA, cache a permission, or "optimise" a route guard. `filterNav` in `Layout.tsx:25-35` stays cosmetic. |
| §4.19 — AI is opt-in, default-off, advisory, never silently mutating a financial record | No redesign may present an AI-derived figure as confirmed, remove a human confirm gate, or make an advisory badge look authoritative. The capture-review screens (WO-12/13) already model this correctly — copy their pattern, don't dilute it. |
| §4.20 — the wire contract is frozen (`{"detail", "code"}` + `X-Request-ID`) | Error-state work consumes the existing shape via `lib/api.ts::apiError`. No slice may ask the backend to change an error body to suit the UI. |

Additionally, §4.14/§4.15 (no cross-currency aggregation, one FX convention) mean **no UI slice may sum a column across currencies to make a table look tidier.** Where a table shows mixed currencies it must keep them separate — a visual-consistency change must never become a numeric one.

And from the task framing: **one engineer plus AI assistance.** Every slice below is sized to be independently shippable and independently revertable. There is no "big-bang redesign" option in this plan, and none is recommended.

---

## 1. Inventory

### 1.1 Routes

`frontend/src/App.tsx` declares **55 routes** in three tiers.

**Public / auth (6)** — eagerly loaded, no shell:
`/login` · `/sso/callback` · `/accept-invite` · `/verify-email` · `/forgot-password` · `/reset-password`

**Authenticated app (39 + catch-all)** — all lazy-loaded, wrapped in `ProtectedRoute` → `Layout`:

| Area | Routes |
|---|---|
| Overview | `/` |
| Payables | `/invoices` · `/invoices/:id` · `/invoices/:id/review` · `/review` · `/captures` · `/captures/:runId` · `/upload` · `/email` · `/payment-runs` · `/vendors` |
| Expenses | `/expenses` · `/expenses/:id` · `/expenses/policy` · `/reimbursements` |
| Receivables | `/issue` · `/issue/reports` · `/customers` · `/receipts` · `/reconciliation` · `/partners` · `/dunning` · `/issuer` |
| Insights | `/explore` · `/benchmark` · `/fx` · `/cash-position` · `/budget` |
| Workspace / admin | `/tax-codes` · `/currencies` · `/cost-objects` · `/documents` · `/team` · `/access` · `/sessions` · `/audit` · `/billing` · `/platform` · `/settings` |

**Design showcase (10)** — `/design/gallery` + `/design` with 9 child routes, public, fixtures-only, no auth, no backend (`App.tsx:83-109`).

**45 page components** live in `src/pages/`. The count exceeds the 39 in-shell routes because the 6 auth pages are also in `pages/`.

**Orphaned from the sidebar (2):** `/issuer` and `/reimbursements` have routes and pages but no entry in `LIVE_NAV`. They are reachable only by in-page links — `/issuer` from `Issue.tsx:46,60,698` and `Settings.tsx:86`; `/reimbursements` from `Expenses.tsx:60`. WO-17 named this explicitly as out of scope ("*Adding `/reimbursements`/`/issuer` to the nav — pre-existing gap … not part of this IA change*", `WO-17-I12.md:74`). **It is still open.** Company details — the legal-entity record every issued invoice depends on — cannot be reached from the navigation at all.

### 1.2 Current navigation — exactly as shipped

Two nav datasets exist and they are **not** duplicates:

- `src/components/shell/nav.ts::NAV_GROUPS` — 9 fixture destinations under `/design/*`. Powers the showcase only.
- `src/lib/nav.ts::LIVE_NAV` — **the live IA**. 5 groups, **31 items**, plus a conditional 32nd (`Platform`, appended in `Layout.tsx:47-52` when `user.is_platform_admin`).

Live group sizes: **Overview 1 · Payables 9 · Receivables 7 · Insights 5 · Workspace 9.**

Each item carries three cosmetic gating flags — `module`, `admin`, `owner` — filtered in `Layout.tsx::filterNav` (lines 25-35), which drops empty groups. Icons are inline 18px SVG paths built by `shell/nav.ts::icon()`; there is no icon dependency.

`src/components/shell/AppShell.tsx` is the shell both `Layout.tsx` (live) and `design/DesignLayout.tsx` (fixtures) mount. As shipped it provides:

- a **fixed left sidebar**, `w-64`, `hidden … lg:flex` (line 131) — brand mark, org switcher, scrolling nav list;
- a **mobile drawer** — hamburger at line 149-159 (`lg:hidden`, `aria-label="Open navigation menu"`, `aria-expanded`) opening the same `NavList` inside `ui/Drawer` (line 140-143), auto-closing on navigate;
- a **sticky top bar** with breadcrumbs, optional search (omitted live — no search backend), and `UserMenu`;
- a **skip link** to `#main` (line 123-128);
- `<main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-4 py-6 …">` (line 180).

Optional props `entities`/`onSwitchEntity` and `search`/`onSearch` are deliberately **not** passed by the live `Layout` because no legal-entity model and no search endpoint exist. That restraint is correct and should be preserved — see §3.

### 1.3 Reusable components

**`src/components/ui/` — 30 files, ~1,992 LOC, exported from a single barrel `index.ts` (61 lines).** This kit is genuinely well built. `DataTable.tsx` alone provides `overflow-x-auto`, `scope="col"`, `aria-sort`, an `sr-only` caption, `aria-busy`, skeleton rows, a built-in empty state, and keyboard-activatable rows. `QueryState.tsx` standardises the loading/error/empty triad with a retry action. `Form.tsx::FormField` generates the control id, links the `<label>`, and wires `aria-describedby` / `aria-invalid` / `aria-required` with a `role="alert"` error message.

| Component | Path | LOC |
|---|---|---|
| Button | `ui/Button.tsx` | 58 |
| FormField / TextInput / Select / Textarea / `inputClass` | `ui/Form.tsx` | 129 |
| CurrencyInput · DateInput · TaxRateInput · FileUpload | `ui/{CurrencyInput,DateInput,TaxRateInput,FileUpload}.tsx` | 77 / 47 / 90 / 151 |
| DataTable (+ `useSort`) | `ui/DataTable.tsx`, `ui/useSort.ts` | 160 / 41 |
| Pagination · SearchInput · FilterBar (+`FilterSelect`,`FilterChip`) | `ui/{Pagination,SearchInput,FilterBar}.tsx` | 57 / 69 / 89 |
| Modal · Drawer · ConfirmDialog · Portal · `useFocusTrap` | `ui/{Modal,Drawer,ConfirmDialog,Portal}.tsx`, `ui/useFocusTrap.ts` | 118 / 116 / 58 / 31 / 83 |
| EmptyState · ErrorState · QueryState · Spinner · Skeleton | `ui/{EmptyState,ErrorState,QueryState,Spinner,Skeleton}.tsx` | 22 / 42 / 49 / 20 / 16 |
| PageHeader · Breadcrumbs · Card/StatCard · Tabs/TabPanel · Timeline | `ui/{PageHeader,Breadcrumbs,Card,Tabs,Timeline}.tsx` | 40 / 48 / 47 / 135 / 52 |
| Badge/Dot · StatusBadge (+`STATUS_MAP`) | `ui/{Badge,StatusBadge}.tsx` | 30 / 56 |

**Outside the kit** (`src/components/`, not in the barrel): `Toast.tsx` (toast provider + module-level `toast` bridge), `Charts.tsx` (Recharts wrappers), `KpiCard.tsx`, `Field.tsx`, `SettingRow.tsx`, `Switch.tsx`, plus the structural `Layout.tsx`, `ModuleGate.tsx`, `ProtectedRoute.tsx`.

### 1.4 Styling system

Tailwind **v4** (`tailwindcss@^4.3.3`, `@tailwindcss/postcss`). **There is no `tailwind.config.js`** — configuration is CSS-first in `src/index.css`.

The entire design-token set is **five colours**:

```css
@theme {
  --color-brand-50:  #eef4ff;  --color-brand-100: #d9e6ff;
  --color-brand-500: #3b6ef2;  --color-brand-600: #2f57d4;
  --color-brand-700: #2545ab;
}
```

There are **zero** semantic tokens (no success/warning/danger/info), **zero** spacing, radius, shadow, z-index or typography tokens (`grep -c "success\|warning\|danger\|--spacing\|--radius\|--font" src/index.css` → 0). Everything else is raw Tailwind palette classes (`slate-*`, `rose-*`, `emerald-*`, `amber-*`, `sky-*`, `indigo-*`, `violet-*`) written inline at ~2,000 call sites.

`index.css` also defines **six legacy `@utility` classes** — `card`, `btn`, `btn-primary`, `btn-ghost`, `input`, `label`, `badge` — which are a *parallel, competing* system to the `ui/` components (see §2.3). It defines four keyframe animations correctly gated behind `prefers-reduced-motion: no-preference`.

`color-scheme: light` is hard-set and **`grep -rn "dark:" src/` returns 0**. No dark mode anywhere. For an EU B2B finance tool this is a defensible deliberate choice, not a defect — it is recorded here as a **non-goal**, not a backlog item.

### 1.5 Semantic-colour fragmentation

Status colour is expressed **eight different ways**:

| Vocabulary | Location |
|---|---|
| `STATUS_STYLES` | `lib/format.ts:33` |
| `ISSUED_STATUS_STYLES` | `lib/format.ts:41` |
| `VALIDATION_STYLES` | `lib/format.ts:64` |
| `METHOD_STYLES` | `lib/format.ts:96` |
| `INBOUND_STATUS_STYLES` | `lib/format.ts:111` |
| `SEVERITY_STYLES` | `lib/format.ts:120` |
| `EXPENSE_STATUS_STYLES` | `lib/format.ts:126` |
| `STATUS_MAP` (tone-based, the *designed* one) | `ui/StatusBadge.tsx:10` |

Seven of the eight are raw Tailwind class strings; only `StatusBadge`'s uses the `Tone` abstraction. `paid` is `bg-emerald-100 text-emerald-700` in two of them and defined separately in both.

### 1.6 Responsive and accessibility posture as it stands

**Responsive.** The shell is genuinely responsive (§1.2). Page *content* mostly is not. `main` is capped at `max-w-6xl` (1152px) — narrow for a data-dense finance product on a 1440–1920px finance workstation, which is the primary work surface for this persona. Of the **32 pages containing a raw `<table>` (43 tables total)**, only **14** wrap one in `overflow-x-auto`; the rest clip or overflow on narrow viewports (§2.5).

**Accessibility.** The kit is accessible; the pages largely are not. Across all 45 pages: **13 ARIA attributes total** (9 `aria-label`, 1 each of `aria-selected`, `aria-live`, `aria-hidden`, `aria-describedby`), **160 raw `<button>` elements** against **3** occurrences of `focus-visible`, and **53 `className="label"` labels against 6 `htmlFor` attributes**. Details in §6.

**Testing.** 10 Playwright specs in `frontend/e2e/` (`smoke`, `nav`, `dashboard`, `masters`, `cash-position`, `upload-duplicate`, plus three confirm-dialog specs and `visual.spec.ts`). `visual.spec.ts` targets `/design` **only** — the live app has no visual-regression baseline.

---

## 2. The ten highest-impact problems

Ordered by severity, then by blast radius. Every item cites a file read at `af78f38`.

---

### P1 — CRITICAL — A failed data request renders as "you have no data"

**Current problem.** 39 pages call `useQuery`. **26 of them never reference `isError`, `ErrorState` or `QueryState`.** The canonical case is `pages/Invoices.tsx:43` — `const { data, isLoading } = useQuery<InvoiceList>({…})`. `isError` is not destructured. When `GET /invoices` returns 500, 403 or times out, `data` stays `undefined`, `isLoading` goes false, and line 171-175 renders:

```tsx
{data && data.items.length === 0 && !isLoading && (
  <tr><td colSpan={…} className="px-4 py-10 text-center text-slate-400">No invoices found.</td></tr>
)}
```

The failure is indistinguishable from an empty ledger. The same pattern is in `Expenses.tsx`, `PaymentRuns.tsx`, `InvoiceDetail.tsx`, `CashPosition.tsx`, `Explore.tsx`, `IssuedReports.tsx`, `Fx.tsx`, `Review.tsx`, `ReviewInvoice.tsx`, `Team.tsx`, `Audit.tsx`, `Access.tsx`, `Platform.tsx` and 13 more.

**Why it's a problem.** This is the single most damaging defect in the frontend, and it is a *financial-correctness* defect, not a cosmetic one. The product's stated promise is "(c) produces an audit-ready financial record" (master context §1) and its stated failure mode is "**every one of those promises dies if a number is wrong**". A finance lead who opens Invoices during a partial outage sees an empty AP ledger and reasonably concludes there is nothing to pay. It also silently violates the project's own Definition of Done §7.2 — *"Loading, empty and error states on every new screen"* — on 26 screens. There is no toast fallback either: the axios interceptor raises toasts only for some paths, and a rendered-empty table produces no notification at all.

**Recommended solution.** Wrap each query's render in the existing `ui/QueryState` (`QueryState.tsx`), which already distinguishes error → `EmptyState` + Retry, loading → skeleton, empty → empty state. No new component is needed. Where a page runs several queries, wrap the primary one and give secondary panels their own `QueryState`. The empty copy must be distinct from the error copy ("No invoices match these filters" vs "Couldn't load invoices").

**Expected improvement.** Removes an entire class of silent-wrong-state incidents; brings 26 screens into compliance with DoD §7.2; gives users a Retry affordance instead of a dead end.

**Difficulty.** **M — 4 days** for the 26 pages (mechanical, ~1 hour each including a test). The top 8 money-bearing pages are **S — 1.5 days** and should ship first.

---

### P2 — CRITICAL — Form controls are not programmatically labelled

**Current problem.** Across `src/pages/` there are **91 `className="input`** occurrences and **53 `className="label"`** occurrences, against **6** `htmlFor` attributes in total. The `label` utility (`index.css:49-51`) is purely visual — it applies `mb-1 block text-sm font-medium text-slate-600` and creates no programmatic association. `pages/Login.tsx:55-72` — the first screen any user sees — has five such pairs:

```tsx
<label className="label">Email</label>
<input className="input" type="email" value={email} … required />
<label className="label">Password</label>
<input className="input" type="password" … />
```

No `id`, no `htmlFor`, and no `autoComplete` on either field. `pages/Issue.tsx` has 12 of these, `Settings.tsx` 4, `Expenses.tsx` 3. Worse, `Expenses.tsx:280-289` renders an inline editable grid where every cell is a bare `<input className="input">` inside a `<td>` with no accessible name at all — a data-entry surface for spend amounts and VAT.

**Why it's a problem.** A screen-reader user hears "edit text, blank" for the login password field and for every amount cell in the expense grid. This is a WCAG 2.1 **1.3.1 Info and Relationships** and **4.1.2 Name, Role, Value** failure at Level A — the tier that EU accessibility procurement (EN 301 549, which follows WCAG 2.1 AA) treats as non-negotiable. For a product sold to EU SMEs and accountancy practices, an inaccessible login screen is a procurement blocker, not a nice-to-have. The missing `autoComplete="email"` / `autoComplete="current-password"` additionally breaks password-manager autofill for every user, accessible or not.

**Recommended solution.** `ui/Form.tsx::FormField` already solves this completely — it calls `useId()`, sets `htmlFor`, and wires `aria-describedby`/`aria-invalid`/`aria-required`. Replace `<label className="label">` + `<input className="input">` pairs with `<TextInput>` / `<Select>` / `<Textarea>`. Add `autoComplete` on the auth pages. For the `Expenses` inline grid, each cell input needs an `aria-label` derived from its column header (the grid is too dense for visible labels).

**Expected improvement.** Closes a Level-A conformance gap on the auth flow and every form; restores password-manager autofill; removes the visual/programmatic-label divergence permanently, because `FormField` cannot be used incorrectly.

**Difficulty.** **M — 4 days.** Auth pages + the top 3 forms are **S — 1.5 days** and carry most of the value.

---

### P3 — HIGH — The design system is built, documented, and almost entirely unused

**Current problem.** The kit described in §1.3 is exercised by the fixtures showcase far more than by the product. Measured adoption across the 45 live pages:

| Primitive | Live-page adoption | Competing legacy usage |
|---|---|---|
| `PageHeader` | **0 pages** | 53 hand-rolled `<h1>` |
| `FilterBar` | **0 pages** | hand-rolled filter rows |
| `TextInput` / `Select` | **2 uses** | 91 `className="input` |
| `Pagination` | **1 page** (`CaptureQueue`) | hand-rolled prev/next |
| `Tabs` | **1 page** (`IssuedReports`) | stacked sections |
| `DataTable` | **3 pages** (`Audit`, `Explore`, `Team`) | **43 raw `<table>` in 32 pages** |
| `Modal` / `Drawer` | 3 pages | ad-hoc conditional blocks |
| `QueryState` | 7 pages | see P1 |
| `EmptyState` | 10 pages | `<td>` placeholder text |
| `Button` | 63 uses | 86 `btn-primary`/`btn-ghost` |
| `Card` | — | 103 `className="card` |

`PageHeader` is the sharpest illustration: **all 19 references to it are in `src/design/`** (the 9 fixture routes plus `Gallery.tsx`). Not one live page imports it. 25 of 45 pages import nothing from `components/ui` at all.

**Why it's a problem.** This is the *root cause* of P4, P5, P6, P8 and P10 — every one of those is a symptom of pages hand-rolling what the kit already provides. It also means the design system's quality is invisible to customers: the accessible, responsive, skeleton-loading `DataTable` renders three screens while 32 screens render markup with none of those properties. And it is actively decaying — `/design` (1,123 LOC of fixtures) is the only consumer of `PageHeader`, `FilterBar` and `Tabs`, so those primitives are maintained against fixtures rather than against real data shapes, and will drift.

**Recommended solution.** Reframe the redesign as a **migration programme, not a build programme.** Do not build a new design system — adopt the one that exists, page by page, in dependency order (states → tables → forms → chrome). Add a lint-style guard so new pages cannot reintroduce the legacy path. Retire the six legacy `@utility` classes only *after* migration, not before.

**Expected improvement.** Converts ~2,000 lines of duplicated markup into composition; every migrated page inherits the kit's a11y and responsive behaviour for free; a single future restyle then reaches the whole app.

**Difficulty.** **XL — 15-20 days total**, but it decomposes cleanly into independently shippable S/M slices (§7). No slice needs the others to land first.

---

### P4 — HIGH — Four different page-title treatments, and duplicate `<h1>`s

**Current problem.** Page headings across `src/pages/`:

| Class string | Count |
|---|---|
| `text-2xl font-semibold tracking-tight` | 27 |
| `text-xl font-semibold` | 16 |
| `text-lg font-semibold` | 9 |
| `mb-1 text-lg font-semibold` | 1 |

`Invoices.tsx:59` is `text-2xl`; `Vendors.tsx:96` and `PaymentRuns.tsx:122` are `text-xl`. Nothing distinguishes those pages — the difference is drift, not hierarchy. Worse, `Expenses.tsx` renders `<h1>Expenses</h1>` **twice** (lines 47 and 58) in two different branches, and several pages render an `<h1>` in a loading branch and another in the loaded branch. Page-level actions are laid out ad hoc: `Invoices.tsx:58-78` builds a bespoke flex row with an inline "Export:" label and three unlabelled-by-role buttons.

**Why it's a problem.** The title is the first thing a user reads on every screen; four sizes make the product feel assembled rather than designed, which is precisely the "avoid AI-generated-dashboard" failure mode the brief calls out. Structurally, a document whose top-level heading changes size per route gives screen-reader users an unstable landmark, and duplicate `<h1>`s break the single-main-heading convention assistive tech relies on for orientation.

**Recommended solution.** `ui/PageHeader.tsx` (40 LOC) already takes `title`, `description`, `actions` and `meta`. Adopt it on all 45 pages; it renders exactly one `<h1>` and gives actions a consistent slot. Hoist it above the loading branch so the title is stable while data loads.

**Expected improvement.** One title treatment, one actions slot, exactly one `<h1>` per route, stable during loading. Highest visual-coherence gain per hour of any item in this list.

**Difficulty.** **S — 2 days** for all 45 pages. This is the cheapest high-impact win available.

---

### P5 — HIGH — Wide financial tables clip on narrow viewports

**Current problem.** 32 pages contain a raw `<table>`; only 14 wrap one in `overflow-x-auto`. `Invoices.tsx:129` is the worst case because it does not merely omit the scroll container, it **actively clips**:

```tsx
<div className="card overflow-hidden p-0">
  <table className="w-full text-sm">
```

`overflow-hidden` on a container holding a 5–6 column table with two right-aligned money columns means that below roughly 700px CSS width the Total column is cut off with **no way to scroll to it**. Combined with `main`'s `max-w-6xl` cap and `px-4`, the usable table width on a phone is ~320px. `Vendors.tsx:129,206` and `PaymentRuns.tsx:152` have the same shape without the clip — they overflow the viewport instead, producing a horizontally scrolling *page*.

**Why it's a problem.** The amount column is the one column that must never be unreachable in an accounts-payable table. Approvers and finance leads do check invoice queues from a phone. `ui/DataTable.tsx:78` already wraps every table in `overflow-x-auto rounded-xl border …` — the correct behaviour exists and these pages simply don't use it.

**Recommended solution.** Migrate table pages to `DataTable`, which fixes clipping, sorting semantics, loading skeletons and the empty state in one move. As an immediate stop-gap for the highest-traffic pages, change `overflow-hidden` → `overflow-x-auto` (a one-line fix with no other behavioural effect). Separately, reconsider `max-w-6xl` for table-heavy routes (§8).

**Expected improvement.** No money column is ever unreachable; tables degrade to horizontal scroll inside their own card rather than scrolling the page body.

**Difficulty.** **S — 1 day** for the stop-gap across all 32 pages; folded into the `DataTable` migration (**L**) for the real fix.

---

### P6 — HIGH — Pagination is hand-rolled, inconsistent, and sometimes absent

**Current problem.** `ui/Pagination.tsx` (57 LOC) exists and is used by exactly **one** page (`CaptureQueue.tsx`). `Invoices.tsx:180-193` hand-rolls it:

```tsx
<button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
<span>Page {page} / {totalPages}</span>
<button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>Next</button>
```

`PAGE_SIZE` is a module constant (`Invoices.tsx:11`) with no page-size control. Several list pages have no pagination at all and render whatever the API returns. Additionally, `Invoices.tsx:44` puts the raw search string `q` in the query key with no debounce — every keystroke fires a request.

**Why it's a problem.** Page size is a workflow decision in AP — an approver clearing a backlog wants 100 rows, not 20 — and it is unavailable. Inconsistent pagination controls mean the same interaction sits in a different place with different affordances on each list. The undebounced search generates one request per keystroke against a tenant-filtered, RLS-enforced query; it is wasteful at best and a self-inflicted load pattern at worst.

**Recommended solution.** Adopt `ui/Pagination` everywhere a list paginates; add a page-size selector to it once, centrally. Debounce search inputs (~300ms) — `ui/SearchInput.tsx` is the natural place, so every consumer inherits it.

**Expected improvement.** One pagination control app-wide; user-controlled page size; request volume on search drops by roughly an order of magnitude.

**Difficulty.** **M — 3 days** including the page-size addition to `Pagination` and the debounce in `SearchInput`.

---

### P7 — MEDIUM — No Tooltip primitive; native `title=""` used instead

**Current problem.** There is **no Tooltip component** in `ui/` (the `Tooltip` hits in `components/Charts.tsx` are Recharts' chart tooltip, unrelated). Pages use the native `title` attribute instead — 21+ occurrences, concentrated in exactly the places where explanation matters most: `CaptureReview.tsx` (7), `CashPosition.tsx` (4), `Currencies.tsx` (2), `Audit.tsx` (2), plus `Invoices.tsx:68` (`title={`Export the invoice ledger for ${e.label}`}`).

**Why it's a problem.** Native `title` never appears on touch, is not reliably announced by screen readers, cannot be styled, and has a ~1s browser-controlled delay. `CashPosition.tsx` is the acute case: WO-18 (I1.3) deliberately relabelled that page so the UI "never implies a bank balance", and part of that honesty work is carried in `title` attributes — an explanation a mobile user can never see. A disclosure that only appears on desktop hover is not a disclosure. It is also a hard prerequisite for the collapsed sidebar rail in §3.

**Recommended solution.** Build `ui/Tooltip.tsx` on the existing `Portal` + `useFocusTrap` infrastructure: hover **and** focus triggered, `role="tooltip"` + `aria-describedby`, Escape to dismiss, touch-tap support. Replace the `title` attributes that carry meaning (leave purely decorative ones). For load-bearing financial caveats like `CashPosition`'s, prefer persistent inline helper text over a tooltip entirely.

**Expected improvement.** Explanations reach touch and screen-reader users; unblocks the sidebar rail; removes the last unstyleable UI surface.

**Difficulty.** **S — 2 days** (component + tests + replacing the meaningful call sites).

---

### P8 — MEDIUM — Focus indication is inconsistent across ~160 controls

**Current problem.** `src/pages/` contains **160 raw `<button>` elements** and just **3** occurrences of `focus-visible`. The kit's `Button` defines `focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-offset-1` with a per-variant ring colour (`ui/Button.tsx:17-27`), and `AppShell`'s nav links and hamburger carry `focus-visible:ring-brand-300`. Raw `<button>`s and the `btn-primary`/`btn-ghost` utility classes (`index.css:33-43`) define **no** focus ring — `btn` sets only `transition disabled:opacity-50` — so they fall back to the user-agent default outline.

**Why it's a problem.** Keyboard users get a brand-blue 2px ring on some controls and a browser default outline on others *within the same page* — `Invoices.tsx` mixes `btn-ghost` export buttons (line 67), a `btn-primary` link (line 76), a raw `<button>` filter chip (line 113) and raw prev/next buttons (line 183). Inconsistent focus is a WCAG 2.4.7 (AA) risk and, more practically, makes keyboard navigation feel broken to power users — and finance reviewers clearing an approval queue are keyboard power users.

**Recommended solution.** Add the focus ring to the `btn` `@utility` in `index.css` — a **one-line change that immediately fixes 86 call sites** — then migrate raw `<button>`s to `ui/Button` during the page migrations.

**Expected improvement.** Uniform, visible, brand-consistent focus on every interactive control. The `index.css` line alone is the single highest-leverage accessibility fix in the codebase.

**Difficulty.** **S — 0.5 days** for the utility fix; the rest rides along with §7's page migrations.

---

### P9 — MEDIUM — `Settings.tsx` is a 721-line single scroll with eight sections and no form semantics

**Current problem.** `pages/Settings.tsx` is 721 lines rendering 8+ `<section>` blocks stacked vertically — Modules, Invoice validation, Single sign-on (OIDC), Right to erasure (GDPR), Data retention & legal hold, and more — each with an `<h2 className="text-sm font-semibold text-slate-600">`. There is **no `<form>` element anywhere in the file**; every save is a `<button className="btn-primary" onClick={() => save.mutate()}>`. `ui/Tabs.tsx` exists (135 LOC) and the fixture `design/routes/Settings.tsx:43` demonstrates the intended tabbed treatment — the live page does not use it.

**Why it's a problem.** Destructive, irreversible administrative operations — GDPR erasure (line 277+) and retention purge (line 377+) — sit in the same undifferentiated scroll as toggling a display module, reachable by accidental scroll with no navigational separation. Without a `<form>`, pressing Enter in any field does nothing, there is no native validation, and there is no submit semantics for assistive tech. An `<h2>` at `text-sm` is also smaller than the body text it introduces, inverting the visual hierarchy.

**Recommended solution.** Split into `ui/Tabs` panels (General · Modules · Validation · SSO · Data & retention), with the destructive Data & retention panel visually and structurally separated and every destructive action behind the existing `ui/ConfirmDialog`. Wrap each panel's fields in a real `<form onSubmit>`.

**Expected improvement.** Destructive admin actions stop sharing a scroll with cosmetic toggles; keyboard submit works; section headings regain a correct type scale.

**Difficulty.** **M — 3 days** (large file, several independent mutations to preserve carefully).

---

### P10 — MEDIUM — Bulk actions and row selection exist on one page and nowhere else

**Current problem.** `PaymentRuns.tsx:114,166` implements row selection with checkboxes and a live `selectedTotal` in the primary action label (`Create run ({money(selectedTotal, "EUR")})`, line 149) — a genuinely good pattern. `Expenses.tsx:162` has checkboxes for a different purpose (build a report from card transactions), with no select-all. `Invoices.tsx`, `Vendors.tsx`, `Customers.tsx`, `Receipts.tsx` and `Review.tsx` have **no** selection at all. There is no shared selection hook, no bulk-action bar component, and no select-all/indeterminate header checkbox anywhere.

**Why it's a problem.** Approving, exporting or scheduling invoices one at a time is the dominant time cost in AP for the target persona, and `/review` — the approval queue — is exactly where bulk action is most valuable and entirely absent. Two pages having incompatible selection idioms means users cannot transfer the interaction between screens.

**Recommended solution.** Extract a `useSelection` hook and a `BulkActionBar` component from the working `PaymentRuns` implementation (do not design a new one — promote the one that already works), including a select-all/indeterminate header checkbox in `DataTable`. Roll out to `/review` first. **Note:** any bulk approval must still respect segregation of duties (master context §4.8) — the server refuses self-approval per item, and the UI must surface per-item failures in a partial-success result rather than reporting a blanket success.

**Expected improvement.** Bulk approval on the AP queue is the highest-value workflow improvement in the product for the paying persona.

**Difficulty.** **M — 4 days** (hook + bar + `DataTable` support + `/review` rollout, including partial-failure reporting).

---

### Watch-list (below the top ten, recorded so they are not lost)

- **Dynamic content is not announced.** One `aria-live` region exists across 45 pages. Filter results, async saves and queue updates change silently for screen-reader users. Folds into the state-migration slices.
- **`/design` divergence risk.** 1,123 LOC of fixtures is the sole consumer of `PageHeader`, `FilterBar` and `Tabs`, and the only target of `visual.spec.ts`. Once live pages adopt those primitives, retarget visual regression at real routes and let the showcase become a gallery only.
- **Eight status-colour vocabularies** (§1.5) — consolidate onto `StatusBadge`'s `Tone`.
- **Two orphaned routes** (§1.1) — `/issuer`, `/reimbursements`.

---

## 3. Navigation decision

### Verdict: **WO-17 already solved this. Do not rebuild it. Scope down to four gaps.**

The brief's hypothesis was a "left-sidebar-collapsible" navigation. Checking the code rather than the WO prose:

| Hypothesis element | Status in `AppShell.tsx` | Evidence |
|---|---|---|
| Is the nav a left sidebar? | **Yes** | `<aside className="fixed inset-y-0 left-0 hidden w-64 flex-col … lg:flex">` — line 131 |
| Is it grouped? | **Yes** — 5 groups, 31 items | `lib/nav.ts::LIVE_NAV`; group headers at `AppShell.tsx:48` |
| Is there a mobile drawer? | **Yes** | `<Drawer open={mobileNav} … side="left" size="sm">` — line 140 |
| Is there a hamburger? | **Yes**, `lg:hidden`, with `aria-label` + `aria-expanded` | line 149-159 |
| Does the drawer close on navigate? | **Yes** | `onNavigate={() => setMobileNav(false)}` — line 142 |
| Is there a skip link? | **Yes** | line 123-128 |
| Does it collapse on desktop? | **No** | `w-64` is fixed; no collapsed state exists |
| Tooltips when collapsed? | **No** | no collapsed state, and no `Tooltip` primitive (P7) |

**A full navigation rebuild would be waste.** WO-17 shipped a correct, accessible, responsive, permission-filtered grouped sidebar with a working mobile drawer. It also made two restraint decisions that are *right* and must be preserved: no legal-entity switcher and no global search box are wired, because neither concept exists in the backend, and mounting either would be placeholder UI (`AppShell.tsx:21-30`, `Layout.tsx:20-23`). Any redesign proposal that adds a global search box to the top bar is proposing invented functionality and must be rejected.

### What is genuinely missing (the only nav work worth doing)

1. **Desktop collapse to an icon rail.** The sidebar is permanently 256px. On a 1440px laptop that is 18% of width permanently spent on navigation, in a product whose core surfaces are wide financial tables already capped at `max-w-6xl` (P5, §8). Needs: a collapse toggle, a `w-16` icon-only rail, and persistence in `localStorage`. **Blocked on P7** — an icon-only rail without accessible tooltips is worse than no rail.
2. **Scale management in the Payables and Workspace groups.** Both hold 9 items; all 5 groups are always fully expanded, so an admin sees 31 links with no collapse and no memory. Group-level collapse with persisted state is the cheap fix. (A "recent/pinned" section is a *possible* later refinement — not proposed now, because there is no usage data to justify the ordering it would imply.)
3. **Breadcrumbs are single-level.** `Layout.tsx:54-55` builds `crumbs` from `matchNavItem(pathname)`, which returns one item. `/invoices/inv-123` shows just "Invoices" — never the record. `/expenses/:id`, `/captures/:runId` and `/invoices/:id/review` are all detail routes with no trail back and no record identity in the chrome. The `Breadcrumbs` component already accepts a `Crumb[]`; the deficiency is only in what `Layout` passes it.
4. **Two orphaned destinations** (§1.1) — `/issuer` (Company details) and `/reimbursements`. Adding `Company details` under Receivables and `Reimbursements` under Payables is a two-line change to `lib/nav.ts`.

**Target structure: unchanged.** Keep Overview · Payables · Receivables · Insights · Workspace, and keep using only the app's real routes. The two orphans slot into existing groups. No new module, section or destination is proposed anywhere in this document.

---

## 4. Design-system gap

Against the brief's checklist. **"Exists"** means the file is present and correct; adoption is tracked separately because it is the real gap (P3).

| Brief item | Status | Path | Live adoption | Action |
|---|---|---|---|---|
| Button | **Exists** | `ui/Button.tsx` | 63 uses vs 86 legacy | **Consolidate** — migrate `btn-*`; add focus ring to the `btn` utility now (P8) |
| Input | **Exists** (`TextInput`) | `ui/Form.tsx` | **2 uses** vs 91 raw | **Adopt** — top priority (P2) |
| Select | **Exists** | `ui/Form.tsx` | ~2 uses | **Adopt** (P2) |
| Tabs | **Exists** | `ui/Tabs.tsx` | **1 page** | **Adopt** — `Settings` first (P9) |
| Badge | **Exists** ×2 | `ui/Badge.tsx`, `ui/StatusBadge.tsx` | mixed | **Consolidate** — 8 status vocabularies → `Tone` (§1.5) |
| Modal | **Exists** | `ui/Modal.tsx` | 3 pages | Adopt during migration |
| Drawer | **Exists** | `ui/Drawer.tsx` | shell + 3 pages | No action |
| **Tooltip** | **MISSING** | — | — | **BUILD** (P7) — blocks the sidebar rail |
| Toast | **Exists, misplaced** | `components/Toast.tsx` | 9 pages | **Move to `ui/`, export from the barrel** — it is not in `ui/index.ts` |
| Pagination | **Exists** | `ui/Pagination.tsx` | **1 page** | **Adopt** + add page-size (P6) |
| EmptyState | **Exists** | `ui/EmptyState.tsx` | 10 pages | Adopt (P1) |
| LoadingState | **Exists** | `ui/Skeleton.tsx`, `ui/Spinner.tsx` | partial | Adopt (P1) |
| ErrorState | **Exists** | `ui/ErrorState.tsx`, `ui/QueryState.tsx` | 7 pages | **Adopt — CRITICAL** (P1) |
| PageHeader | **Exists** | `ui/PageHeader.tsx` | **0 pages** | **Adopt** (P4) |
| Sidebar | **Exists** | `components/shell/AppShell.tsx` | live + fixtures | **Extend only** — collapse rail (§3) |

**Net build work: one component (`Tooltip`) and one move (`Toast` → `ui/`).** Everything else on the brief's list already exists. Plus the token additions in §8.

**Also needed, not on the brief's list:** `useSelection` + `BulkActionBar` (P10), and a select-all/indeterminate header column in `DataTable`.

---

## 5. Table and form consistency audit

### Tables — four samples

| | `Invoices.tsx` (196 LOC) | `Expenses.tsx` (379) | `PaymentRuns.tsx` (361) | `Vendors.tsx` (287) |
|---|---|---|---|---|
| Uses `DataTable` | No — raw `<table>` (L130) | No — 3 raw tables | No — raw (L152) | No — 2 raw (L129, L206) |
| Imports the `ui` kit | **No** | **No** | Partially | **Yes** (Badge, Button, Card, ConfirmDialog, EmptyState, QueryState, Skeleton) |
| Page title | `text-2xl` (L59) | `text-2xl` **×2** (L47, L58) | `text-xl` (L122) | `text-xl` (L96) |
| Horizontal overflow | **`overflow-hidden` — clips** (L129) | `overflow-x-auto` ✓ (L148, L193, L264) | none | none |
| Loading | `<td>Loading…` (L144) | ad hoc | ad hoc | `Skeleton` ✓ |
| Empty | `<td>No invoices found.` (L173) | ad hoc | ad hoc | `EmptyState` ✓ (L121, L198) |
| **Error** | **none** | **none** | **none** | `QueryState` ✓ |
| Pagination | hand-rolled prev/next (L180-193) | none | none | none |
| Row selection | none | checkboxes, no select-all (L162) | checkboxes + `selectedTotal` ✓ (L114) | none |
| Overflow menu | none | none | none | none |
| Cell padding | `px-4 py-3` | `px-3 py-2` / `px-2 py-1` | `px-4 py-3` | mixed |

**Concrete inconsistencies.** (a) Cell padding varies between `px-2 py-1`, `px-3 py-2` and `px-4 py-3` across pages and *within* `Expenses.tsx`. (b) Three different empty-state treatments across four pages. (c) Error handling exists on exactly one of the four. (d) Row selection exists on two pages with two different idioms and one missing select-all. (e) **No page has a row overflow/kebab menu** — per-row actions are either inline buttons or absent, so adding a fourth row action anywhere has nowhere to go. (f) `Vendors.tsx` proves the kit is adoptable — it uses six primitives correctly and still hand-rolls its tables, which is exactly the P3 pattern.

### Forms — three samples

**`Settings.tsx` (721 LOC).** No `<form>` element at all; 8+ sections in one scroll; 4 `className="label"` with no `htmlFor`; `<h2>` at `text-sm` (smaller than body); errors via `toast.error(apiError(e))` — correct and consistent, the best error handling in the codebase — but success/failure feedback is a transient toast even for irreversible GDPR erasure and retention purge, which deserve a persistent result panel.

**`Login.tsx`.** Real `<form onSubmit>` ✓; 5 unlabelled fields; no `autoComplete`; org-name and user-name fields appear conditionally, shifting layout mid-task.

**`Expenses.tsx` inline grid (L264-298).** An editable table where every cell is a bare `<input className="input">` with no accessible name, no per-cell validation display, and amount/VAT entered as free text inputs rather than `ui/CurrencyInput` — which exists specifically to enforce the money formatting this product's §4.9 invariant depends on. Validation appears only after submit, as a toast.

**Cross-cutting form findings.** No form uses `FormField`'s error slot, so inline field-level validation is absent product-wide; every form reports errors as toasts, which vanish. No form marks required fields (`FormField` supports `required` and renders the indicator). No form has unsaved-changes protection — navigating away from `Settings` mid-edit loses input with no warning.

---

## 6. Accessibility baseline (Phase 1 triage)

Full QA is Phase 3. Three representative screens, spot-checked.

**`Login.tsx` — the front door.**
- **FAIL (WCAG 1.3.1, 4.1.2, Level A):** 5 controls with visually-adjacent but programmatically-unassociated labels (§P2).
- **FAIL (best practice):** no `autoComplete` on email or password.
- **PASS:** real `<form onSubmit>`, so Enter submits.
- **RISK:** conditional org/name fields shift focus position between renders.

**`Invoices.tsx` — the busiest work surface.**
- **FAIL (1.3.1):** search and status controls unlabelled (L82, L94).
- **FAIL (2.4.7 risk):** mixed focus treatments — `btn-ghost` (L67), `btn-primary` link (L76), raw `<button>` chip (L113), raw prev/next (L183) — only some have a defined ring (§P8).
- **FAIL (1.3.1):** `<th>` elements lack `scope="col"` (L133-138). `DataTable` sets it; this hand-rolled table does not.
- **FAIL (4.1.3):** no live region — changing the filter silently replaces the table body.
- **PASS:** the clear-filter button has `aria-label="Clear workflow filter"` (L115).
- **PASS (contrast):** `text-slate-500` (#64748b) on white ≈ 4.76:1 and `text-slate-400` (#94a3b8) on white ≈ 2.85:1 — the former passes AA for body text, **the latter fails** and is used for the empty/loading message (L144, L173) and the "Export:" label (L63). `text-slate-400` on white appears throughout the codebase, including `ui/Form.tsx`'s hint text and `App.tsx:70`'s loading fallback. **This is a systemic token problem, not a page problem** — resolve it in §8.

**`AppShell.tsx` — the chrome (the good news).**
- **PASS:** skip link to `#main`; `<main tabIndex={-1}>` so it is focusable.
- **PASS:** `<nav aria-label="Primary">`; hamburger has `aria-label` + `aria-expanded`; decorative icons `aria-hidden`.
- **PASS:** `focus-visible:ring-2 focus-visible:ring-brand-300` on nav links.
- **PASS:** brand-600 (#2f57d4) on white ≈ 7.4:1; active nav `brand-700` on `brand-50` passes comfortably.
- **GAP:** the mobile `Drawer` uses `useFocusTrap` — confirm on a real device in Phase 3 that focus returns to the hamburger on close.
- **GAP:** no `aria-current="page"` beyond `NavLink`'s active class (React Router v7's `NavLink` sets `aria-current="page"` by default — verify it survives the custom `className` function).

**Triage summary.** The shell is in good shape. Page content is not. The two systemic fixes with the widest reach are **`FormField` adoption** (P2) and **the `btn` focus ring** (P8) — together they resolve the majority of Level-A findings without touching page logic. The `text-slate-400` contrast issue is a token decision (§8).

---

## 7. Phased plan

The brief's six-phase structure, **adapted to the gap analysis**. Phases whose work is already done are marked and skipped — this is the "don't redo shipped work" adjustment.

### Already done — do not redo

| Shipped | What it delivered | Verdict |
|---|---|---|
| **WO-16 (I1.1)** composed home dashboard | `pages/Dashboard.tsx` — uses `QueryState` (L39) with a proper `errorTitle`, `EmptyState`, skeletons, deep-links to filtered worklists | **Satisfies the brief.** Exemplar for the migration. |
| **WO-17 (I1.2)** grouped nav IA | `lib/nav.ts` + `AppShell` wired live: sidebar, 5 groups, mobile drawer, skip link, cosmetic filtering | **Satisfies the brief except four gaps** (§3). Extend, don't rebuild. |
| **WO-18 (I1.3)** cash-position relabel | Honest labelling on `/cash-position` | **Satisfies the brief.** Caveat: some disclosure sits in `title` attributes invisible on touch (P7). |
| **WO-21 (I1.5)** report → Excel/PDF | Export buttons on `/explore` | **Satisfies the brief.** Cosmetic only: buttons are `btn-*`, migrate with the rest. |
| **WO-12/13** capture-review screens | `CaptureReview.tsx`, `CaptureQueue.tsx` — use `Button`, `ConfirmDialog`, `EmptyState`, `ErrorState`, `Skeleton`, `Spinner`; correct advisory-AI framing | **Satisfies the brief.** Best-adopted pages in the app. |
| **WO-44 (E1.4)** duplicate candidates | `CaptureQueue.tsx:109` duplicate-candidate rendering | **Satisfies the brief.** |

**Phase 1 (audit) is complete with this document. Phase 2 (design-system foundation) is ~85% complete already** — the kit exists, is documented in `docs/DESIGN_SYSTEM.md`, and only needs `Tooltip`, the `Toast` move, and token additions.

### The plan

Slices are numbered from **WO-45** (the highest existing work order in `docs/plan/plan-a/wo/` is `WO-44-E14.md`) and prefixed `UX` so they slot into the Plan A pipeline as a new UX epic alongside the `I` board.

| WO | Slice | Phase | Effort | Priority | Depends on | Status |
|---|---|---|---|---|---|---|
| **WO-45-UX1** | Async-state truth: fix `QueryState`'s error branch, then `QueryState` + `PageHeader` on the 8 money-bearing pages; `btn` focus ring | 3 | **M — 4d** | **P0** | nothing | ✅ **Completed** |
| WO-46-UX2 | Form accessibility: `FormField`/`TextInput`/`Select` on auth + the 3 major forms; `autoComplete`; `aria-label` on the Expenses grid | 3 | M — 4d | P0 | nothing |
| WO-47-UX3 | `Tooltip` primitive + move `Toast` into `ui/`; replace meaning-bearing `title` attributes | 2 | S — 2d | P1 | nothing |
| WO-48-UX4 | Design tokens: semantic colours, contrast fix for `slate-400`, consolidate the 8 status vocabularies onto `Tone` | 2 | S — 2d | P1 | nothing |
| WO-49-UX5 | `DataTable` migration wave 1 — Invoices, Expenses, PaymentRuns, Vendors, Receipts, Review | 4 | L — 7d | P1 | WO-45 |
| WO-50-UX6 | `QueryState` + `PageHeader` on the remaining ~31 pages | 3 | M — 3d | P1 | WO-45 |
| WO-51-UX7 | Pagination + page size + debounced search, app-wide | 4 | M — 3d | P1 | WO-49 |
| WO-52-UX8 | Sidebar collapse rail + group collapse + persistence; add `/issuer` and `/reimbursements` to nav; multi-level breadcrumbs | 5 | M — 4d | P2 | WO-47 |
| WO-53-UX9 | `useSelection` + `BulkActionBar` + select-all in `DataTable`; roll out to `/review` (SoD-aware partial failure) | 4 | M — 4d | P2 | WO-49 |
| WO-54-UX10 | `Settings.tsx` into `Tabs`; real `<form>`s; separate destructive panel | 4 | M — 3d | P2 | WO-46 |
| WO-55-UX11 | `DataTable` migration wave 2 — the remaining ~26 table pages | 4 | L — 7d | P2 | WO-49 |
| WO-56-UX12 | A11y QA pass: `aria-live`, keyboard walkthrough, contrast sweep, axe in CI; retarget `visual.spec.ts` at live routes | 6 | M — 4d | P2 | WO-45…WO-55 |

**Total ≈ 47 days.** Every slice is independently shippable and revertable. WO-45, WO-46, WO-47 and WO-48 have no dependencies and can proceed in any order.

### Scope stubs

**WO-46-UX2 — Form accessibility and labelling.** Replace the `<label className="label">` + `<input className="input">` idiom with `ui/Form`'s `TextInput`/`Select`/`Textarea` on the 6 auth pages and the three largest forms (`Settings.tsx`, `Issue.tsx` — 12 unlabelled fields — and `Issuer.tsx`), add `autoComplete` on all auth fields, and give every cell input in the `Expenses.tsx` inline grid an `aria-label` derived from its column header. Adopt `FormField`'s `error` slot so validation renders inline instead of only as a toast, and its `required` indicator. Use `ui/CurrencyInput` for every money field so the §4.9 money formatting is enforced at the input boundary. Acceptance: zero `className="label"` in the touched files; every input in them has a programmatic name; axe reports no 1.3.1/4.1.2 violations on `/login`.

**WO-47-UX3 — Tooltip primitive and Toast consolidation.** Build `ui/Tooltip.tsx` on the existing `Portal`, triggered by hover **and** focus, with `role="tooltip"`, `aria-describedby` wiring, Escape-to-dismiss and tap support; export from the barrel. Move `components/Toast.tsx` to `components/ui/Toast.tsx` and export it (update the ~11 importers). Replace the 21+ meaning-bearing native `title` attributes; for `CashPosition.tsx`'s WO-18 honesty disclosures prefer persistent inline helper text over a tooltip, since a financial caveat must not be hover-gated. Acceptance: `Tooltip` in the barrel and the Gallery; every replaced tooltip reachable by keyboard and by tap.

**WO-48-UX4 — Design tokens and semantic colour.** Extend `index.css`'s `@theme` with the semantic ramps in §8 (success/warning/danger/info/neutral), radius, shadow and z-index tokens. Fix the `text-slate-400`-on-white contrast failure by promoting secondary text to `slate-500` and reserving `slate-400` for large text and disabled states only. Consolidate the seven raw status maps in `lib/format.ts` onto `StatusBadge`'s `Tone`, keeping the label vocabularies. Acceptance: every status colour resolves through one map; no body-size text on white below 4.5:1 in the touched files; no visual regression in `/design` snapshots beyond the intended contrast changes.

**WO-49-UX5 — DataTable migration wave 1.** Convert the six highest-traffic table pages to `ui/DataTable` with `Column<T>` definitions, inheriting `overflow-x-auto`, `scope="col"`, `aria-sort`, skeleton rows and the empty state; delete the hand-rolled markup including `Invoices.tsx`'s clipping `overflow-hidden`. Preserve every existing behaviour exactly — deep-linked filters, the workflow chip, per-currency rendering (no cross-currency sums, §4.14). Add characterisation Playwright assertions **before** converting each page. Acceptance: zero raw `<table>` in the six files; the existing e2e specs pass unmodified.

**WO-50-UX6 — Async states and headers, remainder.** Apply the WO-45 pattern to the remaining ~18 `useQuery` pages that still lack an error state, and replace the remaining hand-rolled `<h1>`s across all ~31 untouched pages. Purely mechanical; batch by area (Receivables, Insights, Workspace) so each commit is reviewable. Acceptance: `grep -L "QueryState\|isError" $(grep -l useQuery src/pages/*.tsx)` returns empty; exactly one `<h1>` per page, all via `PageHeader`.

**WO-51-UX7 — Pagination and search.** Adopt `ui/Pagination` on every paginating list; add a page-size selector (25/50/100) to the component once; add ~300ms debounce inside `ui/SearchInput` so all consumers inherit it; persist page size per list in `localStorage`. Acceptance: no hand-rolled prev/next remains; typing 10 characters into invoice search fires one request, not ten.

**WO-52-UX8 — Navigation completion.** The four §3 gaps and nothing more: (1) a desktop collapse toggle rendering a `w-16` icon rail with `Tooltip` labels, persisted in `localStorage`; (2) per-group collapse with persisted state; (3) multi-level breadcrumbs — extend `Layout.tsx`'s single `matchNavItem` crumb to a parent+record trail for `/invoices/:id`, `/expenses/:id`, `/captures/:runId`, `/invoices/:id/review`; (4) add `/issuer` ("Company details", Receivables) and `/reimbursements` (Payables) to `LIVE_NAV`. **Explicitly out of scope:** a global search box and a legal-entity switcher — no backend concept exists for either, and WO-17 was right to omit them. Acceptance: collapsed rail shows an accessible tooltip per item; state survives reload; `nav.spec.ts` extended and green.

**WO-53-UX9 — Bulk actions.** Extract `useSelection` and `BulkActionBar` from the working `PaymentRuns.tsx` implementation; add a select-all/indeterminate header column to `DataTable`; roll out to `/review` first, then `/invoices`. Bulk operations must respect segregation of duties (§4.8) — the server refuses self-approval per item, so the UI reports a **partial-success result** listing per-item outcomes, never a blanket success. Acceptance: selecting 10 invoices where 2 are self-submitted shows 8 succeeded and 2 refused with the server's `code`, and no client-side permission decision is made.

**WO-54-UX10 — Settings restructure.** Split `Settings.tsx` into `ui/Tabs` panels (General · Modules · Validation · SSO · Data & retention), wrap each panel's fields in a real `<form onSubmit>`, isolate destructive operations (GDPR erasure, retention purge) into their own visually distinct panel behind `ConfirmDialog`, and replace transient success toasts for irreversible actions with a persistent result panel. Fix the inverted `<h2 className="text-sm">` hierarchy. No mutation logic changes. Acceptance: every existing mutation still fires with identical payloads; Enter submits within a panel; destructive actions are not reachable by scroll alone.

**WO-55-UX11 — DataTable migration wave 2.** The remaining ~26 table pages, same method as WO-49, batched by area. After this, delete the `card`/`input`/`label`/`badge`/`btn*` `@utility` classes from `index.css` and assert their absence in CI.

**WO-56-UX12 — Accessibility QA and visual regression.** Add `aria-live` announcements for filter results and async saves; full keyboard walkthrough of the five primary flows; contrast sweep; add `@axe-core/playwright` to CI over the top 10 routes; retarget `visual.spec.ts` from `/design` to live routes and reduce the showcase to a gallery. Acceptance: zero axe critical/serious violations on the 10 covered routes; documented keyboard path through capture → review → approve → pay.

---

## 8. Visual direction

**"Modern European B2B FinTech — professional, calm, data-focused, avoiding AI-generated-dashboard clichés."** Translated into checkable rules that **extend** `index.css`'s existing `@theme`. The `--color-brand-*` ramp (#3b6ef2 family) is kept unchanged — it is a credible, restrained European fintech blue and re-picking it would invalidate the `/design` visual snapshots for no benefit.

### Tokens to add (`src/index.css` `@theme`)

```css
/* Semantic — replaces raw emerald/amber/rose/sky scattered across 8 status maps */
--color-success-50/-100/-600/-700   /* from emerald — already the de facto "paid" */
--color-warning-50/-100/-600/-700   /* from amber   — "pending" */
--color-danger-50/-100/-600/-700    /* from rose    — "overdue", destructive */
--color-info-50/-100/-600/-700      /* from sky     — "sent"/"viewed" */
--color-neutral-*                   /* alias the slate ramp already in use */

--radius-card: 0.75rem;   /* the rounded-xl already used by `card`/DataTable */
--radius-control: 0.5rem; /* the rounded-lg already used by Button/input */
--shadow-card / --shadow-overlay
--z-dropdown / --z-drawer / --z-modal / --z-toast   /* currently ad-hoc: z-30, z-50 */
```

### Checkable rules

1. **Numbers are the loudest thing on screen.** Financial figures: `font-medium` minimum, right-aligned, tabular. Add `font-variant-numeric: tabular-nums` to money cells so columns align digit-for-digit — currently they don't, which is the single most "not a finance product" detail in the UI. *Check:* every money cell right-aligned and tabular.
2. **Exactly one primary action per screen.** `Button variant="primary"` appears at most once per view; everything else is `secondary`/`ghost`. *Check:* `grep -c 'variant="primary"'` per page ≤ 1. `Invoices.tsx` currently has one primary link plus three `btn-ghost` exports — already close; keep it.
3. **Colour carries meaning, never decoration.** A colour outside the neutral ramp must encode a status, a chart series or an action. No coloured card backgrounds, no gradient headers, no coloured KPI tiles. *Check:* every non-neutral colour traces to a semantic token or `CHART_PALETTE`.
4. **No decorative chrome — the anti-cliché rule.** Explicitly banned: gradient backgrounds, glassmorphism, oversized emoji or icon badges beside metrics, animated counters, drop shadows above `--shadow-card`, full-bleed hero sections, purple-to-pink accents. The existing `shadow-xs` on `card` is the ceiling for resting elevation. *Check:* a reviewer can name every visual element's function.
5. **Density is a feature.** Table rows stay at `px-4 py-3` (the `DataTable` default) with `dense` (`px-4 py-2`) available for high-count lists. One padding scale app-wide — the current `px-2 py-1` / `px-3 py-2` / `px-4 py-3` mix (§5) is drift. *Check:* no bespoke cell padding outside `DataTable`.
6. **Type scale: four sizes, no more.** `text-2xl` page title (via `PageHeader` only) · `text-base` section heading · `text-sm` body and tables · `text-xs` metadata. Section headings must never be smaller than the body they introduce (`Settings.tsx`'s `text-sm` `<h2>` violates this). *Check:* four sizes across the app.
7. **Contrast floor 4.5:1 for body text.** `text-slate-400` on white (2.85:1) is demoted to large text and disabled states only; secondary text becomes `slate-500` (4.76:1). *Check:* automated contrast sweep in WO-56.
8. **Motion is functional and short.** The four existing keyframes (120-180ms, `prefers-reduced-motion`-gated) are the complete motion vocabulary. No new animation, no scroll-triggered reveals, no skeleton shimmer beyond the existing `Skeleton`. *Check:* no `animation`/`transition` beyond `index.css`'s set plus Tailwind's `transition`.
9. **Empty states state a fact and offer one action.** No illustrations, no mascots. `EmptyState` title + description + at most one `Button`. *Check:* no `<img>`/`<svg>` illustration inside any `EmptyState`.
10. **Calm means quiet defaults.** Neutral chrome, white surfaces, `slate-50` page background (already the body default), brand blue reserved for the active nav item, primary actions and links. Status colour appears only in badges and validation. *Check:* on a typical screen, non-neutral pixels are a small minority.

---

## 9. Recommended immediate action

Ship **WO-45-UX1** (`docs/plan/plan-a/wo/WO-45-UX1-async-state-and-page-header.md`, written alongside this audit). It is the most self-contained slice with the highest value-to-risk ratio: it fixes the CRITICAL silent-failure defect on the eight pages where money is at stake, applies `PageHeader` app-wide for the largest visual-coherence gain available per hour, and lands the one-line `btn` focus-ring fix that repairs 86 controls at once. It has **no dependencies**, touches no backend code, adds no new component, and every change is individually revertable.

---

*Phase 1 complete. Phases 2-6 are scoped in §7. No application code was modified.*
