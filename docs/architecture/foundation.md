# Platform foundation

The concrete, runnable foundation every future module builds on: the repository
layout, how to start it, the environment contract, the commands, the boundary
tests, and a working health-check demo.

Design stance — **the smallest maintainable foundation, no placeholder
abstractions.** Every seam below is load-bearing *today*; nothing is scaffolding
for an imagined future. The reason each abstraction exists is stated inline.

Rules that govern *changing* any of this live in
[`engineering-rules.md`](engineering-rules.md).

---

## 1. Final file structure

```
Bid_it/
├── Makefile                      # one-word dev commands (make help)
├── docker-compose.yml            # local: postgres + api + worker + web
├── docker-compose.prod.yml
├── .pre-commit-config.yaml       # ruff lint+format on commit (= CI gate, earlier)
├── .github/
│   ├── workflows/ci.yml          # lint · backend tests · postgres RLS · docker build
│   ├── workflows/release.yml
│   └── dependabot.yml            # weekly grouped dep-update PRs (pip/npm/actions/docker)
├── backend/
│   ├── pyproject.toml            # ruff + mypy config (tooling only; deps stay in requirements)
│   ├── requirements.txt          # pinned runtime deps
│   ├── requirements-dev.txt      # pinned tooling (ruff, mypy, pre-commit)
│   ├── Dockerfile
│   ├── alembic.ini · alembic/    # migrations — the production schema source of truth
│   ├── pytest.ini
│   └── app/
│       ├── main.py               # FastAPI app: middleware stack, error handlers, health
│       ├── worker.py             # background job worker (python -m app.worker)
│       ├── seed.py               # dev seed (python -m app.seed)
│       ├── openapi.py            # export the OpenAPI schema (python -m app.openapi)
│       ├── core/                 # (16) cross-cutting INFRASTRUCTURE — see §below
│       ├── models/               # (27) SQLAlchemy ORM tables + enums
│       ├── schemas/              # (27) Pydantic v2 request/response models
│       ├── services/             # (49) business logic — one capability per module
│       └── api/
│           ├── deps.py           # auth/session/tenant dependencies
│           ├── router.py         # mounts every route module under /api/v1
│           └── routes/           # (28) thin HTTP endpoints
│   └── tests/                    # (59 files) pytest — unit + HTTP + boundary + RLS
├── frontend/                     # React + Vite + TS (strict) SPA
│   ├── tsconfig.json             # "strict": true + noUnused* + noFallthrough
│   ├── package.json · vite.config.ts · tailwind.config.js
│   └── src/{lib,pages,components}
└── docs/
    ├── architecture/{overview,domain-modules,data-flows,security-boundaries,
    │                 deployment,data-model,engineering-rules,foundation}.md
    ├── architecture/adr/         # numbered decision records
    └── product/                  # requirements, personas, risks, pricing
```

### The `core/` foundation modules (why each exists)

`core/` is infrastructure — it may not import `services` or `api` (enforced by
`tests/test_boundaries.py`). Every module here is used by real feature code:

