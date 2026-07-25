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
- **Schema is owned by Alembic.** `create_all` is disabled in production; every
  release runs `alembic upgrade head` exactly once (a Job / one-off container),
  never inside the serving pods.

---

## 2. Configuration (12-factor)

Everything deployment-specific comes from the environment (`app/core/config.py`).
The must-set production variables:

| Variable | Required | Notes |
|---|---|---|
| `SECRET_KEY` | ✅ | JWT signing key — `openssl rand -hex 32`. Rotating it logs everyone out. |
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://user:pass@host:5432/invoiceiq` |
| `ENVIRONMENT` | ✅ | `production` (disables `create_all`, enables JSON logs) |
| `CORS_ORIGINS` | ✅ | Public SPA origin(s), comma-separated |
| `HSTS_ENABLED` | ✅ | `true` once TLS is live |
| `STORAGE_BACKEND` | ✅ | `s3` for prod (AWS S3 / MinIO); `local` only for a single node with a persisted volume. |
| `STORAGE_S3_BUCKET` | s3 | Bucket for document bytes (default `invoiceiq-documents`). |
| `STORAGE_S3_ENDPOINT_URL` | s3 | Endpoint for S3-compatible stores (MinIO); omit for AWS S3. |
| `STORAGE_S3_REGION` / `STORAGE_S3_PREFIX` | | Region; optional key prefix within the bucket. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | s3 | Object-store credentials (boto3). Store as secrets. |
| `WEB_CONCURRENCY` | | uvicorn workers per pod (default 4) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | | per-worker pool (default 10/10) — see §7 |
| `INBOUND_EMAIL_SECRET` | | shared secret for the `/email/inbound` webhook |
| `SMTP_HOST` / `SMTP_*` | | outbound email relay (else sends are recorded-only) |
| `CLAMAV_ENABLED` / `CLAMAV_HOST` | | malware scanning of uploads (fails closed) |

`SECRET_KEY` and `DATABASE_URL` are **secrets** — never bake them into an image
or commit them. Use k8s Secrets / a secrets manager. `backend/.env.example`
lists everything.

---

## 3. Deployment workflow

```
 git tag v1.2.0 ─▶ push tag ─▶ Release workflow builds+pushes images to GHCR
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
kubectl -n invoiceiq create secret generic invoiceiq-secrets \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://user:pass@pg:5432/invoiceiq"
kubectl apply -f deploy/k8s/10-config.yaml     # ConfigMap (edit CORS_ORIGINS)

# every release: migrate first, then roll out (set image tag in the manifests)
kubectl apply -f deploy/k8s/20-migrate-job.yaml
kubectl -n invoiceiq wait --for=condition=complete job/invoiceiq-migrate --timeout=180s
kubectl apply -f deploy/k8s/30-backend.yaml -f deploy/k8s/40-frontend.yaml -f deploy/k8s/50-ingress.yaml
```

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
  - `backend` — full pytest suite + migration consistency on SQLite
  - `postgres` — **the tenancy gate**: migrations on real Postgres + RLS
    enforcement (`tests/test_rls.py`) + concurrent numbering, run as a
    **`NOSUPERUSER`** app role (a superuser would bypass RLS and prove nothing)
  - `frontend` — typecheck + build
  - `docker-build` — both images build
- **`.github/workflows/release.yml`** (on a `v*.*.*` tag): builds and pushes
  immutable, versioned images to GHCR. Deployment stays a deliberate, separate
  step — CI/CD produces artifacts; a human (or ArgoCD/Flux) promotes them.

---

## 5. Monitoring & logging

- **Structured logs.** In production every request emits one JSON line
  (`method`, `path`, `status`, `duration_ms`, `request_id`, `client`) — ship to
  Loki / CloudWatch / Datadog. Each response carries `X-Request-ID` (propagated
  from the edge if present) and `X-Response-Time-Ms`, so a user-reported error
  maps to exact log lines. Uncaught 500s are logged with their request id.
- **Metrics.** `/metrics` exposes Prometheus counters + latency histograms
  (`http_requests_total`, `http_request_duration_seconds`) labelled by route.
  Scrape it; alert on error-rate and p95 latency. (Enabled when
  `prometheus-client` is installed — it ships in the image.)
- **Health.** `/health` = liveness (process up, no I/O); `/health/ready` =
  readiness (DB reachable → 200, else 503 so the LB drains the pod).
- **Suggested alerts:** readiness failing > 1 min; 5xx rate > 1%; p95 latency
  > 1s; Postgres connections > 80% of `max_connections`; disk/backups.
- **Errors.** Wire Sentry/GlideError by setting its DSN and adding the SDK — the
  request-id in logs already gives you correlation.

---

## 6. Reliability / reducing downtime risk

- **Rolling updates, `maxUnavailable: 0`** + readiness gating = zero-downtime deploys.
- **Probes:** startup (slow first boot) → liveness (restart wedged pods) →
  readiness (drain on DB loss). `preStop` sleep + `terminationGracePeriodSeconds`
  let in-flight requests finish (uvicorn drains on SIGTERM; tini forwards it).
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
- [ ] Previous image tag known; `kubectl rollout undo` (or re-deploy prior tag) rehearsed.
