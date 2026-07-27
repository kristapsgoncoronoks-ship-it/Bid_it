# Security Findings (extract)

> This is a standalone extract of the **"§2. Security Findings"** section of
> `docs/audit/system-architecture.md` (Lead System Architect, independent SaaS review board),
> pulled out for discoverability. The parent document is authoritative — read it for the full
> architecture context (§1), performance-lite notes (§3), and the verification traceability
> table (§4). Debate-adjusted severities are annotated in place; see `docs/audit/agent-debate.md`
> for full rationale on every adjustment.

**Repo/branch:** `/home/user/Bid_it`, `claude/bidit-invoice-data-analytics` (HEAD `e0c2e4a`)
**Reviewer:** Lead System Architect
**Method:** read-only; every finding backed by a command run or file/line read in-session.

Format: Location → Evidence → Attack scenario → Severity → Proposed fix.

---

### 2.1 [INFORMATIONAL / Meta-finding] Prior-session ARCH_plan.md risk claims are broadly STALE — verify future claims fresh, don't cite the doc as current
**Evidence:** I independently re-checked three of ARCH_plan.md's headline code-risk claims against the CURRENT source, not the doc:
1. Claim: `vendors.py`/`partners.py` have no authz/audit. **False today** — `partners.py` router carries `dependencies=[Depends(require_perm(authz.Permission.ISSUED_READ))]` (line 30) plus `_WRITE` on mutations, and an explicit `audit.record(...)` call at `partners.py:197` (grepped and confirmed present).
2. Claim: business logic lives in the `invoice_review.py:94` controller (`_reconcile`). **False** — line 94 area is `_recon_out`, a pure DTO mapper; the actual reconciliation math lives in `app/services/validation.py:177` (`def reconcile(...)`), called from the route at lines 181/360.
3. Claim: `analytics.summary()` hard-codes `"EUR"`. **False** — `app/services/analytics.py:70` only falls back to `"EUR"` when a tenant has **zero** invoices at all; otherwise it uses `_pick_currency()` and returns `available_currencies`.
**Conclusion:** `docs/plan/plan-a/ARCH_plan.md` (marked SUPERSEDED at its own line 3) is stale on every code-specific claim spot-checked. **Severity:** informational. **Action:** none required in code; stop citing ARCH_plan.md's risk list as current.

