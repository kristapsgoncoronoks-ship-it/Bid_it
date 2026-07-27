# Adversarial Debate — P0/P1 Findings

Every P0/P1 finding raised by the four independent audits (`functional-audit.md`, `system-architecture.md`
/ `security-findings.md`, `test-baseline.md`, `commercial-readiness.md`) was cross-examined by a separate
debate pass before being carried into `docs/audit/remediation-roadmap.md`. Per the review charter, verdicts
are recorded as reached — **agreement was not forced**. Every debated finding below either held its severity,
was upgraded, or was downgraded, on the evidence; none were rejected outright, and there were no findings
over the debate cap this session (every P0/P1 candidate was individually cross-examined).

Verdict legend: **CONFIRMED** (severity unchanged) · **SEVERITY_ADJUSTED** (upgraded or downgraded, content
not disputed) · **DISPUTED / REJECTED_INSUFFICIENT_EVIDENCE** (would indicate a documented disagreement — none
occurred this session, see note at the end).

---

## 1. Credit-note creation lacks a row lock, violating the codebase's own non-negotiable invariant on over-crediting
**Source:** Lead Product Developer (`functional-audit.md` §2.1), submitted **P1**
**Verdict:** **CONFIRMED, severity raised to P0**

Every cited line was independently re-verified: `backend/app/api/routes/issued.py:620` (`create_credit_note`,
unlocked `_load()`) vs. `:770` (`record_payment`, `lock=True`); `_load()`'s lock branch only fires when
explicitly requested; the only lock `create_credit_note` takes (`issuer.lock`, line 646) is acquired *after*
`already_credited()` has already been read from the unlocked row, so it protects numbering, not
`credited_total`.

The debate went further than re-reading code: it stood up a real Postgres 16 cluster mirroring
`.github/workflows/ci.yml`'s own setup, and wrote a scratch reproduction (deleted after, never committed)
that replayed the route's exact read→compute→lock-issuer→build→write sequence with two genuinely concurrent
tasks issuing partial credit notes (€300 + €400) against a €1000 invoice. **Result: both credit-note rows
persisted, but `credited_total` ended up at €300, not €700 — a reproduced lost update.** That leaves
`effective_total` overstated by €400, which `record_payment`'s overpay cap (`body.amount_paid > effective`)
would wrongly permit.

`docs/plan/shared/00_MASTER_CONTEXT.md:89` states verbatim: *"13. No overpayment / no over-crediting,
enforced under a row lock (SELECT … FOR UPDATE)"* — a named non-negotiable invariant, already enforced (and
tested) at `record_payment`, `payment_run.pay_run`, and issuer numbering, but missed here.

**Why raised to P0:** this is not a theoretical race — it was reproduced live, on the same DB engine and
migration set CI itself uses, and it is a reproduced financial-correctness violation of a named non-negotiable
invariant in a product whose core value is AR/invoice-data integrity. The proposed fix (add `lock=True` to
the existing `_load` call in `create_credit_note`, mirroring `record_payment`) reuses an already-tested code
path and introduces no new risk.

---

## 2. CSV formula-injection sanitization is inconsistently applied across financial exports
**Source:** Lead Product Developer (`functional-audit.md` §3.1); overlapping finding from Lead System
Architect (`security-findings.md` §2.4, Explore export specifically), both submitted **P1/P2**
**Verdict:** **CONFIRMED at P1** (merged into one roadmap item covering all three unsafe writers)

Verified every cited sink is unsafe and live-wired: `payment_run.py:425-442` (`export_csv`, writes
`inv.invoice_number` raw), `reimbursement.py:287-307` (`export_csv`, writes `employee_name`/`title` raw),
`explore.py:242-252` (`to_csv`, writes dimension values including `Vendor.name` raw) — all three reachable
from live authenticated routes (`payment_runs.py:249`, `reimbursements.py:223`, `analytics.py:228`) as
`text/csv` attachment downloads.

Contrast confirmed genuine: `erp_export.py`, `audit_export.py`, `report_writers.py` all implement the same
`_safe()` pattern (prefix a leading `=+-@\t\r` with `'`), proven deliberate by `tests/test_arch_audit_fixes.py`'s
own docstring recording a prior fix ("M2: issued-report CSV export neutralises formula injection") plus
dedicated tests in `test_audit_export.py`/`test_erp_export.py`/`test_report_writers.py`.

Threat model confirmed and arguably broader than stated: `invoice_number`/`Vendor.name` can originate from
AP invoice capture (a vendor-supplied PDF/e-invoice), meaning an *external* vendor — not just an internal
user — could plant a payload that later reaches the bank-payment CSV a treasury/finance staffer downloads
and opens in Excel: a genuine cross-trust-boundary CWE-1236 vector.

