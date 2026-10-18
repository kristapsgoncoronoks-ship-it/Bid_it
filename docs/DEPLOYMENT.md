# InvoiceIQ — Production Deployment Guide

Everything needed to take InvoiceIQ from a git tag to a running, observable,
zero-downtime production service. Two supported targets:

- **Docker Compose** — single VM / small footprint (Cloudflare → nginx origin → API).
- **Kubernetes** — horizontal scale, rolling deploys, autoscaling (`deploy/k8s/`).

For a **single VPS** end-to-end walkthrough (e.g. Hostinger KVM) using the
self-contained `docker-compose.hostinger.yml` — local document storage, no
separate object store, TLS via Cloudflare or Let's Encrypt — see
[`DEPLOY-HOSTINGER.md`](DEPLOY-HOSTINGER.md).

---

## 1. Infrastructure architecture

```
                    ┌─────────────┐
   users ───TLS───▶ │ Cloudflare  │  (WAF, DDoS, CDN, TLS edge)
                    └──────┬──────┘
                     TLS (Full Strict)
                    ┌──────▼───────────────┐
                    │  Ingress / nginx      │  terminates origin TLS,
                    │  (SPA + /api reverse   │  serves the static SPA,
                    │   proxy)               │  proxies /api → backend
                    └───────┬───────┬───────┘
             static assets  │       │ /api/*
                    ┌───────▼──┐  ┌─▼──────────────┐
                    │ frontend │  │ backend (N ×)  │  FastAPI + uvicorn workers
                    │ (nginx)  │  │ stateless      │  autoscaled on CPU
                    └──────────┘  └─┬────────┬─────┘
                                    │        │
                            ┌───────▼──┐  ┌──▼───────────┐
                            │ Postgres │  │ (optional)   │
                            │ primary  │  │ ClamAV, SMTP │
                            │ +backups │  │ relay, S3    │
                            └──────────┘  └──────────────┘
```

**Design properties**

- **Stateless backend.** Document bytes (uploaded originals, receipts, logos,
  audit-snapshot PDFs) live in **object storage**, not the database (ADR-0008) —
  the `s3` backend (AWS S3 or any S3-compatible service such as MinIO). With that
  backend a replica holds no local disk state, so any replica serves any request
  and scaling out is trivial; the stateful components are Postgres and the object
  store. (The `local` filesystem backend also exists for single-node dev — there
  the storage directory *is* state and must be a persisted volume; it does not
  scale across replicas.)
- **Tenant isolation** is enforced at the ORM layer for every request (see
  `app/core/tenant.py`) — independent of how many replicas run.
- **Schema is owned by Alembic.** `create_all` is disabled in production. On
  Kubernetes every release runs `alembic upgrade head` exactly once as a Job
  (`20-migrate-job.yaml`), never inside the serving pods. The compose stacks
  migrate inside the API container on boot (`alembic upgrade head && exec
  uvicorn`) unless the one-shot overlay `docker-compose.hostinger.migrate.yml`
  is applied (§3) — OPS-007.

---

## 2. Configuration (12-factor)

Everything deployment-specific comes from the environment (`app/core/config.py`).
The must-set production variables:

| Variable | Required | Notes |
|---|---|---|
| `JWT_SIGNING_KEY` / `JWT_SIGNING_KEY_FALLBACKS` | | SEC-JWT-001: sign access tokens and OIDC state with a key that is NOT the application secret, so a signature rotation never touches the local KEK. Unset = `SECRET_KEY` (historical behaviour). Rotation: set the new key, keep the previous one in `JWT_SIGNING_KEY_FALLBACKS='["previous"]'` (JSON list, max 3, verify-only) for at least 24 h (the access-token lifetime), then remove it. A key believed compromised goes nowhere near the fallbacks: drop it and revoke sessions. **First enablement is itself a rotation:** tokens minted before were signed with `SECRET_KEY`, which leaves the verification list the moment `JWT_SIGNING_KEY` is set — deploy with `JWT_SIGNING_KEY_FALLBACKS='["<current SECRET_KEY>"]'` for 24 h, otherwise every existing token and in-flight SSO login is invalidated at the deploy. Production refuses the dev default, duplicates, and an active key listed as a fallback. A forged token — however signed — still needs a live session row **of that user** (SEC-012), so a leaked fallback key is bounded by the sessions the attacker already holds. |
| `SECRET_KEY` | ✅ | Application secret (and JWT signing key when `JWT_SIGNING_KEY` is unset) — `openssl rand -hex 32`. Rotating it logs everyone out **and, with the default `KEK_PROVIDER=local`, makes every stored encrypted secret (tenant SSO client secrets) permanently unreadable** — the KEK is derived from it. Back `.env` up beside the database; set `KEK_PROVIDER=env` + `KEK_KEY` in production so the two can rotate independently. |
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://user:pass@host:5432/invoiceiq` |
| `ENVIRONMENT` | ✅ | `production` (disables `create_all`, enables JSON logs) |
| `CORS_ORIGINS` | ✅ | Public SPA origin(s), comma-separated |
| `HSTS_ENABLED` | ✅ | `true` once TLS is live |
| `STORAGE_BACKEND` | ✅ | `s3` for prod (AWS S3 / MinIO); `local` only for a single node with a persisted volume. |
| `STORAGE_S3_BUCKET` | s3 | Bucket for document bytes (default `invoiceiq-documents`). |
| `STORAGE_S3_ENDPOINT_URL` | s3 | Endpoint for S3-compatible stores (MinIO); omit for AWS S3. |
| `STORAGE_S3_REGION` / `STORAGE_S3_PREFIX` | | Region; optional key prefix within the bucket. |
| `STORAGE_LOCAL_SHARED` | | `true` acknowledges that EVERY backend and worker replica mounts one shared volume when `STORAGE_BACKEND=local` in production (the single-VPS stack sets it); otherwise the process logs a startup warning — never a refusal (ARCH-002). Never set it on a multi-host deployment. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | s3 | Object-store credentials (boto3). Store as secrets. |
| `WEB_CONCURRENCY` | | uvicorn workers per pod (default 4) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | | per-worker pool (default 10/10) — see §7 |
| `INBOUND_EMAIL_SECRET` | ✅ | shared secret for the `/email/inbound` webhook. **Mandatory** — production refuses to boot without it, and the endpoint rejects every request (401) unless the provider presents it. Generate: `python -c "import secrets;print(secrets.token_urlsafe(32))"`. **Deploy ordering:** set the env var in the environment *before* rolling out a release that requires it, or the new pods will refuse to boot. |
| `SMTP_HOST` / `SMTP_*` | | outbound email relay (else sends are recorded-only) |
| `CLAMAV_ENABLED` / `CLAMAV_HOST` | | malware scanning of uploads (fails closed) |
| `WORKER_LIVENESS_PATH` / `WORKER_LIVENESS_MAX_AGE_SECONDS` | | worker liveness file (`/tmp/invoiceiq-worker-liveness`, must be writable — the k8s `/tmp` emptyDir) and the age past which `python -m app.worker_probe` fails (180; keep it above 2 × the 60 s lease heartbeat and below the probe's `periodSeconds × failureThreshold`). Process environment only — the probe does not read `.env`. |
| `OCR_PROCESS_TIMEOUT_SECONDS` | | per-page/per-image budget for one native Tesseract call (default 120, max 300). A page past it is the capture outcome `processing_timeout`, not a hung OCR worker. Long multi-page captures stay valid: the job lease is renewed every 60 s while the worker owns it. |
| *(webhook egress)* | | Outbound webhook POSTs use a pinned transport (resolve → vet every answer → connect to that IP with the original Host/SNI; no redirects). It does **not** honour `HTTPS_PROXY`/`HTTP_PROXY`: a deployment that must egress through a proxy will see deliveries fail as connection errors (retried, then dead-lettered) — route the worker's egress at the network layer instead. |

`SECRET_KEY` and `DATABASE_URL` are **secrets** — never bake them into an image
or commit them. Use k8s Secrets / a secrets manager. `backend/.env.example`
lists everything.

**CI-only secret — `PII_SCAN_SALT`:** the salt for the hashed Fleet Fuel PII
deny-list (`scripts/pii_denylist.json`), consumed by the required `pii-scan`
CI job and the nightly history scan. Not a runtime variable — the app never
reads it. Generate once (`python -c "import secrets;print(secrets.token_hex(32))"`),
store it as a GitHub Actions secret and in the password manager; changing it
invalidates every committed hash. Details: `docs/transport/harvest-protocol.md`.

---

## 3. Deployment workflow

```
 git tag v1.2.0 ─▶ push tag ─▶ Release workflow builds+pushes images to GHCR
 (also: push to main ─▶ same workflow ─▶ `main` + `sha-<7>` tags — a BUILD, before CI's verdict)
                                        │
                                        ▼
                        (1) run DB migration Job  →  wait for complete
                                        │
                                        ▼
                        (2) roll out backend + frontend to the new tag
                                        │
                                        ▼
                        (3) readiness probes gate traffic; old pods drain
```

Migrations run **before** the new code, and the rollout is `maxUnavailable: 0`
so there is always a healthy replica serving traffic (zero downtime). A bad
release is rolled back by pointing the Deployment back at the previous image tag
(`kubectl rollout undo`).

### Docker Compose (single VM)

```bash
# one-time: strong secret + Cloudflare Origin cert on the host
export SECRET_KEY=$(openssl rand -hex 32)
export APP_ORIGIN=https://app.example.com
export TLS_CERT_DIR=/etc/invoiceiq/certs        # holds origin.pem + origin.key

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# The backend container runs `alembic upgrade head` then serves; nginx fronts TLS.
```

Two opt-in overlays for the single-VPS stack (`docker-compose.hostinger.yml`),
both owner cutovers described in `docs/DEPLOY-HOSTINGER.md`:

- **`docker-compose.images.yml`** (OPS-003) — run the CI-built images from GHCR
  instead of building on the box: `build:` removed, `image:` set to
  `ghcr.io/<owner>/<repo>/{backend,frontend}:${IMAGE_TAG}`; a rollback is
  `IMAGE_TAG=sha-<previous> … pull && up -d --no-build`.
- **`docker-compose.hostinger.migrate.yml`** (OPS-007) — migrations as a
  one-shot `migrate` service the API waits for (`service_completed_successfully`)
  instead of `alembic upgrade head &&` inside the restarting API container,
  where a failing migration crash-loops against a half-applied schema. Needs
  Compose v2.20+.

**Object storage (do not ship the defaults).** The compose stack stores document
bytes via the `s3` backend against a bundled **MinIO** container whose base-compose
credentials (`invoiceiq` / `invoiceiq-secret`) and published console (ports
`9000`/`9001`) are **dev defaults**. Before going live, do ONE of:

- **Managed S3 (recommended):** point `STORAGE_S3_ENDPOINT_URL` (omit for AWS),
  `STORAGE_S3_BUCKET`, `STORAGE_S3_REGION`, and `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` at your object store, and remove the `minio` /
  `minio-init` services (add a prod-override that drops them).
- **Self-hosted MinIO:** change `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` (and the
  matching `AWS_*` on backend + worker) to strong secrets, stop publishing
  `9000`/`9001` to the host, and back the `miniodata` volume up.

Either way the bytes are the legal record — `POST /api/v1/integrity/documents/verify`
re-hashes every stored object (receipts, logos, email attachments, **and the
original supplier-invoice uploads**) against the DB to prove integrity after a
storage change or restore.

### Kubernetes

```bash
kubectl apply -f deploy/k8s/00-namespace.yaml
# Every key the manifests and the production validator need (ARCH-002 /
# OPS-011): a Secret with only the first two boots a pod that refuses to start.
kubectl -n invoiceiq create secret generic invoiceiq-secrets \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://user:pass@pg:5432/invoiceiq" \
  --from-literal=INBOUND_EMAIL_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  --from-literal=AWS_ACCESS_KEY_ID="…" --from-literal=AWS_SECRET_ACCESS_KEY="…" \
  --from-literal=DB_USER=invoiceiq --from-literal=DB_PASSWORD="…"
kubectl apply -f deploy/k8s/10-config.yaml     # ConfigMap (edit CORS_ORIGINS, STORAGE_S3_*)
# Point every image at your registry and the release you are deploying — one
# placeholder convention across all manifests (a test holds it):
sed -i 's#ghcr.io/OWNER/REPO#ghcr.io/<owner>/<repo>#; s#:VERSION#:1.2.0#' deploy/k8s/*.yaml

# every release: migrate first, then roll out (set image tag in the manifests)
kubectl apply -f deploy/k8s/20-migrate-job.yaml
kubectl -n invoiceiq wait --for=condition=complete job/invoiceiq-migrate --timeout=180s
kubectl apply -f deploy/k8s/30-backend.yaml -f deploy/k8s/40-frontend.yaml -f deploy/k8s/50-ingress.yaml
```

### Runbook note — payment-run re-exports and the WO-9 migration

- **What a re-export means to the bank.** Every SEPA export now carries a
  `MsgId` unique to that *generation* (`RUN-<id8>-<n>-<random>`), so a
  re-export is a **new payment instruction**: if the first file was already
  submitted, uploading the second one instructs the bank to pay **again** —
  banks will not deduplicate it, because the message id differs by design (the
  old behaviour, a duplicate id, is what banks reject or silently drop). The
  UI and API therefore require an explicit `confirm_reexport=true`, and every
  export is audited (`payment_run.exported`) with its MsgId — use those audit
  rows (or `payment_runs.last_msg_id`) to answer a bank query about a file.
- **First deploy of migration `d4e6f8a0b2c4`.** Existing runs/batches are
  backfilled `export_count = 0` (treated as never exported), so a file that was
  in fact already sent to the bank before the deploy can be re-exported ONCE
  without the confirmation prompt — warn treasurers on the deploy day. Existing
  OPEN runs must now be approved by a second user before they can be paid.
- **Never downgrade `d4e6f8a0b2c4` in production** without first extracting
  `payment_runs.last_msg_id` / `reimbursement_batches.last_msg_id` and the
  `payment_run.exported` audit rows to a CSV — the downgrade drops the columns
  and the MsgId values may be needed to trace a payment with the bank.

---

## 4. CI/CD

- **`.github/workflows/ci.yml`** (every push/PR): backend tests + a
  migration-consistency check (`alembic heads` == 1, `upgrade head`,
  `alembic check` for un-migrated model drift), frontend typecheck+build, and a
  **docker-build** job that builds both images (catches Dockerfile regressions).
  Cached pip/npm; in-progress runs cancel on a new push.
- **Required PR checks (branch protection).** The workflow triggers on
  `pull_request` with **no job-level filters**, so every job below runs on every
  PR — but GitHub only *blocks* a merge on jobs listed as required checks.
  Enable branch protection on `main` in GitHub → Settings → Branches with these
  required status checks (this list is the contract; it cannot be asserted from
  inside the repo):
  - `lint` — ruff + `mypy app` (whole app)
  - `backend` — full pytest suite + migration consistency on SQLite. This job
    is what makes the structural gates blocking: **route-authorization coverage
    in both directions** (`tests/test_authz_coverage.py`, ADR-0024) and
    **behavioural tenancy parity over the real query path**
    (`tests/test_tenancy_parity.py`, WO-10) run here — a route without a
    permission classification, a tenant table without a probe/reasoned
    exemption, or a cross-tenant leak fails this required check.
  - `postgres` — **the tenancy gate**: migrations on real Postgres + RLS
    enforcement (`tests/test_rls.py`) + concurrent numbering, run as a
    **`NOSUPERUSER`** app role (a superuser would bypass RLS and prove nothing)
  - `frontend` — typecheck + build
  - `docker-build` — both images build
  - `pii-scan` — the Fleet Fuel PII quarantine (`scripts/pii_scan.py`, WO-6):
    structural EU-VAT/IBAN patterns + the salted deny-list. Requires the
    `PII_SCAN_SALT` repository secret; the deny-list is populated from the
    owner-held archive (see `docs/transport/harvest-protocol.md`).
- **`.github/workflows/release.yml`** (on a `v*.*.*` tag, and — OPS-003, audit
  2026-09-05 — on every push to `main`): builds and pushes images to GHCR —
  `1.2.0` / `1.2` for a tag, `main` and `sha-<short>` for a main push — so
  every commit the auto-deploy lands has an immutable image a rollback can
  pull. Deployment stays a deliberate, separate step — CI/CD produces
  artifacts; a human (or ArgoCD/Flux) promotes them. Consuming them on the VPS
  is the opt-in overlay `docker-compose.images.yml` (docs/DEPLOY-HOSTINGER.md).
- **The `deploy` job runs in the `production` GitHub Environment** (OPS-013):
  deploy history under Deployments; protection rules — required reviewers, a
  wait timer, branch restriction — are set by the owner in Settings →
  Environments and need no workflow change.

---

## 5. Monitoring & logging

- **Structured logs.** In production every request emits one JSON line
  (`method`, `path`, `status`, `duration_ms`, `request_id`, `client`) — ship to
  Loki / CloudWatch / Datadog. Each response carries `X-Request-ID` (propagated
  from the edge if present) and `X-Response-Time-Ms`, so a user-reported error
  maps to exact log lines. Uncaught 500s are logged with their request id.
  Worker log lines emitted inside a job carry `job_id` and `job_kind` (absent,
  not null, outside a job; `request_id` is `"-"` on the worker), so "OCR failed
  on page 3" is traceable to the job row (reference integration PAT-030).
- **Metrics.** `/metrics` exposes Prometheus counters + latency histograms
  (`http_requests_total`, `http_request_duration_seconds`) labelled by route,
  and the queue gauges `invoiceiq_jobs{status}`, `invoiceiq_jobs_dead{kind}`
  (a recovered kind is re-published as 0) and
  `invoiceiq_jobs_oldest_pending_seconds`. Scrape it; alert on error-rate and
  p95 latency. (Enabled when `prometheus-client` is installed — it ships in the
  image.) The queue gauges are per PROCESS: each uvicorn worker publishes what
  its own last `/health/queue` call saw, and the worker's refresh warms only
  the worker's registry, which nothing scrapes (OPS-014) — route queue alerts
  off `/health/queue`'s body, which is computed on every call.
- **Health.** `/health` = liveness (process up, no I/O); `/health/ready` =
  readiness (DB reachable → 200, else 503 so the LB drains the pod).
  **`/health/queue` is a REQUIRED uptime check** since Stripe webhook events
  are applied by the worker, not the API (reference R2): it answers 503 when
  the oldest ready job is older than 15 min or a dead-letter exists
  (`QUEUE_DLQ_ALERT_THRESHOLD`), which is how a dead worker or a dead-lettered
  `billing.apply_subscription_event` (Stripe already considers it delivered)
  becomes visible — an absent or under-provisioned fleet; a dead or wedged
  worker PROCESS is the liveness probe's (below). Point the same monitor that
  watches `/health/ready` at it.
  Its body carries `dead_by_kind` (`{"billing.apply_subscription_event": 1}`)
  and `/metrics` the matching gauge `invoiceiq_jobs_dead{kind}` — route the
  page by kind: a dead billing apply is a customer off their plan, a dead
  `webhook.deliver` is a receiver that is down. Kinds only, never tenant data.
- **Worker liveness** is separate from the queue SLO and deliberately so. The
  worker touches `WORKER_LIVENESS_PATH` (`/tmp/invoiceiq-worker-liveness`)
  every loop tick and every lease heartbeat inside a long job;
  `python -m app.worker_probe` (import-light, no database) exits non-zero once
  the file is older than `WORKER_LIVENESS_MAX_AGE_SECONDS` (180). It is the
  worker's compose `healthcheck` and its Kubernetes `livenessProbe`. It fails
  for a dead process, a blocked event loop, or a hang in the loop body outside
  a job (reclaim, scheduler, snapshot, claim — a database that accepts
  connections and never answers included; that restart churns until it does).
  It does NOT fail for a handler that awaits forever (the lease heartbeat keeps
  ticking for it; there is no per-job deadline — BE-024), and NOT for a queue
  backlog — that is `/health/queue`'s page, and restarting every replica on a
  backlog would only lose the jobs in flight. On compose an `unhealthy` worker
  is visible in `docker compose ps` (Docker does not restart on it;
  `restart: unless-stopped` covers a crash) — Kubernetes restarts it. The path
  and limit must be in the process ENVIRONMENT (compose/k8s inject them): the
  probe does not read `.env`, the application does.
  Support symptom for the same fault: "I paid but the page still shows the old
  plan" — the Plan & billing page polls for 90 s after Checkout and then tells
  the owner not to subscribe again and to refresh later; a refresh starts a
  new wait. Check `/health/queue` and the `billing.apply_subscription_event`
  job for that workspace before anything else.
- **Read the nightly PII history scan** (`pii-history.yml`, 02:23 UTC, Actions
  tab). It is the Fleet Fuel quarantine's authoritative sweep and it is RED
  until the owner decides DECISIONS §24 (SEC-013: it failed unread on 36 of its
  first 37 nights). A red night with the SAME masked hits as §24 is the known
  state; a NEW hit is an incident — the harvest protocol applies.
- **`BILLING_SUCCESS_URL` must keep its `?checkout=success` query** (default
  `https://<app>/billing?checkout=success`): the Plan & billing page keys its
  post-Checkout "activating…" state — the poll, the disabled plan buttons, the
  refusal to subscribe twice — on that literal query. A success URL without
  it silently loses the feature (reference R4).
- **Suggested alerts:** readiness failing > 1 min; 5xx rate > 1%; p95 latency
  > 1s; Postgres connections > 80% of `max_connections` — size for **two**
  connections per worker process (the job's session plus the lease heartbeat's
  short-lived renewal session, STIR-P1-01); disk/backups. A per-document OCR
  timeout answers 503 on the three OCR routes and counts toward the 5xx rate by
  design — it is the operator's signal to look at the document, not the server.
- **Errors.** Wire Sentry/GlideError by setting its DSN and adding the SDK — the
  request-id in logs already gives you correlation.

---

## 6. Reliability / reducing downtime risk

- **Rolling updates, `maxUnavailable: 0`** + readiness gating = zero-downtime deploys.
- **Probes:** startup (slow first boot) → liveness (restart wedged pods) →
  readiness (drain on DB loss). `preStop` sleep + `terminationGracePeriodSeconds`
  let in-flight requests finish (uvicorn drains on SIGTERM; tini forwards it).
  The **worker** finishes its in-flight job on SIGTERM (its lease stays
  renewed while it does), so its grace period should be at least the OCR budget
  (`OCR_PROCESS_TIMEOUT_SECONDS`, 120 s) per page it may still be reading — or
  accept that a killed job is reclaimed 300 s later and re-run idempotently.
  Its liveness probe is the heartbeat file (§5): `periodSeconds` 60 ×
  `failureThreshold` 5 exceeds the 180 s staleness limit, so a pod that is one
  slow tick late is not restarted; `/tmp` is the emptyDir the read-only root
  filesystem leaves writable, which is where the heartbeat lives.
- **PodDisruptionBudget** keeps ≥2 backends during node drains.
- **DB resilience:** `pool_pre_ping` discards dead connections, `pool_recycle`
  avoids stale sockets. Run Postgres HA (managed RDS/Cloud SQL or Patroni) with
  automated backups + PITR; test restores.
- **Migrations are decoupled** from pod start, so a slow/failed migration never
  crash-loops the fleet.

---

## 7. Scaling

- **Backend:** horizontal. HPA scales 3→12 on 70% CPU; raise `maxReplicas` as
  needed. Because the app is stateless, replicas scale linearly.
- **Connection math (important):** total Postgres connections =
  `replicas × WEB_CONCURRENCY × (DB_POOL_SIZE + DB_MAX_OVERFLOW)`. Keep it under
  Postgres `max_connections`. At scale, put **PgBouncer** (transaction pooling)
  in front and give the app a small pool. Example: 12 replicas × 4 workers × 20
  = 960 — too many for a default Postgres; PgBouncer collapses that to a few
  dozen server connections.
- **Frontend:** static; scale trivially or move to a CDN/object store.
- **Heavy work** (OCR, PDF render) is CPU-bound and already offloaded to a
  threadpool; if it dominates, split it to a dedicated worker deployment.

---

## 8. Production readiness checklist

**Secrets & config**
- [ ] `SECRET_KEY` is a fresh 32-byte random value (not the dev default), stored as a secret.
- [ ] `DATABASE_URL` points at managed Postgres with TLS; credentials in a secret.
- [ ] `ENVIRONMENT=production`, `HSTS_ENABLED=true`, `CORS_ORIGINS` = exact public origin(s).
- [ ] No `.env`, `*.db`, or secrets in the image (`.dockerignore` verified).

**Data**
- [ ] `alembic upgrade head` runs as a pre-deploy Job; `alembic check` is green in CI.
- [ ] Postgres automated backups + PITR enabled; a restore has been tested.
- [ ] Postgres `max_connections` ≥ the connection math in §7 (or PgBouncer in place).

**Network & TLS**
- [ ] TLS end-to-end (Cloudflare Full Strict / cert-manager); HTTP→HTTPS redirect.
- [ ] Upload size cap aligned (nginx `client_max_body_size` ≥ app `max_upload_mb`).
- [ ] Real client IP restored at the edge (Cloudflare `CF-Connecting-IP`).

**Reliability**
- [ ] Liveness/readiness/startup probes configured; rollout is `maxUnavailable: 0`.
- [ ] HPA + PodDisruptionBudget applied; resource requests/limits set.
- [ ] Containers run as non-root, read-only rootfs, dropped capabilities.

**Observability**
- [ ] JSON logs shipped to a log store; `/metrics` scraped by Prometheus.
- [ ] Alerts live: readiness, 5xx rate, p95 latency, DB connections, backups.
- [ ] Error tracking (Sentry) wired with release + request-id correlation.

**Security**
- [ ] `CLAMAV_ENABLED=true` (uploads scanned, fail-closed) for untrusted intake.
- [ ] Dependency/image scanning in CI (e.g. `pip-audit`, Trivy) — recommended add-on.
- [ ] Rate limiting at the edge (Cloudflare/nginx) on auth + upload endpoints.

**Rollback**
- [ ] Previous image tag known; `kubectl rollout undo` (or re-deploy prior tag) rehearsed. Compose, after the OPS-003 cutover: `IMAGE_TAG=sha-<previous> … pull && up -d --no-build`; before it, rollback = rebuild the previous commit (runbook §6).


### Security image update (2026-10-16, pending review)

The origin now runs as NGINX UID/GID 101; API/workers remain UID 10001. Keep mounted
NGINX config readable and certificate directories traversable. The origin private
key must be read-only and group-readable by GID 101 (for example root:101, 0440),
never world-readable. Verify isolated TLS startup before rollout; an old root-only
key mount will fail. Production API/worker/origin drop capabilities and enable
no-new-privileges; database/MinIO initialization behavior is unchanged.

Builds use pinned bases and optionally accept a **public CA bundle** using
`docker build --secret id=build_ca,src=/path/to/ca-bundle.pem …`. Standard proxy build
arguments can be supplied for restricted build networks; do not disable TLS checks
or bake runtime credentials/proxy secrets into images. Final-image SBOM/audit runs
are now part of the Docker gate. [Image verification and remaining release blockers](security/V05-IMAGES-2026-10-16.md)
must be reviewed before any production update; a successful build is not clearance.

### Upload ingress capacity (CAP-CAPACITY, pending review)

Upload capacity controls are proposed for review in
[CAP-CAPACITY ingress qualification](security/CAP-CAPACITY-INGRESS-2026-10-18.md).
API body reads default to 30 seconds idle / 300 seconds total; these timers stop
after the body completes and do not cancel storage or transactions. Compose and
Kubernetes carry `UPLOAD_BODY_IDLE_SECONDS` / `UPLOAD_BODY_TIMEOUT_SECONDS`.
The origin streams API bodies with HTTP/1.1 and permits two upload requests across
its NGINX workers; tune its configuration only with measured capacity evidence.
Coordinate API and origin rollout, keeping the 20 MiB envelope and command lookup
available. Kubernetes `/api` bypasses frontend NGINX, so its streaming annotations
do not establish a shared cluster quota. Full deployment/hardware/release acceptance
remains open.

### Upload storage exhaustion (CAP-DISK-FULL, pending review)

A classified upload whose multipart staging hits ENOSPC/EDQUOT now returns HTTP
503, `upload_storage_full`, `Cache-Control: no-store` and `Retry-After: 1`.
The response asks the user to check committed status before retrying. The SPA
retains its command key and requires a manual retry; the one-second header is
not a promise that storage will have recovered. Capture object-write failures
retain per-file `storage_unavailable` outcomes and any earlier accepted files.

Operators should check available bytes, inodes and applicable quotas separately
for API temporary storage and the configured object volume. Database readiness
can remain healthy while storage is full. Restore capacity under the deployment's
approved storage policy; do not delete content-addressed originals or receipts to
make a retry succeed. [Bounded filesystem evidence and remaining shared-disk/S3
limits](security/CAP-DISK-FULL-2026-10-18.md). No deployment limits or retention
policy changed in this repair.

### Stored capture input limits (CAP-OBJECT-READ, pending review)

UI and email extraction workers now bound fetched originals using the existing
`filesec.max_bytes()` owner. A stored object over the current limit is retained
and appears as `stored_file_too_large` in failed captures, with administrator
remediation and no unchanged-input Retry button. Review the object/reference and
configured limit before recovery; the application neither deletes the original
nor raises the limit automatically. Other downloads/exports keep their existing
contracts and still require capacity qualification. [Two-worker PDF/OCR and read
boundary evidence](security/CAP-OBJECT-READ-2026-10-18.md) is a small ordinary-input
profile, not a recommended global worker count or hardware acceptance.

### Capture original verification (CAP-WORKER-SOURCE-HASH, pending review)

Capture workers verify the bounded fetched source against its recorded digest
before parsing. A mismatch appears as `stored_file_changed` with administrator
restoration guidance; no draft is produced from changed bytes. The reference and
command stay intact and the object is not deleted. Restore the exact original
under the existing approved storage recovery procedure, then replay supported
work. The worklist does not promise that an intact original remains available.
[Native refusal/restoration and remaining email-state limits](security/CAP-WORKER-SOURCE-HASH-2026-10-18.md).

The image gate now publishes a readable GitHub job summary and `summary.md` in
`final-image-security`, including statuses, advisory/package versions and
scanner-reported fixes. Unavailable images say not scanned. Full JSON/SBOM
artifacts and fail-closed enforcement remain. [Current CI failure sweep](security/CI-FAILURE-SWEEP-2026-10-18.md).

The SQLAlchemy candidate now pins **2.1.4** with qualified result/type APIs and
stronger tested tenant scoping for nested ORM-column `EXISTS` queries. No schema
migration is added. Roll back the pin, compatibility and control changes together;
2.0 alone no longer meets the new nested-predicate assertion. The benchmark now
initializes its own startup heap once rather than inferring startup from any
already-frozen objects; application GC settings and growth limits are unchanged.
[Evidence and pending Lead review](security/CI-DEPS-SQLA21-2026-10-18.md).
The subsequent [remaining backend dependency candidate](security/CI-DEPS-BACKEND-REST-2026-10-18.md)
updates FastAPI/Uvicorn, asyncpg/Alembic, Pydantic, boto3 and pypdfium2. It adds no
data migration. Existing tokens and invoice/payment commands replay on the same
PostgreSQL database after the image switch. The derived OpenAPI snapshot removes
only Decimal patterns; its Lead approval and exact-head CI are pending. Roll back
this dependency/snapshot commit or use its preceding qualified image if required.
Neither candidate clears the complete build/service image gate.

### Local storage volume initialization (SYS-03, pending review)

New backend images prepare `/app/var/storage` as UID/GID 10001, mode `0700`.
Docker initializes an empty named volume from that directory; API and worker must
mount the same volume at that path and run as UID 10001. Stored objects are `0600`.
Keep `STORAGE_LOCAL_SHARED=true` limited to replicas sharing the actual volume.
Bind mounts, `volume-nocopy` and custom storage paths need separate operator
preparation; the image cannot initialize their ownership through this mechanism.

Nonempty existing volumes retain their contents, ownership and modes. The image
does not run an ownership repair at startup. A root-owned legacy volume may still
refuse writes: stop intake, inventory ownership/ACLs and references, verify backups
and test a proposed ownership or copy/restore plan on a clone before authorizing it.
Do not run recursive ownership changes or make customer storage world-writable to
clear an upload error. A root-access check alone cannot verify every nested object.

After building a candidate, run `python3 scripts/check_storage_volume.py IMAGE`.
It creates only uniquely named synthetic volumes, checks independent-process
original/page I/O, restart/read-only behavior and existing-file metadata, and cleans
up its own resources. Fixture-only root prepares an isolated legacy volume; actual
storage checks run as UID 10001 with dropped capabilities and no-new-privileges.
The CI runtime-exclusion gate still seeds unwanted build data and now permits
exactly one empty prepared storage directory. [Evidence and rollout limits](security/SYS-03-STORAGE-VOLUME-2026-10-17.md).

### Image audit and runtime update (SYS-04, pending review)

The backend now uses pinned Python 3.14 Alpine, including native PDF compatibility,
English/OSD OCR data, DejaVu fonts and the same UID/storage contract. The origin
updates libexpat; PostgreSQL is pinned to the already-tested official PG16.15 digest.
Both final application images passed the recorded HIGH/CRITICAL scan. Build/service
findings and unavailable images still block release; a runtime-only scan cannot
clear the inventory. [Compatibility evidence and residuals](security/SYS-04-IMAGE-COVERAGE-2026-10-17.md).

CI loads backend/frontend final and `build` targets, then runs
`python scripts/image_audit.py` with PyYAML 6.0.3. For local runs, supply the four
actual local tags using `--backend`, `--frontend`, `--backend-build` and
`--frontend-build`. It pulls declared manifest service images, resolves immutable
IDs and writes sanitized SBOM/audit evidence plus `image-security/summary.json`.
Unavailable/unmapped/unpinned references and scanner/evidence errors fail closed;
one failure does not stop later targets. Review exact IDs, platform and source before
promotion. CI/browser/maintenance images outside these manifests and deployed GHCR
artifact identity need separate verification. No production deployment is implied.
