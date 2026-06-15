# Security & Compliance Evolution Plan — operating as a multi-client SaaS

**Purpose.** A concrete, ordered plan to evolve the platform into a multi-client SaaS **without
creating GDPR, antitrust, or security problems for the operator.** Grounded in the read-only
security audit (single-tenant baseline is strong; the product is *not* multi-client-ready yet).
Companion to `docs/MULTI_TENANCY.md` (the technical phasing) and `docs/FINDINGS.md` (which already
flags competition law as the #1 risk). This doc is the *operator's* compliance roadmap and the
**go-live gate**.

> **The one rule that prevents 90% of the legal risk:** do **not** turn the `multitenant` switch ON
> for real clients until every item in §1 ("Hard go-live gates") is done and proven. A single
> cross-tenant leak is a GDPR Art. 33/34 personal-data breach — reportable within 72 hours, with
> fines and trust damage. Until then the system is single-tenant and safe.

---

## 0. Current posture (where the line is today)

- **Safe now (single-tenant):** scrypt password hashing + per-user/per-IP lockout + timing-equalized
  verify; API tokens stored SHA-256 only, constant-time compare, default-off; envelope-encrypted
  portal credentials; central CSRF (fail-closed) + `_guard` on every route; CSP/HSTS/X-Frame;
  XML via ElementTree, zip-bomb + path-traversal guards; parameterized SQL throughout; setup wizard
  gated on zero-active-users.
- **Not safe for multi-client:** tenant isolation is a *foundation only* (nothing scoped), the worker
  carries no tenant, one KEK decrypts all tenants, no GDPR data-subject rights, the benchmark pools
  across clients, and one export has CSV formula injection.

**Implication:** the work ahead is *isolation + governance*, sequenced so you never onboard a second
client into a shared store that isn't yet partitioned.

---

## 1. Hard go-live gates — MUST be done before the 2nd client shares an installation

Each maps to an audit finding. None is optional; each is a breach or a real vulnerability if skipped.

| Gate | What | Why (risk if skipped) |
|---|---|---|
| **G1 — Scope every table** | Add `tenant_id` to every product + platform store (full list in the audit's inventory); wire `tenancy.scope_clause()` / Postgres RLS into **every** query reachable by any route; a green cross-tenant test per table. | Cross-tenant read/write = GDPR Art. 33/34 breach. |
| **G2 — Tenant-context the worker** | `tenant_id` on `intake_jobs`, stamped at enqueue from `current_tenant()`, `set_tenant()` in the worker before each register/close/fetch job. | Async jobs would write the wrong tenant's data even after the web path is scoped. |
| **G3 — Per-tenant credential custody** | Move off the `local` KEK to `env`/KMS with per-tenant `FFS_KEK_KEY_<TENANT>` (BYOK); run the re-wrap migration. | One key bulk-decrypts every tenant's supplier portal logins. |
| **G4 — Benchmark isolation** | Tenant-scope `benchmark.db` (`my_prices`/`wholesale_prices`) and the peer cohort; a client sees only its own entities. Keep any *pooled* benchmark OFF (see §3). | Antitrust hub-and-spoke information exchange between competitors. |
| **G5 — API tokens tenant-scoped** | `tenant_id` on `api_keys`; bind on `verify()`; scope every `/api/v1` query. | One client's token reads every client's customer master/IBANs. |
| **G6 — Platform stores scoped** | error_log, login_log, import_log, api_usage, audit CSVs get `tenant_id`; admin/log views never show another tenant's rows (PII in stack traces). | An admin "recent errors" view leaking another tenant's data is itself a breach. |
| **G7 — GDPR data-subject rights** | Per-tenant **export** (Art. 15/20) and **erasure/rectification** (Art. 17/16); a retention + auto-purge policy per data class. | Legally required of a controller/processor; no mechanism exists today. |
| **G8 — CSV/cell injection** *(do now — not multi-tenant-gated)* | Neutralize leading `= + - @ \t \r` in free-text cells of the accounting-ledger CSV and the xlsx exports. | Formula injection executes on the client's machine from an ingested supplier name/note. **Exploitable today.** |

G8 is being fixed immediately (it's a current vulnerability). G1–G7 are the multi-client gate.

---

## 2. GDPR framework (what you sign up to as operator)

- **Roles.** When you host clients' data you are typically a **processor** (Art. 28) acting on each
  client's instructions; the client is the **controller**. Each client needs a **DPA** with you, and
  you need a **sub-processor register** (Art. 28(2)/(4)) for: SharePoint/FTP backup target, AISP
  aggregator, factoring partner, AI backend (if enabled), and the KMS provider. List them, get the
  client's consent to each, and flow down the same obligations.
- **Records of processing (Art. 30).** Maintain a register: categories of data subjects (client staff,
  drivers via vehicle IDs), data categories (identity, IBAN, location-of-fuelling, credentials),
  purposes, recipients, retention, and transfers.
- **Data-subject rights (Art. 15–22).** Build per-tenant **access/export**, **erasure**,
  **rectification**. (G7.) Have a runbook to action a controller's forwarded request within a month.
- **Data minimisation & retention (Art. 5).** Define retention per class (transactions/VAT have legal
  tax minimums — keep; audit CSVs, error logs, data-lake AI artifacts — set a purge). Nothing
  auto-deletes today; add it.
- **Breach (Art. 33/34).** A 72-hour breach runbook; tenant-stamped logs so you can scope an incident
  to one tenant instead of declaring a breach for all. (G6.)
- **Residency / transfers (Ch. V).** Confirm the off-site backup and SharePoint/FTP target are in the
  EU/EEA (or covered by SCCs/adequacy). Encrypt snapshots at rest (they currently bundle `security.db`
  = password + API-key hashes + PII in error traces).
- **Security of processing (Art. 32).** Per-tenant encryption keys (G3), provable isolation (G1), audit
  on every write (G6) — the technical measures that make Art. 32 defensible.

## 3. Antitrust / competition framework (the benchmark is the trap)

- **The rule:** never let one client see another client's current/identifiable pricing — directly or via
  an aggregate small enough to reverse-engineer. That is the **hub-and-spoke information exchange** the
  EU 2023 Horizontal Guidelines treat as a competition-law violation.
- **Default posture (safe):** keep all benchmark/price intelligence **strictly intra-tenant** — a client
  benchmarks only its own entities/fuel cards. This is G4 and removes the risk entirely.
- **If you ever want a *pooled* cross-client benchmark as a product** (a real revenue idea, but
  counsel-gated): only via an **independent trustee/aggregation** model — minimum cohort of **distinct
  clients** (not entities), aggregation + **time-lag** so no current price is exposed, suppression when a
  cell could single out a contributor, and explicit legal sign-off + participant agreements. Do **not**
  build this on the live shared `benchmark.db`. Treat it as a separate, gated product (see
  `docs/STRATEGY.md` Opp. 3 / `docs/FINDINGS.md`).

## 4. The phased sequence (maps to MULTI_TENANCY.md P0–P5)

- **P0 — Foundation** *(done)*: tenant registry + context + OFF-by-default switch + the plan. No client data
  touched.
- **P1 — Schema**: add `tenant_id` + backfill the existing single tenant on every store in the inventory
  (incl. platform stores G6 and `intake_jobs` G2). No behaviour change while OFF.
- **P2 — Enforce**: wire `scope_clause`/RLS into every query, table-by-table, **each landing with a green
  cross-tenant access test**; tenant-scope API keys (G5) and the benchmark/peer cohort (G4). This is the
  bulk of the work and the real isolation guarantee.
- **P3 — Custody**: per-tenant KEK/BYOK (G3) + re-wrap migration; encrypt backups, document residency (G9
  audit item); tenant-scoped backup isolation decision.
- **P4 — Rights & governance**: GDPR data-subject export/erasure + retention/auto-purge (G7); Art. 30/28
  registers, DPA template, 72-hour breach runbook; egress allow-list before tenant-admins configure
  portal/market URLs.
- **P5 — Assurance**: SOC 2 Type II + ISO 27001/27017/27018; pen-test of the tenant boundary; only then
  market multi-client / white-label.

**Activation rule:** the `multitenant` switch flips ON for a real second client only after **P1+P2 are
complete for every reachable table** (G1, G2, G4, G5, G6) and **P3 custody** (G3) is in place. P4 (rights)
must be live before or at the same time — it's a legal precondition, not a follow-up.

## 5. The operator decisions that shape the build (make these first)

1. **Isolation model:** shared-DB + Postgres RLS (app-level + DB-level defence in depth) for most clients,
   vs **DB-per-tenant** for a high-value client demanding hard isolation? (RLS footguns — non-owner role +
   FORCE RLS, transaction-local `set_config` — are documented in MULTI_TENANCY.md.)
2. **Pooled benchmark:** intra-tenant only (safe default), or pursue the counsel-gated pooled product (§3)?
3. **Backup residency & isolation:** where do off-site backups physically live (EU?), and per-tenant or
   shared snapshots?
4. **Retention periods** per data class (tax/legal minimums vs GDPR minimisation).
5. **AI review in SaaS:** if offered, the AI backend becomes a named sub-processor and the
   "derived-data-only, never PDF/IBAN" guarantee needs contractual + test enforcement.
6. **KMS provider** for G3 (cloud KMS vs Vault) — also the credential-custody go-live decision.

## 6. Minimum compliant multi-client launch checklist

Single-client today is fine. To launch the *first shared multi-client install*, all of:
☐ G1 every table scoped + cross-tenant test green · ☐ G2 worker tenant-context · ☐ G3 per-tenant KEK ·
☐ G4 benchmark intra-tenant · ☐ G5 API tokens scoped · ☐ G6 platform stores + logs scoped ·
☐ G7 data-subject export/erasure + retention live · ☐ G8 CSV/cell injection fixed *(done)* ·
☐ encrypted backups + documented EU residency · ☐ DPA + Art. 30/28 registers + 72h breach runbook ·
☐ tenant-boundary pen-test.

---

### One-paragraph version
The single-tenant product is secure; the multi-client version is not yet, because isolation is a
foundation only. Fix the one current-day vulnerability now (CSV injection, G8). Then treat the
`multitenant` switch as locked until you have scoped every table and the worker (G1–G2, G5–G6),
given each tenant its own encryption key (G3), kept the benchmark intra-tenant to avoid the antitrust
hub-and-spoke trap (G4), and built GDPR data-subject rights + retention + the Art. 28/30 governance
(G7, §2). Do that in the P1→P5 order, gate activation on a green cross-tenant test per table, and you
become a defensible processor rather than a breach waiting to happen.
