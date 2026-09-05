# SYSTEM AUDIT 2026-09-05 — live dashboard

Second full audit. The first (2026-08-16, `docs/audit/`) closed its bounded
backlog by WO-42; R15 (perf harness) closed in WO-R, R19 (onboarding) in WO-P.
Still open from it by design: R5(a) live billing account (owner), R14
backup/restore tooling (decision-gated — `scripts/backup.sh` now exists, host
install is the owner's).

Register, debate and master backlog: `FINDINGS-AND-BACKLOG.md`. Final report:
`FINAL-REPORT.md` (written at Phase 11–12).

## PROJECT STATUS
Overall health: AMBER → trending GREEN (every P0/P1 engineering item implemented; owner items open)
Build: PASS (CI #529 all jobs at d8a92ec = production; feature head not yet certified by CI)
Tests: PASS at every commit's targeted runs; full backend + e2e regression of the feature head PENDING (Phase 11)
Security: GREEN (SEC-001 CRITICAL fixed + migrated; SEC-002/003/004 fixed; SEC-005/007/011 P2 open)
Architecture: GREEN (layering/authz/tenancy gates intact; OpenAPI contract gate added)
Data integrity: GREEN (DB-001/002/004 fixed; DB-012 gate reads zero drift; statement bytes vaulted — WO-AF)
Performance: GREEN (harness seeds what it measures; every endpoint within its ceiling at scale 1200, incl. ap_aging 1.23×)
Commercial readiness: AMBER (billing owner-side; decisions §1–§3, §18–§20 open)

## ISSUE COUNTS
Total findings registered: 121 (9 specialists) → after debate: ACCEPT 96 · MODIFY 11 · REJECT 6 · DEFER 8
P0: 1 (SEC-001) — DONE · P1: 25 engineering rows — 25 DONE · owner-blocked P1: 2 rows · P2: ~45 · P3: ~30 · P4: 2
Completed: 26 · In progress: 0 · Blocked (owner): 2 · Rejected: 6 · Deferred: 8

## CURRENT EXECUTION
Current task: full regressions of the feature head → main push → FINAL-REPORT (Phase 11–12)
Responsible agent: Lead Developer (implementation), QA (regression), Lead Architect (Phase 12 review)
Current finding: —
Action being performed: running the full backend + e2e regressions
Validation required: full backend pytest 0 failed; full `npm run test:e2e` 0 failed; ruff/mypy/tsc/gates clean; CI on the pushed head

## BASELINE (executed)
| Check | Result |
|---|---|
| Backend pytest | 2992 passed / 14 skipped / 0 failed (34:09) at 4d1d4d0; 3074 collected at the feature head |
| Playwright e2e | 429 passed (4.0m) at 4d1d4d0; 13 visual snapshots CI-only; 47 spec files at the feature head |
| ruff check / format | clean |
| mypy app | clean, 388 files |
| tsc --noEmit / check-labels / check-bundle | clean / 137 / 422.2 kB raw, 124.2 kB gz (budget 460/135) |
| CI | #528 (4d1d4d0) SUCCESS; #529 (d8a92ec) SUCCESS |
| Production | d8a92ec deployed; alembic head a9c1e3f5b7d2 applied (feature head adds c3e5a7b9d1f2, d4f6a8b0c2e4, e6a8c0d2f4b6) |
| Perf (shape, scale 1200) | dashboard 1.49× · ap_aging 1.23× · cash_position 1.49× · explore 2.20× · reliability 6.23× — all within ceiling |

## DECISION LOG
- ADR-A1..A6 — see `FINDINGS-AND-BACKLOG.md` §Decision log (retry semantics, dead-letter, savepoint scope, CSP shape, compose overlay semantics, threadpool over async rewrite).
- ADR-A7 (PERF-005): the payables worklist is bounded at 200 soonest-due rows with `items_total`/`truncated` on the wire; the summary figures stay whole-population. Ceiling for ap_aging returned to 4.0 (an 8.0 restatement was made and withdrawn in the same session — recorded in `docs/perf/BASELINE-2026-08-27.md`).
- ADR-A8 (FE-001): the `MutationCache` backstop fires ONLY for mutations without their own `onError`, so no double toasts; `retry` never retries a 4xx.
- ADR-A9 (FE-002): public paths (`/login`, `/accept-invite`, `/verify-email`, `/forgot-password`, `/reset-password`, `/sso/callback`, `/portal/`, `/design`) are exempt from the 401 bounce; `?next=` accepts same-origin relative paths only and never a public page.
- ADR-A10 (FE-008): every delete goes through `useConfirm` (promise-shaped `ConfirmDialog`); `window.confirm` sites left as-is for now (P2 FE-009 sweep). Customers "archive" → "deactivate" because the server soft-deactivates.
- ADR-A11 (PROD-011): "a pending invitation counts" now means not-accepted AND not-expired — a semantic tightening of the onboarding derivation, flagged here as a behaviour change (the docstring already promised "pending").
- ADR-A12 (QA-003): the API contract snapshot lives at `docs/api/openapi.json` (1.3 MB, indent 2 for reviewable diffs); a generated TS client stays P3.
- ADR-A14 (PROD-003): the nav is drawn from the permissions the API serves on every identity response; each item names the permission its destination's ROUTER requires for its primary read (configuration surfaces name `settings.manage`). The SPA keeps a full matrix mirror ONLY as a fallback for a response without `permissions`, and a backend test fails the build if the mirror drifts from `ROLE_PERMISSIONS`. Visible consequence: employees no longer see Upload/Team/Issue (all 403 before); finance managers now see Audit log and Reimbursements; employees now see Cost objects (its router is `invoice.read`).
- ADR-A13 (DB-012): constraint parity compares column-sets, FK actions and CHECK texts, never constraint NAMES (batch_alter_table renames them).

## LEDGER
Format: problem / change / files / tests / result / regression risk / status.

- **SEC-001 (P0, 55a6b67; hardened this commit)** — SSO/SCIM users got `bcrypt("!sso-no-password")` as a REAL password / unusable sentinel `"!"` + `has_usable_password`, `verify_password` refuses non-hashes, data migration c3e5a7b9d1f2 retires the literals / security.py, oidc.py, scim.py, migration / 6 tests (route test returned 200 on old code) / all green / LOW / DONE. **Phase 12 (R2-A1):** the migration now scans only users of orgs with an `sso_connections` row (two bcrypt rounds per row under a 300 s deploy health gate; production user count UNKNOWN from the repository), and `verify_password` refuses the two literals as PLAINTEXT on every login, so a row the bound misses is harmless — both asserted in the migration test with an orphan org.
- **SEC-002 (4b38a5c)** — uvicorn `--forwarded-allow-ips '*'` let any client forge its IP for the limiter and audit / removed on every deploy path; `TRUSTED_PROXY_COUNT=1` / Dockerfile, compose ×2, k8s / 5 tests / DONE.
- **QA-001 (2a682eb)** — Postgres-only tests not all listed in the postgres CI job / all ten listed; a test asserts the list / ci.yml / 2 tests / DONE.
- **BE-001/002/003 (3e9358c)** — `retry` on RUNNING/SUCCEEDED; stale reclaim never dead-lettered; `enqueue` IntegrityError poisoned the session / guard + 409; dead-letter at max attempts else backoff; savepoint / jobs.py, routes/jobs.py / 5 + 1 pg-gated / DONE.
- **BE-005 (0e41463)** — charge claim committed after `charge_mit` → crash = double charge / claim committed BEFORE the provider call / billing.py / patched-provider test / DONE. **Residual (Phase 12 review, R2-C1):** the claim is keyed per DAY; a charge the provider settled whose response was lost is NOT a missed charge but a repeated one the next day, because `everypay_next_charge` advances only on `settled`. P2: key the claim on the period paid or reconcile by `order_reference` before charging.
- **Deployment hygiene (63a6408)** — no CSP, `/assets/` dropped headers, db/minio published, dev stack hard-coded production, no post-deploy assertion, no backup script, no log rotation / all fixed / nginx, compose ×3, ci.yml, scripts/backup.sh / 8 tests + CSP verified live by Playwright / DONE (owner: DEPLOY_HEALTH_URL var, cron install).
- **Docs truth (5ac16f7)** — deploy.sh stale, SECRET_KEY/KEK consequence undocumented, RELEASE-READINESS stale / corrected; docs-truth gate extended / DONE.
- **WO-AE (d398d00)** — IdP could assign only the 4-tier ladder; owner assignable / one vocabulary `IDP_ASSIGNABLE_ROLES` (owner excluded), validators, server-fed role selects / 11 backend + 5 e2e / DONE.
- **PROD-001 (f1dbfd8)** — suspended org: `/auth/me` and `/billing` 401'd → owner locked out of the card form / suspended-tolerant identity dependency for exactly those; SPA suspended mode / 4 backend + 2 e2e / MED (auth variant) / DONE.
- **DB-004 (2dc9851)** — statement duplicate guard was a SELECT / unique `(org_id, sha256)` + IntegrityError→ReconError; pre-flight migration refuses over duplicates / 2 tests / DONE.
- **DB-001 (02ed215)** — `billing_payments.amount_eur` Float / Numeric(14,2), Decimal through service+provider / migration with `USING round(...)` / 2 tests / DONE.
- **DB-002 (1e3b69e)** — two issuers could share a numbering prefix / `PrefixInUse` 409; distinct defaults for a second entity / 2 tests / DONE.
- **Blocking I/O (ac313de)** — ClamAV socket, Stripe SDK, urllib, report writers, bcrypt on the event loop / `run_in_threadpool` at every site; clamd timeout / 7 thread-identity tests / DONE.
- **PERF-004/002/003/005/010 (3196e2c, fbc14df, 0c24bc8)** — harness measured empty scenarios; dashboard/cash-position reduced in Python; ap_aging unpaginated / harness seeds; SQL aggregates; bounded worklist with totals / perf re-measured all within ceiling / 4 + 1 tests / DONE.
- **FE-001/002/003 + FE-019 (this commit)** — silent mutations; 401 hijack of public flows and no `?next=`; no ErrorBoundary; 4xx retried / MutationCache backstop; public-path allowlist + `?next=` (open-redirect-safe); `ErrorBoundary` keyed by pathname; retry predicate / main.tsx, api.ts, ProtectedRoute.tsx, Login.tsx, Layout.tsx, ErrorBoundary.tsx / 6 e2e (3 proven to bite by seeding) / LOW / DONE.
- **FE-007/008 (this commit)** — 4 tables clipped by `overflow-hidden`; 17 deletes fired with no confirmation; Customers "archive" called DELETE (soft-deactivate) / `overflow-x-auto`; `useConfirm` at every site with a consequence sentence; honest verb + success toast / 18 page files, ui/useConfirm.tsx / e2e (Customers flow; vat-admin spec updated for the new step) / LOW / DONE.
- **PROD-008 (this commit)** — demo credentials on the production sign-in page / `import.meta.env.DEV` gate; bundle gate fails if `demo1234` reaches dist/ (proven by injecting it) / Login.tsx, check-bundle.mjs / DONE.
- **PROD-011 (this commit)** — onboarding "Invite your team" → `/settings`; any invitation row ticked the step / `/team`; pending = not accepted AND not expired (behaviour change, ADR-A11) / onboarding.py / 1 test (bites on old code) / DONE.
- **QA-002 (this commit)** — SEPA `CtrlSum`/`NbOfTxs` had a single-transfer oracle only / four payees, one without IBAN: refusal names it; acknowledged export has NbOfTxs 3 / CtrlSum 351.00 at both levels / test_sepa.py / bites when NbOfTxs is hard-coded / DONE.
- **QA-003 (this commit)** — no contract gate SPA↔API / `docs/api/openapi.json` snapshot + `test_openapi_truth.py` naming changed paths/schemas / Makefile, README / bites on a schema default change / DONE.
- **DB-012 (this commit)** — parity test compared tables+columns only / uniques, indexes, FK ON DELETE, CHECK texts, NOT NULL compared / test_migrations.py / result: ZERO drift at head e6a8c0d2f4b6; bites when an `ondelete` is dropped / DONE.
- **PROD-003 (this commit)** — nav gated on the 4-tier ladder while routers gate on the 8-role matrix: dead links for employees (Upload, Team, Issue), hidden live surfaces for finance managers (Audit log, Reimbursements) / `permissions` on every identity response; `perm` on every nav item (router permission); `PERMISSIONS_BY_ROLE` fallback mirror; Layout filters on `hasPerm` / auth.py, schemas/auth.py, nav.ts, roles.ts, AuthContext.tsx, Layout.tsx, docs/api/openapi.json / 3 backend tests (identity carries perms; nav items real perms + no ladder flags; mirror == matrix) + 4 nav e2e (employee, finance manager, approver, served-wins + fallback) / MED (auth-shaped change; server unchanged as the control) / DONE.
- **WO-AF (this commit)** — statement bytes digested, keyed on everywhere, never stored: a finding pointed at a file nobody could open / vault through `documents.store` (prefix `statements`) BEFORE ingest, catalog row in each branch, `GET /transport/statements/{sha}/file` (VAT_READ, catalog-gated 404, inert, audited), `file_available` on findings, download control on the screen / statements.py, documents.py, document_registry.py, schemas, StatementIntake.tsx / 6 backend + 1 e2e; seed bit / LOW / DONE.

## PHASE 12 — second architectural review (independent, read-only, range 55a6b67^..edaaea4)
Verdict: **YES WITH CONDITIONS** — no CONFIRMED regression to an existing flow. Conditions closed before main: R2-S1 (Content-Disposition helper + missing-object 404), R2-A1 (migration bounded to SSO/SCIM orgs), R2-C1 documented. Recorded as P2/P3 rows R2-* in the backlog; T-1 (suspended spec mocked `/modules` as 200 where the server 401s) fixed in the same commit.
- **Phase 12 conditions (this commit)** — R2-S1: `GET /transport/statements/{sha}/file` uses `security_headers.content_disposition` and answers 404 when the catalog row's object is gone (2 tests: CR/LF + Lithuanian filename; deleted object); R2-A1: bounded migration + plaintext refusal (above); R2-C1: BE-005 residual documented and queued P2; T-1: suspended-workspace spec meets the 401 the server sends for `/modules`.
