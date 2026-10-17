# E16-03 — Project profitability with a declared EUR basis

AI-assisted implementation/test/documentation drafting; external code copied: none.
Implemented on the development branch, pending exact-head CI and lead review.
Not merged/deployed; image/security/history/pilot acceptance blockers remain.
Sources: [uploaded-review reconciliation](../reviews/RECONCILIATION-2026-10-16.md),
ECC E16-03 and the existing [project design](../design/project-profitability.md).

## Result and invariants

Live detail, bulk project summary and close-time snapshot now share one batched
ledger projection. Original invoice/credit/expense/manual amounts are never rewritten.
Issued and credit-note net subtotals convert independently on their document dates.
Received-invoice net costs use stated FX where available, otherwise the existing
cached rate policy; expense amounts use their report currency and spending date,
without treating the receipt's original currency as the report currency. Unsupported
legacy receipt conversions are explicitly incomplete. EUR manual corrections remain
signed amounts. Existing lifecycle, approval, bin, line/split/header allocation and
tenant rules are retained.

The existing WO-8 convention applies: foreign units per EUR, conversion divides,
ROUND_HALF_UP cents, latest cached rate on/before the source date; the existing
outside-window/indicative approximation flag is preserved. Sources disclose original
amount/currency, reporting amount, rate/date/method, approximation and allocation.
Received allocation converts the whole base first; local line/split/remainder shares
partition that converted total, with residue on the largest source share and a
stable project-ID tiebreak (the established allocation principle). A three-way
USD0.03 at 2 USD/EUR case allocates exactly EUR0.02, not three independently rounded
EUR0.01 costs. Over-allocation is a visible incomplete source, not an invented total.

A missing/invalid conversion produces nullable figures and explicit issues, never
an assumed 1:1 rate or confident margin. The response basis contract is **version 2**
with reporting_currency, complete, issues and FX trace. The frontend refuses unknown
basis/version, missing required money strings and incomplete results. Gross fallback
costs receive a separate mixed-cost basis, preserving the existing fallback honestly.
This is live reporting with recorded rate provenance, not statutory/accountant acceptance.

New closed snapshots retain their versioned figures and conversion evidence. Rate
refreshes/late sources appear only as labelled live differences; they are not posted
accounting adjustments. Archiving a closed project now preserves the snapshot;
explicit reopen clears it under the existing audited transition. Older stored
snapshots without verified v2 evidence stay byte-for-byte unchanged and are shown
as unverified historical values, with no confident EUR profit. Malformed legacy
snapshots cannot crash the whole project list. Already-discarded historic snapshots
are not reconstructed or silently certified by this change.

The invoicing plan exposes an unknown remainder without failing a valid plan save.
Final-invoice composition refuses unknown EUR revenue before financial mutation;
generated documents cannot insert an unsupported invoicing remainder. The next-actions
surface shows a currency-review task instead of a fictitious final-invoice amount.
Unknown costs alone do not invalidate independently known issued revenue in the plan.
There is no database migration/backfill or changed legal invoice/payment ledger.

## Action descriptions and engineering contracts

| Control | Purpose / outcome / recovery | Engineering prompt |
|---|---|---|
| Project P&L / project list | Read supported EUR figures or explicit incomplete/unverified state; review source/rate evidence. | Keep one tenant-scoped Decimal projection; test EUR100+USD100=EUR190, credits, missing FX, allocations, report currency and frozen evidence. Never replace a missing figure with zero. |
| Currency conversion evidence | Inspect source units, rate/date/method and approximation. | Preserve source IDs/provenance and report schema version; do not imply an approximate cache rate is externally accepted. |
| Source PDF / received-source link | Download the authorized issued source PDF, or navigate to the actual received invoice. Pending disables repeated PDF clicks; failure has scoped explanation and safe read retry. | Reuse identity-bound downloadFile and permissions; assert browser download bytes match the API response; cover a failing read without success claims. |
| Invoicing plan / final-invoice draft | Save valid contractual rows even when a projection is incomplete; refuse unknown billing remainder before creating financial work. | Test successful save/readback with unknown FX, explicit composition refusal and the currency-review next action. |
| Close / archive / reopen | Freeze verified evidence; archive preserves it; explicit reopen discards under audit. | Refuse incomplete close atomically; preserve old JSON exactly; test rate changes, corrupt legacy input and archive/reopen history. |

## Verification and limits

Three independent initial regressions reproduced EUR200 instead of EUR190,
missing completeness metadata and an old snapshot presented as verified frozen EUR.
The repaired implementation passed **72 relevant backend tests** across project,
allocation, offer, template, next-action, costing and performance modules. A final
overlapping next-action/currency group passed 17 tests after the review-task change;
these are not additive coverage totals. The fixed-statement-count and detail/bulk
identity assertions remain enforced, including original monetary expectations.
One real migrated **non-superuser PostgreSQL** regression passed for Numeric money,
EUR190 and cross-tenant 404/empty-summary isolation; it is registered in the required
PG CI job. Documentation/PG-test-registration checks passed (9 tests).

Affected browser modules passed **27 tests**; they mock error/UI branches and are
not credited as 27 real-backend journeys. Separately, an **unmocked Chromium → Vite
→ FastAPI → PostgreSQL** synthetic journey observed EUR190, missing-rate refusal
and a real source-PDF download: 5,834 saved bytes exactly matched the HTTP response;
SHA-256 `fac45299178f1c3a88be2d225251c70152fde86a71c39efd76e9ee7c80069cc6`.
No browser errors, provider contact or production data. Sources were prepared via
real API writes; this was report/download browser verification, not browser-driven
invoice authoring or the entire E16-05 pilot/restore journey.

Ruff, whole-app mypy (415 files), frontend lint/type/build, current-tree PII and
unchanged bundle gate passed: **435.8 kB raw / 127.6 kB gzip**, budget 460/135.
No baseline, assertion, timeout or budget was relaxed. Existing mocked response
fixtures now explicitly declare valid v2 basis metadata; the monetary and query
count assertions remain. This statement-count result is not a minimum-hardware,
large-dataset latency or peak-RAM certification. Full-head CI remains required.

## Rollout / rollback / review

Review the nullable P&L/plan wire diff with current clients; deploy paired API/UI
artifacts. No migration or historical rewrite is performed. Users must review
unverified legacy history before an explicit correction/reopen; the agent does not
choose the old intended financial basis. Preserve the source records and snapshots.
Rollback must keep unsupported currencies/legacy bases visibly unavailable; returning
to raw cross-currency sums is not an acceptable financial rollback. Lead review must
cover FX date/rounding/provenance, archive retention, refusal/transaction paths and
the additional source-trace payload. Personal-finance and statutory approval claims
remain outside this slice.

Follow-up audit check: detailed source references remain in the tenant business
snapshot; the close audit records its original aggregate figures, source count,
basis version and SHA-256 of the exact stored snapshot. It does not copy source
document numbers into audit metadata. The snapshot/audit/privacy regression group
passed 32 tests, retaining required atomic audit events.
