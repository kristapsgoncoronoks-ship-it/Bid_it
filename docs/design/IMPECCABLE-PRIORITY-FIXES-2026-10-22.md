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

The UI repair changes no backend source, schema, financial calculation, workflow security gate or screenshot tolerance. Existing system typography and light theme are retained. A fresh CI advisory for `pypdf` is handled as a separate dependency repair in this candidate; its qualification is recorded separately from the frozen UI evidence.

## Action contracts

| Action | Outcome and recovery | Follow-up development prompt |
| --- | --- | --- |
| Confirm reviewed invoice | The displayed monetary unit follows the reviewed draft. The existing server commit/fingerprint contract remains authoritative. | Keep display currency coupled to the canonical review across manual edits, region application and readback. Never substitute EUR for missing currency or introduce implicit FX. |
| Retry Next actions | Read current tasks with GET; show progress, preserve other dashboard sections and avoid replaying Done/Dismiss. | Preserve local error ownership, cached-data permission eviction and identity fences when extending dashboard task controls. Test unavailable and forbidden responses separately. |
| Select a tab | Bring the selected control into the local strip without moving the whole document. | Retain roving keyboard navigation and selected-control visibility at narrow widths, text enlargement and desktop-to-mobile resize. |
| Close or submit a dialog | Reach controls by keyboard and local scrolling at short heights/enlarged text. | Exercise standard footers and form-owned action rows without weakening focus trapping or restoring page-level overflow. |
| Inspect Automation rows | Scroll the named table region to reach columns/actions; execute the existing action once. | Preserve publication/command semantics while improving mobile table presentation; do not fork the workflow into a decorative preview. |

## Qualification and limits

The [delivery receipt](evidence/IMPECCABLE-PRIORITY-FIXES-2026-10-22.json)
records source identities, both broad browser runs and hashed evidence.
The final complete local browser run passes **781/781 with zero retries, skips
or flaky classifications**, with failure traces enabled. The original failed
run remains recorded below. Ordinary final-head hosted CI and human/Lead review
remain required; this is not release clearance.

Evidence is retained under `/workspace/artifacts/impeccable-fixes-2026-10-22/`, with large connected/re-audit artifacts in the task-specific `/tmp` directories named in their receipts. These are synthetic fixtures, never customer invoice acceptance or production sizing.

Shared-control measurements are primary text **6.11:1**, secondary captions
**7.58:1** on white and success confirmation **5.36:1**. The matched rendered
capture detector reports **10 → 2** hits against immutable PR #77. The remaining
two are the logotype contrast exception and a legitimate editable-table border;
this is not an application-wide accessibility score.

The actual connected journey uploaded USD 100 through the durable extraction
worker, saved CAD then USD corrections with new fingerprints, confirmed with
HTTP 201 and rejected duplicate creation with HTTP 409. Independent PostgreSQL
readback found one USD 100 invoice, one extraction job and one create audit.
Its separate EUR 92.53 reporting value used bundled seeded FX rates; no live
provider refresh or implicit preview conversion occurred. No email was sent.

Lint, type checking, production build and the unchanged entry-bundle budget pass:
**436.3 kB raw / 128.7 kB gzip**, against **460 / 135 kB**. These are asset sizes,
not production latency or hardware-capacity measurements.

### Newly reported PDF dependency findings

The branch-only visual-preparation run exposed a genuine dependency-audit
failure for `pypdf` 6.21.0, separate from the UI changes. The pin advances to
6.21.2, covering [GHSA-5hx5-vpf9-5wcv / CVE-2026-113786](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5hx5-vpf9-5wcv)
and the newer cross-reference parsing advisories
[GHSA-gmh7-3g9r-mjh5](https://github.com/py-pdf/pypdf/security/advisories/GHSA-gmh7-3g9r-mjh5)
and [GHSA-jr96-gvgh-j3r4](https://github.com/py-pdf/pypdf/security/advisories/GHSA-jr96-gvgh-j3r4).
Fresh runtime/development requirements auditing resolves 78 packages with zero
known vulnerabilities and no ignored advisories. All **136 existing PDF/OCR,
archive, email and report compatibility tests pass** without failures or skips
in an isolated patched Python 3.12 environment, including the supervised child
parser; the original UI/connected evidence retains its earlier dependency
identity. Upstream fix evidence is not a local exploit-reproduction claim.

### Browser-load investigation

The first complete local run passed 780 of 781 cases. The remaining Claim status
case displayed the lazy-page load recovery boundary; its original network/error
cause was not captured. Its source differs from PR #77 only in caption colors,
and the unchanged focused cohort passes 22/22, followed by 66/66 instrumented
repetitions with zero failed requests or page-load errors. No application or test assertion was
changed to make it pass. Preserve this event and its investigation separately
from repaired defects; a clean rerun does not establish the original cause.

**Follow-up prompt:** capture the rejected module request, HTTP result and
`PageLoadError.cause` if this recurs in normal CI or production. Reproduce that
cause before changing loader behavior; preserve the existing reload recovery,
test assertions, retry policy and screenshot budgets.

Focused browser checks establish the reported defects before repair and exercise the repaired controls. The broader functional suite, production build and bundle budget are recorded in the final delivery receipt. The connected currency journey separately checks browser display, actual API responses and independent PostgreSQL invoice/effect counts. Rendered Impeccable comparisons use identical fixtures on immutable PR #77 and this candidate; they do not manufacture a new whole-app rubric score.

Visual baselines must be generated and reviewed in the repository's version-matched Playwright CI image. Host-generated differences are not accepted as Linux CI goldens. Existing absolute pixel allowances remain unchanged. A branch-only baseline refresh is preparation, not normal CI or release clearance; the final PR head still needs its ordinary checks.

The branch-only CI job generated all 13 references; 11 changed and two remained
byte-identical. The bot commit is a direct, PNG-only child of the frozen UI
source. Independent visual review plus the parent agent's representative review
found the intended darker controls/captions, larger navigation labels and a
one-pixel gallery shift from tab-strip padding. These are AI-assisted candidate
reviews, not the human/Lead approval required by the repository's
[AI engineering policy](../architecture/AI-ENGINEERING-POLICY.md). The draft PR
must retain that review and normal CI as merge requirements.

AI assistance covered implementation, tests, upstream security research and
documentation. Independent agents reviewed the other authors' changes; source
hashes, failure-first cases, actual server/database evidence and CI-generated
images support review. No external implementation code was copied into the app.
The pinned Impeccable tool/skill was used outside the app runtime.

The audit's historical **11/20** score describes main `90891d8d`. It is not revised by this bounded repair. Screen-reader, physical touch device, every status palette and every app workflow remain outside this qualification. At 200% root text the currency dialog fits and its controls remain reachable, but the underlying Currencies page still has horizontal overflow in a 390px viewport; full-page enlarged-text reflow remains follow-up work.

## Remaining ordered work

1. Integrate and qualify existing menu/planning candidates for IMP-07, IMP-08 and IMP-16; resolve sibling migrations before combining planning and selected-region OCR.
2. IMP-09 / IMP-10: add invoice decision context and repair the project navigation parent.
3. IMP-11 / IMP-12 / IMP-13: bounded photo previews, deferred optional analytics and settled/cancelled search; measure requests, transfer and memory before claiming savings.
4. IMP-14 / IMP-15: shared mobile target policy and full semantic status contract. This batch introduces foundational roles, not complete status consolidation.
5. Repeat the connected audit and release checks on the combined reviewed revision. The original customer invoice and wider OCR language/table-quality acceptance remain open.
