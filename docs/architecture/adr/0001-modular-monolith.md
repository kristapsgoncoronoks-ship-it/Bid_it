# ADR-0001 — Modular monolith over microservices

**Status:** Accepted

## Context
A small team must own a financial-data SaaS for years. We need transactional integrity across invoices/audit/jobs, low operational cost, and the ability to evolve modules independently — without the distributed-systems tax.

## Selected approach
A **modular monolith**: one deployable codebase, strict internal module boundaries (see [domain-modules](../domain-modules.md)), one Postgres, cross-module coupling only through defined seams (events, queue, audit). Two entrypoints from one image: API and Worker.

## Alternatives considered
- **Microservices per domain** — independent scale/deploy, but network calls, distributed transactions, eventual consistency, and multiplied ops for a small team.
- **Serverless functions** — fine for spiky glue, poor fit for a stateful transactional core with a durable queue.
- **Single unstructured monolith** — cheap now, unmaintainable later (no boundaries).

## Why appropriate
Financial data wants ACID and one source of truth; a monolith gives it for free. One deploy, one image, one DB to back up and reason about. Module boundaries preserve the *option* to split the few natural seams (extraction, notifications, analytics) later without paying for it now.

## Risks
- Boundary erosion over time (sideways imports) → mitigated by the dependency rules + review.
- A single scaling ceiling on the API/DB → mitigated by stateless replicas + read replicas + worker tiers.

## Revisit when
A specific module has a *proven, measured* need for independent scaling or an independent release cadence, and the team is large enough to operate a service — extract that one seam, not the whole system.
