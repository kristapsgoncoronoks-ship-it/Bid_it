# Invoice and project context — 23 October 2026

This candidate implements IMP-09 and IMP-10 from the [Impeccable audit](IMPECCABLE-AUDIT-2026-10-22.md), stacked on PR #78 at `97178b2`. It preserves the existing light financial-workspace design and shared controls. Main and Hostinger are outside this task's changes.

The preceding candidate has [completed hosted qualification](IMPECCABLE-PR78-HOSTED-2026-10-23.md): 4,530 backend passes / 184 skips, 202 PostgreSQL cases, six growth checks, nine actual journeys, 781 functional and 13 ordinary visual tests. All ten image targets pass the HIGH/CRITICAL gate. These 22 October results qualify that preceding revision; this new candidate requires its own checks.

## Resulting behavior

The invoice worklist groups supplier and invoice reference first, shows due date and workflow without requiring a workflow filter, and retains the source-currency total. Issue date, invoice status and tax remain available as supporting context. Narrow containers display labelled facts within the same invoice row, retaining one selection checkbox and one invoice link. Search remains explicitly **Search number**; supplier search is not introduced.

The existing paginated API supplies `supplier_name` through one tenant-guarded outer join. It is the **current authorized supplier-master name**, not the supplier identity frozen on the original document. Missing or unavailable names are explicit. Invoice counts, filters, ordering, 20-row paging, financial values, export scope and draft-selection/deletion rules remain unchanged. Query work remains two invoice/supplier reads at one and twenty rows; this preserves bounded work, rather than claiming a pre-existing N+1 defect was repaired.

Project detail resolves its permitted Cost objects parent through central route metadata. The parent breadcrumb and **All projects** return to `/cost-objects?kind=projects`. Direct entry, tab selection and browser history use a validated URL kind, so returning from a project retains the Projects tab. The generic Cost objects sidebar destination remains unchanged. No extra project-name request or duplicate route/permission model is introduced.

Changing the tab through browser history must not turn one kind of master-data draft into another. Open create/rename dialogs retain their origin context and close/reset when the kind changes. Dispatched create, rename and transition requests retain their original kind and submitted values; obsolete completion callbacks must not overwrite a newer dialog. This is client context protection, not a new durable command or replay contract.

## Public contract and boundaries

- `GET /invoices` adds nullable `items[].supplier_name`. Explicit invoice and vendor organization predicates prevent a malformed cross-company link from revealing a name; missing links preserve the invoice and its count.
- A list-only `InvoiceListItemOut` schema keeps invoice detail unchanged. OpenAPI replaces the former list-only `InvoiceOut` component and updates the list item reference. All paths and `InvoiceDetailOut` remain identical. Regenerated clients may see the new model name; the complete schema change must not be described as additions only.
- No database migration, dependency, monetary arithmetic, authentication rule, server mutation contract or external provider changes are required. The small shared PageHeader/export layout corrections preserve their actions and copy.
- Project return context covers the Projects tab. The existing local archived filter is not persisted by this change. Broader photo, project-body reflow, analytics, search and semantic-status work remain separate backlog items.

## Action contracts and development prompts

| Control | Description / completed outcome | Maintenance prompt |
| --- | --- | --- |
| Invoice reference | Open the selected invoice; supplier, due, workflow and total are already visible before opening it. | Preserve authorized current-supplier projection, explicit unknown values and source currency. Do not introduce per-row supplier requests or label original total as outstanding balance. |
| Search / status / workflow filter | Preserve the worklist filters and pass them unchanged into the separately scoped export preview. | Preserve query identity, filter scope and page reset while improving presentation. The export preview also applies its own entity/period/currency and approval rules. Supplier-name search or settled search requires a separate contract. |
| Prev / Next | Read another 20-row page without changing the existing explicit selection contract. | Keep count and page populations aligned, query work bounded and draft delete guards intact. |
| Accounting CSV | Preserve the existing entity/period/currency preview and download contract, with wrapping controls at narrow widths. | Improve layout without changing export filters, disabled provider readiness, durable preview identity or retry semantics. |
| Project parent / All projects | Return to the Projects tab through the central list URL. | Keep active navigation, parent breadcrumb and return destination aligned with filtered permissions and browser history. Do not infer project access from sidebar visibility. |
| Cost-object kind tabs | Change the validated list kind and retain it through Back/Forward. | Close/reset drafts from a different kind; bind pending mutations to their dispatched kind, identifiers and values. Prevent obsolete callbacks from clearing a newer draft. |

## Qualification

Evidence is recorded under `/workspace/artifacts/impeccable-context-2026-10-23/`; large browser/service artifacts use task-specific `/tmp` directories. The final delivery receipt distinguishes failure-first checks, focused tests, whole-frontend checks and actual browser/API/PostgreSQL completion. Existing visual references and tolerances are retained; host control comparisons are separate from normal CI's pinned Jammy runtime.

