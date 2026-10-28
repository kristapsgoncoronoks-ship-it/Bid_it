# Bid_it — Impeccable design audit, 22 October 2026

Historical assessment of main `90891d8d`. See the separate
[priority repair candidate](IMPECCABLE-PRIORITY-FIXES-2026-10-22.md) for subsequent
implementation and qualification; the findings and score below retain their
original source scope.

## Implementation integrity verdict

**Needs repair.** The app is recognizably a financial workspace, but it does not yet apply one coherent presentation, feedback and responsive system across its workflows. Repair factual monetary labels and shared accessibility first. Keep the existing financial behavior, light theme, system typography, permission boundaries and useful component kit; a framework replacement or decorative redesign is not justified.

## Health score and priorities

**11/20 — Acceptable; significant work needed.** This is a bounded expert judgment using Impeccable’s five-dimension rubric, not an automated percentage, production release clearance or WCAG certification.

| Dimension | Score | Evidence |
| --- | --- | --- |
| Accessibility | 2/4 | Contrast and invisible focused primary actions; accessible primitives already exist. |
| Performance | 3/4 | Lazy routes and entry gates are good; photos, optional charts and search can be lighter. |
| Theming | 2/4 | Partial tokens/tones; missing focus tokens and duplicate status vocabularies. |
| Responsive design | 2/4 | Mobile shell works; tabs, automation and enlarged-text modal need repair. |
| Implementation integrity | 2/4 | Financial context and error states vary across otherwise product-specific workflows. |

**17 findings in current main: 0 P0, 7 P1, 10 P2, 0 P3.** Three already have repairs in pending work (IMP-07, 08, 16); qualify those implementations instead of creating duplicate fixes. Fourteen require new repair work. P1 means significant difficulty or a verified relevant accessibility failure; no P0 blocking finding was established in this bounded review.

Top priorities:

1. IMP-01: correct the currency beside final capture confirmation.
2. IMP-02/03: make meaningful text, primary actions and keyboard focus readable.
3. IMP-04/05/17: contain mobile navigation/table overflow and make enlarged-text dialogs operable.
4. IMP-06: make dashboard task-read failures explicit and recoverable.
5. Integrate/qualify existing menu/planning repairs; improve invoice/project context, then reduce avoidable photo/chart/search work.

## Scope, provenance and evidence

