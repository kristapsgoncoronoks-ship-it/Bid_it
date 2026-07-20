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

## Revisit when
A partner ecosystem needs richer query flexibility (consider a GraphQL read layer *alongside* REST), or internal service extraction needs gRPC between services.
