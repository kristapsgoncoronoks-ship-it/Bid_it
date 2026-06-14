# Integrator manual — `/api/v1` (external token API)

This is the **human integrator guide** for the versioned external API. The
**machine-readable contract** is [`docs/openapi.yaml`](openapi.yaml) (OpenAPI 3.1) —
import it into your CRM connector / codegen tool. For the short prose overview see
[`docs/API.md`](API.md); this manual is the authoritative, end-to-end reference and the
two do not contradict (the **committed code is the source of truth** for both).

The API exposes two capability groups:

- **Analytics reads** — benchmark, VAT claim status, savings intelligence (read-only).
- **Basic CRM sync** — read + write the in-app customer master, so an outsourced /
  external CRM can keep it in step. It is deliberately minimal: richer CRM duties are
  intended to live **in that external CRM**, delegated to it via this seam, not rebuilt
  in the app.

It is a clean machine contract: **token-only** (never the session cookie), **scoped per
endpoint**, **metered per key**. The internal session-authed `/api/*` routes used by the
app's own UI are NOT part of this contract.

---

## 1. Authentication & key issuance

### Default-off

No API keys exist on a fresh install, so the whole `/api/v1` surface is **inert** — every
request returns `401` until an admin issues a key. There is no anonymous access and no
implicit key.

### Minting a scoped key (admin)

Keys are minted from the **Admin panel** (admin-only — the `/admin` route):

1. Open **Admin → "API keys (machine access — /api/v1)"** card.
2. Enter a **label** (free text, for your own bookkeeping) and tick the **scopes** the
   key should carry (one checkbox per scope — see the table below).
3. Submit **"+ Issue API key"**.
4. The plaintext token is shown **exactly once**, inline, as a copy-me code block:

   > API key #N issued (label). **Copy it now — it is shown only once and cannot be
   > recovered:** `…token…`

   Copy it immediately. The server stores only the token's **SHA-256 hash**; the
   plaintext is never recoverable. If you lose it, revoke the key and issue a new one.

Internally `api_keys.issue(label, scopes, owner)` generates a 256-bit token
(`secrets.token_urlsafe(32)`), persists only `sha256(token)` in a BLOB column in
`security.db`, and records the issuing admin as `owner`. Issuance and revocation are
audit-logged.

### Presenting the token

Send the token on **every** request, by either header (both are accepted and equivalent):

```
Authorization: Bearer <token>
```
or
```
X-API-Key: <token>
```

`Authorization: Bearer` takes precedence if both are present. The token is compared
**constant-time** (`secrets.compare_digest`) against every active key's stored hash, so a
wrong token cannot be distinguished by timing.

### 401 vs 403 (the auth posture)

| Situation | Status |
|---|---|
| Valid key that holds the endpoint's required scope | `200` / `201` (success) |
| Missing / malformed / unknown token | `401` |
| Revoked key | `401` |
| No keys issued at all (default-off) | `401` |
| Valid key that **lacks** the endpoint's required scope | `403` |

A `401` is a **no-oracle** response: it never reveals whether the token was simply
unknown vs. revoked. A `403` names the scope the key is missing
(`key not authorized for scope <scope>`).

### Scopes

A key carries a **set** of scopes. Each endpoint requires **exactly one** scope, checked
server-side **regardless of HTTP method** (so a read-only key can never write). The full
issuable vocabulary (`api_keys.SCOPES`):

| Scope | Grants |
|---|---|
| `api:benchmark` | `GET /api/v1/benchmark` — read the internal price benchmark summary |
| `api:claims` | `GET /api/v1/claim-status` — read VAT claim status / readiness (non-sensitive fields only) |
| `api:savings` | `GET /api/v1/savings` — read the savings-intelligence summary |
| `api:crm` | `GET /api/v1/customers`, `GET /api/v1/customers/{code}` — read customer master for CRM sync |
| `api:crm.write` | `POST /api/v1/customers`, `PATCH /api/v1/customers/{code}` — create / update customer master |