- Method: official [Impeccable 4.7.0 skill](https://github.com/pbakaus/impeccable/blob/2fd24e3a9887e9fc009408ffbbb486e74998694e/.agents/skills/impeccable/SKILL.md), [audit playbook](https://github.com/pbakaus/impeccable/blob/2fd24e3a9887e9fc009408ffbbb486e74998694e/skill/reference/audit.md) and Operate guidance; pinned revision `2fd24e3a`. Official engine 0.1.16 downloaded into task artifacts and SHA-256 verified against its release sidecar. No global hooks or app runtime dependency were installed.
- Primary source: current `main` at `90891d8d52cf5aefc4467e3fa7f6070c042acec2`. Full frontend source inventory 162 files/69 SPA pages. Mechanical all-tree scan plus focused independent human source reviews; eight rendered surfaces, not a visual exercise of every page/role/state.
- Pending work is separate: PR #75 `0bbeb26e`, PR #76 `24abfa67`, PR #77 `60e1c223`. No combined or deployed state is assumed. Their integration and migration graph remain separate engineering work.
- Chromium synthetic UI audit: 12 desktop/mobile cases, 8 surfaces, 18 screenshots, zero API writes and zero page errors in the 12 cases. Desktop 1440×900, mobile 390×844, short 390×320 and enlarged-text 390×568. No production API, live credentials, customer OCR run or external email/calendar calls.
- Impeccable source scan returned 0 findings. A separate calibration HTML fixture returned 5 findings, confirming the detector ran. The actual rendered capture scan returned 23 hits: 14 contrast, 7 undersized functional text, 1 line-length, 1 nested-card. 21 primary and 2 advisory hits are not 23 unique defects. Manual triage excludes logotype/disabled exceptions and legitimate table grouping; repeated findings map to shared repairs.
- Engine `context` ran once and found no root PRODUCT.md/DESIGN.md. Its scoped-audit path permits using existing code/context; repository README, docs/DESIGN_SYSTEM.md and user complaints supplied the Operate context. No unrequested init/redesign brief was written.
- Developer tooling was reused for rendering. Main lock versions match except the already patched Node-side follow-redirects 1.16.1 versus main 1.16.0. This is not a fresh locked production build or performance benchmark.
- USD fixture note: parsed draft is deliberately USD 100 while the preview reuses an independent EUR synthetic invoice image. This isolates display binding and proves no OCR-accuracy conclusion.
- 200% text uses root font 32px; it is not physical-device or browser-zoom certification. No screen-reader or real-touch-gesture qualification was performed.

## Ordered finding register

| ID | Priority | Finding | State |
| --- | --- | --- | --- |
| IMP-01 | P1 | Capture confirmation mislabels source-currency money | Open in main and pending feature sources |
| IMP-02 | P1 | Meaningful text and primary actions have insufficient contrast | Open in main and pending feature sources |
| IMP-03 | P1 | Shared primary buttons lose visible keyboard focus | Open in main and pending feature sources |
| IMP-04 | P1 | Long tab strips overflow the whole mobile page | Open in main and pending feature sources |
| IMP-05 | P1 | Currency modal clips controls when text is enlarged | Open in main and pending feature sources |
| IMP-06 | P1 | Dashboard hides a failed Next actions read | Open in main and pending feature sources |
| IMP-07 | P1 | Schedule converts a team lookup failure into a personal view | Main defect; already addressed in pending PR #76, requires integration qualification |
| IMP-08 | P2 | Navigation exposes too many destinations with technical labels | Main defect; pending PR #75 addresses grouping/labels, inherited by 76/77 |
| IMP-09 | P2 | Invoice list omits context needed to choose the next action | Open in main and pending feature sources |
| IMP-10 | P2 | Project detail loses its navigation parent | Open in main and pending feature sources |
| IMP-11 | P2 | Project photo previews eagerly retain full originals and hide read errors | Source-confirmed opportunity; transfer/heap savings unmeasured |
| IMP-12 | P2 | Optional dashboard charts load with the task home | Source-confirmed opportunity; historical asset bytes, no production latency claim |
| IMP-13 | P2 | Invoice search sends one request per typed prefix | Open in main and pending feature sources |
| IMP-14 | P2 | Small shared controls need a mobile hit-area policy | Open in main and pending feature sources |
| IMP-15 | P2 | Shared and legacy status styles lack one semantic contract | Open in main and pending feature sources |
| IMP-16 | P2 | Calendar controls report copied/readiness without reliable feedback | Main defect; pending PR #76 addresses feedback, requires integration qualification |
| IMP-17 | P2 | Automation action table overflows mobile page | Open in main and pending feature sources |

## Detailed findings and development prompts

### IMP-01 — P1 — Capture confirmation mislabels source-currency money

**Category:** Implementation integrity. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/pages/CaptureReview.tsx:536`, `frontend/src/pages/CaptureReview.tsx:809`, `frontend/src/lib/format.ts:48`.

**Evidence:** Browser: draft currency USD, line amount 100, footer “Net of tax · subtotal €100.00”. No FX conversion takes place. Pending 77 retains money(subtotal) without currency.

**User impact:** The final financial decision shows the wrong monetary unit. This undermines trust even when the stored invoice may be calculated correctly.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Carry the reviewed source currency through every preview; show missing/invalid currency explicitly and preserve canonical server calculations.

**Acceptance:** EUR/USD/unknown-currency drafts and edited/region-applied currency have truthful labels. Include fractional line-rounding parity if preview arithmetic changes.

**Development prompt — `/impeccable harden`:**

> Bind capture monetary previews to the reviewed draft currency. Preserve source/review fingerprints, manual corrections and authoritative server rounding. Prove USD 100 never renders as EUR 100, unknown currency is explicit, and region/manual edits refresh the preview without implicit FX.

### IMP-02 — P1 — Meaningful text and primary actions have insufficient contrast

**Category:** Accessibility / Theming. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/index.css:6`, `frontend/src/components/ui/Button.tsx:23`, `frontend/src/components/ui/Form.tsx:72`, `frontend/src/components/Field.tsx:6`, `frontend/src/components/Toast.tsx:46`.

**Evidence:** Measured source colors: white/brand 500 4.4695:1; meaningful slate 400/white 2.6282:1; success-toast white/emerald-600 3.6652:1. Browser confirms enabled primary action rgb(255, 255, 255) on rgb(59, 110, 242). Rendered detector repeats low-contrast captions/navigation. Seven functional shell labels are 10px.

**User impact:** Instructions, navigation context and success confirmation are harder to read; enabled normal-size action text falls below AA contrast.

**Standard:** WCAG 2.2 SC 1.4.3; 10px is an Impeccable legibility heuristic, not a WCAG absolute font-size prohibition

**Recommendation:** Define readable semantic text/action/state colors and use them in existing controls. Increase tiny functional labels where needed without replacing the system font.

**Acceptance:** Meaningful normal text≥4.5:1, large/nontext at applicable 3:1; disabled/decorative/logo exceptions documented. Recheck actual default/hover/focus/success surfaces.

**Development prompt — `/impeccable colorize`:**

> Repair enabled action, secondary text and success-feedback contrast through shared semantic roles. Verify applicable 4.5:1 and 3:1 thresholds without rounding failures upward. Keep domain status meanings; exempt disabled controls and logotypes where appropriate. Do not treat all 415 slate 400 uses as proven violations.

### IMP-03 — P1 — Shared primary buttons lose visible keyboard focus

**Category:** Accessibility / Implementation integrity. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/components/ui/Button.tsx:19`, `frontend/src/components/ui/Button.tsx:23`, `frontend/src/index.css:3`.

**Evidence:** Browser New currency button: :focus-visible=true, outline-style: none, all box-shadow components transparent/zero, --tw-ring-color empty and brand 400 undefined. Theme defines 50/100/500/600/700 while shared controls request 300/400.

**User impact:** Keyboard users cannot reliably locate the primary save/create action.

**Standard:** WCAG 2.2 SC 2.4.7 and applicable SC 1.4.11; SC 2.4.13 is AAA, not the AA criterion

**Recommendation:** Use a resolvable semantic focus token or qualified existing token in every variant; retain forced-colors behavior.

**Acceptance:** Keyboard-focused primary/secondary/subtle/links/toggles/close controls show a visible indicator; measure actual computed/painted state, not the class string.

**Development prompt — `/impeccable harden`:**

> Repair shared focus token references using existing palette or one documented semantic focus role. Preserve keyboard and forced-color support. Add a rendered keyboard-focused primary action regression; verify focus visibility and applicable nontext contrast rather than asserting a class name exists.

### IMP-04 — P1 — Long tab strips overflow the whole mobile page

**Category:** Responsive design. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/components/ui/Tabs.tsx:72`, `frontend/src/components/ui/Tabs.tsx:91`, `frontend/src/pages/VatAdmin.tsx:184`.

**Evidence:** Chromium 390px viewport: VAT administration document/body width 722px. Last Status codes tab spans 657–722px; tablist has no scoped scrolling/wrapping.

**User impact:** Configuration sections and current location disappear to the side; users must move the whole page to discover them.

**Standard:** WCAG 2.2 SC 1.4.10 reflow risk;390px failure reproduced, 320px/zoom coverage remains acceptance work

**Recommendation:** Give the shared tablist a contained mobile overflow contract or a clearly labelled alternative, retaining roving keyboard behavior.

**Acceptance:** At 320/390px and text/zoom enlargement, page itself fits; every tab is discoverable and keyboard focus reveals it inside a scoped region. Preserve Arrow/Home/End and selection semantics.

**Development prompt — `/impeccable adapt`:**

> Adapt shared Tabs for long labels and eight-section configurations. Contain overflow locally with a visible affordance or provide an accessible mobile alternative. Preserve roving tabindex and panel IDs; verify 320/390px, enlarged text and keyboard access to the last tab.

### IMP-05 — P1 — Currency modal clips controls when text is enlarged

**Category:** Responsive design / Accessibility. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/components/ui/Modal.tsx:56`, `frontend/src/components/ui/Modal.tsx:69`, `frontend/src/components/ui/Modal.tsx:84`, `frontend/src/pages/Currencies.tsx:151`.

**Evidence:** Normal-text 390×320 modal buttons fit (bottom 306px): that suspected failure was rejected. Separate 390×568 probe with root text size 200%: dialog−96.5→664.5px, close entirely above viewport, footer 519.5→631.5px, body scrolling locked. Wheel and footer focus do not restore the complete view.

**User impact:** People enlarging text lose visible access to dialog context/controls while the background is locked.

**Standard:** Relevant WCAG 2.2 SC 1.4.4/1.4.10; test used root font 32px, not physical-device/browser-zoom certification

**Recommendation:** Bound modal height to available visual viewport and make its body scroll, retaining reachable controls and existing focus management.

**Acceptance:** Normal and 200% text, landscape/short viewports and long errors remain operable; header/actions are visible or scroll-reachable. Keep focus trap, Escape and focus restoration.

**Development prompt — `/impeccable adapt`:**

> Give Modal a viewport-bounded scrolling contract. Qualify actual currency/project forms at 200% text, short viewports and long server errors. Keep header, errors and action controls reachable without removing focus trapping, Escape, portal or background isolation.

### IMP-06 — P1 — Dashboard hides a failed Next actions read

**Category:** Implementation integrity. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/pages/Dashboard.tsx:149`, `frontend/src/pages/Dashboard.tsx:178`.

**Evidence:** Source catches all errors as null. Browser mocked /next-actions 500 removes the panel with no local error/retry, just as an empty/refused panel would disappear.

**User impact:** An owner cannot distinguish no work from tasks that could not be checked.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Suppress only documented permission refusal; show a compact independent read error with GET-only retry for genuine failures.

**Acceptance:** 500/offline/timeout is visibly different from empty and 403. Retry 200 restores the panel without dismissing/completing tasks or interrupting other dashboard panels.

**Development prompt — `/impeccable harden`:**

> Harden Dashboard Next actions by separating documented 403 from transport/server failure. Keep a named local error and GET-only retry. Preserve task dismiss/complete pending states and keep unrelated dashboard content available. Verify success, empty, refused and failed states independently.

### IMP-07 — P1 — Schedule converts a team lookup failure into a personal view

**Category:** Implementation integrity. **State:** Main defect; already addressed in pending PR #76, requires integration qualification.

**Location:** `frontend/src/pages/Schedule.tsx:188`, `frontend/src/pages/Schedule.tsx:264`, `frontend/src/pages/Schedule.tsx:295`.

**Evidence:** Browser mocked members 500 hides +Assign and planner filters, displays “Your assignments”, and gives no team-read recovery.

**User impact:** An outage looks like a change of planning authority, confusing the person arranging work.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Keep authority separate from service availability; qualify the repair already implemented in pending PR #76.

**Acceptance:** 403 retains documented personal view; authorized 500 shows team error/retry with assignment context retained; retry restores planner controls.

**Development prompt — `/impeccable harden`:**

> Do not duplicate pending PR #76. During integration verify that only documented access refusal selects personal mode and that team 500/offline has a named GET retry. Preserve existing assignment display and permission enforcement.

### IMP-08 — P2 — Navigation exposes too many destinations with technical labels

**Category:** Implementation integrity / Information architecture. **State:** Main defect; pending PR #75 addresses grouping/labels, inherited by 76/77.

**Location:** `frontend/src/lib/nav.ts:53`, `frontend/src/lib/nav.ts:93`, `frontend/src/lib/nav.ts:258`, `frontend/src/components/shell/AppShell.tsx:48`.

**Evidence:** Main registers 50 destinations across six groups and renders supplied groups expanded. Visibility is role/module-filtered; this is not 50 entries for every user. Labels include Issue, Captures, Dunning and Cost objects.

**User impact:** Users translate task intent into internal terminology and scan a long menu.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Integrate the existing pending PR #75 task groups and clearer labels; preserve permissions and quick navigation.

**Acceptance:** Same 50 route/module/permission tuples remain reachable; current group opens, keyboard expansion works, scoped preferences do not leak across accounts/companies.

**Development prompt — `/impeccable distill`:**

> Qualify pending PR #75 task-oriented collapsible navigation rather than building a competing sidebar. Preserve every route and permission tuple, current-location behavior and Ctrl/CommandK. Use familiar task labels and scoped expansion defaults.

### IMP-09 — P2 — Invoice list omits context needed to choose the next action

**Category:** Implementation integrity / Layout. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/pages/Invoices.tsx:261`, `frontend/src/pages/Invoices.tsx:292`, `frontend/src/lib/types.ts:536`.

**Evidence:** Rendered list shows Number, Issue date, Status, Tax, Total; supplier and due date are absent, and workflow is conditional on a workflow filter. Supplier name is not in the list DTO.

**User impact:** An approver/payer must open records to learn who is owed and when; “ready” alone does not explain the approval/payment workflow.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Add a bounded supplier projection and due/workflow context; demote secondary tax information or use an optional detail view.

**Acceptance:** Choose a payable using supplier, number, due date, workflow and source-currency total without opening each row. Keep pagination, export scope, permission checks and mobile readability; no N+1 supplier calls.

**Development prompt — `/impeccable layout`:**

> Reshape the invoice worklist around supplier, invoice reference, due date, workflow and source-currency amount. Add supplier_name through one bounded authorized backend projection. Keep existing filters, row selection, export contracts and 20-row pagination. Verify desktop/mobile and avoid per-row network lookups.

### IMP-10 — P2 — Project detail loses its navigation parent

**Category:** Implementation integrity. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/lib/nav.ts:277`, `frontend/src/components/Layout.tsx:62`, `frontend/src/App.tsx:238`.

**Evidence:** Project list is /cost-objects while detail is /projects/:id; exact/longest-prefix nav matching finds no parent. The mismatch remains in pending PR #75/76/77.

**User impact:** Following a project removes the active menu/breadcrumb context, making the page feel disconnected.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Central route-parent metadata should link project details to the permitted project-list destination.

**Acceptance:** Direct entry and list navigation show correct parent/current title and support return navigation without bypassing gating.

**Development prompt — `/impeccable clarify`:**

> Clarify project wayfinding with explicit centralized parent-route metadata. Preserve permitted navigation and list context; verify breadcrumbs, active menu and direct-link/back behavior for /projects/:id without adding duplicate routes or permission logic.

### IMP-11 — P2 — Project photo previews eagerly retain full originals and hide read errors

**Category:** Performance / Implementation integrity. **State:** Source-confirmed opportunity; transfer/heap savings unmeasured.

**Location:** `frontend/src/pages/ProjectDetail.tsx:429`, `frontend/src/pages/ProjectDetail.tsx:437`, `frontend/src/pages/ProjectDetail.tsx:442`, `frontend/src/pages/ProjectDetail.tsx:514`.

**Evidence:** Source: every photo mounts an original-blob query; object URLs have no matching revoke and staleTime is Infinity. Missing data always renders a pulsing placeholder, including failures. No byte/heap benchmark was run.

**User impact:** Phone users download/decode unnecessary evidence and may wait forever on a refused/missing image.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Bound derivative thumbnails and near-viewport loading; fetch original only on open/download; own/release URLs and show genuine read errors.

**Acceptance:** A 30-photo fixture initially fetches only near-visible small thumbnails, no originals. Open one fetches that original. Replacement/unmount releases URLs;403/404/network is distinct from loading.

**Development prompt — `/impeccable optimize`:**

> Optimize authenticated project photos using bounded derivative thumbnails and near-viewport loading. Keep originals/checksums untouched and tenant-protected. Own and revoke object URLs safely across remounts; provide explicit read-error recovery. Measure requests/bytes rather than claiming universal memory savings.

### IMP-12 — P2 — Optional dashboard charts load with the task home

**Category:** Performance. **State:** Source-confirmed opportunity; historical asset bytes, no production latency claim.

**Location:** `frontend/src/pages/Dashboard.tsx:4`, `frontend/src/pages/Dashboard.tsx:311`, `frontend/src/components/Charts.tsx:1`.

**Evidence:** Dashboard statically imports Charts despite rendering SpendSnapshot only for permitted cash data. Historical 75/76/77 chart chunks are 403, 927raw/112, 324gzip bytes; these are candidate artifacts, not a fresh main transfer measurement.

**User impact:** Users can download/parse analytics code without needing it, including roles whose chart panel is absent.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Lazy-load optional analytics behind permission and explicit expansion/near-viewport activation; keep primary tasks first.

**Acceptance:** Non-report dashboard does not request Recharts/analytics. Eligible users reach tasks before optional charts; independent chart errors do not hide worklists. Preserve 460/135 KiB entry gates.

**Development prompt — `/impeccable optimize`:**

> Extract optional Today analytics into an independent lazy boundary. Retain the existing task list and entry budgets. Do not import charts or query analytics for denied roles. Load eligible analytics on deliberate expansion or near viewport; measure cold/warm bytes and task readiness under consistent conditions.

### IMP-13 — P2 — Invoice search sends one request per typed prefix

**Category:** Performance. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/pages/Invoices.tsx:40`, `frontend/src/pages/Invoices.tsx:44`, `frontend/src/pages/Invoices.tsx:130`.

**Evidence:** Browser typing abcdef at 100ms/character produces six q GETs (plus initial list). Query function does not consume AbortSignal. No stale-result corruption or slow-backend claim was established.

**User impact:** Normal typing multiplies requests and work, especially over weaker links.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Separate immediate input from a short settled query and cancel superseded requests; Enter/clear stay immediate.

**Acceptance:** A rapid burst produces at most one settled search request, absent explicit Enter; clear/filter/page changes remain correct and cancelled queries do not toast as failures.

**Development prompt — `/impeccable optimize`:**

> Optimize invoice search with a short settled server-query value and consumed Axios/TanStack AbortSignal. Keep immediate typing, Enter/clear, URL/filter/page semantics and identity-scoped caches. Verify recorded request prefixes and delayed/reordered responses; add no debounce dependency.

### IMP-14 — P2 — Small shared controls need a mobile hit-area policy

**Category:** Responsive design. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/components/ui/Button.tsx:30`, `frontend/src/components/Switch.tsx:14`, `frontend/src/components/ui/Modal.tsx:103`.

**Evidence:** Source sizes: small Button roughly 24px tall, normal about 36px, small Switch 36×20px, close controlabout 26×26px. These are not automatically AA failures; spacing/inline exceptions apply.

**User impact:** Dense finance controls become harder to tap accurately on a phone.

**Standard:** WCAG 2.2 SC 2.5.8 AA 24px or exceptions;44px is enhanced/SC 2.5.5 AAA, not universal AA

**Recommendation:** Use larger mobile hit areas while retaining compact desktop density and nonoverlapping targets.

**Acceptance:** Measure target rectangles and spacing. Aim 44px for important mobile actions; independently assess AA 24px minimum and applicable exceptions.

**Development prompt — `/impeccable adapt`:**

> Adapt shared small actions, toggles and close controls with a mobile hit-area contract. Preserve desktop table density and avoid overlapping click regions. Measure 24px AA minimum/spacing exceptions separately from the 44px enhanced target; test touch before claiming physical-device qualification.

### IMP-15 — P2 — Shared and legacy status styles lack one semantic contract

**Category:** Theming / Implementation integrity. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/index.css:3`, `frontend/src/components/ui/Badge.tsx:5`, `frontend/src/components/ui/StatusBadge.tsx:10`, `frontend/src/lib/format.ts:133`, `frontend/src/components/Charts.tsx:34`.

**Evidence:** Five product brand tokens, partial Badge tones and seven independent legacy style maps coexist with StatusBadge, chart hex values and a separate switch color. Tailwind utilities are tokens; the gap is duplicated product-level semantics.

**User impact:** Contrast/focus/status fixes do not propagate consistently across AP, AR, expenses and communications.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Consolidate semantic text/surface/border/focus/state roles incrementally using current components.

**Acceptance:** Each domain status retains its name/meaning/permission behavior while shared visual roles and state combinations are documented and contrast-tested. Light-only remains supported by choice.

**Development prompt — `/impeccable extract`:**

> Extract semantic state, surface, text and focus roles from existing Badge/StatusBadge/legacy maps. Migrate incrementally without changing financial states, workflow labels or permission checks. Qualify contrast and default/hover/focus/disabled behavior; do not require a new framework, dark mode or font download.

### IMP-16 — P2 — Calendar controls report copied/readiness without reliable feedback

**Category:** Implementation integrity. **State:** Main defect; pending PR #76 addresses feedback, requires integration qualification.

**Location:** `frontend/src/pages/Schedule.tsx:75`, `frontend/src/pages/Schedule.tsx:84`, `frontend/src/pages/Schedule.tsx:116`.

**Evidence:** Main PhoneSync has no named feed-loading/error state and marks Copied before waiting for clipboard success. Source comparison confirms pending PR #76 repairs the feedback.

**User impact:** A person connecting a phone calendar may believe the link is ready/copied when it is not.

**Standard:** Product usability; no separate WCAG conformance claim

**Recommendation:** Qualify the pending PR #76 feed and clipboard feedback instead of implementing another control.

**Acceptance:** Feed lookup failure and clipboard rejection remain visible/recoverable; success appears only after the action succeeds. Keep feed subscription separate from verified Google/Outlook push delivery.

**Development prompt — `/impeccable harden`:**

> Qualify pending PR #76 calendar feed controls for loading/error/retry and awaited clipboard success. Verify rejected clipboard and feed500 with truthful visible feedback. Keep subscription refresh timing distinct from push-provider delivery and do not send real emails/calendar events during UI tests.

### IMP-17 — P2 — Automation action table overflows mobile page

**Category:** Responsive design. **State:** Open in main and pending feature sources.

**Location:** `frontend/src/pages/Automation.tsx:380`, `frontend/src/pages/Automation.tsx:743`.

**Evidence:** Browser 390px viewport: document 487px. Rule table right edge 486.8px, including Publish/Disable actions outside the viewport; no enclosing local table overflow contract.

**User impact:** Important controls require moving the whole page sideways, weakening planning/rule-management usability.

**Standard:** Table 2D exceptions require context; this is a reproduced usability issue, not an automatic blanket WCAG failure

**Recommendation:** Give tables local overflow or a purposeful mobile representation, keeping each action associated with its rule.

**Acceptance:** Document fits 320/390px; every rule action is discoverable via a labelled local region or mobile view. Preserve publish/dry-run/disable semantics, pending/error states and permission gating.

**Development prompt — `/impeccable adapt`:**

> Adapt Automation tables for narrow viewports using contained accessible overflow or a mobile rule summary. Preserve actual publish, dry-run and disable commands and their pending/error states. Verify long names and keyboard/touch discovery without hiding financial or execution state.

## Strengths to preserve

- Permission/module-filtered routing and company/account isolation; explicit source/review identity in pending OCR work. A design change must not weaken them.
- Familiar light theme and system UI typography suit an Operate product. Dark mode, display fonts, extra animation and a new framework are not prerequisites.
- Shared labelled FormField, real dialogs/drawers, focus trap/restoration, tab keyboard behavior, skip link and main landmark. Fix gaps in these primitives rather than replacing the kit.
- QueryState/ErrorState already distinguish failed reads from empty data in many flows. Extend that contract to remaining independent panels.
- Route lazy loading, bounded invoice pagination, existing 460/135 KiB entry limits, chart-on-entry prohibition and reduced-motion-aware transitions. Do not raise budgets to hide regressions.
- Capture preview fetches one reduced page and manages object URL lifetime; reuse that pattern for media.
- Pending 75/76 already improve menu grouping, planning-team recovery and calendar feedback; pending PR #77 provides deliberate visual OCR compare/apply with saved-review safety.

## Recommended execution sequence

1. `/impeccable harden`: IMP-01 monetary preview and IMP-06 dashboard read recovery.
2. `/impeccable colorize` + `/impeccable extract` + `/impeccable harden`: IMP-02/03 readable semantic colors and visible focus; propagate through existing components.
3. `/impeccable adapt`: IMP-04/05/17 responsive tab/table/dialog contract, followed by IMP-14 mobile hit areas.
4. `/impeccable distill` + `/impeccable clarify` + `/impeccable layout`: qualify existing menu/planning work, then IMP-09/10 task context.
5. `/impeccable optimize`: IMP-11/12/13 photos, optional charts and settled/cancelled search. Measure request/byte changes, preserve current gates.
6. `/impeccable polish`: verify the connected workflows and shared states; then rerun `/impeccable audit` on these same source/state/viewport fixtures for a comparable score.

Each item can be executed individually or in a chosen batch. The prompts above are ready for implementation; this audit makes no application UI fixes.

## Corrections to earlier or automatic assumptions

- The older docs/design/UX-AUDIT.md is a historical snapshot, not current counts/evidence. Broad claims that login/forms are unlabelled everywhere or shared QueryState treats errors as empty are stale; they were not reused.
- A normal-text 390×320 currency dialog did not strand its footer. The distinct 200% text case did clip controls and is IMP-05. Preserve both positive and negative controls.
- The detector’s brand-mark contrast hit is exempt logotype content; its disabled Save corrections hit is not an enabled-control AA failure. Enabled primary contrast is established separately.
- Bordered editable table inside a card is useful task grouping, not inherently a design defect. An 87-character help line is not by itself a WCAG failure.
- 44px is an enhanced/recommended touch target; AA 24px and spacing/other exceptions are assessed separately. WCAG has no universal 11px font-size minimum.
- Recharts 3 default auto animation honors reduced motion; absence of a manually specified animation flag was not reported as a defect.
- Missing dark mode, a system font and financial data density are not failures for this product.

## Evidence archive

Local task artifacts: `/workspace/artifacts/impeccable-design-audit-2026-10-22/`. The standalone HTML report embeds selected real screenshots. `findings.json`, `detector-source.json`, `detector-rendered.json`, `detector-triage.json`, `tool-provenance.json`, `main-source-manifest.json` and `browser/receipt.json` preserve scope/results. Artifact paths are review evidence, not application dependencies. The [committed evidence summary](evidence/IMPECCABLE-2026-10-22.json) records their hashes.

No application source, financial algorithm, permission policy, dependency lock, visual baseline, infrastructure or live deployment was changed.
