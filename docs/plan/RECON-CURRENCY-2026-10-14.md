# Reconciliation source currency — 2026-10-14

Implemented locally for review; no push, merge or deployment. This continues the
supplied reviews' financial-trust recommendation after the capture/payment slices.
AI-assisted implementation and validation; existing architecture and dependencies.

## Problem and resulting behavior

`bank_lines.currency` already retained structured-statement currency and the
matching service already refused stated non-EUR units. `BankLineOut` discarded
that field and the reconciliation page labelled every amount EUR. A USD100 line
therefore appeared as €100; an unstated unit appeared equally certain.

The API now includes nullable source currency on list and status-mutation results.
The page uses that unit and prints the code beside the formatted amount. Missing
units display `Currency unknown`, without a euro symbol, and explain that the
existing matching flow assumes EUR and needs verification before confirmation.
Foreign lines have a disabled Find match button with an adjacent explanation;
the existing server guard still rejects direct attempts. Ignore/Restore remain
available; ignoring is a review disposition, not evidence of settlement.

CSV imports now retain optional `currency`, `ccy`, or `transaction currency`
columns, trimming/uppercasing three-letter codes. Blank/absent stays null.
A malformed nonblank unit on an imported row rejects the whole request with its
row number and no echoed source value; no partial statement is persisted.
This checks code shape, not a complete ISO-code registry. Existing camt/MT940
currency parsing is unchanged. Candidate responses name their current EUR ledger
unit; the page identifies receipts, reimbursements, supplier runs and direct
invoice payments distinctly.

No migration, monetary recalculation, FX conversion, data backfill or new
dependency is required. Historical CSV/PDF lines whose currency was not stored
remain unknown. The legacy policy permitting unknown-unit matches is preserved
and disclosed, not represented as verified currency. The shared CSV parser also
serves expense intake; expense persistence/reporting is outside this slice.
Account/session isolation, durable reconciliation commands, allocation controls
and complete bank balances remain separate roadmap work.

## Button/action descriptions and maintenance prompts

| Action | Outcome | Engineering prompt |
|---|---|---|
| Statement file | Imports a selected source; errors remain visible. Optional CSV unit is retained. | Preserve source currency through parser, persistence and response; refuse malformed explicit units without logging source values or committing partial rows. |
| Reconcile / Hide lines | Shows/hides the statement's line list. | Keep the list tenant-scoped and expose nullable source currency; never replace missing units with EUR during serialization or rendering. |
| Find match / Close | Opens/closes candidates; disabled for stated foreign units with visible explanation. | Keep the UI consistent with the server's currency guard; prove a USD line cannot offer or confirm EUR candidates. |
| Match | Associates the chosen cash target and refreshes committed status. | Display the target's currency and exact kind; verify that a real EUR match persists, with amount/tenant/concurrency controls intact. |
| Ignore | Marks a line ignored without settling a cash record. | Retain the source currency in mutation results and distinguish ignored from matched settlement. |
| Unmatch | Clears the association for review. | Preserve source monetary values and currency while clearing only the existing reconciliation association. |
| Restore | Returns an ignored line to unmatched review. | Preserve unknown/foreign units; restoration must not make an ineligible unit matchable. |
| Retry reads | Repeats a failed statements/lines/candidates request. | Retain visible error recovery and never turn a failed read into a false empty result. |

## Validation evidence

- 47 backend regressions passed across the new contract (initial nine cases),
  reconciliation currency/matching, direct payments and bank CSV/PDF/camt parsers.
- Final contract suite: 10 passed, adding camt import → API → refusal/match checks.
- Two Chromium browser regressions passed: source/unknown unit rendering and
  foreign disabling; EUR candidate kind/currency and completed matching.
- Unmocked local SQLite API → Chromium: synthetic registration, receipt, actual
  CSV import, USD/EUR/null displayed, EUR match persisted, USD remained unmatched.
- Backend Ruff and whole-app mypy passed; frontend lint, production build and
  bundle gate passed (433.7 kB raw / 127.0 kB gzip; limits 460 / 135).
- OpenAPI regenerated. No new database behavior requires a migration or
  PostgreSQL-specific test in this slice. Full CI and visual suites were not run.

Rollback is the scoped code/API/UI change; no rows need rewriting. Newly retained
CSV currency should remain stored rather than be discarded during a rollback.