`api:crm.write` does **not** imply `api:crm`: grant both on a key that needs to both read
and write the customer master.

---

## 2. Endpoint reference

Base URL is **deployment-relative**: the API lives at `<your-host>/api/v1`. The examples
below use `https://host` as a placeholder. All EUR figures are quantized HALF_UP
(`money.f2`); all prices are **NET EUR/L, VAT excluded, rebates applied**.

The uniform error body for every non-2xx response is `{"error": "<message>"}`.

### 2.1 `GET /api/v1/benchmark` — scope `api:benchmark`

Internal price benchmark per supplier/country (Diesel) for a period. `?period=YYYY-MM`
is optional; omit it for the latest available period.

```bash
curl -s https://host/api/v1/benchmark?period=2026-03 \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "period": "2026-03",
  "basis": "NET EUR/L (VAT excluded)",
  "rows": [
    {"supplier": "Neste", "country": "LT", "litres": 12345, "doc": 1.2345, "eff": 1.2300}
  ]
}
```

`doc` = document NET EUR/L (`SUM(net_eur)/SUM(qty)`, 4 dp); `eff` = effective NET EUR/L
after rebates (`SUM(net_eur_eff)/SUM(qty)`, 4 dp). `period` is `null` when no data exists.

### 2.2 `GET /api/v1/claim-status` — scope `api:claims`

VAT claim workflow status / readiness per (entity, country, period) for a calendar year.
`?year=YYYY` is optional (defaults to `2026`). Each (entity, country) yields the quarters
`Q1`..`Q4` plus a `YEAR` annual roll-up. **Non-sensitive fields only** — never IBAN /
payout / fee / bank / PII.

```bash
curl -s https://host/api/v1/claim-status?year=2026 \
  -H "X-API-Key: $TOKEN"
```

```json
{
  "claims": [
    {
      "entity": "BALTIC TRANSPORT OU",
      "country": "PL",
      "period": "Q1",
      "vat_eur": 1234.56,
      "vat_local": 1234.56,
      "currency": "EUR",
      "lines": 42,
      "verdict": "READY (>= EUR 400 quarterly min)",
      "missing": [],
      "deadline": "30 Sep 2027"
    }
  ]
}
```

`missing` lists months of the quarter with no loaded data. `verdict` is one of the
readiness strings (`READY …` / `DEFER TO ANNUAL …` / `BELOW ANNUAL MIN …`).

### 2.3 `GET /api/v1/savings` — scope `api:savings`

The savings-intelligence summary: avoidable overpay + recoverable contract €, addressable
total, per-country breakdown, an anomaly count, and the top-€ actions. `?period=YYYY-MM`
is optional.

```bash
curl -s https://host/api/v1/savings?period=2026-03 \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "period": "2026-03",
  "avoidable_overpay_eur": 4210.00,
  "recoverable_contract_eur": 1875.50,
  "anomaly_count": 3,
  "total_addressable_eur": 6085.50,
  "by_country": [
    {"country": "LT", "overpay_eur": 3000.00, "recover_eur": 500.00, "addressable_eur": 3500.00}
  ],
  "top_actions": [
    {"kind": "Route volume to cheaper supplier", "country": "LT",
     "detail": "LT Diesel: best Neste @ 1.2300 €/L vs your avg 1.2600 (10,000 L)",
     "eur": 300.00}
  ]
}
```

`period` is `null` when no data exists. Anomalies carry no € — they appear only as
`anomaly_count`.

### 2.4 `GET /api/v1/customers` — scope `api:crm`

List the customer master (core, non-secret fields), ordered by `code`.

```bash
curl -s https://host/api/v1/customers \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "customers": [
    {"code": "ACME", "company_name": "Acme OU", "country": "LT",
     "status": "active", "active": true, "countries_active": ["LT", "PL"]}
  ]
}
```