| Module | Reason it exists (immediate use) |
|---|---|
| `config.py` | Twelve-factor settings + **production fail-fast validation** (won't boot with a dev secret / SQLite / open CORS). |
| `database.py` | Async engine + `get_session` dependency + per-connection tuning. |
| `errors.py` | Framework-agnostic `AppError` so services signal failure without importing FastAPI. |
| `observability.py` | Request-ID middleware, structured JSON logs, Prometheus `/metrics`. |
| `tenant.py` | Row-level tenant scoping: ORM guard + `TenantScopeMiddleware` + RLS GUC. |
| `security.py` / `security_headers.py` | Password hashing / JWT; HSTS, nosniff, frame-deny headers. |
| `roles.py` | Role hierarchy + `is_admin_or_above` checks used by every guarded route. |
| `ratelimit.py` | Per-process abuse/brute-force guard (stricter tier on `/auth/*`). |
| `storage.py` | Object-storage seam (memory/local/S3) for document bytes — off the DB. |
| `keyvault.py` | Envelope encryption (AES-256-GCM) for stored third-party secrets. |
| `money.py` | Decimal quantisation — the only correct way to round/sum currency. |
| `residency.py` | Region-pinning backstop (421) behind the LB's region routing. |
| `metrics.py` | Close-time dashboard aggregates (engine writes, app reads). |
| `dimensions.py` | Cost-allocation dimension keys (now backed by the `costing` master tables). |

## 2. Local startup instructions

Zero-config path (SQLite, no Docker) — good for tests and most feature work:

```bash
make install          # backend venv + frontend deps (runtime + dev tooling)
make seed             # demo tenant: demo@invoiceiq.app / demo1234
make backend          # API on http://localhost:8000  (docs at /docs)
make frontend         # SPA on http://localhost:5173
make worker           # (optional) drain the background job queue
pre-commit install    # (optional) run lint+format on every commit
```

Production-like path (Postgres + worker + web, one command):

```bash
make up               # docker-compose: postgres + api + worker + web on :8080
```

The API creates its schema automatically outside production. In production the
image runs `alembic upgrade head` before boot (see `deploy/` and
[`deployment.md`](deployment.md)); it will **refuse to start** with an insecure
config (§Environment below).

## 3. Environment-variable reference

Every value has a safe local default; only the starred ones **must** be set in
production. Full annotated list: `backend/app/core/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` turns on JSON logs + config validation. |
| `SECRET_KEY` ★ | dev value | JWT signing key. `openssl rand -hex 32`. Prod refuses the dev default. |
| `DATABASE_URL` ★ | SQLite file | Async SQLAlchemy URL. Prod refuses SQLite. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | `10/10/30` | Postgres pool (per worker process). |
| `CORS_ORIGINS` ★ | localhost SPA | Comma-separated allowed origins. Prod refuses `*` with credentials. |
| `LOG_JSON` | unset→prod-only | Force structured JSON logs on/off. |
| `METRICS_ENABLED` | `true` | Expose `/metrics` (needs `prometheus-client`). |
| `STORAGE_BACKEND` | `local` | `local` \| `s3` \| `memory`. |
| `STORAGE_LOCAL_PATH` / `STORAGE_S3_*` | `./var/storage` | Filesystem root or S3 bucket/endpoint/region/prefix. |
| `KEK_PROVIDER` / `KEK_KEY` | `local` / — | Secret-sealing key. `env` (BYOK) requires `KEK_KEY` (base64 32 bytes). |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | unset | Outbound email relay; unset = record-to-outbox only. |
| `INBOUND_EMAIL_DOMAIN` / `INBOUND_EMAIL_SECRET` | `in.invoiceiq.app` / — | Per-org intake addresses + webhook auth. |
| `BILLING_PROVIDER` | `auto` | `auto` \| `stripe` \| `everypay` \| `none`. |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_*` / `STRIPE_METER_*` | unset | Stripe subscription + metered usage. |
| `EVERYPAY_API_USERNAME` / `EVERYPAY_API_SECRET` / `EVERYPAY_ACCOUNT_NAME` / `EVERYPAY_API_BASE_URL` | unset / demo base | Baltic card gateway. |
| `BILLING_SUCCESS_URL` / `BILLING_CANCEL_URL` / `BILLING_PORTAL_RETURN_URL` / `API_PUBLIC_BASE_URL` | localhost | Checkout redirects + public API base. |
| `SSO_POST_LOGIN_URL` / `SSO_ERROR_URL` | localhost SPA | Where SSO lands / bounces. |
| `CLAMAV_ENABLED` / `CLAMAV_HOST` / `CLAMAV_PORT` / `CLAMAV_UNIX_SOCKET` | `false` | Optional upload malware scanning (fails closed when on). |
| `MAX_UPLOAD_MB` | `15` | Upload size cap. |
| `HSTS_ENABLED` / `HSTS_MAX_AGE` | `false` / 2y | Emit HSTS on HTTPS. |
| `RATE_LIMIT_ENABLED` / `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_AUTH_PER_MIN` | `true`/`300`/`20` | Abuse guard tiers. |
| `SERVICE_REGION` / `DEFAULT_TENANT_REGION` / `ENFORCE_REGION_PINNING` | `eu` / — / `false` | Data-residency backstop. |
| `QUEUE_SLO_MAX_PENDING_AGE_SECONDS` / `QUEUE_DLQ_ALERT_THRESHOLD` | `900` / `0` | `/health/queue` degraded thresholds. |

## 4. Development commands

`make help` lists them; the core set:

| Command | Does |
|---|---|
| `make backend` / `make frontend` / `make worker` | run API / SPA / job worker |
| `make seed` | load the demo tenant |
| `make fmt` | ruff autofix + format the backend |
| `make lint` | ruff lint + format-check (no writes) |
| `make typecheck` | mypy on the foundation layer (`app/core`) |
| `make openapi` | write `backend/openapi.json` from the live schema |
| `make check` | **lint + typecheck + test** — the CI gate, locally |
| `make up` / `make down` / `make logs` | docker-compose lifecycle |
| `make migrate` | `alembic upgrade head` |
| `make migration m="…"` | autogenerate a migration |

## 5. Test commands

```bash
make test                                     # full backend suite (default markers)
cd backend && . .venv/bin/activate
python -m pytest tests/ -q                    # everything
python -m pytest tests/test_boundaries.py -q  # just the architectural boundaries
python -m pytest tests/ -q -m "not slow"      # skip slow-marked tests
python -m pytest tests/test_foundation.py -q  # this slice's acceptance evidence
```

CI additionally runs the suite against a **real Postgres** as a non-superuser to
prove row-level security and Postgres-only migrations (`tests/test_rls.py`).

## 6. Architectural boundary tests

`tests/test_boundaries.py` statically inspects imports (AST, nothing executed)
and fails if the layering is violated:

- `test_models_do_not_import_services_or_api` — models are the bottom layer.
- `test_core_does_not_import_services_or_api` — `core` is infrastructure.
- `test_services_do_not_import_the_web_layer` — a service never reaches into `api`.
- `test_app_package_is_importable` — no circular import sneaks in.

Companion structural guards already in the suite:

- `test_tenant_registration.py` — every tenant-scoped model is registered in the
  ORM tenant guard (a forgotten table can't silently skip scoping).
- `test_rls.py` — Postgres refuses a cross-tenant read/write at the DB layer.
- migration-drift guard — the ORM matches the migrated schema (`alembic check`).
- `test_costing.py` — cross-tenant FK is structurally blocked at the DB.

## 7. Working health-check demonstration

Three probes, each with a distinct job (`app/main.py`). Captured live from a
running instance:

```console
$ curl -i localhost:8000/health
HTTP/1.1 200 OK
x-request-id: 84333658eacb4745bbf836990c45ed4c
x-response-time-ms: 0.8
{"status":"ok","version":"0.1.0","app":"InvoiceIQ"}

$ curl localhost:8000/health/ready          # 503 if the DB is unreachable
{"status":"ready"}

$ curl localhost:8000/health/queue          # 503 when the queue SLO is breached
{"status":"ok","dead":0,"pending":0,"oldest_pending_seconds":0,
 "by_status":{"queued":0,"running":0,"failed":0,"succeeded":0,"dead":0}}

$ curl -i -H 'X-Request-ID: demo-trace-123' localhost:8000/health | grep -i x-request-id
x-request-id: demo-trace-123                 # upstream trace id is propagated, not replaced
```

- **`/health`** — liveness (no I/O). Load balancer / k8s `livenessProbe`.
- **`/health/ready`** — readiness; `SELECT 1`; **503** when the DB is down so the
  replica leaves rotation.
- **`/health/queue`** — background-queue SLO (dead-letter depth + oldest-pending
  age); **503 (degraded)** pages an uptime check.

Every response carries `X-Request-ID` (generated, or propagated from an upstream
proxy as shown) and `X-Response-Time-ms`, so any request is traceable end to end.
