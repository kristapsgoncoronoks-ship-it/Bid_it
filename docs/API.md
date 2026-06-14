# External API — `/api/v1` (token contract)

> The machine-readable contract is [`docs/openapi.yaml`](openapi.yaml) (OpenAPI 3.1)
> and the full integrator guide (auth, key issuance, per-endpoint curl examples, the
> CRM playbook) is [`docs/API_MANUAL.md`](API_MANUAL.md). This page is the short prose
> overview; the code is the source of truth for all three.

A small, **versioned** machine API over the platform's analytics capabilities and
a **basic CRM-sync surface** (read + write to customer master). It is a clean
external contract: **token-only** (never the session cookie), scoped per endpoint,
metered per key. The internal session-authed `/api/*` routes (used by the app's
own UI) are unchanged and are **not** part of this contract.

The analytics endpoints are read-only. The CRM endpoints are deliberately
**minimal** — just enough for an outsourced/external CRM (e.g. a top-10 vendor's
connector) to read and maintain the in-app customer master that feeds VAT claims.
Richer CRM duties are intended to be **delegated to that external CRM via this
seam**, not rebuilt in the app.

## Default-off

No API keys exist on a fresh install, so the API is **inert** — every request
returns `401`. An admin must explicitly issue a key (Admin panel → *API keys*)
before any `/api/v1` call can succeed.

## Authentication

Send the bearer token on every request, either way:

```
Authorization: Bearer <token>
X-API-Key: <token>
```

- Tokens are high-entropy (`secrets.token_urlsafe(32)`, 256 bits).
- Only the **SHA-256 hash** of a token is stored (in `security.db`, `api_keys`);
  the plaintext is shown **once** at issue and is never recoverable — copy it then.
- Verification re-hashes the presented token and compares **constant-time**
  (`secrets.compare_digest`) against active keys.

### Responses

| Situation | Status |
|---|---|
| Valid key, holds the endpoint's scope | `200` (JSON) |
| Missing / malformed / unknown token | `401` |
| Revoked or otherwise inactive key | `401` |
| Valid key, but lacks the endpoint's scope | `403` |

Error bodies are `{"error": "<message>"}`. A `401` never reveals whether a token
was simply unknown vs. revoked (no oracle).

## Scopes

A key carries a set of capability scopes. Each endpoint requires exactly one:

| Scope | Grants |
|---|---|
| `api:benchmark` | `GET /api/v1/benchmark` |
| `api:claims` | `GET /api/v1/claim-status` |
| `api:savings` | `GET /api/v1/savings` |
| `api:crm` | `GET /api/v1/customers`, `GET /api/v1/customers/<code>` (CRM read) |
| `api:crm.write` | `POST /api/v1/customers`, `PATCH /api/v1/customers/<code>` (CRM write) |

A read-only `api:crm` key **cannot** call the write endpoints — the guard checks
the endpoint's required scope regardless of HTTP method, so an `api:crm`-only key
gets `403` on `POST`/`PATCH`. Mint a key with `api:crm.write` for write access.

## Analytics endpoints (v1 — read-only)

All prices are **NET EUR/L, VAT excluded, rebates applied**. EUR figures are
quantized (`money.f2`, HALF_UP). No write or extract endpoints exist for analytics.

### `GET /api/v1/benchmark?period=YYYY-MM`

Internal price benchmark per supplier/country for a period (defaults to the
latest). Reads the product DB via the read-only boundary.

```json
{"period":"2026-03","basis":"NET EUR/L (VAT excluded)",
 "rows":[{"supplier":"...","country":"LT","litres":123,"doc":1.2345,"eff":1.2300}]}
```

### `GET /api/v1/claim-status?year=YYYY`

VAT claim status / readiness per (entity, country, period). **Non-sensitive
fields only** — workflow verdict, EUR/local VAT totals, line count, deadline.
Never returns IBAN, payout destination, fees, secrets or other PII.

```json
{"claims":[{"entity":"...","country":"PL","period":"2026-Q1","vat_eur":1234.56,
            "vat_local":1234.56,"currency":"EUR","lines":42,
            "verdict":"READY (>= 400 EUR quarterly min)","missing":[],"deadline":"..."}]}
```

