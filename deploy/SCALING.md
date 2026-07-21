# Scaling InvoiceIQ — sizing for 100 and 1,000 tenants

Grounded in measured numbers (single uvicorn worker, local benchmark):

| Measurement | Value |
|---|---|
| Fixed cost per authenticated request | ~7 ms (JWT verify + user load + tenant middleware + rate-limit + audit), before any query |
| List endpoint latency (empty data, SQLite) | p50 ~10 ms, p95 ~12 ms |
| Per-worker throughput ceiling (`/health`, no DB) | ~1,750 req/s — flat past concurrency 8 (single-process GIL bound) |
| Realistic per-worker throughput (DB-backed endpoint) | ~150–250 req/s |
| Worker resident memory (RSS) | ~144 MB |
| Cold import | ~10.5 s (SQLAlchemy + Prometheus init) — within the 60 s startup probe budget |

The app is **I/O-bound, stateless, and light per request**. Web throughput is the easy axis. The axes that actually bind at scale are **Postgres connections**, the **background-job tier** (scales with tenant count, not RPS), and **analytics query cost** on larger datasets.

## Load model

Assumes ~5 users/tenant, ~10% concurrent at peak, ~0.3 req/s per active user, 3× burst headroom.

| | 100 tenants | 1,000 tenants |
|---|---|---|
| Users | ~500 | ~5,000 |
| Peak concurrent users | ~50 | ~500 |
| Peak web RPS (with burst) | ~50 | ~450 |
| Data volume/yr (invoices+lines+txns) | ~1–5 M rows | ~10–50 M rows |
| Document storage | tens of GB | 100s of GB → ~1 TB |

## Recommended topology

| Tier | 100 tenants | 1,000 tenants |
|---|---|---|
| Web (FastAPI) | 3 pods × 1 vCPU / 768 Mi, `WEB_CONCURRENCY=2` | 4–8 pods (HPA → 12), same shape |
| Worker (`35-worker.yaml`) | 2 pods | 3–6 pods, trimmed DB pool |
| Postgres | 2 vCPU / 8 GB, direct | 4–8 vCPU / 16–32 GB **+ read replica** for analytics |
| PgBouncer (`25-pgbouncer.yaml`) | not needed | **required** (transaction mode) |
| Redis | optional | recommended — shared rate-limit + dedup |
| Object storage | S3/R2 bucket | same + lifecycle policies |
| Indicative cost/mo | ~$150–300 | ~$900–2,000 |

## The connection budget (why PgBouncer at 1,000)

Total Postgres connections = `web_workers × web_pods × (DB_POOL_SIZE + DB_MAX_OVERFLOW)` + worker-tier connections.

- 100 tenants: 3 pods × 2 workers × 20 = **120** + workers — trim pools or a modest `max_connections` and you fit. Direct is fine.
- 1,000 tenants: 8 pods × 2 workers × 20 = **320** + worker tier — past a typical `max_connections`. PgBouncer (transaction mode, `DEFAULT_POOL_SIZE=25`) collapses this to ~25–50 real server connections.

**Enabling PgBouncer (all three are required):**
1. Deploy `25-pgbouncer.yaml` (add `DB_USER` / `DB_PASSWORD` to the `invoiceiq-secrets` Secret).
2. Point `DATABASE_URL` at it: `postgresql+asyncpg://invoiceiq:PASS@pgbouncer:6432/invoiceiq`.
3. Set `DB_PGBOUNCER=true` in the config. asyncpg's prepared-statement cache is incompatible with transaction pooling; this flag disables it (`app/core/database.py:build_connect_args`). **Omit it and queries fail intermittently under load** — this is the classic asyncpg-behind-PgBouncer footgun.

## The background-job tier (why it scales with tenant count)

The daily scheduler (`app/services/scheduler.py`) enqueues per-tenant jobs — recurring-invoice generation, dunning, retention purge, usage reporting — plus the integrity sweeps. At 1,000 tenants that is thousands of jobs/day.

- **Execution never stampedes:** jobs land in the durable queue and drain through the worker pool; the atomic claim means each runs once, and only `N_workers` run at a time. So the lever is **worker-pool size**, not execution staggering. Size the worker tier by how quickly the queue must drain (watch queue-lag / dead-letter depth on `/metrics`).
- **The only burst** is the midnight *enqueue* (a batch of cheap INSERTs when the calendar day flips). If that write burst ever matters, spread it with a per-tenant `scheduled_at` offset in `enqueue_daily`; it's not needed at these scales.
- Keep the worker tier **separate from web** (it already is — `35-worker.yaml`) so a slow OCR/PDF job never steals a web request's CPU.
- If AI vision-capture is ever enabled (default off), it becomes the dominant per-document cost and wants its own worker pool.

### Worker lanes — isolating the heavy modules

The queue supports **lanes**: a worker started with `--kinds <a,b>` (or `--exclude <a,b>`)
leases only those kinds, so a resource-heavy module can run on its own dedicated pool
and never starve — or be starved by — the light periodic jobs. A lane-less worker
(`35-worker.yaml`) drains everything; that's the simple default.

`36-worker-lanes.yaml` splits the pool in two, applied *instead of* `35-worker.yaml`:

| Lane | Command | Profile |
|---|---|---|
| `worker-extract` | `--kinds email.extract,upload.extract` | CPU-heavy OCR; larger CPU limit, scale for ingestion bursts |
| `worker-general` | `--exclude email.extract,upload.extract` | light periodic work; small footprint |

The two are complementary — together they cover every kind, so no job is orphaned.

**What's isolated today vs. not.** Both extraction paths now run on the worker tier:
email-attachment extraction (`email.extract`) and **direct-upload OCR** (`upload.extract`,
Stage B) — the upload endpoint stores the file, queues the parse, and returns 202; the
client polls `GET /invoices/upload/{id}` for the draft, so no OCR runs in a web request.
Both land on `worker-extract`. **Still inline on the web tier:** PDF/Excel generation
(invoice & expense PDFs). Moving that off is the same recipe — a `report.render` kind + an
async "generating → download" flow — and the extract/docs lanes here are ready to receive it.

## Analytics at scale

Reporting/dashboard queries grow with row count. At 1,000 tenants route read-heavy analytics to a **read replica** (a second `DATABASE_URL` for report endpoints), and confirm composite indexes cover the tenant-scoped filters the dashboards issue. Web-transaction latency stays flat; only the analytical scans grow.

## Quick wins already applied

- nginx origin gzip (`frontend/nginx.prod.conf`) — compresses API JSON + JS/CSS for direct-origin hits and cuts origin→edge egress.
- `WEB_CONCURRENCY=2` matched to the 1-core pod limit (was 4 — GIL-bound over-subscription).
- Backend memory request 512 Mi (was 384 Mi — under-provisioned a 2-worker pod at ~144 Mi RSS each).
