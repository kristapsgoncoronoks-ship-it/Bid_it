# ADR-0015 — REST + OpenAPI, versioned API strategy

**Status:** Accepted

## Context
The SPA, automation platforms (n8n), and future partner integrations need a stable, documented API. We must not accidentally ship an unversioned public contract we can't evolve.

## Selected approach
**REST + auto-generated OpenAPI** (FastAPI) as the single API style, under a **version prefix** (`/api/v1`). First-party SPA + a **token-gated ingest** endpoint (scoped API keys) for automation. A *supported public API* is a deliberate later step with: explicit versioning policy (additive within a version, breaking → new version), rate limits, quotas, scoped tokens, and published docs. Webhooks (ADR-0018) are the outbound half.

## Alternatives considered
- **GraphQL** — flexible client queries, but over-fetch/complexity/authorization-per-field overhead; unnecessary for our resource-shaped domain.
- **gRPC** — great for internal service-to-service; poor browser fit; we're a monolith.
- **Unversioned REST** — cannot evolve safely once external clients exist.

## Why appropriate
REST + OpenAPI is the lowest-friction, best-tooled choice for a resource-oriented financial API and a TS SPA; the generated schema is the contract for free; versioning from day one protects us when partners integrate.

## Risks
- Breaking changes leaking to clients → additive-only within a version; contract tests; deprecation policy.
- Public API support burden → gate GA behind rate limits + docs + scopes; treat as a product.

## Rate limiting (shipped, first version)
`core/ratelimit.py` adds a **per-process fixed-window** abuse guard as ASGI middleware, ahead of tenant/route work:
- **`/auth/*` — strict, keyed by client IP** (`X-Forwarded-For` first hop behind the trusted proxy, else the peer). Bounds credential-stuffing / brute-force *before* authentication, where a per-token key is useless.
- **everything else — keyed by the bearer token** (SHA-256, first 16 bytes, never stored raw) falling back to client IP.

Both tiers are env-tunable (`rate_limit_enabled`, `rate_limit_per_min` = 300, `rate_limit_auth_per_min` = 20); a limit `<= 0` disables that tier; `rate_limit_enabled = False` disables both. Over-limit → `429` with a `Retry-After` header. Health/readiness/metrics probes are exempt so probes are never throttled.

**Deliberate scope + honesty:** the counter is **per process**, so with *N* API replicas the effective global ceiling is *N ×* the limit. This is a coarse first-line guard with **zero added infrastructure** — the right default now. A *precise global* limit needs a shared store (Redis / Postgres) and is the documented scale path, taken when a metric (distributed abuse slipping under the per-replica ceiling) forces it. Deferred with it: **scoped API keys** (a non-user principal — routes currently assume `current` is a real user row, e.g. `employee_id = current.id`), API **versioning policy** beyond the `/api/v1` prefix, and **published OpenAPI docs** as a supported public contract.

## Revisit when
A partner ecosystem needs richer query flexibility (consider a GraphQL read layer *alongside* REST), or internal service extraction needs gRPC between services; **or distributed abuse slips under the per-replica rate ceiling → move to a shared-store limiter and ship scoped API keys.**
