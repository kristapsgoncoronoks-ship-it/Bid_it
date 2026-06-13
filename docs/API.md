# External API — `/api/v1` (token contract)

A small, **versioned, read-only** machine API over the platform's analytics
capabilities. It is a clean external contract: **token-only** (never the session
cookie), scoped per endpoint, metered per key. The internal session-authed
`/api/*` routes (used by the app's own UI) are unchanged and are **not** part of
this contract.

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

## Endpoints (v1 — read-only)

All prices are **NET EUR/L, VAT excluded, rebates applied**. EUR figures are
quantized (`money.f2`, HALF_UP). No write or extract endpoints exist in v1.

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

## Metering

Every `/api/v1` call is logged to `api_usage` (key id, endpoint, timestamp, HTTP
status) and the key's `last_used` is updated, so per-key usage is queryable. The
Admin panel shows each key's call count and last-used time.

## Security posture

- Tokens hashed at rest (SHA-256), shown once, constant-time compared.
- Scoped per endpoint; revocation takes effect immediately (next call → `401`).
- Read-only; no v1 payload carries IBAN / bank / payout / fee / secret / PII.
- The token path is purely additive: it owns only `/api/v1/*` endpoints and never
  weakens or bypasses session auth on any other route.
- Issuance and revocation are audit-logged.

## Follow-ups (not in v1, need a product decision)

- Rate-limiting / quotas per key.
- A key **expiry** policy (auto-expire after N days) — today keys live until revoked.
- Write/extract endpoints, if ever needed, would be a separate, scoped v2 design.