### `GET /api/v1/savings?period=YYYY-MM`

The savings-intelligence summary (`savings_intel.summary`): avoidable overpay +
recoverable contract €, addressable total, per-country breakdown, top actions.

## CRM-sync endpoints (v1 — read + write)

A basic customer-master surface for an external CRM. Payloads carry **core,
non-secret** fields only — never IBAN / bank / payout / fee / PII. Writes go
through the shared, parameterized `customer_master.update_customer` /
`add_customer` writers (no raw SQL from the body) and are **audit-attributed** to
the calling key as `api:<key-label>` in `customers.db`'s audit log.

### `GET /api/v1/customers` — scope `api:crm`

List customers (core fields + activation flag).

```json
{"customers":[{"code":"ACME","company_name":"Acme OU","country":"LT",
               "status":"active","active":true,"countries_active":["LT","PL"]}]}
```

### `GET /api/v1/customers/<code>` — scope `api:crm`

One customer's detail: core fields + the activation checklist (`is_active`) and
per-country status. `404` if the code is unknown.

```json
{"code":"ACME","company_name":"Acme OU","country":"LT","status":"active",
 "reg_number":"123","vat_number":"LT123","legal_address":"...","home_portal":"...",
 "phone":"...","email":"...","nace_code":"4941","active":true,"is_active":true,
 "activation_checklist":[{"label":"Trade registry extract","ok":true}],
 "countries":[{"country":"LT","status":"active"}]}
```

### `POST /api/v1/customers` — scope `api:crm.write`

Create a customer. **Required:** `code`, `company_name`, `country`. Optional real
fields (`reg_number`, `vat_number`, `legal_address`, `home_portal`, `phone`,
`email`) are written immediately, so an API-onboarded customer carries real values
rather than the `INPUT: …` placeholders `add_customer` would otherwise seed.

```
POST /api/v1/customers
{"code":"ACME","company_name":"Acme OU","country":"LT","reg_number":"123",
 "vat_number":"LT123","email":"ops@acme.lt"}
```

Returns **`201`** with the GET-detail shape. Duplicate `code` → **`409`**; missing
required / empty `code` → **`400`**.

### `PATCH /api/v1/customers/<code>` — scope `api:crm.write`

Update a subset of the editable allowlist (`company_name`, `reg_number`,
`vat_number`, `legal_address`, `home_portal`, `phone`, `email`, `nace_code`).
Unknown keys are ignored; a value that is empty/whitespace for a field being set
is rejected (**`400`**). `404` if the code is unknown. Returns **`200`** with the
updated detail.

```
PATCH /api/v1/customers/ACME
{"email":"new@acme.lt","phone":"+370 5 555 1234"}
```

## Metering

Every `/api/v1` call is logged to `api_usage` (key id, endpoint, timestamp, HTTP
status) and the key's `last_used` is updated, so per-key usage is queryable. The
Admin panel shows each key's call count and last-used time.

## Security posture

- Tokens hashed at rest (SHA-256), shown once, constant-time compared.
- Scoped per endpoint; revocation takes effect immediately (next call → `401`).
- No v1 payload carries IBAN / bank / payout / fee / secret / PII.
- The **write** surface (CRM) requires the separate `api:crm.write` scope; an
  `api:crm` read key cannot write (`403`). Writes only touch an **allowlist** of
  editable customer columns via parameterized writers — never `status`/fee/route
  or any other workflow column, and never raw SQL from the request body.
- Every CRM write is **audit-attributed** (`changed_by = api:<key-label>`) in
  `customers.db`, with the actor reset on every path (including errors).
- The token path is purely additive: it owns only `/api/v1/*` endpoints and never
  weakens or bypasses session auth on any other route.
- Issuance and revocation are audit-logged.

## Follow-ups (not in v1, need a product decision)

- Rate-limiting / quotas per key.
- A key **expiry** policy (auto-expire after N days) — today keys live until revoked.
- Richer CRM operations (delete/merge, country activation, document upload) — kept
  out of v1 by design; intended to live in the external CRM, synced via this seam.
