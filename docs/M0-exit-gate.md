# M0 exit gate — "Safe to hold a stranger's money data"

> **Status:** verified 2026-07-26 on branch `claude/bidit-invoice-data-analytics`
> (WO-10; criterion 9 closed the same day by WO-11/B1.5). Every criterion below
> maps to the **test or artifact that proves it** and an honest status:
> ✅ met · 🔶 met-with-owner-action · 🔴 OPEN.
> Baseline discipline: WO-1 started from **761** passing tests; after WO-9 the
> suite stood at **920 passed, 3 skipped**; the WO-10 verification run is pasted
> at the bottom. Nothing was skipped or weakened along the way — fixtures were
> raised in privilege where a gate closed an endpoint, never the reverse.

## Verdict

**M0 is MET in full as of WO-11 (2026-07-26). The last open engineering item —
B1.5, the `users.org_id` dual-write contract step — is now CLOSED** (see the
B1.5 section below for the evidence). What remains are the five owner/legal
actions that are outside the codebase. All in-code exit criteria are
implemented, tested and CI-enforced.

## Criteria → proof → status

| # | M0 exit criterion | Proof (test / artifact) | Status |
|---|---|---|---|
| 1 | Every route declares a permission via a router dependency or sits on a reviewed `PUBLIC_ROUTES` allow-list; CI fails otherwise, **asserted in both directions** | `tests/test_authz_coverage.py` (forward + reverse + self-test on a scratch app, min-route-count guard) · ADR-0024 · WO-1 | ✅ |
| 2 | Vendor bank-detail change is permission-gated, audited, IBAN mod-97 + BIC validated, version-guarded, and lands as a **pending change request requiring a second person's approval** | `tests/test_vendor_change_requests.py`, `tests/test_vendors_authz.py`, `tests/test_bank_id.py` · `app/core/bank_id.py` re-checked inside `build_pain001` · ADR-0025 · WO-2 | ✅ |
| 3 | `partners` router fully permission-gated and audited | `tests/test_partners_authz.py` (incl. the WO-10 follow-up: `partner.document_delete` is now audited — deleting a signed contract can flip the readiness gate and previously left no record) · WO-3 | ✅ |
| 4 | `Organization.status != 'active'` enforced **on every request**, and suspension revokes live sessions | `tests/test_org_suspension.py`, `tests/test_membership_enforcement.py` (role change → bulk session revocation audited) · `app/api/deps.py::get_current_user` (the one org query per request) · WO-4 | ✅ |
| 5 | Inbound-email shared secret **mandatory** (boot fails in production without it) | `app/core/config.py` production validation (`inbound_email_secret is unset` is a boot failure) · `tests/test_email_intake.py` (401 fail-closed without/with wrong secret) · WO-5 | 🔶 met in code; **owner action:** set `INBOUND_EMAIL_SECRET` in production env before deploying this branch — boot refuses without it |
| 6 | Exactly **one** validation engine, service-owned, per-rule `block \| advise` policy; `_reconcile` no longer in a route | `app/services/validation.py` (single `RULES` registry) · `tests/test_validation.py`, `tests/test_reconcile_characterisation.py` (byte-for-byte gate behaviour preserved) · ADR-0026 · WO-7 | ✅ |
| 7 | Exactly **one** FX convention; `fx_source` a validated enum everywhere; no report sums across currencies without conversion; scheduled ECB refresh exists | ADR-0010 (amended by WO-8) + ADR-0026 · `models/fx.FxSource` CHECK-constrained · `tests/test_fx.py`, `tests/test_fx_europe.py`, `tests/test_fx_schedule.py` (daily job), `tests/test_ap_aging.py` (per-currency), `tests/test_reimbursement_sepa.py` / `tests/test_sepa.py` (refusal names the line; no foreign amount labelled EUR) | 🔶 met in code; **owner decision pending:** DECISIONS-NEEDED §9 — whether to restate expense figures a human already approved under the old multiply convention (flagged, deliberately untouched by the migration) |
| 8 | Payment runs enforce **maker ≠ checker**; bank-file export requires `PAYMENT_WRITE`, works only on an approved/paid run, is **export-once guarded**, emits a **unique `MsgId`** per generation, and **surfaces** skipped payees | `tests/test_payment_runs_sod.py` (maker≠checker≠payer + audited override), `tests/test_payment_run_export_guard.py` (export-once + re-export confirm + state gate), `tests/test_sepa.py` (unique MsgId, skipped payees named), `tests/test_payment_run_pay_concurrency.py` (Postgres row-lock race) · WO-9 | ✅ |
| 9 | `users.org_id` dual-write resolved; memberships authoritative | WO-11 (`docs/plan/plan-a/wo/WO-11-B15.md`): membership-EXISTS scoping in the ORM guard (`core/tenant.py::_scope_criteria`) + the `users` RLS policy (migration `e6a8c0b2d4f6`); membership joins in `scim`/`privacy`/`reimbursement`/`expense_approval` · `tests/test_membership_authority.py` (10 tests) · `tests/test_rls.py::test_rls_users_visibility_is_membership_driven` · `test_org_switch`/`test_membership_enforcement` unmodified — see **B1.5 status** below | ✅ |
| 10 | Fleet Fuel real-client data quarantined out of the harvest path; harvest protocol written down | `scripts/pii_scan.py` + `tests/test_pii_scan.py` + the required `pii-scan` CI job (structural EU-VAT/IBAN patterns active; full-history scan of 2,119 blobs clean on 2026-07-25) · `docs/transport/harvest-protocol.md` · WO-6 | 🔶 met in code; **owner actions:** set the `PII_SCAN_SALT` CI secret and populate `scripts/pii_denylist.json` from the owner-held archive (`identifiers_for_denylist.txt`); counsel decision on the archive itself is DECISIONS-NEEDED §8 (due 2026-09-30) |
| 11 | `README.md` + `ARCHITECTURE.md` regenerated truthfully (or deleted with a pointer) | `README.md` regenerated against verified counts (64 tables / 64 migrations single-head / ~980 collected tests / 7 CI jobs); `ARCHITECTURE.md` reduced to a pointer at `docs/architecture/`; `docs/architecture/data-model.md` build-state markers re-verified against `backend/app/models/` (WO-10) | ✅ |
| 12 | Baseline tests still green + the new authorization-coverage and tenancy-parity tests green in CI | Full-suite transcript below · `tests/test_authz_coverage.py` · `tests/test_tenancy_parity.py` (58-table registry: 52 probed over the real query path, 6 reasoned exemptions, both-direction exemption check, self-test that a deliberately unscoped query fails) · `tests/test_ai_policy.py` (zero external calls at defaults, socket-blocked) | ✅ |