`active` is `true` when `status == "active"`. `countries_active` lists the customer's
countries whose per-country registration is `active`.

### 2.5 `GET /api/v1/customers/{code}` — scope `api:crm`

One customer's detail: core fields + the activation checklist + per-country status. The
`code` is trimmed and upper-cased server-side. `404` if unknown.

```bash
curl -s https://host/api/v1/customers/ACME \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "code": "ACME",
  "company_name": "Acme OU",
  "country": "LT",
  "status": "active",
  "reg_number": "123456",
  "vat_number": "LT123456789",
  "legal_address": "Gedimino pr. 1, Vilnius",
  "home_portal": "https://portal.example.lt",
  "phone": "+370 5 555 1234",
  "email": "ops@acme.lt",
  "nace_code": "4941",
  "active": true,
  "is_active": true,
  "activation_checklist": [
    {"label": "Trade registry extract", "ok": true}
  ],
  "countries": [
    {"country": "LT", "status": "active"}
  ]
}
```

`is_active` is an alias of `active`. This same **detail shape** is the body returned by a
successful `POST` (201) and `PATCH` (200).

### 2.6 `POST /api/v1/customers` — scope `api:crm.write`

Create a customer. **Required** (non-empty after trim): `code`, `company_name`,
`country`. Optional real fields (`reg_number`, `vat_number`, `legal_address`,
`home_portal`, `phone`, `email`) are written immediately, so an API-onboarded customer
carries real values rather than the `INPUT:` placeholders the seed would otherwise set.
`code` is normalised to upper-case.

```bash
curl -s -X POST https://host/api/v1/customers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code":"ACME","company_name":"Acme OU","country":"LT",
       "reg_number":"123456","vat_number":"LT123456789","email":"ops@acme.lt"}'
```

- `201` — created; body is the GET-detail shape (section 2.5).
- `400` — a required field is missing/empty, or a supplied optional field failed
  validation (`{"error": "code, company_name and country are required"}`).
- `409` — a customer with that `code` already exists
  (`{"error": "customer ACME already exists"}`).

### 2.7 `PATCH /api/v1/customers/{code}` — scope `api:crm.write`

Update any subset of the **editable allowlist** (`customer_master.EDITABLE_FIELDS`):
`company_name`, `reg_number`, `vat_number`, `legal_address`, `home_portal`, `phone`,
`email`, `nace_code`, `signatory_name`, `signatory_title`. Keys outside the allowlist are
**ignored** (not an error). The write never touches `status` / fee / route or any other
workflow column, and never runs raw SQL from the body.

```bash
curl -s -X PATCH https://host/api/v1/customers/ACME \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"new@acme.lt","phone":"+370 5 555 1234"}'
```

- `200` — updated; body is the GET-detail shape.
- `400` — no editable field supplied, or a supplied field is empty/whitespace
  (`{"error": "email cannot be empty"}` / `{"error": "no editable fields supplied"}`).
- `404` — no customer with that `code` (`{"error": "customer ACME not found"}`).

---

## 3. CRM integration playbook

The CRM seam lets an outsourced / external CRM own richer customer-relationship duties
while keeping the in-app customer master (which feeds VAT claims) in sync. Two directions:

### PULL (sync into your CRM)

1. **List** with `GET /api/v1/customers` (scope `api:crm`) to enumerate codes + core
   fields + the `active` flag + `countries_active`.
2. **Hydrate** each with `GET /api/v1/customers/{code}` for the full detail, including the
   `activation_checklist` (so your CRM can show onboarding progress) and per-country
   `status`.

Poll on whatever cadence suits you — the reads are cheap and metered.

### PUSH (maintain the master from your CRM)

1. **Create** new customers with `POST /api/v1/customers` (scope `api:crm.write`). Send
   the three required fields plus whatever optional real fields you hold.
2. **Update** existing customers with `PATCH /api/v1/customers/{code}` — send only the
   fields that changed; unknown / non-editable keys are ignored.

