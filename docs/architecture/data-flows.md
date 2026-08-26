# InvoiceIQ — Request & Event Data Flows

> Companion to [overview](./overview.md) and [domain-modules](./domain-modules.md). Shows how a request moves through the stack, how events propagate, the extraction pipeline, and the **idempotency / concurrency / failure-recovery** rules that keep financial effects exactly-once.

---

## 1. Synchronous request flow (write path)

Every authenticated request establishes tenant + actor context *before* any query, so isolation and audit attribution are automatic.

```mermaid
sequenceDiagram
  autonumber
  participant C as Client (SPA)
  participant MW as Middleware
  participant D as get_current_user (dep)
  participant R as Router
  participant S as Service
  participant DB as Postgres
  participant Q as Job queue (table)

  C->>MW: HTTPS request + Bearer JWT
  MW->>MW: TenantScope=None; RequestContext(request-id)
  MW->>D: resolve dependencies
  D->>DB: load user by token sub
  D->>D: set_current_org(user.org_id)<br/>set_current_actor(user.id,email)
  D->>R: authorized User
  R->>R: authorize (role/module/quota)
  R->>S: call service(body)
  S->>DB: SELECT (auto-scoped by ORM guard)
  S->>DB: INSERT/UPDATE (domain state)
  S->>DB: audit.record(...) [same txn]
  S->>Q: jobs.enqueue / webhooks.emit [same txn, commit=false]
  S->>DB: COMMIT (domain + audit + queued jobs atomic)
  R-->>C: serialized response
  MW->>MW: reset tenant + actor context
```

**Key properties**
- **Tenant scope is set from the DB user row**, never from client input or a token claim — a stolen/edited token still can't select another org (guard uses server-derived org).
- **Audit + enqueued side effects commit in the same transaction** as the domain change → either everything happened or nothing did. A webhook is never emitted for a change that rolled back.
- **Middleware resets context** on the way out → no leakage between requests on a reused worker.

---

## 2. Asynchronous event & job flow

Side effects (webhook delivery, recurring generation, reminders) are **decoupled** from the request via the durable queue. The request only *records intent*; the worker *executes*.

```mermaid
sequenceDiagram
  autonumber
  participant S as Service (request)
  participant DB as jobs table
  participant W as Worker
  participant H as Handler (tenant-scoped)
  participant X as External (HTTP/SMTP)

  S->>DB: emit event → WebhookDelivery(pending) + enqueue job(webhook.deliver)
  Note over S,DB: committed with the domain change
  loop worker loop
    W->>DB: reclaim stale leases
    W->>DB: claim next ready job (atomic guarded UPDATE)
    W->>H: set_current_org(job.org_id); dispatch(payload)
    H->>X: signed POST / SMTP send
    alt 2xx
      H->>DB: mark delivered; job → succeeded
    else non-2xx / error
      H->>DB: mark failed; raise
      W->>DB: attempts<max → requeue with backoff<br/>attempts=max → dead-letter
    end
    W->>DB: reset tenant scope
  end
```

**Delivery guarantees:** *at-least-once* execution with *idempotent handlers* → effectively exactly-once outcomes. The claim is an atomic guarded `UPDATE ... WHERE status='queued'`, so two workers never run the same job. A crashed worker's lease is reclaimed. Retries use exponential backoff; exhausted jobs land in a dead-letter state with the error preserved.

---

## 3. Invoice extraction pipeline (deterministic-first)

The single most important accuracy/cost/residency decision. AI is the **last** resort, opt-in, and never authoritative. (ADR-0009)

```mermaid
flowchart TD
  U[Upload / email / API] --> SEC{Security gate<br/>scan + type validate}
  SEC -- reject --> ERR[415/refused]
  SEC -- ok --> VAULT[Store original in object storage<br/>+ sha256 dedup]
  VAULT --> PROBE{Embedded/structured XML?<br/>UBL / CII / Factur-X}
  PROBE -- yes --> XML[parse_einvoice → high-confidence draft<br/>NO AI]
  PROBE -- no --> TXT{PDF text layer present?}
  TXT -- yes --> PARSER[Registered parse_<supplier>() / text parse]
  TXT -- no --> OCR[OCR fallback]
  PARSER --> DRAFT[Draft]
  OCR --> DRAFT
  XML --> DRAFT
  DRAFT --> AIQ{AI capture enabled?<br/>(opt-in, DLP-gated)}
  AIQ -- no --> REVIEW[Human review queue]
  AIQ -- yes --> AI[Vision/verify model<br/>advisory corrections]
  AI --> REVIEW
  REVIEW --> CONFIRM[Human confirm]
  CONFIRM --> FXVAT[FX→EUR w/ provenance · VAT by scheme]
  FXVAT --> REC[(Saved invoice record)]
  REC --> METER[usage +1] & AUDIT[audit event] & EVT[webhook: invoice.created]
```

