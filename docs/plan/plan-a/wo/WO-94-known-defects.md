# WO-94 — two known defects, each with its evidence (G2.7 D7 · backlog N3)

> Neither defect is speculative. Each was found by an earlier order in this
> programme, recorded with a source, and deliberately left:
>
> - **G2.7 / D7** — reported by WO-93's recon and written into
>   `docs/plan/plan-a/wo/WO-93-client-claim-status.md` § *"Recorded defect, not
>   fixed here"*, and into `app/services/transport/client_status.py`'s module
>   docstring: `lock.withdraw_claim` sets `status = "withdrawn"` but leaves
>   `status_code` populated, while `BA_fleet_fuel.md` §3.D **D7** says
>   withdrawal *"also NULLs `status_code`"*.
> - **N3** — `docs/BACKLOG.md` row N3: a route hard-codes
>   `_MAX_UPLOAD = 15 MB` duplicating `filesec._max_bytes()`
>   (`settings.max_upload_mb`). *"Two sources of truth drift; a config change
>   silently doesn't take effect at the route."*

**WORK ORDER 94 — the two recorded defects, fixed where each is owned:
`app/services/transport/lock.py` (`withdraw_claim` NULLs `status_code`, audited
old→new in the same transaction, plus the backfill migration for rows already
carrying the inconsistent pair) and `app/services/filesec.py` +
`app/api/routes/{invoices,expenses,reconciliation,invoice_review,issued,issuer}.py`
(one configured upload cap, read rather than re-typed, with an AST scanner that
refuses a second definition). Effort S 1–2d. Priority P1. Milestone M5.
Depends on: WO-59 (G2.7 — the `status_code` column and its only other writer,
`status.set_status_code`, whose audit meta shape this order reuses), WO-93 (the
recon that found D7, and the pinned immunity test that must keep meaning what
it says), WO-85/WO-87 (the AST-scanner-with-a-seeded-violation-self-test
precedent).**

---

## RECON — both defects, verified independently before any fix

### Defect 1 (G2.7 / D7) — REAL

`docs/plan/shared/specs/BA_fleet_fuel.md` line 545, verbatim:

> **D7.** Rejection **keeps** locks (mirrors 3B's `approved` engine state). Only
> `withdraw_claim` releases, and it also NULLs `status_code`.

`app/services/transport/lock.py::withdraw_claim` deletes every
`VatClaimedInvoice` row and sets `claim.status = "withdrawn"`. It never touches
`claim.status_code`. The stale value is not hypothetical: `submit_claim` stamps
`status_code = "2"` on every claim that can ever reach a withdrawable state
(withdraw refuses any status outside `submitted`/`approved`/`paid`), so **every
withdrawn claim in this codebase carries a code**, and `set_status_code` may
have moved it on to `2B`/`3D`/… before the withdrawal.

The proof is already a green test — `tests/transport/test_wo93_client_status.py`
`::test_wo93_a_withdrawn_claim_is_not_shown_and_its_stale_code_is_never_read`
asserts `withdrawn.status_code == "2"` *after* a real `withdraw_claim` call,
labelled *"the stale code this surface must not read"*.

Blast radius today: `client_status.py` dispatches on the engine status first and
is immune by construction (WO-93 built it that way deliberately, and pinned it).
`/vat-claims` and every other operator surface renders `status_code` straight
off the row, so a withdrawn claim reads as *"Submitted"* / *"Under appeal"*
beside its `withdrawn` engine status. Nothing re-derives money from the code, so
no euro is wrong — this is a **truthfulness** defect in the claim record, not a
financial one.

### Defect 2 (N3) — REAL, and wider than the backlog row

The backlog cites `routes/invoices.py:55`; the constant is at **line 73** today
(`_MAX_UPLOAD = 15 * 1024 * 1024`), enforced at line 802. The harm the row
predicts was reproduced, both directions, through the real HTTP path:

```
# max_upload_mb = 50, a 20 MB PDF
POST /api/v1/invoices/upload -> 413 {"detail":"File too large (max 15 MB)"}
# the configured cap is raised and the route does not notice.

# max_upload_mb = 1, a 5 MB PDF
POST /api/v1/invoices/upload -> 415 {"detail":"File too large (max 1 MB)"}
# the route lets it through; filesec refuses it — as 415 Unsupported Media
# Type, which is the wrong status for a size refusal.
```

A sweep (`grep -rn "1024 \* 1024" backend/app`) found **seven** hard-coded byte
caps across **six** route modules, not one:

| Site | Cap | filesec gate it precedes | Relation to `max_upload_mb` (15) |
|---|---|---|---|
| `routes/invoices.py:73` | 15 MB | `filesec.check` | duplicate of the configured value |
| `routes/expenses.py:254` (bank statement) | 15 MB | `filesec.check` | duplicate |
| `routes/reconciliation.py:64` | 15 MB | `filesec.check` | duplicate |
| `routes/expenses.py:309` (receipt scan) | 5 MB | `filesec.check` | deliberately TIGHTER |
| `routes/expenses.py:1181` (receipt attach) | 5 MB | `filesec.check` | deliberately TIGHTER |
| `routes/issuer.py:141` (logo) | 2 MB | `filesec.check` | deliberately TIGHTER |
| `routes/invoice_review.py:73` + `routes/issued.py:1267` (`_ATTACH_MAX`) | 25 MB | `filesec.reject_active_content` | **LOOSER — and therefore DEAD** |

The last row is a second finding. `reject_active_content` caps at
`_max_bytes()` too, so the 25 MB promise is unreachable:

```
_ATTACH_MAX (invoice_review) MB = 25
_ATTACH_MAX (issued)         MB = 25
filesec._max_bytes()         MB = 15
16 MB attachment: REJECTED by filesec -> File too large (max 15 MB)
```

Two routes advertise 25 MB in their own 413 message and refuse at 15 MB with a
415 quoting a different number. That is the same defect N3 names, in its most
misleading form.

---

### Objective and business value

Both defects are *"two definitions of one truth"*. In the claim lifecycle the
two definitions are the spec's and the code's, and the record loses: a withdrawn
claim keeps saying it is submitted. In the upload path they are the route's and
the operator's, and the operator loses: `MAX_UPLOAD_MB` is documented in
`docs/architecture/foundation.md` as *"Upload size cap"* and, on the primary
capture endpoint, it is not.

Who pays. The claim record is the audit-ready financial record customers buy
(master-context §1); an accountant reconciling a withdrawn claim against a
filing sees a status pair that contradicts itself, and every hour spent
resolving that is an hour the product was supposed to save. The upload cap is
the first thing a customer hits when they onboard a year of scanned PDFs: today
raising `MAX_UPLOAD_MB` for them is a support ticket that silently achieves
nothing, and the operator who set it has no way to know.

### Scope

**In scope**
- `withdraw_claim` NULLs `status_code`, in the same transaction, audited
  old→new (§4.16), with **no other lifecycle behaviour altered** — the same
  status flip, the same lock deletion, the same refusals, the same audit action.
- The backfill for rows already carrying the inconsistent pair, as an Alembic
  data migration with a printed reconciliation count.
- One configured upload cap: `filesec.max_bytes()` becomes the single public
  source, every route reads it, no route re-types a byte literal.
- The purpose-specific tighter caps (receipt 5 MB, logo 2 MB) survive as
  **named policy in `filesec`**, clamped by the configured general cap.
- An AST scanner (`tests/test_upload_cap_single_source.py`) refusing any second
  cap definition anywhere under `app/`, with a seeded-violation self-test.

**Out of scope**
- The `ENGINE_OF` engine-state transitions (`3`→`approved`, `3A`→`paid`, …) —
  still G2.9-entangled and decision-gated (`status.py`'s own docstring,
  `docs/DECISIONS-NEEDED.md` §10). This order touches exactly one column on
  exactly one transition.
- Any change to *which* engine statuses hold locks (R5) or to withdraw's
  refusal (`claim_not_locked`).
- Streaming/chunked upload limits, `client_max_body_size` at the proxy, or the
  ASGI request-body cap (`docs/DEPLOYMENT.md` checklist) — a deployment
  concern, unchanged here.
- Raising or lowering any effective limit. Every cap this order touches keeps
  the value that is *actually enforced today* (see BEHAVIOUR CHANGE below).

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/lock.py` | `withdraw_claim` NULLs `status_code`; audit meta carries old→new |
| `backend/alembic/versions/<new>_wo94_withdrawn_claims_null_status_code.py` | **new** — backfill + reconciliation print |
| `backend/app/services/filesec.py` | `max_bytes(purpose=None)`, `too_large_message(purpose=None)`, `PURPOSE_MB` |
| `backend/app/api/routes/invoices.py` | drop `_MAX_UPLOAD`; read `filesec.max_bytes()` |
| `backend/app/api/routes/expenses.py` | three caps read `filesec.max_bytes(...)` |
| `backend/app/api/routes/reconciliation.py` | cap reads `filesec.max_bytes()` |
| `backend/app/api/routes/issuer.py` | logo cap reads `filesec.max_bytes("logo")` |
| `backend/app/api/routes/invoice_review.py` | drop `_ATTACH_MAX`; read `filesec.max_bytes()` |
| `backend/app/api/routes/issued.py` | drop `_ATTACH_MAX`; read `filesec.max_bytes()` |
| `backend/tests/transport/test_wo94_withdraw_status_code.py` | **new** |
| `backend/tests/test_wo94_upload_cap.py` | **new** — behaviour + the AST scanner |
| `backend/tests/transport/test_wo93_client_status.py` | the pinned immunity test keeps its meaning (see below) |
| `docs/transport/rules.md` | the D7 rule row gains its consumer |
| `docs/BACKLOG.md` | N3 moves out |
| `TODO.md` | WO-94 row + suite line |

### BEHAVIOUR CHANGE — stated loudly, not slipped in

1. **`withdraw_claim` now writes a second column.** A withdrawn claim's
   `status_code` becomes `NULL` where it previously kept its last value. Any
   consumer reading `status_code` on a withdrawn claim sees `None`. Verified
   consumers: `client_status.py` (immune by construction — engine status first),
   `/vat-claims` (renders the code; a withdrawn claim now correctly shows no
   code), `status.set_status_code` (already refuses a withdrawn claim with
   `claim_not_submitted`, unchanged). No query filters on `status_code`.
2. **Existing withdrawn rows are repaired by migration.** The pre-repair value
   is not recoverable on downgrade, and deliberately so — it contradicted D7.
3. **Upload size refusals change status code on three paths.** Where a file
   exceeded the *configured* cap but not the route's larger hard-coded one, the
   response was `415 Unsupported Media Type` from `filesec`; it is now `413
   Content Too Large` from the route. Same acceptance decision, correct status.
   Concretely: the two 25 MB attachment paths (16–25 MB files) and any path
   where an operator has lowered `MAX_UPLOAD_MB`.
4. **Raising `MAX_UPLOAD_MB` now takes effect on `POST /invoices/upload`,
   `/expenses/import/bank-statement` and `/reconciliation/import`.** With the
   default of 15 the accepted set is byte-identical to today; with a raised
   configured cap these three endpoints accept more than they did. This is the
   fix N3 asks for, and it is the one place this order changes what the system
   accepts.
5. **No cap is loosened at its default.** The three 15 MB duplicates equal the
   default; the two 25 MB constants were already unreachable (effective 15 MB)
   so lowering them to the configured cap changes no acceptance decision; the
   5 MB and 2 MB caps stay 5 MB and 2 MB, now clamped so that lowering
   `MAX_UPLOAD_MB` below them takes effect there too.

### Implementation guidance

1. Characterise first: a test that asserts today's `withdraw_claim` leaves the
   code, and the two probe assertions above for the cap. Confirm green, then fix
   and invert them.
2. `withdraw_claim`: capture `old_code = claim.status_code`, set
   `claim.status_code = None` **beside** the existing `claim.status =
   "withdrawn"`, before the single `await db.flush()` — one transaction, one
   flush, one audit call, unchanged ordering. Audit meta gains
   `{"old_status_code": …, "new_status_code": None}`, the field names
   `status.set_status_code` already uses (§4.16 old→new; the names are reused so
   an audit consumer cannot need two shapes for one column).
3. The migration is a pure `UPDATE … SET status_code = NULL WHERE status =
   'withdrawn' AND status_code IS NOT NULL`, the shape
   `ee6f191d4b4f_rename_sysadmin_role_to_owner.py` established. It **counts
   first and prints** the rows it will repair (a reconciliation report an
   operator can check), and is idempotent. The repair is trivially derivable —
   D7 states the one correct value for every such row and it is a constant —
   so it is done rather than reported. `downgrade()` is a documented no-op:
   the discarded value was invalid by the rule the upgrade enforces.
4. `filesec.max_bytes(purpose=None)` returns the configured general cap, or
   `min(PURPOSE_MB[purpose] * MB, general)` for a named tighter purpose — so
   *lowering* the configured cap always takes effect everywhere, and a purpose
   can never widen it. `too_large_message(purpose)` renders the matching
   sentence from the same number, so no route can quote a figure it does not
   enforce.
5. Every route replaces its literal with `filesec.max_bytes(...)` and its
   message with `filesec.too_large_message(...)`, keeping its existing 413.
6. The scanner walks `app/**/*.py` and flags (a) any `N * 1024 * 1024` byte
   literal outside `filesec.py`, and (b) any `len(...) > <not a
   filesec.max_bytes call>` comparison in a route module. Seeded-violation
   self-test in the same file (template rule 6).

### The WO-93 pinned test — kept, and kept meaning what it says

`test_wo93_a_withdrawn_claim_is_not_shown_and_its_stale_code_is_never_read`
asserts two things: that a real `withdraw_claim` leaves `"2"` behind, and that
the portal is immune to whatever code a withdrawn claim carries. The first
assertion is a statement about the defect and *must* change — leaving it would
mean the fix did not happen. The second is the immunity, and it is the point of
the test.

So the test is **strengthened, not weakened**: it now asserts the fix
(`status_code is None` after withdrawal), then writes a stale code back onto the
row by hand — exactly the shape a pre-WO-94 database holds — and asserts the
portal still shows nothing. The immunity is now proven against a row the
lifecycle can no longer produce, which is strictly more than it proved before.
Its docstring records that the underlying cause is gone and why the pin stays.

### Invariants this order must preserve

- **§4.16 (every mutation audited, old→new in the same transaction).** The
  `status_code` clear rides the existing `TRANSPORT_CLAIM_WITHDRAW` event, in
  the caller's open transaction; the meta gains old→new. No second audit row —
  one operation, one event.
- **§4.17 (never rewrite an audit row).** The backfill touches
  `vat_refund_claims` only. No audit row is edited; the hash chain is untouched.
- **§4.4 (opaque 404).** `withdraw_claim`'s org-scoped `_get_claim` is
  unchanged.
- **R5 (only `withdraw_claim` releases locks).** Untouched — the delete, the
  `_LOCK_HOLDING_STATUSES` refusal and the structural single-deleter test all
  stand.
- **§4.20 (wire contract).** No response shape changes. The upload paths keep
  `{"detail","code"}`; only which status code carries a size refusal moves, on
  the paths named above.
- **§3 (no business logic in a route).** The caps move *out* of the routes into
  `filesec`; the route keeps a boundary check and the HTTP mapping.

### Database / migration impact

One data-only revision on the existing `vat_refund_claims` table. No column, no
table, no index, no RLS policy (no new tenant table — the tenancy parity set is
unchanged, so `tests/test_rls.py::test_rls_migration_covers_every_tenant_table`
needs no new entry). Backfill rule: `status = 'withdrawn' AND status_code IS NOT
NULL → NULL`, printed and counted. Downgrade: safe no-op; it loses the ability
to restore a value the rule says must not exist.

### Testing requirements

`backend/tests/transport/test_wo94_withdraw_status_code.py`
- `test_wo94_withdraw_nulls_the_status_code` — D7's own sentence.
- `test_wo94_withdraw_nulls_a_manually_set_code_too` — set `2B` via
  `set_status_code`, withdraw, assert `None` (the code is not merely "the `2`
  submit stamped").
- `test_wo94_the_withdraw_audit_event_carries_old_to_new` — one
  `transport.claim_withdraw` row, `meta["old_status_code"] == "2"`,
  `meta["new_status_code"] is None`.
- `test_wo94_withdraw_changes_nothing_else_on_the_claim` — every other column
  (`ref_period`, `refund_country`, `entity_id`, `vat_eur`, `action_deadline`,
  `status_note`, …) is byte-identical before and after; only `status` and
  `status_code` move.
- `test_wo94_a_withdrawn_claim_cannot_be_resubmitted_or_recoded` — `submit_claim`
  refuses with `claim_not_draft`, `set_status_code` with `claim_not_submitted`,
  and the code stays `None` after both refusals.
- `test_wo94_the_backfill_repairs_a_legacy_row` — drive a row into the legacy
  shape, run the migration's repair predicate, assert `NULL` and idempotence.

`backend/tests/test_wo94_upload_cap.py`
- `test_wo94_upload_under_the_configured_cap_is_accepted` / `…_over_the_cap_is_413`.
- `test_wo94_raising_the_configured_cap_takes_effect_at_the_route` — the recon
  probe, inverted: `max_upload_mb = 50` → a 20 MB PDF is no longer 413.
- `test_wo94_lowering_the_configured_cap_takes_effect_at_the_route` — 1 MB → a
  5 MB PDF is 413 from the route, message quoting **1 MB**.
- `test_wo94_a_purpose_cap_is_tighter_and_cannot_exceed_the_general_cap`.
- `test_wo94_no_second_upload_cap_is_defined_anywhere` — the AST scan.
- `test_wo94_the_cap_scanner_detects_a_seeded_violation` — the self-test.

Authorization and cross-tenant: both surfaces are already covered
(`tests/transport/test_wo76_claim_routes.py` for withdraw's `VAT_SUBMIT` grant/
denial and its cross-tenant 404; `tests/test_access.py` for the upload route).
This order adds no route, no permission and no new tenant surface, so it adds no
new authorization case — it must leave those green unmodified.

### Acceptance criteria (verifiable checklist)

- [ ] `lock.withdraw_claim` returns a claim with `status == "withdrawn"` and
      `status_code is None`.
- [ ] Exactly one `AuditEvent` with `action == "transport.claim_withdraw"`, its
      `meta` containing `old_status_code` and `new_status_code: null`.
- [ ] `grep -n "status_code" backend/app/services/transport/lock.py` shows the
      submit stamp and the withdraw clear, and nothing else.
- [ ] `test_wo93_a_withdrawn_claim_is_not_shown_and_its_stale_code_is_never_read`
      passes, still asserts the portal shows nothing, and now asserts the fix.
- [ ] `alembic upgrade head && alembic check` clean; single head.
- [ ] `grep -rn "1024 \* 1024" backend/app` returns matches only in
      `app/services/filesec.py`.
- [ ] `grep -n "_MAX_UPLOAD\|_ATTACH_MAX" backend/app` returns nothing.
- [ ] With `max_upload_mb = 50`, a 20 MB PDF POSTed to `/invoices/upload` is not
      413.
- [ ] With `max_upload_mb = 1`, a 5 MB PDF is 413 and the message says 1 MB.
- [ ] `test_wo94_the_cap_scanner_detects_a_seeded_violation` fails the scanner
      on seeded source and passes it on the compliant rewrite.
- [ ] Full backend suite: 2332 passed / 10 skipped + the new tests, zero
      regressions, zero assertions weakened.

### Rollback strategy

Code revert for both halves; they are independent commits and either can be
reverted alone. The migration's `downgrade()` is a no-op, so reverting the code
leaves the repaired rows repaired — which is the correct end state under D7
either way, and the narrow mitigation if only the lifecycle half must go back.
No effect is one-way except the discarded stale codes, which carried no
financial meaning (nothing derives an amount from `status_code`; the euro on a
claim is the frozen `vat_eur` column).

### Documentation to update

- `docs/transport/rules.md` — the D7 sentence gains its implementation +
  consumer (the R5 row, which already owns *"only `withdraw_claim` releases"*,
  and the R17 row, which owns the code vocabulary).
- `docs/BACKLOG.md` — N3 removed, with the wider finding recorded in this file.
- `TODO.md` — the WO-94 row and the suite line.
- No ADR is contradicted. `docs/architecture/foundation.md`'s `MAX_UPLOAD_MB`
  row becomes true of the primary upload endpoint for the first time; its text
  needs no change.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo94_withdraw_status_code.py \
                 tests/test_wo94_upload_cap.py \
                 tests/transport/test_wo93_client_status.py -q
python -m pytest -q                       # full baseline: 2332 passed / 10 skipped + new

# the fix, demonstrated rather than asserted
grep -rn "1024 \* 1024" app | grep -v "services/filesec.py"   # must print nothing
grep -rn "_MAX_UPLOAD\|_ATTACH_MAX" app                       # must print nothing
grep -n "status_code" app/services/transport/lock.py

cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```

---

## As built — what changed while implementing, and why

**1. The scanner's second signal was narrowed, and the narrowing is asserted.**
The order specified *"any `len(...) > <not a filesec.max_bytes call>` comparison
in a route module"*. Run over the tree, that flagged five lines that are not
byte caps at all — `len(parts) >= hops` (`core/ratelimit.py`), `len(items) >=
limit` (`services/approval_policy.py`), `len(text) >= _TEXT_LAYER_MIN_CHARS`
(`services/pdf_ocr.py`) and two `len(msg_id) > MSG_ID_MAX` (`services/sepa.py`).
Signal 2 is now keyed on the BUFFER NAME (`BYTE_BUFFERS` — the identifiers
every upload path binds `await file.read()` to), and a fourth self-test
asserts those five shapes stay unflagged. A scan whose output an author learns
to ignore is worse than no scan.

**2. The megabyte-literal signal folds the constant chain.** `N * 1024 * 1024`
parses as `(N * 1024) * 1024`, so matching a `1024 * 1024` node misses every
cap actually written in this codebase. The detector evaluates the constant
multiplication chain instead and flags any product that is a whole number of
megabytes.

**3. The scan covers all of `app/`, not just the route package.** A service
growing its own `25 * 1024 * 1024` forks the truth exactly as effectively, and
a routes-only scan would not see it.

**4. `_max_bytes()` was removed rather than kept as a private alias.** With
every caller on `max_bytes()`, leaving it would have been a second name for
one thing — the shape this order exists to remove. An unknown purpose raises
`KeyError` rather than silently falling back to the general cap: a typo that
quietly LOOSENS a limit is the failure mode worth being loud about.

**5. A `updated_at` carve-out in the "nothing else changed" test.** The claim's
own change stamp is supposed to move on any write, so asserting it did not
would assert nothing about the lifecycle. Every other mapped column is
compared, and the exclusion is named in the test.

**6. One deviation from the process, recorded rather than hidden.** The
migration commit `21f31ab` did not carry README's Alembic revision count, which
`tests/test_docs_truth.py` pins — so that commit does not build on its own. It
was repaired in the next commit (`00324d9`) rather than at the end of the
order. The rule ("a commit changing a docs-truth-pinned number MUST update
README.md in that SAME commit") was known and still missed; the honest record
is that the repair is one commit late.

**7. The Postgres gate was run even though no tenant table was added.** A
migration lands, so `alembic upgrade head`, `downgrade -1`, `upgrade head` and
`alembic check` were exercised on a real PostgreSQL 16 cluster under a
`NOSUPERUSER` `appuser` role, and the backfill was proven there on a seeded
legacy row (`withdrawn/3B` + `submitted/2` → *"clearing status_code on 1
withdrawn claim(s)"* → `withdrawn/NULL` + `submitted/2`).

---