Note on the original criterion list: ARCH_plan M0 also folds the audit-coverage
idea into #1's discipline — every financial mutation audits in-transaction
(invariant §4.16); WO-2/3/9/10 each added the missing audit events they found
(vendor writes, partner mutations + document delete, payment-run transitions).

## B1.5 — `users.org_id` dual-write: ✅ MET (WO-11, 2026-07-26)

**Resolution:** memberships are now authoritative everywhere a tenant-scoping
decision is made, and `users.org_id` survives only as the explicitly-documented
**active-org pointer** (the outcome ARCH_plan's acceptance allows; the column
drop is deferred cleanup, tracked in
`docs/security/multi-org-membership-plan.md` §6e). Concretely:

- **Both defence layers on the `users` table scope by membership, not by the
  pointer**: the ORM guard (`app/core/tenant.py::_scope_criteria`) applies a
  membership-EXISTS predicate for `User`, and migration `e6a8c0b2d4f6` replaced
  the `users` RLS policy with the same predicate — a `users.org_id` pointer
  WITHOUT a membership row makes the row invisible (`USING` has no pointer
  disjunct; only `WITH CHECK` admits the same-transaction
  create-user-then-membership insert window).
- **The four divergent services now read memberships**: `scim.py` (roster,
  get, and create-conflict — the cross-workspace conflict is the DB
  unique-email constraint → 409, since a scoped session can no longer read a
  foreign user row), `privacy.py` (DSAR scan), `reimbursement.py` (SEPA payee
  bank details), `expense_approval.py` (approver e-mails). A member whose
  active org is elsewhere is now visible to their other orgs in all four; a
  non-member stays at zero rows / opaque 404.
  `grep -rn "User.org_id" app/services/{scim,privacy,reimbursement,expense_approval}.py`
  returns nothing.
- **No behaviour change to org switching**: `tests/test_org_switch.py` and
  `tests/test_membership_enforcement.py` pass **unmodified** (git diff empty);
  the pointer is still what `deps` scopes to, but only AFTER the per-request
  live-membership check (WO-4), which the code now documents as THE
  authoritative tenancy decision.

