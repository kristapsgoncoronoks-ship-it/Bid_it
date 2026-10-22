# Impeccable priority repairs — 22 October 2026

This candidate implements the first seven repairs from the [design audit](IMPECCABLE-AUDIT-2026-10-22.md): IMP-01 through IMP-06 and IMP-17. It is stacked on selected-region OCR PR #77 (`60e1c223`), which inherits OCR/navigation PR #75. Work-order planning PR #76 remains a separate candidate. Main and the Hostinger installation have not changed.

## Resulting behavior

| Finding | Repair and preserved boundary |
| --- | --- |
| IMP-01 | Capture confirmation displays the reviewed draft currency. Missing, invalid and `XXX` currency are explicit. Manual/region edits refresh the label. Subtotal arithmetic, FX, authoritative rounding, command identity and review fingerprints are unchanged. |
| IMP-02 | Shared action, secondary-text and success-feedback roles provide readable colors. Contextually reviewed captions adopt the text role across existing pages; logo, disabled and decorative exceptions remain. Chart axis labels use the same text role. This is not a claim that every arbitrary status color meets every contrast requirement. |
| IMP-03 | Shared buttons, inputs and related controls use a defined visible-focus role. Forced-colors mode receives a real outline. Existing keyboard behavior and validation semantics remain. |
| IMP-04 | Tabs scroll locally, expose overflow guidance and reveal the selected tab after keyboard selection or viewport resizing. Roving focus, disabled tabs and panel associations remain; mounting a lower tab strip does not scroll the document. |
| IMP-05 | The complete dialog scrolls within the viewport, including enlarged headings and form-owned action rows. Footer rows wrap. Focus trapping, Escape, focus restoration and body scroll locking remain. |
| IMP-06 | Next actions distinguishes permission refusal from failed reads. Availability failures show a local explanation and GET-only retry. Previously loaded tasks remain visible but their mutation controls pause until refresh succeeds; a later 403 clears them. Other dashboard sections remain usable. |
| IMP-17 | Automation rules and run tables scroll in named, keyboard-accessible local regions. Existing editing, publishing and run actions are unchanged. |

No backend source, schema, app dependency, lockfile, financial calculation, workflow security gate or screenshot tolerance changes in this batch. Existing system typography and light theme are retained.

## Action contracts

| Action | Outcome and recovery | Follow-up development prompt |
| --- | --- | --- |
| Confirm reviewed invoice | The displayed monetary unit follows the reviewed draft. The existing server commit/fingerprint contract remains authoritative. | Keep display currency coupled to the canonical review across manual edits, region application and readback. Never substitute EUR for missing currency or introduce implicit FX. |
| Retry Next actions | Read current tasks with GET; show progress, preserve other dashboard sections and avoid replaying Done/Dismiss. | Preserve local error ownership, cached-data permission eviction and identity fences when extending dashboard task controls. Test unavailable and forbidden responses separately. |
| Select a tab | Bring the selected control into the local strip without moving the whole document. | Retain roving keyboard navigation and selected-control visibility at narrow widths, text enlargement and desktop-to-mobile resize. |
| Close or submit a dialog | Reach controls by keyboard and local scrolling at short heights/enlarged text. | Exercise standard footers and form-owned action rows without weakening focus trapping or restoring page-level overflow. |
| Inspect Automation rows | Scroll the named table region to reach columns/actions; execute the existing action once. | Preserve publication/command semantics while improving mobile table presentation; do not fork the workflow into a decorative preview. |

## Qualification and limits

Evidence is retained under `/workspace/artifacts/impeccable-fixes-2026-10-22/`, with large connected/re-audit artifacts in the task-specific `/tmp` directories named in their receipts. These are synthetic fixtures, never customer invoice acceptance or production sizing.

Focused browser checks establish the reported defects before repair and exercise the repaired controls. The broader functional suite, production build and bundle budget are recorded in the final delivery receipt. The connected currency journey separately checks browser display, actual API responses and independent PostgreSQL invoice/effect counts. Rendered Impeccable comparisons use identical fixtures on immutable PR #77 and this candidate; they do not manufacture a new whole-app rubric score.

Visual baselines must be generated and reviewed in the repository's version-matched Playwright CI image. Host-generated differences are not accepted as Linux CI goldens. Existing absolute pixel allowances remain unchanged. A branch-only baseline refresh is preparation, not normal CI or release clearance; the final PR head still needs its ordinary checks.

The audit's historical **11/20** score describes main `90891d8d`. It is not revised by this bounded repair. Screen-reader, physical touch device, every status palette and every app workflow remain outside this qualification. At 200% root text the currency dialog fits and its controls remain reachable, but the underlying Currencies page header still produces 424px document width in a 390px viewport; full-page enlarged-text reflow remains follow-up work.

## Remaining ordered work

1. Integrate and qualify existing menu/planning candidates for IMP-07, IMP-08 and IMP-16; resolve sibling migrations before combining planning and selected-region OCR.
2. IMP-09 / IMP-10: add invoice decision context and repair the project navigation parent.
3. IMP-11 / IMP-12 / IMP-13: bounded photo previews, deferred optional analytics and settled/cancelled search; measure requests, transfer and memory before claiming savings.
4. IMP-14 / IMP-15: shared mobile target policy and full semantic status contract. This batch introduces foundational roles, not complete status consolidation.
5. Repeat the connected audit and release checks on the combined reviewed revision. The original customer invoice and wider OCR language/table-quality acceptance remain open.