**Rules**
- The **original bytes are what gets vaulted** (esp. hybrid Factur-X PDFs), hashed for dedup + integrity.
- **Structured formats never fall to AI.** AI belongs to post-extraction *assistance*, not capture of a figure a deterministic path can read.
- **Email/bulk** parse/OCR runs on the **worker tier** (CPU isolation): the webhook stores bytes + enqueues `email.extract`, the worker parses out-of-band. Interactive single-file upload parses synchronously off the event loop by design (see ADR-0009).
- **A human confirms** every draft before it becomes a booked record.
- **The review queue has a UI** (WO-12 / E1.1): `/captures` lists parsed-but-unconfirmed runs; `/captures/{run_id}` shows the source document side by side with per-field provenance (status `extracted|defaulted|missing`, confidence — `null` = exact from a structured source, `< 0.75` flagged low — original vs normalized vs reviewed values, provider) plus advisory duplicate warnings, and owns the confirm step. Two read-only endpoints back it: `GET /invoices/captures/{run_id}/fields` (the LIVE field rows incl. `reviewed_value`, so a reloaded screen still shows corrections) and `GET /invoices/captures/{run_id}/source` (the original bytes served inert — nosniff + content-disposition, mime from the document registry). Human corrections go through `POST /invoices/captures/{run_id}/review`, which records a `capture.field_review` audit event with old→new per field — the machine's capture is kept next to the correction, never rewritten.
- **Line items carry the same provenance** (WO-13 / E1.2): `extraction_fields.line_index` scopes a row to `line_items[n]` (NULL = header), six fields per line (`description`/`category`/`quantity`/`unit_price`/`amount`/`tax_rate`) from every provider with identical confidence semantics — `null` = exact, `0.85` text-layer, `0.55` OCR. CSV/JSON report per-cell presence honestly (`extracted` vs `defaulted`, incl. a computed amount); draft-derived providers mark structural fills (`category`, zero `tax_rate`) `defaulted`. The **line flag rule deliberately differs**: a `defaulted` line cell does not flag `low_confidence` (structural fills occur on nearly every line and would drown the queue's "needs a look" count) — only a sub-threshold score or a missing value flags; the status badge still shows. A correction targets `(field, line_index)` and is audited under the meta key `line_items[i].field`. The review screen's line table is editable per cell with the low-confidence emphasis; totals stay server-recomputed on confirm.

---

## 4. Idempotency (how we prevent duplicate financial effects)

| Surface | Mechanism | Effect |
|---|---|---|
| API ingest / retried uploads | SHA-256 content dedup in the vault | Same file → same document, not two. |
| Job enqueue | Optional **idempotency key** unique on `(org_id, kind, key)` | Re-enqueue while a job is live = no-op. |
| Job execution | **Handlers are idempotent**; queue is at-least-once | Re-run never double-acts. |
| Recurring generation | `next_run_date` advanced **in the same txn** as invoice creation | A re-run never re-emits a passed occurrence. |
| Daily scheduler | Jobs keyed by **date** (`kind:YYYY-MM-DD`) | Enqueuing 100×/day = one job/day. |
| Webhook delivery | Delivery row + `X-InvoiceIQ-Delivery` id; receiver dedups on id | Receiver can safely ignore repeats. |
| Payments | Guarded update; amount capped at amount owed | No over-credit / over-pay. |
| Invoice numbering | Sequence incremented in the **same txn** as the row | Gap-free per entity; no duplicate numbers under concurrency. |

**Design stance:** we assume *at-least-once* everywhere and make the *outcome* idempotent. We never rely on "it only runs once."

---

## 5. Concurrency controls

| Scenario | Control |
|---|---|
| Two workers claim one job | Atomic guarded `UPDATE ... WHERE status='queued'`; loser's rowcount=0 → tries next. |
| Two requests number an invoice for one entity | Numbering + row insert in one transaction; DB uniqueness on `(entity, number)` as backstop. |
| Concurrent usage-counter increment | Upsert-with-retry on the `(org,period,metric)` unique constraint. |
| Concurrent audit events in one txn | `flush()` after add so the next `seq` sees the prior event; unique `(org_id, seq)`. |
| Leader-only periodic work (backup/scheduler) | Leader election (Postgres advisory lock) — one worker acts. |
| Bulk mutations | Per-item transactions where possible; a failure drops one item, not the batch. |
| Long analytics reads vs. writes | Reads on replicas (target); writers on primary; no read locks held. |

We prefer **optimistic, DB-enforced** concurrency (unique constraints + guarded updates) over application-level locks. Advisory locks are used only for singleton periodic work.

---

## 6. Failure recovery

| Failure | Detection | Recovery |
|---|---|---|
| Worker crash mid-job | Stale lease (`locked_at` past cutoff) | `reclaim_stale` returns the job to `queued`; another worker runs it (idempotent). |
| Handler throws | Exception in `run_once` | Rollback, reload job, backoff-requeue; after max attempts → dead-letter with error. |
| External endpoint down (webhook/SMTP) | non-2xx / timeout | Retry with backoff; dead-letter after budget; delivery row records last attempt; manual retry available. |
| Poison job (never succeeds) | Reaches max attempts | Dead-letter; alert on DLQ depth; operator inspects + fixes + retries. |
| DB transaction failure | Exception | Whole unit (domain + audit + enqueue) rolls back together; nothing partially applied. |
| Partial file/store corruption | Integrity re-hash vs. stored sha256 | Mismatch forces full re-hash; failure raised to error log + banner; restore from backup. |
| Migration failure | Alembic error | Migrations run before serve; a failed migration blocks rollout (fail-closed), not a half-migrated prod. |
| Region/infra outage | Health/readiness probes fail | LB stops routing; restore from verified backups (see [deployment.md](./deployment.md)); DR runbook. |

**Principle:** *fail closed on writes, degrade gracefully on reads.* A capture that can't complete is **queued, not dropped**. A limit that's hit **warns, never deletes**.

---

## 7. Read path (analytics)

```mermaid
flowchart LR
  C[Client] --> API[Analytics router]
  API --> SVC[analytics service]
  SVC -->|DB-side aggregation<br/>single currency| PG[(Postgres primary / replica)]
  PG --> SVC --> API --> C
  Note1[Materialised close-time metrics<br/>rebuilt by the engine at period close] -.-> PG
```

- Aggregation happens **in the database**, not Python, so it stays fast as volume grows.
- Reports are **single-currency**; mixed-currency totals are forbidden by construction.
- Heavy period aggregates are **materialised at close** and read back; an un-rebuilt period falls back to a live query.
- Analytics reads target **replicas** (as they're introduced) so they never contend with the write path.

---

## 8. Enterprise & compliance flows (SSO · billing · lifecycle)

These surfaces reuse the same request/job spine above; the additions are **an
external authority** (an IdP, a billing provider) and a **fail-closed rule** at
the point of trust.

**SSO login (OIDC) — the IdP is the authority; we never trust the redirect.**
```mermaid
sequenceDiagram
  autonumber
  participant C as Browser
  participant API as API (unauthenticated route)
  participant IDP as Tenant IdP
  C->>API: GET /auth/sso/{slug}/authorize
  API->>API: PKCE + nonce + signed stateless `state`
  API-->>C: 302 → IdP (authorize URL)
  C->>IDP: authenticate (MFA at the IdP)
  IDP-->>C: 302 → /auth/sso/callback?code&state
  C->>API: callback
  API->>IDP: exchange code (server-to-server) + fetch JWKS
  API->>API: validate ID token (RS256, iss/aud/exp, nonce)<br/>JIT match/create in-org; group→role map
  API-->>C: 302 → SPA with our internal JWT (fragment)
```
*Machine principals* (SCIM, the Stripe webhook) authenticate as a **token/signature, not a user** — they set tenant scope explicitly and never pass through `get_current_user`. SCIM deactivation is a **soft** delete (row kept). ID-token/assertion validation **fails closed**; SAML assertion consumption returns **501** until a vetted library + real IdP land.

**Billing — the provider event is the authority, applied idempotently.**
- Stripe: Checkout → **signed webhook** → `apply_subscription_event` (plan/status), deduped by `processed_stripe_events`.
- EveryPay: hosted page → **server-side verify** (never the browser redirect) via `billing_payments` → same applier; recurring is a merchant-initiated (MIT) **queue job**.
- Metered usage: a daily job reports `count − reported` deltas (watermark → no double-count).

**Data-lifecycle (retention purge / GDPR erasure) — job-driven, gated, audited.**
Both run through the standard job/handler path (§2) under tenant scope, are **blocked by an active legal hold**, **exclude** `audit_events` + `issued_invoices`, delete associated object bytes, and write an audit event (erasure logs a *hashed* subject id — never the cleartext email).

---

## 9. Project lifecycle flow (offer → contract → invoicing → close)

```mermaid
sequenceDiagram
  participant U as User
  participant P as Project page
  participant S as Services
  participant DB
  U->>P: create offer (draft) → send → accepted
  P->>S: project_offers.transition
  S->>DB: status + estimated_revenue (same txn, audited)
  U->>P: define invoicing plan rows
  U->>P: issue invoices with project_id · allocate supplier invoices · add cost entries
  P->>S: project_profit.pnl (live)
  S-->>P: revenue / costs / profit + basis: net_eur_live
  U->>P: generate contract from a template
  S->>S: doc_templates.render (unknown tokens stay visible) → PDF
  S->>DB: project_documents row (audited)
  U->>P: close project
  S->>DB: snapshot P&L + status=closed (SAME transaction)
  Note over S,DB: after close: late docs → labelled adjustments,<br/>frozen figure untouched; reopen discards snapshot (audited)
```

Invariants on this path: the invoicing plan and the P&L share ONE revenue
computation (no forked math); the close snapshot commits atomically with the
status change; allocation splits are cent-exact with a deterministic residue
rule (largest percent); template rendering never silently drops an unknown
token; a platform edit to a master template never reaches a workspace's saved
copy.

---

## 9b. Automation sweep (trigger → condition → action, WO-J)

Admin-authored rules run on the same durable rails as everything else — no new
infrastructure, no new mutation paths: every action a rule takes is an existing
service call, so audit, permissions and idempotency come for free.

```mermaid
sequenceDiagram
  autonumber
  participant SCH as Scheduler (daily sweep)
  participant Q as jobs queue
  participant ENG as automation engine
  participant SVC as existing services<br/>(mailer · crm)
  SCH->>Q: enqueue `automation.sweep` (per org)
  Q->>ENG: run handler (tenant scope)
  ENG->>ENG: load PUBLISHED rule versions only
  loop each rule × matching record
    ENG->>ENG: trigger match? conditions hold?<br/>fire policy (once-per-record / cooldown / every sweep)
    alt would exceed 25 fires this sweep
      ENG->>ENG: record run as THROTTLED (visible in run log)
    else fires
      ENG->>SVC: ordered actions (email self / email customer / CRM note)<br/>{{field}} tokens rendered from the record
      ENG->>ENG: automation_runs row — what fired, on what, result
    end
  end
```

Guarantees: a rule executes only as a published, immutable version (revert =
re-publish an old version as a new one); dry-run evaluates without side
effects; the run log is the complete history; the recycle-bin purge and the
onboarding checklist follow the same pattern — daily jobs and derived reads
over existing rows, never a parallel write path.

---

## 10. Data-flow invariants (must hold on every path)

1. Tenant scope is set from the server-side user row before any query, and reset after the request.
2. Domain change + its audit event + its enqueued side effects **commit atomically or not at all**.
3. Every deferred effect is **idempotent** and **at-least-once**.
4. Money is converted with **provenance**; no mixed-currency aggregation.
5. Original document bytes are **vaulted + hashed** before any lossy processing.
6. A human confirms every extracted figure before it is booked.
