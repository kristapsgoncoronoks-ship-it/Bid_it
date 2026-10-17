# R012 — replay-safe AR cash and explained corrections

Status: implemented locally for review on 13 October 2026. CI, merge and production
deployment are not claimed. This completes the requested local R011 → R002 → R012
sequence after the N1 supplier-identifier slice.

The supplied **Bid_it development review** preview reported the wrong payment
permission, Mark paid using an invoice's original total after credits, duplicate
secondary effects on retry, and unexplained negative entries. These are review
findings, not instructions expanding the user's authorized development scope.

## Contract

- `POST /issued/{id}/payments` requires `payment.write`, a positive incremental
  amount in exact cents, invoice currency, reviewed outstanding balance and an
  `Idempotency-Key`. Method, date and reference are captured with the entry.
  The invoice lock refreshes aggregate state before validating the balance.
- Tenant/key uniqueness binds the command to its invoice, action and normalized
  input digest. Identical retries return the original entry plus current invoice
  state. Changed inputs refuse; they cannot reuse a key or silently record cash.
- Cash, derived invoice balance, mandatory audit, keyed webhook intent and durable
  jobs commit together. Cash command callers opt into strict webhook enqueue;
  other emit callers retain their existing best-effort behavior. A missing audit
  or failed job enqueue rolls the entire cash transaction back.
- `GET /issued/{id}/payment-commands/{key}` and payment history require
  `payment.read` and tenant/invoice scoping. They support committed-status recovery.
- `POST /issued/{id}/payments/{payment_id}/reverse` requires `payment.write`,
  reviewed balance/currency, a separate command key and a nonblank reason. It
  appends a full negative offset with a unique, same-tenant reversal link. It
  never edits the original cash entry. Correction to a smaller payment means a
  full explained reversal followed by an explicit positive replacement.
- Receipt-linked entries stay in receipt/bank allocation correction workflows;
  links in payment history lead to those screens. Negative originals, previously
  reversed entries, or historical unlinked negative corrections refuse direct
  reversal. Historical entries are not guessed into a new attribution model.
- The legacy cumulative `PATCH /payment` now requires `payment.write`, refuses
  decreases, and treats unchanged totals as a true no-op without changing dates,
  audit, webhook or job counts. A deterministic legacy key binds an increase to
  exact cumulative inputs; repeating that historical input after a reversal
  returns current state rather than applying cash again. New clients should use
  explicit positive commands; genuinely new cash with the same cumulative inputs
  cannot be distinguished by the legacy contract.
- Browser writes bind expected account and company. One account/company/invoice
  recovery slot covers both recording and reversal. An unresolved command blocks
  replacement cash actions. Its bounded path/amount/date/method/reference/reason
  envelope and key are retained in tab session storage so **Retry previous payment**
  can resend the original inputs after refresh, after lookup. These financial
  inputs persist until resolution or tab closure; invoice creation forms still
  retain only key/digest, not customer contents.
- A definite balance/currency/correction refusal permits a reviewed new command.
  An uncertain response retains the original command. UI success awaits relevant
  query invalidations. Permission-aware controls and history remain available
  for fully paid invoices; bank reconciliation remains a separate operation.

## Buttons, descriptions and development prompts

| Button | Description | Engineering prompt |
|---|---|---|
| Mark paid | Record only the reviewed remaining balance as a bank transfer. | Preserve credited/partial outstanding as the incremental command amount; require payment.write, cap under lock, replay one entry and await refresh. |
| Record… | Open an additional-payment form with a reviewed balance. | Capture positive amount, method, reference and date; keep the reviewed balance fixed during editing and show currency. |
| Record payment | Commit the positive command once. | Validate exact cents and currency, retain durable command identity, reject stale balance and preserve inputs on an uncertain response. |
| Payment history | Show the scoped append-only ledger, including after full payment. | Require payment.read, expose source/reversal links and display loading, empty and recoverable errors. |
| Hide payments | Collapse the ledger without changing cash. | Hide only presentation; retain pending-command protection and confirmation state. |
| Retry history | Reload history after a read failure. | Refetch the account/company/invoice query and surface failure without claiming any financial write. |
| Reverse payment | Open confirmation for a positive direct entry. | Keep receipt allocation correction in its existing workflow; show full offset and original date; disable already reversed and unauthorized actions. |
| Confirm reversal | Append a reasoned full offset. | Require a trimmed explanation, current reviewed balance and command key; retain original entry, unique reversal link, audit and keyed intent atomically. |
| Check previous payment | Read the committed result of an uncertain command. | Look up the saved key in the same scope; clear only a found result and keep 404/transport outcomes recoverable. |
| Retry previous payment | Lookup, then replay the exact saved cash envelope if needed. | Preserve key, original path and inputs across refresh; forbid replacement commands while unresolved; recover both cash and reversal. |
| Cancel (editor/confirmation) | Close local inputs without altering ledger history. | Do not send a financial request or discard unresolved command identity when closing either form. |

