# System audit 2026-09-05 — findings register, debate and master backlog

Second full audit of InvoiceIQ (the first, 2026-08-16, lives beside this file
and closed its bounded backlog by WO-42). Nine specialist investigations ran
read-only against the tree at `d8a92ec`; every HIGH/CRITICAL finding below was
then re-verified by the Lead Developer by reading the cited code before it
entered this register. Certainty is the Lead's after that check, not the
specialist's.

Severity: CRITICAL / HIGH / MEDIUM / LOW. Certainty: CONFIRMED / LIKELY /
POSSIBLE / UNKNOWN. Decision: ACCEPT / MODIFY / REJECT / DEFER (with the reason
in the debate section). Status uses the backlog vocabulary.

## 1. Findings register

### Security (SEC)

| ID | Title | Sev | Certainty | Evidence | Decision |
|---|---|---|---|---|---|
| SEC-001 | SSO/SCIM users are created with a bcrypt hash of a **known literal** (`"!sso-no-password"`, `"!scim-no-password"`); `/auth/login` verifies the submitted password against it like any other → the literal signs in as any IdP-provisioned user | CRITICAL | CONFIRMED (read `oidc.py:46,355`, `scim.py:41,144`, `auth.py:132`, `security.py:28-33`) | pre-auth account takeover for every SSO/SCIM tenant | ACCEPT — P0 |
| SEC-002 / ARCH-001 | `--forwarded-allow-ips '*'` makes uvicorn write the client-supplied leftmost `X-Forwarded-For` into `scope["client"]`; the app's own `_client_ip` (with `trusted_proxy_count=0`) then trusts it → per-IP auth rate limit is mintable, audit `ip` forgeable | HIGH | CONFIRMED (uvicorn `proxy_headers.py:176-177`; `Dockerfile:55`; both prod composes) | password spraying across accounts; forged audit attribution | ACCEPT — P1 |
| SEC-003 | SPA origin serves no CSP while the 24h bearer token lives in `localStorage` | MEDIUM | CONFIRMED | XSS → 24h credential exfiltration | ACCEPT — P1 (config) |
| SEC-004 | nginx `location /assets/` declares its own `add_header`, discarding inherited security headers | MEDIUM | CONFIRMED (nginx semantics) | JS/CSS served without nosniff/HSTS | ACCEPT — P1 (config) |
| SEC-005 | Portal/calendar capability tokens sit in the URL path and are written to access logs; portal tokens never expire | MEDIUM | CONFIRMED | credential in logs | MODIFY — P2 redact in logs now; expiry column is an owner decision on duration |
| SEC-006 | SSO delivers the session token in a URL fragment | LOW | LIKELY | browser history exposure | DEFER — P3 with ARCH-012 |
| SEC-007 | 8-char passwords, no MFA, no authenticated change-password; `LoginRequest.password` unbounded | LOW-MED | CONFIRMED | below finance-system bar | MODIFY — P2 bound login length now; policy/MFA is an owner decision |
| SEC-008 / OPS-010 | Default KEK derives from `SECRET_KEY`; rotating it (the documented "logs everyone out") destroys every sealed secret; production validator does not require BYOK | LOW | CONFIRMED | irrecoverable SSO secrets on a routine rotation | MODIFY — P1 docs + startup warning; refusing `local` would break the live deployment |
| SEC-009 | SSRF guard fails open on DNS failure; rebinding window between check and connect | LOW | LIKELY | narrow SSRF | DEFER — P3 |
| SEC-010 | Audit chain has no external anchor | LOW | CONFIRMED (design limit) | tamper-evident, not tamper-proof | DEFER — P4 |
| SEC-011 | Dependency CVE status unassessed; no `pip-audit` in CI | UNKNOWN | UNKNOWN | — | ACCEPT — P2 CI step |

