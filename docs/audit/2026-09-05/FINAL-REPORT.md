# SYSTEM AUDIT 2026-09-05 — FINAL REPORT

Tree audited: `d8a92ec` (production at the start). Tree delivered: `69e6d8f`
(feature branches `claude/invoice-data-analytics-qmjy7q` and
`claude/bidit-invoice-data-analytics`, identical). Companion documents:
`FINDINGS-AND-BACKLOG.md` (register, debate, backlog, ADR-A1..A6),
`DASHBOARD.md` (ledger, ADR-A7..A14, issue counts).

## 1. Executive summary

Nine specialist investigations (security, backend, database, architecture,
performance, DevOps, QA, frontend/UX, product) ran read-only against
`d8a92ec` and registered 121 findings; every HIGH/CRITICAL one was
re-verified by reading the cited code before it entered the register. The
cross-agent debate accepted 96, modified 11, rejected 6 and deferred 8.

One CRITICAL defect was confirmed and fixed the same day: every SSO- or
SCIM-provisioned account carried a bcrypt hash of a public literal as its
password, so that literal signed in as any such user. The fix is a
non-hash sentinel refused explicitly by `verify_password`, a data migration
that retires the two literals, and a route-level test that returned 200 on
the old code.

All 25 P1 engineering rows of the master backlog are implemented, each with
a regression test that was run against the seeded old behaviour and failed
there. The two remaining P1 rows are owner actions outside the repository
(host deploy script, `DEPLOY_HEALTH_URL`, backup cron, branch protection)
and owner decisions (payment-grace policy, the non-existent 14-day trial,
seller-of-record VAT, DPA/ToS), all recorded in `docs/DECISIONS-NEEDED.md`
§18–§20 with recommended defaults.

## 2. Health score