### Audit attribution

Every CRM **write** is audit-attributed in `customers.db` as
`changed_by = api:<key-label>` (or `api:<key-id>` when the key has no label), resolved
from the authenticated key. The actor is always reset after the request, even on error.
Use a **distinct, well-labelled key per integration** so the audit trail names the source.

### Idempotency & duplicate handling

`POST` is **not** idempotent on `code`: re-posting an existing `code` returns `409`, it
does not overwrite. The safe pattern is **create-or-update**:

```
POST /customers            -> 201  (new)            ... done
                           -> 409  (already exists) ... then PATCH /customers/{code}
```

`PATCH` is naturally idempotent (setting the same fields twice yields the same state).

### Read-key-cannot-write

A key holding only `api:crm` gets `403` on `POST`/`PATCH` — the scope check is per
endpoint, independent of HTTP method. Mint a key with `api:crm.write` for write access
(and add `api:crm` too if the same key also reads).

---

## 4. Error model

All non-2xx responses share one shape:

```json
{"error": "<human-readable message>"}
```

Status codes used across the surface:

| Code | Meaning |
|---|---|
| `200` | Success (GET; PATCH update) |
| `201` | Customer created (POST) |
| `400` | Bad request — missing/empty required field, no editable fields, validation failure |
| `401` | Missing / malformed / unknown / revoked token; or default-off (no keys) |
| `403` | Valid key without the endpoint's required scope |
| `404` | Unknown customer `code` |
| `409` | Duplicate `code` on create |

(`500` `{"error": "internal error"}` is returned only on an unexpected server fault; it is
logged to the app + admin error logs.)

---

## 5. Usage metering

Every `/api/v1` call is metered to `api_usage` (key id, endpoint, timestamp, final HTTP
status), and the key's `last_used` is bumped on each successful verification.
Unauthorized calls are metered too (with their `401`/`403`). The Admin panel's API-keys
card shows each key's **call count** and **last call** time. There is currently **no rate
limit or quota** (a backlog item).

---

## 6. Versioning

- The contract is **`v1`** — pinned in the path prefix `/api/v1` and in
  `docs/openapi.yaml` (`info.version`).
- Changes within `v1` are **additive only**: new endpoints, new optional response fields,
  new optional request fields. Clients MUST tolerate **unknown response fields** (do not
  fail on extra keys) so additive growth never breaks you.
- Anything breaking (removing/renaming a field, changing a status code or required input)
  would ship under a new version prefix, not silently within `v1`.

---

## 7. Security notes

- **TLS.** Tokens are bearer credentials — always call over HTTPS. The app serves HTTPS
  when a TLS certificate is configured; terminate TLS in front of it otherwise.
- **At rest.** Only the SHA-256 hash of a token is stored (`security.db`); the plaintext
  is shown once at issue and is unrecoverable. Constant-time comparison on verify.
- **Least scope.** Grant a key only the scopes it needs. Keep read and write integrations
  on separate keys where practical; never put `api:crm.write` on a key that only reads.
- **Rotation & revocation.** Revoke a key from the Admin panel ("revoke"); it fails on its
  **next** call (`401`), immediately. Rotate by issuing a new key, cutting over, then
  revoking the old one.
- **No PII / secrets in payloads.** No v1 payload carries IBAN / bank / payout / fee /
  secret / PII — neither analytics nor CRM. Writes only touch an allowlist of editable
  customer columns via parameterized writers.
- **Additive, isolated.** The token path owns only `/api/v1/*`; it never weakens or
  bypasses session auth on any other route.

---

## 8. The machine contract

The authoritative, importable schematic is **[`docs/openapi.yaml`](openapi.yaml)** —
OpenAPI 3.1. Its endpoint→scope map mirrors `app.API_V1_SCOPE` exactly; its schemas mirror
the JSON the views return. Point your CRM connector / code generator at it.