### Backend (BE)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| BE-001 | `POST /jobs/{id}/retry` requeues a RUNNING job (clears `locked_by`) → same job executes twice concurrently; not restricted to user-enqueueable kinds (`everypay.charge_mit` included) | HIGH | CONFIRMED (`jobs.py:257-267`, `routes/jobs.py:68-72`) | ACCEPT — P1 |
| BE-002 | `reclaim_stale` ignores `max_attempts` → a job that kills its worker is reclaimed forever (hot crash-loop) | HIGH | CONFIRMED (`jobs.py:244-254`) | ACCEPT — P1 |
| BE-003 | `jobs.enqueue` IntegrityError handler calls `db.rollback()`, discarding the caller's uncommitted business work when `commit=False` | HIGH | CONFIRMED (`jobs.py:106-121`) | ACCEPT — P1 |
| BE-004 | Idempotency unique index is unconditional while the pre-check is live-only → re-enqueue after success/dead returns the old terminal job as `201` | HIGH | CONFIRMED | MODIFY — P2: `retry` (BE-001 fixed) is the operator path; route reports dedup instead of 201 |
| BE-005 / DB-003 | `charge_renewal` flushes the dedupe row, charges the card, then commits → crash/timeout after the charge repeats it next run | HIGH | CONFIRMED (`billing.py:236-253`) | ACCEPT — P1 |
| BE-006 / ARCH-004 | Stripe SDK calls are synchronous inside `async def` — block the event loop (80s default timeout) | HIGH | CONFIRMED | ACCEPT — P1 |
| BE-007 / ARCH-003 | ClamAV scan is blocking untimed socket I/O on the request path, 11 call sites | HIGH | CONFIRMED | ACCEPT — P1 |
| BE-008 | bcrypt hash/verify inline in async auth routes | MED-HIGH | CONFIRMED | ACCEPT — P2 |
| BE-009 | Inbound email has no message-level idempotency; provider retry duplicates the message | MED-HIGH | CONFIRMED | ACCEPT — P2 |
| BE-010 | Export-once counter mutated by `GET` routes | MEDIUM | CONFIRMED | ACCEPT — P2 (breaking verb change; SPA + e2e follow) |
| BE-011 / PERF-005 | `GET /payment-runs` N+1 ×2 per run, unbounded, bare list | MEDIUM | CONFIRMED | ACCEPT — P2 — DONE ac718b9 (one grouped count; `limit`/`offset`) |
| BE-012 | `GET /invoices/captures/review` 2× N+1 per page | MEDIUM | CONFIRMED | ACCEPT — P2 — DONE ac718b9 (two grouped reads per page) |
| BE-013 | Org switcher N+1 | LOW-MED | CONFIRMED | ACCEPT — P3 |
| CONC-001 (new, 2026-09-06) | First concurrency datapoint (CI #543, 8 in flight, scale 300): `dashboard` 12.5×, `ap_aging` 10.1×, `cash_position` 10.4× the serial p95 — more than serialising would cost; `invoice_list`/`explore`/`reliability` 2–5× (overlapping, as expected). Datapoint 1 (CI #544, artefacts removed, zero errors) reproduces it: dashboard 11.4×, ap_aging 12.0×, cash_position 11.7×, transport_reliability 11.2×, with conc p50 at 5–6× and p95 at 11–12× — the last requests of each batch wait for a resource smaller than eight; single-statement reads (`invoice_list` 5.5×, `explore` 4.6×) show no such tail. First suspect: the connection pool (default 5 + 10 overflow) held across multi-statement aggregate reads. Second suspect since PERF-016 (2026-09-06): a gen-2 garbage-collection pass is stop-the-world for every request in flight on the worker, and those four reads are the ones that allocate most — datapoint 2 (the first with the startup heap frozen) says whether the ratio moves before any pool change. The write path is fine (same-org 4.95×, across-orgs 6.57×, no errors) | MEDIUM | LIKELY (two datapoints agree on WHICH endpoints, not yet on why) | ACCEPT — P2: measure pool size as a variable in the next datapoints before any code moves |
| CONC-002 (new, 2026-09-06) | `rate_limit_per_min` = 300 per token/IP (fixed 60 s window): one API token cannot sustain more than 5 requests/second — an integration replaying a day's invoices will meet it (datapoint 0 tripped it at ~340 requests/min) | LOW | CONFIRMED (`app/core/config.py:309`, CI #543 log: 429 × 4) | DEFER — owner policy number (DECISIONS): raise per plan, or per-token burst allowance |
| BE-014 | `issuer.lock()` fallback commits mid-transaction | MEDIUM (latent) | CONFIRMED | ACCEPT — P2 |
| BE-015 | SMTP failures are recorded and never retried | MEDIUM | CONFIRMED | DEFER — P3 (email job kind; design) |
| BE-016 | `audit.record` swallows every exception incl. flush errors that poison the session | MEDIUM | LIKELY | ACCEPT — P2 |
| BE-017 | Worker swallows queue-health failures with bare `pass` | LOW | CONFIRMED | ACCEPT — P3 (trivial, ride along) |
| BE-018 | Two API contracts coexist (bare list vs envelope; `{detail}` vs `{detail,code}`) | MEDIUM | CONFIRMED | DEFER — P3 (large, non-breaking path needed) |
| BE-019 | Blobs written before the owning row commits; no orphan reaper | LOW-MED | CONFIRMED | DEFER — P3 |
| BE-020 | Uploads buffered whole in memory; `/email/inbound` decodes every attachment before writing any | MEDIUM | CONFIRMED | MODIFY — P2 bound attachment count/total for inbound; 15 MB cap already bounds uploads |

### Database & data integrity (DB)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| DB-001 | `billing_payments.amount_eur` is `Float` — the only float money column | HIGH | CONFIRMED | ACCEPT — P1 (migration to `Numeric(14,2)`) |
| DB-002 | Issued-number uniqueness is per org while numbering is per issuer with an identical default prefix → second issuer's first invoice collides and burns a number | HIGH | CONFIRMED (`issued_invoice.py:52`, `issuer.py:74-75`, `issued_service.py:22`) | MODIFY — P1: refuse a duplicate prefix per org at the service + assign a distinct default; constraint migration deferred (P2) with pre-flight |
| DB-003 | = BE-005 | | | |
| DB-004 | Bank-statement duplicate guard is check-then-insert; no unique on `(org_id, sha256)` | HIGH | CONFIRMED | ACCEPT — P1 |
| DB-005 | No per-vendor uniqueness on supplier invoice number | MED-HIGH | CONFIRMED | REJECT hard constraint (legitimate reuse exists); advisory `duplicates` service is the control — P3 tighten advisory |
| DB-006 | Append-only ledgers enforced by convention only | MEDIUM | CONFIRMED | DEFER — P3 (Postgres-only REVOKE/trigger; needs role model) |
| DB-007 | Org delete cascades into `audit_events` and `archived_invoices` | MEDIUM (latent) | CONFIRMED | ACCEPT — P2 (RESTRICT on those two) |
| DB-008 | `invoices.vendor_id` CASCADE | MEDIUM (latent) | CONFIRMED | ACCEPT — P2 (RESTRICT) |
| DB-009 | `users` delete cascades into expense reports | MEDIUM (latent) | CONFIRMED | ACCEPT — P2 (RESTRICT) |
| DB-010 | Nine parent links lack the composite tenant-safe FK | MEDIUM | CONFIRMED | DEFER — P3 (needs `UNIQUE(org_id,id)` targets first) |
| DB-011 | 59 state columns unconstrained `String` | MEDIUM | CONFIRMED | ACCEPT — P2 (CHECKs on the six financial ones, with pre-flight) |
| DB-012 | Model↔migration parity test compares tables+columns only | MEDIUM | CONFIRMED | ACCEPT — P1 (test-only; may surface drift) |
| DB-013 | `amount_paid` caches have no reconciliation check | MEDIUM | CONFIRMED | ACCEPT — P2 (integrity job/report) |
| DB-014 | Vendor IBANs unnormalised, no cross-vendor collision surface | MEDIUM | LIKELY | ACCEPT — P2 |
| DB-015 | Org-blind single-column indexes on `invoices`/`expense_items` | LOW-MED | LIKELY | DEFER — P3 (measure with EXPLAIN first) |
| DB-016 | FX-rate scale inconsistent (8 vs 6 dp) | LOW | CONFIRMED | DEFER — P4 |
| DB-017 | `issued_invoices.issuer_id` FK is single-column despite a composite target built for it | LOW-MED | CONFIRMED | ACCEPT — P2 |

### Architecture (ARCH)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| ARCH-002 | k8s reference config cannot boot (no `INBOUND_EMAIL_SECRET`) and defaults to per-pod local storage on a read-only rootfs | HIGH | CONFIRMED | ACCEPT — P2 (config/docs; storage validator MODIFIED — the live VPS uses local on a shared volume, so refuse-on-local would break production) |
| ARCH-005 | Business logic + transaction boundaries in four god-controllers (5,235 lines, 70 services fan-in) | HIGH | CONFIRMED | DEFER — recorded debt; incremental extraction per touched flow, never a rewrite |
| ARCH-006 | CPU-heavy work inline in requests (OCR, ingest, PDF) | MEDIUM | CONFIRMED | DEFER — P3 (job kinds; SCALING.md names it) |
| ARCH-007 | SMTP inside an open request transaction | MEDIUM | CONFIRMED | DEFER — P3 with BE-015 |
| ARCH-008 | Bounded-context seam enforced one way only | MEDIUM | CONFIRMED | ACCEPT — P2 (mirror test with explicit allowlist) |
| ARCH-009 / PERF-012 | Per-process rate limiter | MEDIUM | CONFIRMED (documented, ADR-0015) | DEFER — P3 after SEC-002 |
| ARCH-010 | Audit advisory lock held to commit | MEDIUM | LIKELY | DEFER — P3 (measure) |
| ARCH-011 / QA-003 / FE-017 | Hand-maintained `types.ts` mirror; no contract gate | MEDIUM | CONFIRMED | ACCEPT — P1 OpenAPI drift gate (ERD pattern); P3 codegen |
| ARCH-012 | 24h non-refreshable token in localStorage | MEDIUM | CONFIRMED | DEFER — P3 (design; server-side revocation exists) |
| ARCH-013 / PERF-011 | Daily scheduler O(tenants×kinds) round-trips, on every worker | MEDIUM | CONFIRMED | ACCEPT — P2 (batch commit; advisory lock) |
| ARCH-014 / PERF-008 | Blocking `urllib` in `fx.refresh_from_ecb` reachable from a route | MEDIUM | CONFIRMED | ACCEPT — P1 (ride with the blocking-I/O batch) |
| ARCH-015 | Upload quota check-then-act | LOW | LIKELY | DEFER — P3 |

### Performance (PERF)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| PERF-001 | Reconciliation candidates load whole cash tables into Python | HIGH | CONFIRMED | ACCEPT — P2 |
| PERF-002 | Dashboard materialises every open payable (twice) for five scalars | HIGH | CONFIRMED | ACCEPT — P2 (after PERF-004 measures it) |
| PERF-003 | Receivables report walks all AR history on every dashboard load | HIGH | CONFIRMED | ACCEPT — P2 |
| PERF-004 | Perf harness never sets `workflow_state` and seeds no `IssuedInvoice` → the three ratios above measured empty sets; the growth gate is blind to them | HIGH | CONFIRMED (grep: neither symbol in `perf_harness.py`) | ACCEPT — P1 |
| PERF-006 | Project P&L summary O(projects×invoices) ×3 queries | HIGH | CONFIRMED | ACCEPT — P2 |
| PERF-007 | openpyxl/reportlab on the loop in analytics export | MEDIUM | CONFIRMED | ACCEPT — P1 (blocking-I/O batch) |
| PERF-009 | Integrity sweeps re-hash every document on the loop in one request | MED-HIGH | CONFIRMED | ACCEPT — P2 (202 + job) |
| PERF-010 | ~80 unbounded bare-list endpoints | MEDIUM | CONFIRMED | DEFER — P3 (house rule + top three) |
| PERF-013 | Local storage default; byte-oriented storage protocol | MEDIUM | CONFIRMED | MODIFY — see ARCH-002 |
| PERF-014 | SPA renders unbounded lists | MEDIUM | CONFIRMED | DEFER — P3 with PERF-010 |
| PERF-015 | `pool_timeout=30` hides saturation | LOW-MED | LIKELY | ACCEPT — P3 |
| PERF-016 (new, 2026-09-06) | A gen-2 garbage collection over the imported app (335,660 tracked objects) costs 131–146 ms and fires on any read that hydrates a few thousand ORM rows (about every third call at 2,400 rows) — a stop-the-world stall per worker, proportional to the process heap, not to the data. Surfaced as the R15 gate reading 7.47× then 12.24× on ONE commit (CI #544/#545; `transport_reliability` itself grows 1.85×). `docs/perf/GC-PAUSE-2026-09-06.md` | HIGH (blocked the queue-tail deploy; a ~140 ms tail on every large read in production) | CONFIRMED (reproduced without HTTP, 11.83×; fixed and re-measured, 2.87×) | ACCEPT — P1 — **DONE**: `gc.freeze()` at the end of `lifespan` (`app/core/gc_tuning.py`), the harness measures the same configuration, p50 ratio printed beside the gated p95 |
| PERF-017 (new, 2026-09-06) | After the freeze a full pass still walks the 160–190k long-lived objects the process builds after boot (compiled-statement caches, validators, registries): 40–56 ms, about every third request at 2,400 rows — the residual tail in the frozen shape runs (large p95 ≈ 100 ms vs p50 ≈ 65). Two remedies, neither a startup line: column `select`s for the whole-window reads (a fifth of the objects, no instance↔state cycles; equivalence proven per read as PERF-002/003 did), or a higher gen-2 threshold (memory cost to measure first) | MEDIUM | CONFIRMED (logged shape run) | ACCEPT — P2, after CONC-001's pool measurement (a full pass stalls every request in flight, so it is CONC-001's second suspect) |

### DevOps / SRE (OPS)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| OPS-001 | CI auto-deploy runs a host-side `deploy.sh` that skips `vps-deploy.sh`'s preflight/backup/health | CRITICAL | CONFIRMED (repo docs); host content UNKNOWN | MODIFY — P1 docs + CI health assertion; host change is an owner action |
| OPS-002 | Green `deploy` ≠ site up (`up -d --build` exits 0 before health) | CRITICAL | CONFIRMED | ACCEPT — P1 CI post-deploy `/health/ready` assertion |
| OPS-003 | No image retention/pinning; `docker image prune -f` deletes the previous build → no rollback | CRITICAL | CONFIRMED | MODIFY — P2 push SHA-tagged images from CI; compose switch is an owner-coordinated cutover |
| OPS-004 | No scheduled backups anywhere in the repo | CRITICAL | CONFIRMED (repo); host snapshots UNKNOWN | ACCEPT — P1 `scripts/backup.sh` + cron docs; installation is an owner action |
| OPS-005 | `docker-compose.yml`+`prod.yml` publishes Postgres/MinIO with default credentials | CRITICAL | CONFIRMED | ACCEPT — P1 |
| OPS-006 | Base compose sets `ENVIRONMENT=production` without `INBOUND_EMAIL_SECRET` → `make up` crash-loops; `prod.yml` worker likewise | HIGH | CONFIRMED | ACCEPT — P1 |
| OPS-007 | Migrations run in the serving container's command with `restart: unless-stopped` | HIGH | LIKELY | MODIFY — P2 one-shot migrate service (requires compose v2 on host; owner-verified cutover) |
| OPS-008 | Observability built but unconnected; `/health/queue` not proxied; no log rotation | HIGH | CONFIRMED | ACCEPT — P1 nginx location + log rotation; monitoring stack P3 |
| OPS-009 | SSH deploy not atomic; four timeouts on record | MEDIUM | CONFIRMED | ACCEPT — P2 keepalives + health poll |
| OPS-011 | k8s manifests not deployable as committed | MEDIUM | CONFIRMED | ACCEPT — P2 |
| OPS-012 | Floating base-image tags | LOW | CONFIRMED | DEFER — P3 |
| OPS-013 | No GitHub Environment on `deploy` | LOW | CONFIRMED | ACCEPT — P2 (owner configures reviewers) |

### QA / testing (QA)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| QA-001 | Eight Postgres-only concurrency/RLS tests execute in **no** CI job (postgres job lists 3 files) | HIGH | CONFIRMED (`ci.yml:132`) | ACCEPT — P1 + meta-test |
| QA-002 | SEPA `CtrlSum`/`NbOfTxs` have no multi-payee oracle | HIGH | CONFIRMED | ACCEPT — P1 |
| QA-003 | No API contract test SPA↔pydantic | HIGH | CONFIRMED | ACCEPT — P1 (OpenAPI drift gate) |
| QA-004 | Visual regression covers `/design` only | MEDIUM | CONFIRMED | DEFER — P3 |
| QA-005 | Extraction tested for provenance, not arithmetic | MEDIUM | CONFIRMED | ACCEPT — P2 |
| QA-006 | Dunning asserts counts, never the amount demanded | MEDIUM | CONFIRMED | ACCEPT — P2 |
| QA-007 | Cash application: one test; refusal figures untested | MEDIUM | CONFIRMED | ACCEPT — P2 |
| QA-008 | Perf gate has no write scenario | LOW | CONFIRMED | DEFER — P3 |
| QA-009 | `RELEASE-READINESS.md` is stale (2403 tests, "no CI runners", closed defects marked open) and outside the docs-truth gate | MEDIUM | CONFIRMED | ACCEPT — P1 truth-up |
| QA-010 | CI retries mask flakes; two midnight-boundary assertions | LOW | CONFIRMED | ACCEPT — P3 |

### Frontend / UX (FE)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| FE-001 | No `MutationCache.onError` backstop; 21 mutations fail with no surface at all | HIGH | CONFIRMED | ACCEPT — P1 |
| FE-002 | 401 interceptor hijacks accept-invite/verify/reset flows and drops the server's message; no `?next=` | HIGH | CONFIRMED | ACCEPT — P1 |
| FE-003 | No ErrorBoundary — one render throw whites out the app | HIGH | CONFIRMED | ACCEPT — P1 |
| FE-004 | 23 of 61 query pages render a 4xx as "no data" | HIGH | CONFIRMED | ACCEPT — P2 (ratchet, top three first) |
| FE-005 | Flash-empty / lie-on-error lists | MEDIUM | CONFIRMED | ACCEPT — P2 with FE-004 |
| FE-006 | 83 hand-rolled tables vs `DataTable` | MEDIUM | CONFIRMED | DEFER — P3 ratchet gate |
| FE-007 | 33 tables with no scroll container; 4 clipped by `overflow-hidden` | MEDIUM | CONFIRMED | ACCEPT — P1 for the four clipped |
| FE-008 | 10 `window.confirm`; 13 unconfirmed deletes; `Customers` "archive" label calls DELETE | HIGH | CONFIRMED | ACCEPT — P1 for the unconfirmed deletes and the verb mismatch |
| FE-009 | Errors rendered in success-green, no `role="alert"` | MEDIUM | CONFIRMED | ACCEPT — P2 |
| FE-010 | Success near-silent | MEDIUM | LIKELY | DEFER — P3 |
| FE-011 | ~114 controls without programmatic labels | MEDIUM | LIKELY | ACCEPT — P2 (gate inverse pass) |
| FE-012 | Only 11 `<form>`s; `required` inert | MEDIUM | CONFIRMED | DEFER — P3 |
| FE-013 | 30 mutation buttons without pending guard | MEDIUM | CONFIRMED | ACCEPT — P2 |
| FE-014 | Three button systems | MEDIUM | CONFIRMED | DEFER — P3 |
| FE-015 | 1,500-line pages | MEDIUM | CONFIRMED | DEFER — P3 (only where a bug is fixed) |
| FE-016 | Unbounded lists client-side | MEDIUM | LIKELY | DEFER — P3 with PERF-010 |
| FE-018 | Zero i18n readiness; en-IE/en-GB inconsistency | MEDIUM | CONFIRMED | MODIFY — P2 centralise locale; i18n is an owner decision |
| FE-019 | 4xx retried; no request cancellation | LOW | CONFIRMED | ACCEPT — P2 (one-line retry predicate) |
| FE-020 | Bundle headroom 9% | LOW | CONFIRMED | no action |
| FE-021 | `/design` fixtures ship to production | LOW | CONFIRMED | ACCEPT — P2 (gate on DEV) |

### Product / commercial (PROD)

| ID | Title | Sev | Certainty | Decision |
|---|---|---|---|---|
| PROD-001 | A declined subscription payment sets `org.status=suspended` → every request 401s, including `/billing/*`, so the customer cannot reach the screen to fix the card; no email | HIGH | CONFIRMED (`billing.py:251`, `billing_provider.py:136-141`, `deps.py:116-122`) | MODIFY — P1: billing routes reachable for a suspended org's `BILLING_MANAGE` holder (bug fix); grace/dunning ladder is an **owner decision** |
| PROD-002 | The documented 14-day trial does not exist in code; signups get full features forever | HIGH | CONFIRMED | **OWNER DECISION** (business behaviour) — P1 to ask |
| PROD-003 | Nav gated on the 4-tier ladder while the API gates on the 8-role matrix → dead links for six roles | HIGH | CONFIRMED | ACCEPT — P1 (drive nav off served permissions) |
| PROD-004 | Block-at-cap ships with no warning, no upgrade path, copy says "ask a platform operator" | MEDIUM | CONFIRMED | ACCEPT — P2 |
| PROD-005 | EveryPay customers cannot cancel/change plan/get a receipt | HIGH | CONFIRMED | DEFER — P2 gated on PROD-006 (owner) |
| PROD-006 | Seller-of-record VAT undecided; subscription invoices at 0% | HIGH | CONFIRMED | **OWNER DECISION** (already §2 in DECISIONS-NEEDED) |
| PROD-007 | No DPA/ToS/privacy policy; nothing accepted at signup | HIGH | CONFIRMED | **OWNER DECISION** (legal) |
| PROD-008 | Demo credentials printed on the production login page | MEDIUM | CONFIRMED | ACCEPT — P1 (gate on DEV) |
| PROD-009 | No workspace deletion / whole-tenant export; ex-client archive export unbuilt (= WO-AI) | HIGH | CONFIRMED | ACCEPT — P2 (WO-AI DONE 5f63ca1: the ex-client archive export; workspace deletion / whole-tenant export remain open) |
| PROD-010 | Three role vocabularies; manual documents four roles | MEDIUM | CONFIRMED | ACCEPT — P2 (labels + manual grid) |
| PROD-011 | Onboarding "Invite your team" links to `/settings` (Team is `/team`); any invitation row ticks the step | MEDIUM | CONFIRMED (`onboarding.py:57-58,88`) | ACCEPT — P1 (trivial) |
| PROD-012..020 | Support surface, i18n, pricing/PLANS drift, audit caveats, admin console, invite email, residency, SOC2/SAML, metering | — | CONFIRMED | owner decisions + P2/P3 |

## 2. Cross-agent debate (HIGH/CRITICAL items)

Format: proposal → support → challenge (Adversarial Reviewer) → alternative → decision.

**SEC-001 unusable-password backdoor.** Proposal (Security): store `NULL`/non-hash for SSO users, refuse password login. Support (Backend, Lead): confirmed by reading; the literal is in a public repo. Challenge: the per-account lockout and rate limit? — irrelevant, the first attempt succeeds. Is a data migration needed? — yes: existing rows carry the hash; bcrypt is salted so rows must be found by verifying against the two literals. Alternative: make the column nullable — a wider schema change touching every reader of `hashed_password`. **ACCEPT, MODIFIED**: keep `NOT NULL`, store the non-modular-crypt sentinel `"!"`, refuse it explicitly in `verify_password` (not only via the library's exception), migrate existing rows by verification, add the route-level reproduction test that fails on the old code. P0.

**SEC-002/ARCH-001 forwarded IPs.** Proposal: pin `--forwarded-allow-ips` to the proxy CIDR. Challenge: the compose network's subnet is not stable across hosts, so a CIDR literal in the Dockerfile is wrong for someone. Alternative (Lead): drop `--proxy-headers` entirely and let the app's own `_client_ip` (already correct: rightmost-N) be the single authority, with `TRUSTED_PROXY_COUNT=1` set in every deployment file. Both audit and rate limit already call the same helper. **ACCEPT, MODIFIED.** Structural test: no deployment file may carry `forwarded-allow-ips '*'`.

**PROD-001 lockout on declined payment.** Proposal (Product): grace period + dunning ladder + banner. Challenge: that is a change to fundamental business behaviour (when a non-paying tenant loses access) and the owner has not decided it; the AUTONOMY RULE forbids deciding it internally. But one part is a defect regardless of policy: the person who can fix the card cannot reach the billing screen. **MODIFY**: ship the reachability fix (a suspended org's `BILLING_MANAGE` holder may call `/billing/*`); put the grace policy to the owner with the recommended default.

**DB-002 numbering collision.** Proposal (DB): change the unique constraint to `(org_id, issuer_id, number)` + unique prefixes. Challenge: a constraint swap on the legal-document table is a migration that can fail mid-deploy on the auto-deploy path (OPS-002/007), and the org-level constraint is not wrong — it is stricter. Alternative: make prefixes unique per org at the service layer and give a new issuer a distinct default; keep the org-level constraint as the backstop. **MODIFY**: service-level now (P1); the DB constraint with a pre-flight report is P2.

**DB-005 supplier invoice number uniqueness.** Proposal: partial unique index per vendor. Challenge: suppliers legitimately reuse numbers (per-year series, credit notes sharing the invoice's number, "1" from a sole trader every month); a hard constraint would refuse real documents at intake, and the `duplicates` advisory service exists for exactly this. **REJECT** the constraint; P3 to tighten the advisory.

**OPS-001..004 deploy path.** Proposal (DevOps): point the host's forced command at `vps-deploy.sh`; one-shot migrate service; SHA-tagged images; scheduled backups. Challenge: the host's `/root/deploy.sh` is outside the repo — the repo cannot change it, only document it; the compose `service_completed_successfully` condition needs compose v2 on the host (UNKNOWN); switching `build:` to `image:` is a cutover that must be coordinated with the owner. Alternative: land everything that is repo-side and safe now — CI asserts `/health/ready` after the SSH step (turns the silent-outage mode into a red job), docs carry the corrected `deploy.sh`, `scripts/backup.sh` exists and its cron line is documented — and mark the host-side steps BLOCKED on the owner. **MODIFY** as stated.

**ARCH-005 god-controllers.** Proposal: extract an application layer. Challenge: 5,235 lines of working, tested money code; a layer extraction is exactly the "architectural purity" rewrite RULE 5 forbids; no defect in this audit is caused by the layering itself. **DEFER**: record as debt; extract a use-case function only when a flow is touched for a defect (credit-note proration and expense decision first).

**ARCH-002/PERF-013 refuse `local` storage in production.** Proposal: production validator rejects `storage_backend=local`. Challenge: the live VPS runs `local` on a shared volume by design (documented in the compose file); the validator would take production down on the next deploy. **MODIFY**: fix the k8s config; add a startup warning (not a refusal) when `local` + `WEB_CONCURRENCY>1` without an explicit acknowledgement. P2.

**QA-001 orphaned Postgres tests.** Proposal: run the whole suite under the Postgres URL. Challenge: that doubles CI time for eight tests. Alternative: list the six missing files and add a meta-test asserting every `pg_only` file is named in the workflow. **ACCEPT, MODIFIED.**

**QA-003/ARCH-011 contract gate.** Proposal: OpenAPI codegen for `types.ts`. Challenge: a 2,762-line hand file cannot be swapped in one change without breaking every page; codegen is a project. Alternative: the ERD pattern — check in `openapi.json`, fail on drift — closes "the server changed and nobody noticed" today; codegen follows. **ACCEPT, MODIFIED.**

**FE-001/002/003.** No challenge survived: each is a one-file backstop with no behaviour change for pages that already handle the case. **ACCEPT.**

**PROD-002 trial.** Proposal: build the 14-day clock. Challenge: whether free signups should lose features after 14 days is the owner's pricing decision, and the code currently contradicts three documents either way. **OWNER DECISION**; engineering prepares nothing until answered, but the docs must stop promising it.

**PROD-008 demo credentials.** Challenge: the owner may demo from production using them. Response: gating the *display* on the dev build removes nothing from the owner, who knows the credentials; a security questionnaire screenshot does not. **ACCEPT.**

## 3. Master priority backlog

Statuses: NOT STARTED · INVESTIGATING · IMPLEMENTING · BLOCKED · TESTING · REVIEW · DONE · REJECTED · DEFERRED.

| ID | P | Task | Owner | Deps | Risk | Cx | Validation | Status |
|---|---|---|---|---|---|---|---|---|
| SEC-001 | P0 | Unusable-password sentinel + retire legacy hashes by migration + route reproduction test | Security/Lead | — | LOW (sentinel is a strict subset of old behaviour) | S | 6 new tests incl. the pre-fix 200 → post-fix 401 reproduction; full regression | NOT STARTED |
| SEC-002 | P1 | Drop `--proxy-headers '*'`; `TRUSTED_PROXY_COUNT=1` in all deployment files; structural test | DevOps/Security | — | MED (client IP for audit changes source) | S | test asserting no `forwarded-allow-ips '*'`; rate-limit spoof test | NOT STARTED |
| QA-001 | P1 | Add six pg-only files to CI postgres job + meta-test | QA | — | LOW | S | meta-test; CI run | NOT STARTED |
| BE-001/002/003 | P1 | Jobs: guard `retry` to dead/failed; dead-letter on stale reclaim at max attempts; savepoint in `enqueue` | Backend | — | MED (queue semantics) | M | 3 regression tests each proven to bite | DONE 3e9358c |
| BE-005 | P1 | Commit the charge claim before `charge_mit` | Backend | — | LOW | S | patched-provider double-run test | DONE 0e41463 |
| PROD-001 | P1 | Billing routes reachable for a suspended org's billing manager | Backend/Product | — | MED (auth dependency variant) | M | route tests: suspended org → `/billing` 200, other routes 401 | DONE f1dbfd8 |
| WO-AE | P1 | IdP role vocabulary (in flight) | Lead | — | LOW | M | 11 backend + 5 e2e | DONE d398d00 |
| SEC-003/004, OPS-008 | P1 | nginx: CSP, repeat headers in `/assets/`, proxy `/health/queue`; compose log rotation | DevOps | — | MED (CSP can break the SPA) | S | serve `dist/` under the CSP locally and run smoke e2e | DONE 63a6408 |
| OPS-002 | P1 | CI post-deploy `/health/ready` assertion | DevOps | — | LOW | S | workflow lint; next main run | DONE 63a6408 (host var DEPLOY_HEALTH_URL: owner) |
| OPS-004 | P1 | `scripts/backup.sh` + documented cron; host install BLOCKED on owner | DevOps | — | LOW | S | script dry-run against scratch PG | DONE 63a6408 (cron install: owner) |
| OPS-005/006 | P1 | compose: hide db/minio ports + require passwords in prod.yml; `ENVIRONMENT` default dev in base; worker secret in prod.yml | DevOps | — | LOW (not the live path) | S | `docker compose config` renders; validator test | DONE 63a6408 |
| OPS-001/010, QA-009 | P1 | Docs: corrected `deploy.sh`, SECRET_KEY/KEK consequence, `.env` in backup, RELEASE-READINESS truth-up | DevOps/QA | — | LOW | S | docs-truth gate extended to RELEASE-READINESS | DONE 5ac16f7 |
| DB-004 | P1 | Unique `(org_id, sha256)` on `bank_statements` + IntegrityError→ReconError | DB | — | LOW (pre-flight) | S | duplicate-import test; migration round-trip | DONE 2dc9851 |
| DB-001 | P1 | `billing_payments.amount_eur` → `Numeric(14,2)`; Decimal in service/route | DB | — | LOW | S | migration round-trip; exact-equality test | DONE 02ed215 |
| DB-002 | P1 | Unique invoice/credit prefix per org at the service; distinct default for a new issuer | Backend/DB | — | LOW | S | second-issuer collision test | DONE 1e3b69e |
| Blocking I/O | P1 | `run_in_threadpool` for `filesec.check` (11 sites), Stripe SDK calls, `fx._fetch`, report writers, bcrypt in auth routes; clamd timeout | Backend/Perf | — | LOW | M | thread-identity tests | DONE ac313de |
| PERF-004 | P1 | Harness seeds `workflow_state` + `IssuedInvoice`/`Payment`; re-measure | Perf | scratch PG | LOW | S | harness run; ceilings re-justified | DONE 3196e2c (+ PERF-002/003 fbc14df, PERF-005/010 0c24bc8) |
| QA-002 | P1 | SEPA multi-payee `CtrlSum`/`NbOfTxs` oracle | QA | — | LOW | S | test | DONE (this commit) |
| QA-003 | P1 | `openapi.json` drift gate (ERD pattern) | QA/Arch | — | LOW | S | test | DONE (this commit) — no drift found; snapshot at docs/api/openapi.json |
| DB-012 | P1 | Parity test compares uniques/checks/indexes/FK ondelete | DB/QA | — | MED (may reveal drift) | S | test | DONE (this commit) — zero constraint drift found at head e6a8c0d2f4b6 |
| FE-001/002/003 | P1 | MutationCache backstop; public-route 401 allowlist + `?next=`; ErrorBoundary in shell | Frontend | — | LOW | S | e2e: silent-mutation toast; accept-invite 401 message; boundary renders | DONE (this commit) |
| FE-007/008 | P1 | Four `overflow-hidden`→`overflow-x-auto`; confirm the unconfirmed deletes; `Customers` verb | Frontend | — | LOW | S | e2e | DONE (this commit) — 17 deletes confirmed, not 13 |
| PROD-003 | P1 | Nav visibility from served permissions | Frontend/Product | — | MED | M | e2e per role | DONE (this commit) — `/auth/me` serves `permissions`; every nav item names its router's permission; in-page controls still on the ladder mirror (P2, PROD-010) |
| PROD-008/011 | P1 | Demo creds gated on DEV; onboarding href `/team` + invitation status filter | Frontend/Backend | — | LOW | S | tests | DONE (this commit) |
| WO-AF | P1 | Statement-byte vaulting | Lead | — | LOW | M | tests | DONE (this commit) — `docs/plan/plan-a/wo/WO-AF-statement-byte-vaulting.md` |
| PROD-002/006/007, grace policy | P1 | Owner decisions | Owner | — | — | — | recorded in DECISIONS-NEEDED | BLOCKED (owner) |
| OPS-001/003/007 host side | P1 | Host `deploy.sh` → `vps-deploy.sh`; image cutover; migrate service | Owner+DevOps | docs above | MED | M | manual deploy rehearsal | BLOCKED (owner) |
| R2-C1 | P2 | BE-005 residual: the dedupe key is per DAY, so a settled charge whose response was lost is charged AGAIN the next day (not "missed once") — key the claim on the period paid or reconcile by `order_reference` first | Backend | Phase 12 review | MED | S | patched-provider test: raise after settle → next-day run charges 0 | DONE (P2 batch 1) — claim keyed on the PERIOD paid; lost-response case stalls as duplicate with the order_reference logged for reconciliation |
| R2-C4 | P2 | A suspended owner who downgrades to Free stays suspended (only a settled payment clears `status`) — belongs with DECISIONS §18 | Backend/Owner | Phase 12 review | LOW | S | route test | BLOCKED (owner §18) |
| R2-B2 | P2 | FE-001 backstop double-surfaces on 6 pages that render `mutation.error` inline without `onError` (Billing, Issuer, Sessions, VerifyEmail, ResetPassword, Portal) | Frontend | Phase 12 review | LOW | S | e2e: one surface | DONE (P2 batch 1) — `meta: { silent: true }` on the 7 inline-rendering mutations; e2e asserts one surface |
| R2-A3 | P2 | 43 page-level controls still gate on the stored-role mirror (`hasVatPerm`/`isAdminOrAbove`) while the nav reads served permissions (= PROD-010 scope) | Frontend | Phase 12 review | MED | M | e2e per role | DONE (P2 product set) — 20 pages on `hasPerm(...)`; e2e: role `user` + served `settings.manage` sees "New tax code", role `admin` without it does not, no served list → mirror (ADR-A25) |
| R2-T2 | P2 | Threadpool test covers `ensure_customer` only; `start_checkout`, `create_portal_url`, `report_usage` unasserted | QA | Phase 12 review | LOW | S | test | DONE (P2 batch 1) |
| R2-A2 | P3 | `cash_position` imports underscore-private helpers from `ap_aging` | Backend | Phase 12 review | LOW | S | — | DEFERRED |
| R2-C3 | P3 | `reclaim_stale` SELECT-then-UPDATE window vs a worker finishing in between (old code had the sibling race) | Backend | Phase 12 review | LOW | S | pg race test | DEFERRED |
| P2 batch 1 | P2 | BE-004 (enqueue answers 200 `deduplicated`), BE-016 (DB failures in `audit.record` propagate), SEC-007 (login password ≤200), SEC-011 (pip-audit CI step; pypdf 6.15.0→6.16.1 for three CVEs; PYSEC-2026-1325 ignored with reason) | Backend/DevOps | — | LOW | S | tests per row | DONE (P2 batch 1) |
| P2 batch 2 | P2 | SEC-005 (portal/calendar tokens masked in the app access log and nginx origin log), BE-009 (inbound email Message-ID idempotency: column, index, migration f1a2b3c4d5e6, both webhook routes), BE-010 (the four bank-file exports are POST; GET is 405; SPA + tests follow) | Backend/DevOps/Frontend | — | LOW–MED (verb change is client-visible) | S–M | 3 + 1 + 1 tests | DONE (P2 batch 2) |
| P2 product set | P2 | FE-004/005 (Issue, SupplierCosts, ExpenseDetail error states + `check-query-errors.mjs` ratchet listing the 20 pages still without one, CI step), FE-009 (42 inline error boxes `role="alert"`), PROD-010 (business-name role labels, MANUAL §1.2 8-row grid with stored key + permission count, `test_prod010_role_docs.py`), PROD-004 (402 copy names the owner and Plan & billing; Upload pre-cap notice with the upgrade path), R2-A3 (above) | Frontend/Product/Docs | — | LOW (wording + visibility; server behaviour unchanged) | M | 8 e2e + 5 backend tests; 4 seeded violations proven | DONE (147fc2e) — CERTIFIED: full backend 3086/15/0, e2e 455/0, CI #537 green, on main (CI #538 deploying) |
| Queue tail | P2 | WO-AJ (receipt-control run as job kind `transport.receipt_control` + panel button), WO-AH (`due_soon` view + route + dashboard card), WO-AG (`vat_country_requirements` + readiness helper + routes + panels), WO-AI (`archive_exports` + zip build job + owner route + public request/download + page), BE-011 (payment-run list grouped count + bound), BE-012 (review queue grouped counts), concurrency measurement mode (harness `--concurrency`, CI informational step, `docs/perf/CONCURRENCY-2026-09-06.md` — datapoint 0 recorded verbatim from CI #543 with its two artefacts, plan cap and per-token limiter, both removed from the measurement; findings CONC-001/002) | Backend/Frontend/DevOps/Docs | — | LOW–MED (two new tenant tables with RLS; one public POST/GET pair, no-enumeration) | L | 18 backend + 2 probes + 13 e2e + 1 structural; 5 seeded violations proven | DONE (595ab85) — batch-end regressions + CI pending; first concurrency datapoint pending from CI |
| PERF-016 | P1 | Freeze the startup heap at the end of `lifespan` (`app/core/gc_tuning.py`); harness freezes once per process and reclaims the seed's garbage before measuring; growth table prints the p50 ratio beside the gated p95; `docs/perf/GC-PAUSE-2026-09-06.md` | Lead | — | LOW (one documented CPython call, after every startup write; no behaviour change) | S | 4 tests (functional freeze, lifespan order, harness order in both modes, gate stays on p95); shape ×3 locally 2.66/1.50/2.45 (was 4.25–12.24); full backend 3111/15/0 (44:35); CI on the branch; main | DONE — see ledger |
| P2 set (remaining) | P2 | BE-008/014/020, DB-007/008/009/011/013/014/017, ARCH-002/008/013, PERF-001/006/009, OPS-003/007/009/011/013, QA-005/006/007, FE-011/013/018/019/021 (FE-004's ratchet still lists 20 pages), PROD-005/009 (PROD-009's ex-client export half = WO-AI DONE; workspace deletion / whole-tenant export still open) | various | | | | | NOT STARTED — the 30-day plan's four weeks are delivered; what remains is P2 debt beyond the plan |
| P3 set | P3 | BE-013/015/017/018/019, DB-005/006/010/015, ARCH-006/007/009/010/012/015, PERF-010/014/015, OPS-012, SEC-006/009, QA-004/008/010, FE-006/010/012/014/015/016 | various | | | | | DEFERRED |
| P4 | P4 | SEC-010, DB-016 | | | | | | DEFERRED |

## 4. Decision log

- **ADR-A1 — Unusable password is a non-hash sentinel, not a nullable column.** Keeps `users.hashed_password NOT NULL` (every reader unchanged); `verify_password` refuses any non-`$2` value explicitly. Alternative (nullable column) rejected as a wider change for the same guarantee.
- **ADR-A2 — One client-IP authority.** uvicorn's proxy-header handling is removed; `ratelimit._client_ip` with `TRUSTED_PROXY_COUNT` is the single implementation for rate limiting and audit. Alternative (pin `--forwarded-allow-ips` to a CIDR) rejected: the compose subnet is not stable across hosts.
- **ADR-A3 — No architectural rewrite.** The four god-controllers are recorded debt, extracted incrementally when touched for a defect. Rejected: an application-layer extraction pass.
- **ADR-A4 — Business-behaviour changes go to the owner.** Trial expiry, payment-grace policy, seller-of-record VAT, password policy/MFA, i18n locales, DPA/ToS are recorded as decisions, not decided in code.
- **ADR-A5 — Contract gate before codegen.** A checked-in `openapi.json` with a drift test (the ERD pattern) lands first; `types.ts` generation is a later project.
- **ADR-A6 — Host-side deployment changes are owner-executed.** The repo documents the corrected `deploy.sh`, ships `backup.sh`, and asserts health from CI; nothing in the repo can change `/root/deploy.sh`.
- **ADR-A15 — Phase 12 conditions closed before main (R2-S1, R2-A1, R2-C1-doc).** (S1) `GET /transport/statements/{sha}/file` uses `security_headers.content_disposition` (RFC 5987, injection-safe) — the hand-rolled header stripped only `"` and would 500 on a non-latin-1 filename; a catalog row whose object is missing is a 404 `statement_not_found`, never an empty 200. (A1) Migration `c3e5a7b9d1f2` scans only users of orgs that have an SSO connection or a SCIM-provisioned identity — bcrypt-verifying two literals costs ~0.5 s per user, and the deploy runs `alembic upgrade head` under a 300 s health gate; the production user count is UNKNOWN from the repository, so the bound is applied regardless. (C1) BE-005's ledger row states the true residual (see R2-C1); the code fix stays P2 because it changes billing semantics (period-keyed claim) and deserves its own certification.
- **ADR-A16 — DB-002 also refuses one issuer using the same prefix for invoices and credit notes** (`issuer.py:118-119`, tested). Unflagged in the DB-002 commit; recorded here. An existing profile with equal prefixes cannot be saved until one is changed — the refusal names both.
- **ADR-A17 — WO-AE tie-break** (later in `ASSIGNABLE_ROLES` wins when ranks tie) is a stated rule in `WO-AE-idp-role-mapping.md`; recorded here for completeness.
- **ADR-A18 — `audit.record` no longer swallows database errors (BE-016).** A SQLAlchemyError while appending the event propagates; attribution/hash failures stay best-effort. Behaviour change, flagged: an operation whose audit row cannot be written now fails instead of committing unaudited (ADR-0012's rule) or failing later at its own commit with a misleading message.
- **ADR-A19 — the renewal claim is keyed on the period paid (R2-C1).** A settled charge whose response was lost now stalls as "duplicate" every day until an operator reconciles it against the provider by the logged `order_reference` and advances `everypay_next_charge` by hand; it can no longer charge the card a second time. The stall is the recoverable side; the runbook line is the warning the worker logs.
- **ADR-A20 — bank-file exports are POST (BE-010).** `POST /payment-runs/{id}/export|sepa` and `POST /reimbursements/{id}/export|sepa`; the GET forms answer 405. A client-visible API change, flagged: the SPA (`downloadFile(..., { method: "post" })`) moved with it and the contract snapshot records both. Producing a payment file advances the export-once counter and audits — a browser prefetch or proxy revalidation must never be able to consume "the one export".
- **ADR-A21 — Message-ID is the only inbound-email identity (BE-009).** A delivery without one is stored on every retry; the platform does not guess an identity from sender + subject + attachment digests, because two genuine messages can share all three.
