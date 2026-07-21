# Architecture Decision Records

Each ADR records one significant decision in a fixed shape:

- **Context** — the forces at play.
- **Selected approach** — what we do.
- **Alternatives considered** — what we rejected.
- **Why appropriate** — the rationale for a 5-year-owned SaaS.
- **Risks** — what could go wrong.
- **Revisit when** — the trigger that reopens the decision.

Status values: **Accepted** (in effect), **Proposed** (agreed direction, not yet built), **Superseded**.

| # | Title | Status |
|---|---|---|
| [0001](./0001-modular-monolith.md) | Modular monolith over microservices | Accepted |
| [0002](./0002-backend-stack.md) | FastAPI + async SQLAlchemy + Pydantic v2 | Accepted |
| [0003](./0003-postgres-primary-datastore.md) | PostgreSQL as the single primary datastore | Accepted |
| [0004](./0004-tenant-isolation.md) | Shared-schema tenant isolation + ORM guard (+ RLS) | Accepted |
| [0005](./0005-authentication-jwt.md) | Stateless JWT authentication | Accepted |
| [0006](./0006-authorization-model.md) | Roles + permission matrix + module gating | Accepted |
| [0007](./0007-background-jobs.md) | DB-backed durable job queue over Celery/Redis | Accepted |
| [0008](./0008-object-storage.md) | S3-compatible object storage for documents | Accepted |
| [0009](./0009-extraction-pipeline.md) | Deterministic-first extraction, opt-in AI | Accepted |
| [0010](./0010-money-tax-fx.md) | Decimal money + ECB FX provenance + VAT engine | Accepted |
| [0011](./0011-idempotency-concurrency.md) | Idempotency keys + guarded/optimistic concurrency | Accepted |
| [0012](./0012-audit-log.md) | Hash-chained append-only audit log | Accepted |
| [0013](./0013-billing-metering.md) | Merchant-of-record billing + usage metering | Proposed |
| [0014](./0014-search.md) | Postgres full-text search before a search engine | Proposed |
| [0015](./0015-api-strategy.md) | REST + OpenAPI, versioned API strategy | Accepted |
| [0016](./0016-config-secrets.md) | Env config + envelope-encrypted secrets (KMS) | Accepted |
| [0017](./0017-feature-flags.md) | DB-backed feature flags (modules + settings) | Accepted |
| [0018](./0018-notifications.md) | Notifications via webhooks + email on the queue | Accepted |
| [0019](./0019-retention-legal-hold.md) | Data retention windows + legal hold | Accepted |
| [0020](./0020-gdpr-erasure.md) | GDPR right-to-erasure respecting statutory retention | Accepted |
| [0021](./0021-sso-scim.md) | Enterprise SSO (OIDC → SCIM → SAML), fixtures boundary | Accepted (OIDC) |

**To add an ADR:** copy the shape above, take the next number, link it here. Never edit an Accepted ADR's decision in place — supersede it with a new one and mark the old one Superseded.