## Migration and compatibility

Migration `a6b7c8d9e0f1` follows `f5a6b7c8d9e0` and adds nullable payment command
key/hash and reversal identity, tenant/key and tenant/reversal uniqueness, and a
deferred same-tenant self foreign key. Historical balances/entries are unchanged.
Apply migrations before serving the new application. Application rollback may
leave additive columns; database downgrade loses replay/correction evidence and
requires resolving uncertain requests and retaining evidence first. Existing
legacy clients that send decreases must move to explained reversal; no new
refund, tax or bank reconciliation policy is invented.

## Evidence and limits

Final local checks: **121 backend regressions passed**, **18 browser checks
passed**, and **9 PostgreSQL checks passed** in the combined financial/RLS run.
The final R012-only PostgreSQL rerun passed both synchronized race cases. The
six existing migration tests passed (empty upgrade/downgrade/re-upgrade and
model parity), and local SQLite/PostgreSQL upgrades to the new head succeeded.
Ruff, whole-app mypy, frontend lint/production build and the bundle budget passed.
First load measured **433.7 KiB raw / 127.0 KiB gzip**, within unchanged budgets.

Recorded verification covers HTTP financial refusals, replay after reversal,
secondary-effect counts, audit/job rollback, positive-only legacy behavior,
partial credits, structural payment permissions, tenant lookup and foreign-key
isolation, receipt routing and historical-negative refusal. PostgreSQL HTTP races
use independent pooled connections and a non-superuser/non-bypass-RLS role, with
three synchronized contenders for same-key and distinct-key payment/reversal.
The new PostgreSQL tests and previous R011 races are included in the CI job list;
browser specs are included in `npm run test:e2e`.

An unmocked Chromium → API → migrated PostgreSQL check issued EUR121, credited
EUR24.20, recorded EUR20, used Mark paid for EUR76.80, replayed the same command,
then reversed EUR76.80 with a reason. Ledger entries EUR20 + EUR76.80 − EUR76.80
explain amount_paid EUR20 and outstanding EUR76.80. No real money or external
webhook provider was contacted. Automated browser specs separately cover lost
responses, refresh recovery, stale refusals and read-only controls.

No new application dependency/framework or financial hardware baseline was
introduced. The frontend first-load bundle gate is checked against its existing
460 KiB raw / 135 KiB gzip budget. Full backend/browser CI, production/provider
delivery and the separate ECC whole-application session/cache isolation repair
are outside this completed local slice.

An additional diagnostic, PostgreSQL `alembic check`, remains non-green because
it compares four existing column-selective `SET NULL` foreign keys against their
portable model declarations: expense-item/invoice/issued-invoice project links
and the issued-invoice issuer link. There is no reported payments schema drift.
The original migration `c4d6e8f0a2b4` deliberately preserves `org_id` using
`SET NULL (project_id)` / `SET NULL (issuer_id)`; this change does not replace
those safeguards. The existing DB-017/DB-018 PostgreSQL delete-isolation test
passed. A final R012-only browser rerun passed all seven cases after the saved
request-path check was tightened.
SQLite `alembic check` is clean. See the existing
[DB-017/DB-018 review](../audit/2026-09-05/FINDINGS-AND-BACKLOG.md).

AI-assisted: implementation, action prompts and validation were drafted and
executed by the coding agent using the existing project architecture.