### 2.2 [Debate-adjusted P4] Tenant isolation is real, three layers deep, and independently proven with a live cross-tenant Postgres probe
Stood up a real Postgres 16 cluster (NOSUPERUSER `appuser`, mirroring CI), ran `alembic upgrade head` clean to `1507ce3eb95f`, and ran `tests/test_rls.py`/`test_numbering_concurrency.py` live — 4 passed including both previously-skipped `@pg_only` raw-SQL cross-tenant tests. Confirmed `FORCE ROW LEVEL SECURITY` is set (`pg_class.relforcerowsecurity = t`) for `vendors`/`invoices`/`users` — without `FORCE`, the owning role (the app's own DB user) would bypass RLS entirely. Confirmed the GUC-unset-fails-open behavior is an intentional, correctly-scoped design limit (`app/api/deps.py:89` sets `set_current_org` before any tenant-scoped query in the real request path).
Three layers, all independently verified: (1) per-query `org_id` filters, (2) ORM `do_orm_execute` guard auto-ANDing `org_id == current_org` onto every SELECT for 58 `TENANT_MODELS`, (3) Postgres RLS with FORCE.
**Debate outcome:** submitted P1 → **downgraded to P4**. Rationale: a "no action required" confirmatory finding shouldn't carry the same severity label as an actual defect; the Senior Test Engineer independently rated the identical fact P4. **Action:** none — genuinely solid. Residual recommendation: document "never grant `appuser` credentials to anything outside the app/worker processes" as an explicit operational rule.

### 2.3 [Debate-adjusted P2] File-upload security gate (`filesec.py`) covers every upload/attachment path — no bypass found
All 8 `UploadFile` route parameters across `app/api/routes/*.py` matched to a `filesec.check(...)`/`reject_active_content(...)` call site preceding storage/parsing, in every case. Non-multipart email intake (`POST /email/inbound`, Mailgun adapter) funnels through `email_intake.process_attachment()` → `filesec.check()` at `email_intake.py:117` before storage. Fail-closed malware scan confirmed at `filesec.py:207-209`. Live HTTP-level tests (`test_security_hardening.py`) POST an `.exe` and an EICAR string through the real endpoint and assert 415.
**Debate outcome:** submitted P1 → **downgraded to P2** — sound and reproduced, but "confirmed no bypass, no proposed remediation" is informational assurance rather than an actionable defect. **Action:** none — recorded as a verified security invariant.

### 2.4 [P2, merges into roadmap P1] CSV/Excel formula injection in the Explore analytics export
**Location:** `app/services/explore.py:242-252` (`to_csv`), exposed at `GET /api/v1/analytics/explore?format=csv`.
Four other CSV writers (`audit_export.py`, `erp_export.py`, `report_writers.py`) implement a `_safe()` helper that prefixes a leading `=+-@\t\r` with a quote to neutralize formula injection; `explore.to_csv` has no such sanitizer and writes dimension values (including free-text `Vendor.name`) raw.
**Attack scenario:** a user with `INVOICE_WRITE` (or a malicious/compromised vendor via AP capture) sets a vendor name to a formula/DDE payload; an auditor exports Explore analytics to CSV and opens it in Excel with legacy execution enabled → exfiltration or code execution.
**Severity:** P2 standalone; this instance is the same defect class the Lead Product Developer found (at P1, debate-confirmed) in `payment_run.py`/`reimbursement.py` — merged into one P1 roadmap item. **Fix:** hoist `_safe()` to a shared `app/core/csv_safety.py`, apply in `explore.to_csv`.

### 2.5 [P3] OIDC `discover()`/`fetch_jwks()` have no SSRF guard, unlike the webhook delivery path
**Location:** `app/services/oidc.py:151-168` — plain `httpx` GET against an admin-supplied `issuer` URL, no `assert_public_url`-style check, unlike `app/services/webhooks.py:49-87` (`assert_public_url`, DNS-resolution-based private/loopback/link-local blocking, catches `169.254.169.254`-class addresses).
**Attack scenario:** an org ADMIN (`SETTINGS_MANAGE`) sets `issuer` to an internal URL, using the app server as an SSRF pivot into the internal network.
**Severity:** P3 — requires an already-privileged actor; elevation-of-blast-radius, not unauthenticated access. **Fix:** reuse `webhooks.assert_public_url()` on the OIDC discovery/JWKS fetch path.

### 2.6 [P4] `LocalStorage._path` containment check uses a bare `startswith`
**Location:** `app/core/storage.py:82-87`. Classic sibling-prefix bypass shape, but **not currently reachable** — every call site builds keys via `storage.content_key(prefix, org_id, sha256)` with session-derived `org_id` and server-computed `sha256`, never raw attacker text. **Fix (cheap hardening, not urgent):** tighten to an exact-match-or-trailing-separator check.

### 2.7 [P4, doc hygiene] Stale TODO on `SsoConnection.client_secret`
**Location:** `app/models/sso.py:35-37` — TODO contradicted by the accurate docstring six lines above. Verified sealing is real and working: `sso_config.py:50-51` seals via `keyvault.seal(...)`; `test_keyvault.py::test_sso_client_secret_sealed_in_db` passes, asserting the stored value is sealed after a live API round-trip. **Fix:** delete the stale TODO comment.

### 2.8 Other injection classes checked, no findings
- **SQL injection:** zero raw-SQL string-interpolation hits outside logging/email-template strings; all DB access via SQLAlchemy 2.0 bound-parameter queries.
- **XXE:** every untrusted-XML parse path uses `defusedxml.ElementTree.fromstring`; the one stdlib `xml.etree` import (`facturx.py`) is write-only.
- **Mass assignment:** partial-update patterns bind against dedicated request-schema types, not the ORM model or raw request body.
- **CSV formula injection:** one real gap, §2.4 (merged into roadmap P1).
- **Path traversal:** theoretical only, §2.6.
- **SSRF:** one real gap on a privileged-actor path, §2.5; webhook delivery itself is well-guarded.

### 2.9 [Debate-confirmed P1, no change] Structural authorization — CI-gated, verified live
`app/core/authz.py` is deny-by-default (8 roles, explicit `ROLE_PERMISSIONS` matrix). Route-level enforcement is structural (ADR-0024) — every route carries a `require_perm(...)` dependency or is in a reasoned `PUBLIC_ROUTES` allow-list. `tests/test_authz_coverage.py` (5 tests, `MIN_EXPECTED_ROUTES=200` canary) passed live.
**Debate outcome:** CONFIRMED at P1 — the board's convention reserves P1 for verified structural controls whose failure would be catastrophic/systemic (broken authz across 200+ routes), distinct from narrower confirmations kept at P2/P4.

---

*Full source, architecture context, and verification traceability table: `docs/audit/system-architecture.md`.*
