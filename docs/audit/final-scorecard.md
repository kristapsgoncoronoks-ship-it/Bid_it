# Final Scorecard — Bid_it (InvoiceIQ)

Scores reflect the **debate-adjusted** state of findings (see `docs/audit/agent-debate.md` and
`docs/audit/remediation-roadmap.md`). Each score is 0-100 and is evidence-based, not aspirational — a
genuine gap (no load testing, no backup/restore tooling, a reproduced financial-correctness bug) suppresses
the relevant score rather than being averaged away by adjacent strengths. Source citations point to the
compiled audit docs in `docs/audit/`.

| Dimension | Score | 
|---|---|
| Functional correctness | **76 / 100** |
| Security | **80 / 100** |
| Architecture | **87 / 100** |
| Maintainability | **76 / 100** |
| Performance | **52 / 100** |
| Test quality | **85 / 100** |
| User experience | **68 / 100** |
| Commercial readiness | **50 / 100** |
| Operational readiness | **55 / 100** |

---

### Functional correctness — 76/100
The three core journeys (AP upload→pay, AR create→issue→PDF/XML→send→credit-note→cash-application,
expenses create→submit→approve→reimburse) are all genuinely implemented end to end with no fake/mocked
steps, honest async status handling (202 not 200 on upload; `delivered`/`queued` not an optimistic "sent"),
and correctly-locked/ledgered payment and numbering paths (`functional-audit.md` §1.1-1.3). But this score is
capped well below "excellent" by a **reproduced** financial-correctness invariant violation: credit-note
creation has no row lock and a live Postgres reproduction showed a genuine lost update on `credited_total`
(`functional-audit.md` §2.1, confirmed and raised to P0 in `agent-debate.md` §1) — this is not a hypothetical
edge case, it is a demonstrated bug in a core AR write path. A second, real SoD asymmetry (reimbursement
payout has no maker≠checker control unlike the analogous AP flow, `functional-audit.md` §2.2, P2) and the
expense-approval concurrency gap (`test-baseline.md` finding #2, confirmed P1) further pull this down from
what would otherwise be a 90+ correctness story.

### Security — 80/100
The strongest dimension after architecture. Independently, adversarially re-verified: three-layer tenant
isolation including live Postgres `FORCE ROW LEVEL SECURITY` proof (`security-findings.md` §2.2), structural
CI-gated route authorization across 200+ routes (`security-findings.md` §2.9), a fully-covered upload/
malware-scan gate on all 8 intake paths with live exploit tests (`security-findings.md` §2.3), envelope-
encrypted SSO secrets proven sealed via a DB round-trip test (`security-findings.md` §2.7), and clean sweeps
for SQL injection, XXE, and mass assignment (`security-findings.md` §2.8). The score is held below 85+ by a
real, evidenced formula-injection gap on the two most money-adjacent CSV exports in the app (bank-payment
run, payroll-adjacent reimbursement — `security-findings.md` §2.4, confirmed P1 in `agent-debate.md` §2),
plus a real (lower-severity) SSRF gap on OIDC discovery (`security-findings.md` §2.5, P3) and a fail-closed
malware-scan branch with zero test coverage, meaning a regression there would go undetected by CI
(`test-baseline.md` finding #1, debate-adjusted P2).

### Architecture — 87/100
Genuinely disciplined: the `models→core→services→api` layering is machine-enforced via AST inspection
(`test_boundaries.py`), not just a docstring claim (`system-architecture.md` §4). The DB-backed job queue with
lane isolation, atomic claim, backoff, dead-letter state, and a real `/health/queue` SLO probe is an
appropriately-sized choice for current scale with no evidence of needing a broker
(`system-architecture.md` §3). Background jobs are dispatched inside the correct tenant scope, closing an
obvious "worker runs unscoped" gap (`system-architecture.md` §1.2). Money handling is consistently
`Decimal`+`q2` with no float paths found (`functional-audit.md` §5). Points withheld for: the CSV-safety
helper being duplicated three times with no shared module — exactly the kind of drift that produced the
security gap above (`functional-audit.md` §4.1); and a stale root README/ARCHITECTURE.md that a future
contributor could still cite by accident despite being disclaimed elsewhere (`functional-audit.md` §4.2).

### Maintainability — 76/100
Positive signals: versioned, additive Alembic migrations (`db_migrate`-style discipline, ~70 revisions
applying clean to a single head), a machine-enforced layering boundary, and genuinely self-proving tests that
verify their own checkers aren't vacuous (`test-baseline.md` — `test_tenancy_parity.py`,
`test_authz_coverage.py`). Held down by concrete drift signals: the 3x-duplicated `_safe`/`_safe_cell` CSV
helper (`functional-audit.md` §4.1) that directly caused the CSV-injection inconsistency; a stale, misleading
inline TODO on `SsoConnection.client_secret` that contradicts its own adjacent docstring
(`security-findings.md` §2.7); and stale root-level docs that the codebase's own canonical context file has
to explicitly disclaim (`00_MASTER_CONTEXT.md:52`, cited in `functional-audit.md` §4.2) rather than being
kept accurate or removed.

### Performance — 52/100
This score is **deliberately suppressed**, not because a problem was found, but because almost nothing was
actually measured. What was checked: a grep-based N+1 sweep across all `app/services/*.py` found one per-row
query, and it is a one-time backfill utility never called from a live route, not a hot-path issue
(`system-architecture.md` §3); index coverage on the universal `org_id` tenant-filter column looks correct
(36 migration files creating `org_id`-prefixed indexes); the full backend suite runtime (~19.5 min for ~1090
tests) showed no flakiness or timeouts in targeted re-runs. But the architect's own report is explicit that
**no load, concurrency-under-realistic-volume, or query-plan (`EXPLAIN ANALYZE`) profiling was performed**,
and there is no load-testing harness in the repo at all (`locust`/`k6`/similar absent,
`system-architecture.md` §3, "What was NOT measured"). A score in the 80s+ would misrepresent a nearly-total
absence of production-representative performance evidence as validated performance; 52 reflects "nothing
found wrong in what little was checked, but the vast majority of what matters for a financial system under
real load is unverified."

### Test quality — 85/100
The suite's hardest-guarantee files are exceptionally well-designed, independently reproduced against a real
Postgres 16 cluster the reviewers stood up themselves rather than trusting a cached CI result
(`test-baseline.md` — `test_rls.py`, `test_tenancy_parity.py`, `test_authz_coverage.py`,
`test_numbering_concurrency.py`, `test_payment_run_pay_concurrency.py`, `test_migrations.py` all pass, several
proving their own checkers are non-vacuous). Money-movement paths carry evidence-grade tests that assert
audit-trail content and even read `inspect.getsource` to verify a lock literally appears in the code
(`test-baseline.md` — `test_payment_run_export_guard.py`). The 1091-passed/4-skipped baseline is real and
independently corroborated. This is not a 100 because the coverage-quality investigation surfaced two
concrete, asymmetric gaps: the ClamAV fail-closed branch has zero test coverage
(`test-baseline.md` finding #1) and — more consequentially — the expense-approval decision path has neither
the protection mechanism nor a test proving its absence is safe, unlike every comparable money-adjacent
mutation in the codebase (`test-baseline.md` finding #2, debate-confirmed P1, `agent-debate.md` §6).

### User experience — 68/100
Real strengths, verified by live-driving the app with Playwright, not just reading component code: consistent
loading/empty/error states via a shared component set, confirm-before-create gates on capture review, and
genuinely good safety UX on Payment Runs specifically (named re-export and missing-IBAN acknowledgement
dialogs, maker-checker copy surfaced directly in the UI) (`commercial-readiness.md` §4). Held down by a real
inconsistency: the AR "Issue" screen is the densest, most action-heavy page in the app (~12 text-only action
links per row, 1,034-line component) with **no confirmation on Void or Write-off** despite the exact same
safety pattern existing two screens away on Payment Runs (`commercial-readiness.md` §5); the Payment-Run
Cancel button and Billing downgrade flow have the same missing-confirmation gap
(`commercial-readiness.md` §4, §7); and there is no guided onboarding/setup-wizard checklist for a first-time
admin (`commercial-readiness.md` §2). None of these are functionally broken, but for a financial product
where destructive actions should be hard to trigger by accident, this is a real, evidenced consistency gap.

### Commercial readiness — 50/100
The underlying engine is real and sells the product's substance well when driven correctly: a real customer
entering invoices through the tested workflow gets correct, self-consistent numbers, and the identity/trust
infrastructure (SAML, OIDC+PKCE, SCIM, envelope-encrypted secrets, RLS) is exactly what an enterprise buyer's
security questionnaire asks for (`commercial-readiness.md` §9). This score is capped at 50, not higher,
because of two independently-reproduced, debate-confirmed P0/P1 blockers that sit directly on the commercial
path: the officially-documented demo login (`demo@invoiceiq.app`, cited in `README.md`,
`docs/DEPLOY-HOSTINGER.md`, `docs/architecture/foundation.md`) shows a page-to-page contradiction on the two
numbers a finance buyer cares about most within four clicks (`commercial-readiness.md` §3, P0, confirmed in
`agent-debate.md` §8), and self-serve billing cannot collect a single real payment today — worse, the
Enterprise tier can be self-upgraded for free by any org owner **even with a live billing provider wired**
(`commercial-readiness.md` §7, P1, confirmed and strengthened in `agent-debate.md` §9). The board's own
classification — "Limited pilot," not "Controlled paid pilot" or "General commercial release"
(`commercial-readiness.md` §8-9) — is the direct basis for this score.

### Operational readiness — 55/100
Real operational maturity exists: a durable DB-backed job queue with lane isolation, exponential backoff,
dead-letter state, and a genuine `/health/queue` SLO probe that 503s on breach rather than just exposing a
counter (`system-architecture.md` §1.2); versioned migrations applying clean end to end; envelope-encrypted
secret custody with a pluggable KEK provider. This score is suppressed, not by a defect found, but by an
**absence explicitly surfaced and not silently skipped**: there is no application-level backup/restore
tooling anywhere in the codebase — `grep -rln "backup|restore" backend/app/*.py` returns nothing relevant,
and the only "backup" hits in the repo are vendored `botocore` data (`test-baseline.md` finding #4). This is
a documented deferral to infrastructure in `docs/DECISIONS-NEEDED.md`, which may be a defensible call for a
managed-Postgres deployment, but it means there is currently **no tested, application-owned disaster-recovery
path** for a system that moves SEPA payment files and holds vendor IBANs — a material operational gap for a
financial system, not a nitpick, and the same "nothing verified" caveat on performance/load behavior
(§Performance above) further weighs on operational confidence at real-world scale.
