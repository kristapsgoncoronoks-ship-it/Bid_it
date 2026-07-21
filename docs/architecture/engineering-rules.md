# Engineering rules

The standing rules for changing this codebase. They exist so that a change made
by anyone — a new engineer, a future you, an agent — comes out looking like the
rest of the system and can't quietly erode an invariant we depend on.

Each rule is enforced one of three ways, marked inline:
**[CI]** a machine gate fails the build · **[review]** a human checks it in PR ·
**[convention]** a documented norm we hold each other to.

Companion docs: [`overview.md`](overview.md) (why the architecture is shaped this
way), [`security-boundaries.md`](security-boundaries.md), [`data-model.md`](data-model.md),
and [`foundation.md`](foundation.md) (the concrete file structure, commands, and
runnable demos).

---

## 1. Naming

- **Modules** are singular, lowercase, purpose-named: `invoice.py`, `costing.py`,
  `billing_provider.py`. A service module and its model share the domain noun.
- **Tables** are plural snake_case (`cost_centers`); **columns** snake_case.
  A foreign key is `<singular>_id` (`department_id`). Money columns name the
  basis: `subtotal` (tax-exclusive), `tax_amount`, `total` (tax-inclusive).
- **Python**: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE`
  constants. A leading `_` marks a module-private helper. **[convention]**
- **Machine error codes** are stable lowercase slugs (`not_found`, `dup_code`) —
  clients branch on the code, never on prose, so the message can be reworded
  freely (see § Error handling). **[convention]**
- **Booleans** read as assertions: `is_admin`, `enforce_region_pinning`, `on_hold`.

## 2. Domain boundaries

The dependency direction is one-way and machine-enforced (`tests/test_boundaries.py`):

```
models  →  (nothing app-specific)          # the bottom: tables + enums
core    →  models                          # cross-cutting infrastructure
services→  core, models                    # business logic
api     →  services, core, models          # the web layer, on top
```

- `models` imports neither `services` nor `api`. **[CI]**
- `core` imports neither `services` nor `api` — it is infrastructure that knows
  nothing about business features. **[CI]**
- `services` does not import `api` — a service never reaches up into the web
  layer. It signals failure with `AppError`, not `HTTPException`. **[CI]**
- One domain's service may call another's, but data crosses **only** through a
  service or schema — never by reaching into another domain's tables. **[review]**

> Known debt: five service modules still import `fastapi.HTTPException`
> (`documents`, `access`, `email_intake`, `mailer`, `modules`). New code must not;
> these are migrated to `AppError` opportunistically. **[review]**

## 3. Service-layer responsibilities

- A service owns a **business capability**, takes an `AsyncSession` + already-authenticated
  identity (`org_id`, `user`), and returns domain objects or plain data — never a
  `Request`/`Response`. It must be unit-testable without HTTP. **[convention]**
- **Every query a service issues is scoped by `org_id`.** The ORM tenant guard
  and Postgres RLS are backstops, not a licence to omit the `WHERE`. **[review]**
- Transactions: a service method is the unit of work — it commits (or the caller
  composes and commits once). Don't leave a half-written aggregate. **[review]**
- Side effects that can fail independently (email, webhooks, external billing)
  go through their own advisory seam and **never fail the primary write**.

## 4. Repository / data-access rules

- All DB access is async SQLAlchemy 2.0 (`select()`, `await db.scalar(...)`).
  No raw SQL in services except a reviewed, parameterised exception. **[review]**
- **Money is `Decimal`/`Numeric(14,2)`**, quantised through `app.core.money`.
  Never `float` for currency; never bare `round()`. **[review]**
- Amounts are stored as three separate columns — tax-exclusive, tax, tax-inclusive
  — plus original **and** reporting currency with FX provenance. Never derive one
  by subtracting at read time. **[review]**
- **Never silently overwrite an approved/immutable record.** A correction is a new
  version or a reversing entry; audit rows are append-only. **[review]**
- Concurrent edits use the optimistic-concurrency `version` column; a stale write
  raises `ConflictError`, it does not clobber. **[CI]** (covered in `test_costing.py`)
- Uniqueness and cross-tenant FK protection live in the **schema** (composite
  `(org_id, id)` keys), not only in Python. **[CI]**

## 5. API validation

- Every request body/query is a **Pydantic v2 schema** (`app/schemas/`); routes
  receive typed models, never raw dicts. Unknown fields are rejected. **[CI]** (type-checked)
- Every response declares `response_model` so the OpenAPI contract is generated
  from types and can't drift. **[review]**
- Routes are thin: parse → authorize → call one service → shape the response.
  Business rules live in the service, not the route. **[review]**
- Semantic validation the schema can't express (a cross-field or DB-dependent
  rule) raises `ValidationError`/`ConflictError` from the service. **[convention]**

## 6. Error handling

- **Wire contract (fixed):** every error response is
  `{"detail": "<message>", "code": "<slug>"}` and carries an `X-Request-ID`
  header. `detail` is human; `code` is the stable machine slug. This shape is
  what the SPA (`frontend/src/lib/api.ts`) and the test suite depend on — don't
  change it. **[CI]**
- **Services raise `app.core.errors.AppError`** (`NotFoundError`, `ConflictError`,
  `ValidationError`, `PermissionError`), not `HTTPException`. The handler in
  `app.main` maps them to the wire shape. **[convention]**
- **Routes** may raise `HTTPException` for purely HTTP-shaped concerns (bad query
  param, method guard) where no domain code is warranted.
- **Never `except: pass`.** Catch narrowly; log with context; re-raise or convert
  to an `AppError`. An unhandled exception is caught by the last-resort 500
  handler, which returns a generic body and **never leaks the internal message**.
  **[CI]** (`test_foundation.py`)
- Chain exceptions with `raise ... from err` in new code (the `B904` lint is
  deferred only to avoid a blanket retrofit). **[convention]**

## 7. Logging

- One structured JSON line per request in production (`app.core.observability`),
  human-readable elsewhere. Every line carries the `request_id`. **[CI]** (wired)
- Log **facts, not secrets**: never a password, token, `secret_key`, IBAN, or raw
  PII. Sealed secrets stay sealed in logs. **[review]**
- `logging.getLogger("invoiceiq.<area>")`, never `print()` in app code (the `T20`
  lint blocks it; `seed.py`/`openapi.py` are the allowed CLIs). **[CI]**
- Levels: `INFO` normal lifecycle, `WARNING` recoverable/degraded, `ERROR` a
  failed operation, `EXCEPTION` inside an except with a stack. No `DEBUG` spam in
  hot paths.

## 8. Secrets

- Secrets come from the **environment only** (`app.core.config`). None are
  committed; `.env` is git-ignored; defaults are dev-only. **[review]**
- Production **fails to boot** if an insecure default remains (dev `secret_key`,
  SQLite URL, `env` KEK without a key, `*` CORS with credentials). **[CI]**
  (`Settings._validate_production`, `test_foundation.py`)
- Stored third-party secrets (portal/SSO credentials) are **envelope-encrypted**
  via `app.core.keyvault` (AES-256-GCM, AAD-bound). A GCM auth failure raises —
  it never silently returns `""`. **[review]**
- Rotating `secret_key` invalidates sessions by design; rotating the KEK needs a
  re-wrap migration (documented in ADR-0016).

## 9. Migrations

- The schema is owned by **Alembic**; `Base.metadata.create_all` is dev/test
  convenience only — production runs `alembic upgrade head` before boot. **[review]**
- **One linear head.** CI asserts `alembic heads` == 1, that migrations apply
  cleanly from empty, and that the ORM matches the migrated schema (`alembic check`
  — no drift). **[CI]**
- Migrations are **forward-only and reviewed**: additive first (add nullable →
  backfill → enforce in a later migration). Never edit a merged migration; write a
  new one. **[review]**
- Every tenant-scoped table adds its RLS policy in the same migration and is
  registered in the tenant guard — a coverage test unions the tables and fails if
  one is missed. **[CI]** (`test_rls.py`, `test_tenant_registration.py`)
- A migration that can lock a large table (index, `NOT NULL`) is called out in the
  PR with the online-safe approach. **[review]**

## 10. Tests

- **No feature merges without tests.** A bug fix adds the regression test that
  was missing. **[review]**
- Tests are isolated and order-independent: in-memory SQLite per test, in-memory
  storage, reset rate-limit counters (`conftest.py` autouse fixtures). No test
  depends on another's state or on wall-clock/network. **[CI]**
- Assert the **contract**, not the implementation: status + body shape + the
  invariant, so a refactor that preserves behaviour stays green.
- Postgres-only guarantees (RLS, PG migrations) run against a **real Postgres** as
  a non-superuser in CI — SQLite can't prove them. **[CI]**
- Marker `@pytest.mark.slow` is excluded from the default run; CI runs the full
  set. Money/FX/VAT invariants have a dedicated golden suite. **[CI]**

## 11. Pull requests

- **Small and single-purpose.** A mechanical change (reformat, rename) is its own
  commit/PR, separate from behavioural change, so review sees signal. **[convention]**
- Green CI is required to merge: lint + format + typecheck + tests + Postgres RLS
  + both Docker images build. **[CI]**
- The PR body states **what changed and why**, the risk, and the acceptance
  evidence (tests run, output). No "trust me". **[review]**
- Touching an architectural boundary, a tenant/security control, or the money
  model requires an explicit callout and, if it's a decision, an ADR. **[review]**
- If a repo PR template exists, fill its sections; never paste secrets/tokens into
  a PR.

## 12. Dependency updates

- Dependencies are **pinned** (`requirements.txt`, `requirements-dev.txt`,
  `package-lock.json`) so a build is reproducible. **[CI]**
- **Dependabot** opens weekly grouped PRs (pip, npm, actions, docker). Patch/minor
  bumps are batched and merge on green CI; **majors are read for breaking changes**
  before merge. **[review]** (`.github/dependabot.yml`)
- A new runtime dependency is justified in review (what it does that stdlib/an
  existing dep can't) and must not be an optional-only import smuggled into a hot
  path. Optional extras (e.g. `prometheus-client`, `mcp`) stay optional and
  degrade cleanly when absent. **[review]**
- Security advisories are acted on promptly; a pin can only go **up** past a known
  CVE, never held back silently.

---

### How the gates map to commands

| Gate | Local | CI job |
|---|---|---|
| Lint + format | `make lint` | `lint` |
| Types (foundation) | `make typecheck` | `lint` |
| Tests | `make test` | `backend` |
| Boundaries / drift / RLS coverage | `make test` | `backend` |
| Postgres RLS | (needs PG) | `postgres` |
| Images build | `make up` | `docker-build` |
| Everything | `make check` | all of the above |

Install the pre-commit hooks (`pre-commit install`) to run lint + format on every
commit — the same gates, earlier.