The [final source/evidence receipt](evidence/IMPECCABLE-CONTEXT-FIXES-2026-10-23.json) binds the checked implementation and all 749 actual-service source entries. The full frontend command `npm run test:e2e -- --workers=4 --retries=0 --trace=retain-on-failure --reporter=line,json --output=/tmp/bidit-context-final-e2e-results-20261023` passes **809/809** in 444.2 seconds: zero skips, unexpected results, flaky results, retries or report errors. This is local qualification; fresh hosted CI and human/Lead review remain required.

The backend cohort passes 95 tests, including five new regressions. Three additional served-navigation permission tests pass. The corrected immutable baseline fails all five new cases for the missing field; an earlier synthetic email-validation fixture error is preserved and excluded. Independent review verifies tenant-safe projection, unchanged detail/path schemas and the disclosed list-component rename. Root-wide Ruff and formatting checks pass (864 formatted files); mypy passes 428 sources with two existing untyped-body notes. The complete backend suite was not executed locally for this batch.

An actual browser → FastAPI → restricted PostgreSQL journey passes invoice display, bounded pagination/combined filters, project direct/list/history/return and same-user independent-session company switching. The accepted scope contains two invoices in one company and one in the other, with matching creation-audit counts. Six relevant tables have FORCE RLS; a cross-company supplier assignment is refused with SQLSTATE 23503 and rolled back. No email or worker job is created. Both attempts' synthetic data remain retained; the first rate-limited attempt is separately preserved. Owned services are stopped and a synthetic dump retained; its catalog was listed, without a restore execution claim.

Independent browser review found and preserved a real regression in the first URL-kind implementation: Back repurposed a typed project draft as a department draft. The repair now closes/resets drafts on kind navigation, freezes mutation arguments and fences obsolete callbacks. Eighteen focused project cases pass, including held create/rename success and failure responses plus a held transition refusal. The independent final-freeze replay also passes with zero writes and page errors. Ten focused invoice cases pass; these 28 cases are included in the full browser suite, not extra unique coverage.

The final invoice renders were reviewed at 1440, 900, 320 and 390 pixels and at 390 pixels with a 32-pixel root font. All facts remain available and the intermediate header no longer overlaps its actions. Project header/return controls fit the reviewed narrow and enlarged-text layouts. All 13 fresh same-host visual-control pairs are byte-identical to immutable PR #78. That comparison does not replace normal CI's pinned Jammy visual assertions. Existing reference images, specifications and tolerances are unchanged.

Root-wide frontend lint, TypeScript and UI guards pass. The production build transforms 845 modules; first load is 437.3 kB raw / 128.9 kB gzip against the unchanged 460 / 135 budgets. The once-per-finish Impeccable source detector returns an empty finding array for the eight changed UI targets. These checks do not establish runtime speed or hardware capacity.

## Remaining findings and ordered follow-ups

| Finding | Evidence / impact | Development prompt |
| --- | --- | --- |
| CTX-01 — organization-list read recovery (P2) | An actual `GET /auth/organizations` rate-limit response left the organization switcher displaying a static dash, with no local error or retry. `useOrgSwitcher` discards the query error; `ScopeSwitcher` treats an empty list as static text. Recovery succeeded after the existing limiter window using normal SPA navigation. No cross-company disclosure was observed; this batch does not change the limiter. | Add truthful loading/current-organization/error states and an accessible GET-only retry with rate-limit guidance. Preserve authoritative identity, cache fences, filtered memberships and switch-command outcome handling. Test 429/403/network recovery and account/session changes; never fabricate a current organization or replay a switch from a read retry. |
| CTX-02 — project-body enlarged-text reflow (P2, existing) | At 390 pixels with a 32-pixel root font, the project header and return fit, while unchanged profitability metadata/final-invoice body controls still produce a 559-pixel document. The immutable comparison has the same overflowing body boxes. This batch improves the old long-title header but does not complete whole-page reflow. | Reflow the project body and its controls within the available width. Keep financial evidence, currencies, disabled states and native keyboard access intact; verify long/missing/error states at narrow widths and 200% text without hiding facts or weakening visual references. |
| Existing audit queue | Currencies background-page reflow, bounded photo previews, optional lazy analytics, settled/cancelled invoice search, mobile hit areas and semantic status consolidation remain open. | Follow the individual IMP-11 through IMP-15 prompts and existing reflow acceptance contracts. Measure requests/bytes before claiming lighter operation; preserve permissions and financial meaning. |

No complete WCAG, customer OCR, live provider, accountant acceptance, production sizing or release-clearance claim follows from these synthetic checks. The earlier local lazy-import rejection remains separately recorded; later green runs did not establish its original cause. AI assistance covers implementation, tests, research and documentation; independent reviews support the required human/Lead review before merge.

## Failure and rollback

Revert the context UI and API/OpenAPI projection together if necessary; there is no data rewrite. Older responses render an explicit unavailable supplier rather than inventing a name. Preserve PR #78's security patches and verified references. Do not weaken gates, regenerate unrelated visual truth or downgrade dependencies to resolve a failure.