**Evidence (2026-07-26, commits ea53dee → this commit):** new proof suite
`tests/test_membership_authority.py` (10 tests: SCIM roster/offboarding of a
switched-away member, 409 conflict with no row created, opaque 404 for a
non-member, SEPA payee not skipped, approver e-mail resolved, DSAR count,
ORM-guard self-test) and
`tests/test_rls.py::test_rls_users_visibility_is_membership_driven` (raw SQL on
scratch Postgres 16: pointer-without-membership sees nothing; scoped foreign
INSERT refused). Full SQLite suite **993 passed, 4 skipped** (983 baseline +
10 new; 4th skip = the new pg-only test). Scratch PG 16 (NOSUPERUSER, fresh
`alembic upgrade head`, plus `downgrade -1`/`upgrade head` round-trip):
`test_rls.py test_numbering_concurrency.py test_payment_run_pay_concurrency.py`
= **5 passed**. `ruff` clean, `mypy app` clean (232 files), single Alembic
head, `alembic check` clean. Only fixture change:
`test_privacy_erasure._seed_subject` now writes the membership row every real
user-creation site writes (no assertion weakened).

## Consolidated OWNER ACTIONS (nothing in-repo can close these)

1. **Set the `PII_SCAN_SALT` repository secret** in GitHub Actions, then
   **populate the PII deny-list** (`scripts/pii_denylist.json`) from the
   owner-held decommission archive's `identifiers_for_denylist.txt` (never
   commit the raw list) — `docs/transport/harvest-protocol.md`.
2. **Enable branch protection on `main`** with required checks: `lint`,
   `backend`, `postgres`, `frontend`, `docker-build`, `pii-scan`
   (`docs/DEPLOYMENT.md` §CI — the list cannot be asserted from inside the repo).
3. **Set `INBOUND_EMAIL_SECRET` in the production environment** before
   deploying this branch — production boot now fails closed without it (WO-5).
4. **DECISIONS-NEEDED §8** — engage counsel on the Fleet Fuel decommission
   archive (lawful basis, retention/destruction, redacted derivative), **due
   2026-09-30**.
5. **DECISIONS-NEEDED §9** — decide whether/how to restate the
   approved/reimbursed expense figures flagged (not changed) by the WO-8 FX
   correction migration, and who communicates with affected employees.

## Verification transcript (2026-07-26, commit series d000bd2 → this commit)

```
$ python3 scripts/pii_scan.py --tree
pii-scan: NOTICE: deny-list is EMPTY — structural patterns only. …
pii-scan: clean            (exit 0)

$ cd backend && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests
All checks passed!
363 files already formatted

$ .venv/bin/mypy app
Success: no issues found in 232 source files

$ .venv/bin/alembic heads | wc -l
1

# scratch Postgres 16 (NOSUPERUSER appuser, port 5433, fresh cluster):
$ DATABASE_URL=postgresql+asyncpg://appuser:apppw@127.0.0.1:5433/invoiceiq alembic upgrade head
… Running upgrade b1c3e5a7f9d1 -> d4e6f8a0b2c4 (head)   # applies cleanly from empty
$ RLS_TEST_DATABASE_URL=… pytest tests/test_rls.py tests/test_numbering_concurrency.py \
      tests/test_payment_run_pay_concurrency.py -q
4 passed

$ pytest tests/test_tenancy_parity.py tests/test_ai_policy.py -q
58 passed

$ cd frontend && npm run build
tsc --noEmit && vite build … ✓ built   (exit 0)

$ cd backend && python -m pytest -q          # full suite, SQLite
983 passed, 3 skipped
```

(3 skips are the Postgres-only markers — they run, un-skipped, in the
`postgres` CI job / the scratch-cluster transcript above.)

## What M1 starts with

- ~~**B1.5** (finish `users.org_id` → memberships)~~ — **DONE** (WO-11,
  2026-07-26; see the B1.5 section above). Deferred follow-up parked in Epic B:
  dropping the `users.org_id`/`users.role` columns once the pointer semantics
  have soaked.
- The M1 theme per `docs/plan/plan-a/ARCH_plan.md`: close the frontend gap on
  the AP/AR paths (capture-review UI is the #1 job-to-be-done with no screen),
  line-item provenance, the composed home dashboard, the grouped navigation IA.
- Accepted-not-yet-implemented registry unifications C1.5/C1.6/C1.7
  (ADR-0026).