**Why P1 and not higher:** no direct fund movement; requires a human to open the file in a
vulnerable/legacy-configured spreadsheet client and interact further. **Why not lower:** it hits the
highest-value export in the app (bank payment-run CSV feeding treasury) plus a payroll-adjacent payout CSV,
is trivially exploitable by a low-privilege or external actor, and the project's own prior fix history shows
it self-rates this bug class as immediate-remediation-worthy. Proposed fix (extend the existing `_safe()` to
the three remaining writers, ideally via one shared helper) is a pure string-prefix transform — no risk to
authz, tenant isolation, or financial calculation logic.

---

## 3. Tenant isolation independently proven with a live cross-tenant Postgres RLS probe (three-layer defense confirmed real)
**Source:** Lead System Architect (`security-findings.md` §2.2), submitted **P1**
**Verdict:** **SEVERITY_ADJUSTED → P4**

The debate independently reproduced the entire claim from scratch (fresh Postgres role/DB matching CI
exactly, clean `alembic upgrade head` to `1507ce3eb95f`, live `test_rls.py` run — 3 passed including both
`@pg_only` tests, direct `pg_class.relforcerowsecurity` check confirming `FORCE ROW LEVEL SECURITY`, `pg_tables`
ownership check confirming FORCE is load-bearing (not decorative), and confirmed the GUC-unset-fails-open
behavior is intentional and documented in `docs/architecture/adr/0004-tenant-isolation.md:12`). Every
technical assertion held up — nothing here was knocked down.

**Why downgraded:** this is a confirmatory finding with **"Proposed action: None required"** — there is no
defect, nothing to fix, no customer-facing risk being reported. The Senior Test Engineer independently ran
essentially the identical experiment (same live Postgres RLS probe, same tests) and rated the same fact
**P4** in their own report. Labeling a "no action required" item P1 alongside the board's actual defects
(the P0 credit-note race, the P1 CSV injection, the P2 reimbursement maker-checker gap) risks misleading a
severity-based triage. Downgraded to P4 to match the Test Engineer's independent, symmetric assessment of
the same underlying fact — the technical content is unchanged, only the severity label moved.

---

## 4. Structural, CI-gated route authorization confirmed live — no missing-permission route found
**Source:** Lead System Architect (`security-findings.md` §2.9), submitted **P1**
**Verdict:** **CONFIRMED, no change**

Re-ran `test_authz_coverage.py` directly (5 passed — the write-up's "6/6" was a minor factual slip, corrected
here, immaterial to substance). Read `app/core/authz.py`'s `ROLE_PERMISSIONS` in full and confirmed it is
genuinely deny-by-default (explicit enumerated frozensets per role, not a superset-minus-exclusions pattern).
Read `test_authz_coverage.py` end to end, confirming the forward check, reverse (stale-allow-list) check, and
the self-test that proves the checker isn't tautological (it's run against a scratch app and shown to
actually flag an unclassified route). Confirmed CI actually runs this as a required, unfiltered gate.
Attempted a bypass search: zero non-introspectable `authz.require(` call sites anywhere in `app/api/routes/`.
Confirmed route-enumeration completeness (39 route modules imported = 39 present on disk; both app-level
health routes captured). Confirmed the three "public" self-service routes are still authenticated (they take
`CurrentUser`), just not further role-gated.

**Severity retained at P1** by board convention: this pass reserves P1 for verified structural controls whose
*failure* would be catastrophic/systemic (broken authz across 200+ routes, or a cross-tenant leak), reserving
P3/P4 for narrower confirmations (a stale doc claim, one TODO comment). This is a consistent, deliberate bar,
distinct from finding #3 above — the distinguishing factor is that finding #3 the author itself labeled
"no action required," while this finding documents an actively-enforced CI gate whose ongoing correctness is
exactly what the board should flag as load-bearing.

---

## 5. Upload/attachment security gate (`filesec.py`) verified to cover every intake path with no bypass
**Source:** Lead System Architect (`security-findings.md` §2.3), submitted **P1**
**Verdict:** **SEVERITY_ADJUSTED → P2**

Independently re-verified every element: route inventory (`grep -rn "UploadFile"` → exactly 8 matches,
matching the claim), gate-before-store/parse ordering in all 8 handlers, the non-multipart email-intake path
(`email_intake.process_attachment()` → `filesec.check()` at line 117, before storage), the fail-closed
malware-scan handler (`filesec.py:207-209`), and — going beyond reading — ran the actual tests, including
HTTP-level tests in `test_security_hardening.py` that POST a real `.exe` and EICAR string through the live
endpoint and assert 415. Also independently searched for a bypass the architect might have missed
(non-`UploadFile` file-accepting routes, all `documents.store` call sites) and found none.

