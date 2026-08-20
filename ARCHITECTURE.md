# InvoiceIQ — Architecture (pointer)

The detailed architecture documentation lives under **[`docs/architecture/`](./docs/architecture/)**.
This file is deliberately a one-page pointer: the long-form document that used to
live here described the project's earliest prototype (a dozen tests, 4 routers) and drifted
fatally from the codebase (64 tables, 1136 backend tests, 41 route modules).
A lying document is worse than no document — the detail now lives where it is
kept true.

Start here, in order:

1. **[Overview](./docs/architecture/overview.md)** — stance, requirement
   challenges, the system at a glance. The authoritative architecture reference.
2. **[ADR index](./docs/architecture/adr/README.md)** — 29 Architecture Decision
   Records; **the specification**. Notable: 0001 modular monolith · 0004 tenant
   isolation · 0010 money/FX/VAT · 0012 hash-chained audit · 0023 bounded
   contexts + transport seam · 0024 structural authorization · 0025 vendor
   bank-detail control · 0026 one validation engine / one FX convention ·
   0027 AI capture policy · 0028 RLS unscoped-GUC sticky empty string ·
   0029 reclaimable-VAT figure.
3. **[Domain modules](./docs/architecture/domain-modules.md)** — data ownership
   per module and the seams between them.
4. **[Data model](./docs/architecture/data-model.md)** — tables, keys, tenancy
   scoping.
5. **[Data flows](./docs/architecture/data-flows.md)** ·
   **[Security boundaries](./docs/architecture/security-boundaries.md)** ·
   **[Foundation](./docs/architecture/foundation.md)** ·
   **[Engineering rules](./docs/architecture/engineering-rules.md)** ·
   **[Deployment](./docs/architecture/deployment.md)**.

Product context: **[docs/product/](./docs/product/)** (PRD, personas, pricing
hypothesis, metrics, risks, workflows). Security: **[docs/security/](./docs/security/)**.
Milestone gate: **[docs/M0-exit-gate.md](./docs/M0-exit-gate.md)**.

Layering (machine-enforced by `backend/tests/test_boundaries.py`):

```
models  →  core  →  services  →  api        (one-way; React SPA consumes the API)
```
