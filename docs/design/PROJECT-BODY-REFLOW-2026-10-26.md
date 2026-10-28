# CTX-02: project body reflow — 26 October 2026

Project details now keep profitability, documents, costs, offers, acceptance and the invoicing plan within the page at narrow widths and enlarged text. The inherited 390px viewport with 32px root text produced a 559px-wide document on ordinary data; the same fixture now stays 390px wide. Deliberately long populated fixtures independently reproduce stronger overflow before the repair and fit afterward.

This is an Impeccable refinement of the existing interface, stacked on PR #82's fixed Node/image-security candidate `f100c2096ef72604d90fb24eecc164715c8a9739`. Main and Hostinger are unchanged. It does not finish the wider UI audit or release backlog.

## Change and action contracts

The profitability grid follows available space, its metadata wraps, and cards/forms constrain native controls to their container. Final-invoice controls wrap instead of forcing page overflow. Four existing tables retain every value in named keyboard-focusable local scroll regions. Coherent money/date strings stay together inside those tables; wide data requires local horizontal scrolling.

Only static layout classes and four wrappers change in `ProjectDetail.tsx`. Copy, decimal render expressions, FX/source evidence, live/frozen/incomplete basis gates, input handlers, query keys, endpoints, permissions, mutations and invalidations are preserved. No new dependency, framework, global style, financial calculation, schema or API contract is introduced.

| Action | Required outcome and evidence |
| --- | --- |
| Inspect profitability and evidence | Keep live/frozen/historical/incomplete context and original amounts readable; do not hide facts to fit. Text-range and separate KPI bounds are checked. |
| Scroll each populated table | Tab reaches its named region, a visible focus indicator remains, ArrowRight scrolls locally, and Tab reaches original far-right actions without moving the whole page. |
| Add cost | Preserve the exact single POST payload, disable during its pending response and retain existing reset/error behavior. |
| Prepare final invoice | Preserve adjustment strings and one exact draft request, pending disabling and visible server refusal. Existing acceptance tests cover the successful handoff. |
| Read project / source PDF | Keep loading, error and retry usable within the page. PDF retry remains GET-only; the focused refusal fixture does not prove a successful real download. |

**Implementation prompt:** repair CTX-02 through bounded existing layout rules; retain all financial facts and actions, use native named local table scroll regions, and verify actual text/control bounds, keyboard access and command payloads against an immutable baseline. Do not mask overflow by clipping facts or changing arithmetic.

## Qualification

The frozen source SHA-256 is `90bd82c2fc5b161ed0b0db3545db53fe4f28440c10558e1537b8864ada4a3d3c`; focused spec SHA-256 is `d5794f3ae26876eb9f8a8f06ef15cc34298d9fd42d164d544448afefe66717ee`. The rebase onto the final security parent preserves both byte-for-byte.

Eleven focused Chromium cases pass, covering five viewport/font combinations, frozen/source evidence, incomplete/historical values, held/failed reads and retries, exact cost/final-draft writes and PDF read refusal. The same enlarged-text regression fails against the immutable head55 control because actual text exceeds its card. Its long populated test fixture was 2433px wide before and 390px after. The independent review uses a different long fixture (4232px before); those populations are not interchangeable measurements.

Independent review also exercises ordinary and long content, 320px/390px viewports with 32px root text, wider layouts, mixed/unknown/frozen basis states and a populated photo. Native Tab/ArrowRight reaches and scrolls all four regions (0→80px) while page scrollX stays zero; original far-right actions and final preparation remain reachable. Root inspected ordinary desktop and enlarged-text captures. Source review finds no introduced blocking issue.

`npm run lint` and `npm run build` pass. The unchanged bundle gate reports 437.5 kB raw / 128.9 kB gzip against 460/135 budgets. The one final Impeccable mechanical detector returns `[]`; this is neither a complete accessibility audit nor WCAG certification. No visual reference or tolerance changes.

The first broad browser run records **839 passed / one failed**, with no retries. Trace evidence shows three bootstrap assets failed with `ERR_NETWORK_CHANGED`, an empty app root and zero API requests; the supplier page/fixtures never ran. The underlying network-change trigger is unproven. Vite shows no corresponding restart. The unchanged failed case passes separately (2.1 seconds, zero retries). The original failure, trace and report are retained; a fresh full run against the rebased composition is recorded separately, never substituted for that history.

All 13 existing local showcase screenshot cases fail against the committed goldens. The same 13 cases also fail on the unchanged control; every control/candidate actual PNG is byte-identical. The showcase, shared CSS, spec, configuration and goldens are unchanged across head55, the final security parent and the candidate. This establishes no candidate delta in those captures; it is not a visual-suite pass. The exact environment/golden mismatch remains unisolated. No reference image or tolerance is changed; hosted visual qualification remains separate.

The final full default browser command against the rebased source passes **840 cases**, zero skips/flakes/retries, in 490.4 seconds with four workers. This is a distinct invocation; it does not erase the original failed run. The focused eleven are included in those 840, not additive coverage. The accompanying delivery receipt binds both reports and the qualified compiler. Hosted results remain separate. Browser tests use synthetic API data and do not prove new PostgreSQL, provider or live financial behavior. No physical-device, native popup, screen-reader, performance-capacity or complete keyboard-accessibility claim is made.

## Inherited findings and individual maintenance prompts

These exist in the immutable baseline and candidate; they are separate from reflow and remain open:

1. **CTX-02-A: file-picker keyboard access.** Attach document and + Add photo wrap hidden inputs in nonfocusable labels. Independent Chromium Tab skips both actions. **Prompt:** use meaningful native keyboard-accessible file-picker controls; preserve accepted types, camera hint, change handler, pending state and routes. Verify Enter/Space chooser activation and synthetic-file commands at narrow/enlarged sizes.
2. **CTX-02-B: table header relationships.** All four tables still use td-only rows; ARIA lacks column headers. This is source/accessibility-tree evidence, not observed screen-reader output. **Prompt:** add appropriate column headers and table context without changing data, row order, actions or local scrolling; verify browser header relationships and narrow-text access.
3. **CTX-02-C: adjustment amount name.** The amount input remains named `±0.00`, without purpose/currency/row context. **Prompt:** give each adjustment amount a meaningful currency-aware label and distinct row association; preserve typed decimal strings and final-draft payloads; check multiple rows, keyboard and enlarged text.

A suspected photo overflow did not reproduce: the nonempty synthetic photo remains inside its card at 320px/32px. Do not record it as a confirmed defect. CTX-03 switch-write outcome recovery, CTX-04 picker containment and CTX-05 Suppliers overflow investigation retain their separate prompts.

## Evidence, failure and review

Bounded evidence is retained under `/workspace/artifacts/project-reflow-2026-10-26/`, with final source/tests and local invocations bound in `evidence/PROJECT-BODY-REFLOW-2026-10-26.json`. Raw initial/final reports use distinct task-specific `/tmp` paths. Only synthetic data is used; no customer file, production data, credentials or external communication is introduced.

Revert the UI commits if layout regresses, retaining the security parent. No migration/data rollback is needed. AI assistance produced code, tests and documentation; independent source/render/keyboard review and behavioral qualification provide evidence, not human approval. Fresh hosted CI and human/Lead review remain required before integration. No merge or deployment is performed.