**Why downgraded:** this is a "verified sound, no bypass found" structural/defensive finding with a proposed
action of **"None."** Per the same board convention used in finding #4, a confirmed-good control with no
remediation should generally sit at P2–P4 rather than P1 unless its failure mode is uniquely catastrophic —
distinguishing this from finding #4 (route authz), the debate placed this alongside the board's own
architecture-side "verified security invariant" tier (P2), consistent with how the Test Engineer's own
`filesec` coverage-gap finding on the ClamAV branch (finding #7 below) was independently rated.

---

## 6. Expense-approval decision endpoint has no optimistic-concurrency version field and no row lock — no test exists to catch a double-decision race
**Source:** Senior Test Engineer (`test-baseline.md`, finding #2), submitted **P1**
**Verdict:** **CONFIRMED, no change**

Independently verified the schema gap (`ExpenseDecision` has no `version` field, unlike its `submit`/
`approve`/`transition`/`BatchPay` siblings which do and are checked) and the lock gap (`expenses.py::_load`
line 135 is a bare `select()`; repo-wide `with_for_update` grep shows every other money-adjacent mutation
route locks — `issued.py`, `payment_runs.py`, `receipts.py`, `reimbursements.py`, `issuer.py`,
`reconciliation.py` — except this one). Read the actual race mechanics: `decide_step()` blindly sets step
status with no guard against an already-decided step, and the ORM UPDATE carries no `WHERE version =
:old_version` clause under READ COMMITTED — a textbook TOCTOU lost update, not hypothetical.

The debate pushed further and found the exposure is **worse than described**: `reimbursements.py::_load` DOES
lock for the protected batch-pay flow, but `expenses.py::decide()` also exposes a live, independently
reachable `mark_reimbursed` action (wired to a real UI button, `frontend/src/pages/ExpenseDetail.tsx:183/187`)
that sets `status="reimbursed"` directly, **completely bypassing the one path that is protected**. Additionally,
`webhooks.emit()` has no idempotency key/dedup, so two racing `decide()` calls would each enqueue a fresh,
un-deduplicated outbound webhook — a plausible double-payment-instruction vector for any subscribed
payroll/accounting integration, even though the in-app ledger itself doesn't move money on this endpoint
directly.

**Severity retained at P1** — one notch below the credit-note P0 (which risks direct over-crediting, already
reproduced live) since the primary batch-pay ledger path is separately guarded, but the unguarded
`mark_reimbursed` shortcut and un-deduplicated webhook double-fire are real enough that P1 is not overstated.
Proposed fix (add a `version` field + `with_for_update` lock, mirroring `reimbursements.py`/`payment_runs.py`)
introduces no new risk — it adds a guard rather than removing one.

---

## 7. ClamAV fail-closed malware-scan branch has zero test coverage
**Source:** Senior Test Engineer (`test-baseline.md`, finding #1), submitted **P1**
**Verdict:** **SEVERITY_ADJUSTED → P2**

Confirmed the coverage gap is exactly as described: `grep -rn "clamav|clamd" backend/tests/*.py` → zero
matches; `test_filesec.py`'s 8 tests never set `clamav_enabled=True` or touch the `clamd` import/`instream()`
branches. Went further to test the proposed remediation's feasibility and the control's *current* correctness:
injected a fake `clamd` module into `sys.modules` (no real ClamAV daemon needed — `clamd` isn't even an
installed/declared dependency, it's commented out in `requirements.txt`) and monkeypatched
`settings.clamav_enabled=True`. Result: an unreachable-daemon `instream()` raise correctly produces
`FileRejected("Virus scan is unavailable — file rejected")`, and a `FOUND` result correctly raises with the
signature — **the control behaves correctly today**, this is a regression-prevention gap, not a live defect.

**Why downgraded:** ClamAV is optional, off by default, and not even a declared runtime dependency — in the
shipped default configuration this branch is unreachable in production, so customer impact is speculative/
future (only bites if and when an operator turns `clamav_enabled` on). Downgraded to P2, one notch above the
same author's own P3 rating for a comparable admin-only-endpoint test gap (`test_fx.py`'s owner-only claim),
since AV fail-closed is a more consequential control than an admin-only refresh endpoint despite currently
being unreachable. Still worth closing cheaply — demonstrated a ~15-line monkeypatch-based test suffices, no
application code change required.

---

## 8. Demo/seed data contradicts itself: Cash Position and Payment Runs show €0 owed while Invoices lists >€1M of "paid"/"pending" AP invoices
**Source:** Commercial Director (`commercial-readiness.md` §3), submitted **P0**
**Verdict:** **CONFIRMED, no change**

Independently reproduced end-to-end, not just re-read: fresh `alembic upgrade head` + `python -m app.seed` →
"Seeded 'Demo Logistics Ltd' with 83 invoices..." (exact match); direct SQL query against the resulting DB
confirmed `('draft', 83, 1063592.82)` as the sole `workflow_state` bucket, while the legacy `status` field
(which the Invoices list badge actually reads) shows the `paid`/`pending`/`overdue` mix the UI displays.
Called `cash_position.summary()` live against the same DB and reproduced the exact `€0.00` payables figures
shown on Cash Position/Dashboard/Payment Runs. Traced all three downstream screens to the same
`workflow_state`-gated query. Root-caused to `app/seed.py` setting only the legacy `status` field, never
driving invoices through the real submit/approve/pay endpoints. Ruled out "intentional design" by confirming
`payment_run.py:369-370` sets both fields together when an invoice IS driven through the real workflow — the
seed script is the sole point of divergence, and no alternate/more-complete sales-demo fixture exists.
Confirmed this demo credential is the actively-documented, customer-facing login (cited in `README.md:79`,
`docs/DEPLOY-HOSTINGER.md:153`, `docs/architecture/foundation.md:88`), not an internal dev-only fixture.

**Severity retained at P0**: 100%-reproducible, zero-effort-to-trigger (four clicks on the officially
documented demo account), total failure mode (100% of seeded AP invoices affected, not an edge case), and the
literal first-impression path a prospect or AE would walk. The fix is fully risk-free (confined to
`seed.py`, a demo-data generator — zero blast radius on real customer data, security, or production code) and
trivial (call the already-tested, already-live `/submit`/`/approve`/schedule/`pay` endpoints from the seed
script instead of hand-setting the legacy enum).

---

## 9. Self-serve billing collects zero real payment today (NullProvider default)
**Source:** Commercial Director (`commercial-readiness.md` §7), submitted **P1**
**Verdict:** **CONFIRMED, no change** (and the debate found the gap materially worse than the original submission)

Confirmed the exact code path (`billing.py::change_plan`'s 409 guard only fires when
`settings.billing_enabled and target.price_eur` are both truthy) and the shipped default (`billing_provider`
resolves to `"none"` with no ops-side secrets configured, so `billing_enabled` is `False` out of the box).
Confirmed the frontend calls this endpoint directly for upgrades with no payment step in the unwired case.

The debate found a **stronger version of the bug**: `plans.py`'s `enterprise` tier has `price_eur=None`
("contact us" pricing) — and because `target.price_eur` is `None` (falsy), the guard evaluates False for
Enterprise **regardless of `billing_enabled`**, meaning any org owner can self-upgrade to the 200-seat
Enterprise plan for free via `PUT /billing/plan` **even in a fully-wired, live-Stripe production deployment**,
with zero payment or sales gate. `tests/test_billing_stripe.py::test_paid_plan_switch_blocked_when_billing_enabled`
only tests the `pro` plan (`price_eur=99`), so this Enterprise bypass has no test coverage and would survive
the finding's own literal proposed remediation ("wire a live billing provider"). Confirmed reachability: the
public, unauthenticated `register` endpoint creates a brand-new org with the registering user as `owner`
(full `BILLING_MANAGE` permission) — i.e. a genuine anonymous signup lands directly in the vulnerable role.
Cross-checked against `docs/architecture/adr/0013-billing-metering.md` and `docs/plan/plan-a/ARCH_plan.md`
(lines 468-473, 1006-1021, 1302), both of which independently already flag this as the top revenue blocker
in the project's own roadmap — corroborating the finding is real, not a stale claim.

**Severity retained at P1**, not raised to P0: no tenant-isolation break, no financial-record corruption, no
cross-customer harm — it's a self-inflicted revenue-leakage/GTM-readiness gap against the vendor's own
org, already tracked internally as a launch blocker. Remediation is amended (see roadmap) to explicitly
include closing the `price_eur=None` Enterprise bypass — "turn `billing_enabled` on" alone does not fix it.

---

## Disputed / rejected findings

**None.** Every P0/P1 finding raised by the four reports was individually cross-examined (9 findings above);
none were rejected as unsupported, and none produced an irreconcilable disagreement between the debate stage
and the submitting reviewer. Three findings were downgraded on severity-labeling grounds alone (tenant
isolation proof, route-authz proof was retained, upload-gate proof, ClamAV coverage gap) without disputing
any underlying factual content — those are recorded above as `SEVERITY_ADJUSTED`, not as disagreements about
what is true, only about how urgently a "no defect found" or "unreachable by default" finding should be
labeled for triage. One finding (credit-note row lock) was strengthened from P1 to P0 on the strength of a
live reproduction. Per the charter's instruction not to force artificial agreement: had a genuine factual
dispute arisen (e.g. a reviewer's evidence not reproducing under the debate stage's own re-run), it would be
recorded here verbatim as DISPUTED with both positions stated — this simply did not occur in this session's
set of P0/P1 candidates.
