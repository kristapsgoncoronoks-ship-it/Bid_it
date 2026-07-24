# ADR-0017 — DB-backed feature flags (modules + settings)

**Status:** Accepted

## Context
We need per-tenant capability toggles (commercial packaging), behavioural switches (validation, AI opt-in, scheduler), and safe rollout of new capabilities — without over-building an experimentation platform.

## Selected approach
**DB-backed, tenant-scoped flags** in two forms:
1. **Module switches** (`module_<key>` app_settings) — capability on/off per tenant, **plan-gated** — the primary product-flag surface.
2. **App settings** — per-tenant behavioural toggles (validation on/off, AI opt-in, backup interval, scheduler on/off).
New capabilities ship **default-off**; AI paths are **opt-in**. Flag reads are cheap and auditable.

## Alternatives considered
- **Third-party flag service (LaunchDarkly/Unleash)** — great for %-rollouts/experiments, but an external dependency + residency questions for a need we don't yet have (per-tenant on/off, not %-experiments).
- **Environment/config flags** — not tenant-scoped, need a deploy to change.
- **Hard-coded on/off** — inflexible, unsellable packaging.

## Why appropriate
The module/settings mechanism already exists, is tenant-scoped, plan-aware, and auditable — exactly what packaging + safe rollout need. It avoids an external system while keeping flags first-class product data.

## Risks
- No native %-rollout / targeting → add later if experimentation becomes a real need.
- Flag sprawl → catalog + review; remove dead flags.

## Revisit when
We need statistical experiments, gradual %-rollouts, or targeting rules across tenants — introduce a dedicated flag/experiment service behind a `flags` read seam, keeping module gating as-is.