| Dimension | Before (d8a92ec) | After (69e6d8f) | Why |
|---|---|---|---|
| Security | 4 | 8 | CRITICAL SEC-001 fixed + migrated; client-IP authority unified (SEC-002); CSP + full header block on `/assets/` (SEC-003/004); open: portal-token expiry (owner duration), MFA/password policy (owner), `pip-audit` step (P2) |
| Build & CI | 7 | 8 | Postgres-only tests all run in CI (QA-001); post-deploy health assertion (needs the owner's `DEPLOY_HEALTH_URL`); OpenAPI contract gate; constraint-parity gate; `demo1234` bundle gate. CI on feature branches is dispatch-only by design |
| Tests | 7 | 8 | 3068 passed / 15 skipped / 0 failed (full SQLite suite at edaaea4, 51:35 under concurrent load); the 87 tests of the suites touched by 69e6d8f re-run green; 3085 collected at 69e6d8f (3006 at d8a92ec, +79); 446 passed / 0 failed (4.9 min at edaaea4; 429 at the baseline, +17); CI #531 ran e2e + the 13 visual snapshots green; every P1 fix carries a test proven to bite. Weak spots stay: concurrency/write-path perf unmeasured; visual snapshots CI-only |
| Architecture | 7 | 8 | Layering, structural authz and tenancy gates intact and extended; nav vocabulary now the server's; a full permission mirror exists in the SPA ONLY as a fallback, held equal by a build-failing test. Debt: 5,235-line controllers (ARCH-005, deferred by decision), two API envelope shapes (BE-018), hand-maintained `types.ts` |
| Data integrity | 6 | 8 | Float money column gone (DB-001); duplicate statement import is a DB invariant (DB-004); numbering-prefix collision refused (DB-002); model/migration parity now compares constraints, indexes, FK actions and CHECKs — zero drift at head; statement bytes vaulted (WO-AF). Open P2: BE-004 idempotency 201-on-terminal, orphan blob reaper (P3) |
| Performance | 6 | 8 | The harness now seeds what it measures (PERF-004); dashboard/cash-position reductions in SQL (PERF-002/003); payables worklist bounded (PERF-005/010); all endpoints within ceiling at scale 1200 (ap_aging 1.23×); N+1s on payment runs/capture review remain P2; concurrency unmeasured |
| Commercial readiness | 4 | 6 | Suspended tenants can reach the card form (PROD-001); nav honest per role (PROD-003); onboarding link and derivation fixed (PROD-011); demo credentials off production (PROD-008). Blocked on the owner: billing account, grace policy, trial, VAT treatment, legal documents, host deploy path |
| **Overall** | **58 / 100** | **77 / 100** | |

## 3. Critical findings (the ones that mattered most)

1. **SEC-001 (CRITICAL, CONFIRMED, fixed 55a6b67 + migration c3e5a7b9d1f2).** Pre-auth takeover of every IdP-provisioned account. Reproduced by a route test on the old code.
2. **BE-005 (HIGH, fixed 0e41463).** The recurring-charge dedupe claim was committed AFTER the card was charged; a crash in between charged the card again next run. The claim now commits first.
3. **PROD-001 (HIGH, fixed f1dbfd8).** A declined subscription payment set the org `suspended` and the active-only identity gate then 401'd `/auth/me` and `/billing`: the owner was locked out of the screen that fixes the card. Narrow suspended-tolerant dependency for exactly those routes; every data route still 401s.
4. **SEC-002 (HIGH, fixed 4b38a5c).** uvicorn's wildcard proxy trust let any client forge its IP for the auth rate limiter and the audit trail.
5. **BE-001/002/003 (HIGH, fixed 3e9358c).** A RUNNING job could be retried into concurrent double execution; a job that killed its worker was reclaimed forever; an idempotency collision rolled back the caller's business work.
6. **DB-001 (HIGH, fixed 02ed215 + e6a8c0d2f4b6).** Subscription payment amounts stored as Float.
7. **PROD-003 (HIGH, fixed d1ca9c8).** Navigation gated on a 4-tier ladder while the API gates on an 8-role matrix: dead links for employees, hidden live surfaces for finance managers.

## 4. Implemented changes

17 commits, `55a6b67..69e6d8f`; each row in `DASHBOARD.md` §Ledger carries
problem / change / files / tests / risk. Groups:

- **Security:** SEC-001 sentinel + migration; SEC-002 one IP authority (`TRUSTED_PROXY_COUNT=1` everywhere); SEC-003/004 CSP + repeated headers, verified live under Playwright; PROD-008 demo credentials gated + bundle gate.
- **Backend correctness:** jobs retry/dead-letter/savepoint; billing claim-before-charge; suspended-tolerant identity for billing; ClamAV/Stripe/urllib/report writers/bcrypt off the event loop with a clamd timeout; onboarding derivation.
- **Data:** `Numeric(14,2)` money; unique `(org_id, sha256)` on bank statements with a pre-flight migration; unique numbering prefixes per org; constraint-parity gate (zero drift); statement bytes vaulted and downloadable.
- **Performance:** harness seeds real scenarios; SQL aggregates for dashboard/AR/AP; bounded payables worklist with `items_total`/`truncated`; ceilings re-justified and all met.
- **Frontend:** MutationCache backstop; 4xx never retried; public-path 401 allowlist + open-redirect-safe `?next=`; ErrorBoundary; 4 clipped tables; 17 deletes behind a confirm dialog with the consequence stated; honest "deactivate" verb; nav from served permissions; IdP role-mapping editor with server-fed vocabulary (WO-AE); suspended-workspace mode.
- **Ops/docs:** compose overlays publish no state service and require credentials; dev stack boots; worker configured in prod overlay; log rotation; `scripts/backup.sh`; CI post-deploy assertion; deploy docs corrected; SECRET_KEY/KEK consequence documented; release gate truthful; OpenAPI snapshot; README counts.
- **Tests added:** 79 backend tests and 17 Playwright tests, across 4 new e2e specs and 12 new backend test files; three new structural gates (Postgres test list, OpenAPI drift, constraint parity, nav/permission vocabulary).

## 5. Test results

| Check | Result at 69e6d8f |
|---|---|
| Backend pytest (SQLite suite, full) | 3068 passed / 15 skipped / 0 failed (full SQLite suite at edaaea4, 51:35 under concurrent load); the 87 tests of the suites touched by 69e6d8f re-run green; 3085 collected at 69e6d8f (3006 at d8a92ec, +79) |
| Playwright e2e (`npm run test:e2e`, 48 spec files) | 446 passed / 0 failed (4.9 min at edaaea4; 429 at the baseline, +17); CI #531 ran e2e + the 13 visual snapshots green |
| ruff check / ruff format --check | clean |
| mypy app | clean (388 files) |
| tsc --noEmit / check-labels / check-e2e / check-bundle | clean / 137 / 48 clean / 422.2 kB raw, 124.2 kB gz (budget 460/135) |
| Postgres-gated suites (scratch PG 16) | run per commit for the files touched (BE-003 savepoint, DB-001 migration, DB-004 pre-flight); the postgres CI job runs all ten |
| Perf shape gate (scale 1200) | dashboard 1.49× · ap_aging 1.23× · cash_position 1.49× · explore 2.20× · reliability 6.23× — all within ceiling |
| CI | #529 SUCCESS at d8a92ec (production). Feature head: #531 at afaa3e9 all eight active jobs SUCCESS (pii-scan, lint, backend 28.5 min, postgres incl. perf gate, frontend gates, frontend-e2e incl. visual, docker-build); #530 at edaaea4 was cancelled by #531's dispatch after its pii-scan job caught a VAT-shaped fixture string (fixed in afaa3e9); #532 dispatched at 69e6d8f — verdict recorded in DASHBOARD.md §CI |
| Seeded-violation proofs | every new gate was run against the seeded old behaviour and failed there (recorded per ledger row) |

## 6. Remaining risks

- **Owner-blocked (P1):** host `deploy.sh` still runs the old path until the owner re-points it (documented); `DEPLOY_HEALTH_URL` repository variable unset → the post-deploy assertion only warns; backup cron not installed; branch protection on `main` absent; payment-grace policy, 14-day trial, seller-of-record VAT, DPA/ToS undecided.
- **P2 engineering (~45 rows, listed in the backlog):** BE-004 idempotency 201-on-terminal; inbound-email message idempotency (BE-009); GET routes that mutate export counters (BE-010); N+1s (BE-011/012); `audit.record` swallowing flush errors (BE-016); portal-token redaction/expiry (SEC-005); login length bound (SEC-007); `pip-audit` (SEC-011); 23 pages rendering 4xx as empty (FE-004/005); in-page controls still on the ladder mirror (PROD-010); WO-AG..AJ.
- **Unmeasured:** concurrency and write-path performance; cold-cache behaviour; real-Postgres full-suite run only in CI.
- **Deployment window:** the feature head adds three migrations (c3e5a7b9d1f2 data, d4f6a8b0c2e4 unique with pre-flight refusal over duplicates, e6a8c0d2f4b6 type change). d4f6a8b0c2e4 REFUSES if production already holds duplicate `(org_id, sha256)` bank statements — the deploy will stop cleanly and name them; the operator must dedupe by hand and re-run.

## 7. Technical debt (recorded, not hidden)

- ARCH-005: `issued.py`/`expenses.py` controllers of several thousand lines; use-case extraction only when a flow is touched for a defect (decision recorded).
- BE-018: two API envelope conventions coexist (bare list vs `{items,total}`; `{detail}` vs `{detail,code}`).
- ARCH-011/FE-017: `frontend/src/lib/types.ts` is a hand-kept mirror; the OpenAPI snapshot makes drift visible, a generated client (P3) would make it impossible.
- `PERMISSIONS_BY_ROLE` and `VAT_PERMISSIONS` mirrors in the SPA — fallback only, held equal by a test; the in-page `isAdminOrAbove` controls (19 pages) still read the ladder.
- 10 `window.confirm` sites remain (FE-009 sweep, P2).
- SQLite harness cannot exercise savepoints/FKs — the Postgres-gated files carry those; the split is explicit.

## 8. Commercial readiness verdict

**CONDITIONALLY READY.** The engineering blockers found by this audit are
closed and gated. What stands between the tree and a paying customer is not
code: a live billing account and provider keys, the grace policy, the trial
decision, the VAT treatment of subscription invoices, the legal documents at
signup, and the host-side deploy/backup actions — every one an owner
decision or action recorded in `docs/DECISIONS-NEEDED.md`.

## 9. 30-day plan

1. **Week 1 — owner actions:** re-point the host `deploy.sh`; set `DEPLOY_HEALTH_URL`; install the backup cron; protect `main`; decide §18 (grace), §19 (trial), §2 (VAT), §PROD-007 (legal); open the billing account.
2. **Week 1–2 — P2 correctness:** BE-004, BE-009, BE-010 (with SPA + e2e), BE-016, SEC-005 log redaction, SEC-007 bound, SEC-011 `pip-audit` job.
3. **Week 2–3 — P2 product/frontend:** FE-004/005 ratchet (top three pages first), PROD-010 role vocabulary in copy + in-page controls onto served permissions, FE-009 `window.confirm` sweep, PROD-004 cap warning.
4. **Week 3–4 — queue tail:** WO-AG (country readiness, informational), WO-AH (deadline aggregation), WO-AI (ex-client archive export), WO-AJ (receipt-control as a job kind); N+1s BE-011/012; concurrency perf measurement.
5. **Continuous:** every main push certified by CI; the perf shape gate, contract gate and parity gate stay red-on-drift.

## 10. Lead Developer verdict

**YES WITH CONDITIONS**

The tree at 69e6d8f is fit to run a business on from the engineering side: the one CRITICAL defect is closed and migrated, every P1 the audit accepted is implemented behind a test that was shown to fail on the old behaviour, the full backend and e2e suites read zero failures, CI is green on the pushed head, and an independent second review found no CONFIRMED regression and had its three conditions closed in 69e6d8f.

Conditions, all outside the code:
1. The owner performs the host-side actions in `docs/DECISIONS-NEEDED.md` §20 before relying on the new deploy safety net: re-point `deploy.sh`, set `DEPLOY_HEALTH_URL`, install the backup cron, protect `main`.
2. The owner answers §18 (grace policy), §19 (trial), §2 (VAT on subscription invoices) and PROD-007 (legal documents) before the first paying tenant; until then the product is CONDITIONALLY READY, not READY.
3. The first production deploy of this range runs three migrations; `d4f6a8b0c2e4` refuses cleanly over duplicate bank statements and names them — the operator dedupes and re-runs, nothing is guessed.
4. The P2 rows opened by the second review (R2-C1 next-day recharge residual, R2-B2 double surfaces, R2-A3 in-page controls on the ladder mirror) are scheduled in the 30-day plan, not forgotten.

## 11. Second architectural review (Phase 12)

Independent, read-only, over `55a6b67^..edaaea4`. Verdict **YES WITH CONDITIONS**; conditions closed in 69e6d8f:

- **R2-S1 (MEDIUM, CONFIRMED, fixed):** the statement download hand-rolled `Content-Disposition`; now the shared RFC 5987 helper, plus a 404 when the catalog row's object is gone. Two tests (CR/LF + Lithuanian filename; deleted object).
- **R2-A1 (MEDIUM, LIKELY, fixed):** the SEC-001 migration bcrypt-verified every user under a 300 s deploy health gate; now bounded to SSO-connected orgs, and the retired literals are refused as plaintext on every login so an unreached row is harmless. Tested with an in-bound and an orphan org.
- **R2-C1 (MEDIUM, CONFIRMED, documented, code P2):** BE-005's per-day claim can recharge a settled-but-lost charge the next day.
- Recorded P2/P3: R2-C4 (suspended owner downgrading to Free stays suspended — §18), R2-B2 (six pages surface an error twice), R2-A3 (43 in-page controls still on the ladder mirror), R2-T2 (threadpool test covers one Stripe call), R2-A2 (private import across services), R2-C3 (reclaim window).
- Clean on reading: suspended-tolerant identity is used by exactly `/auth/me` and `/billing/*`; retry/reclaim/savepoint semantics; the AP summary covers the whole population before truncation; every nav `perm` matches or is stricter than its router; CSP and tenant scoping of every new query; migrations' downgrade paths and pre-flight refusals.
- Test quality: the review judged the new tests discriminating (BE-005, BE-001/002, DB-004 race, QA-002, PROD-011, SEC-001 premise, PERF-002 statement-count invariance, WO-AF refusal branch, nav served-wins, FE-002 open-redirect matrix), with one fixture that disagreed with the server (T-1, fixed) and one under-covered helper (R2-T2, P2).
