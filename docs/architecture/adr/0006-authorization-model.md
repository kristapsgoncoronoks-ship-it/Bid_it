# ADR-0006 — Role hierarchy + permission matrix + module gating

**Status:** Accepted

## Context
We need authorization that is simple to reason about, configurable per tenant, plan-aware, and that structurally prevents a company user from gaining system-wide power.

## Selected approach
Four **company-scoped roles** (low→high): `user` (read-only) < `processor`/`user` (day-to-day) < `admin` (business admin) < `owner` (company top role, **not** a system admin), plus a **separate `is_platform_admin`** operator flag that always outranks company roles and is never a company role. Layered checks:
1. Role rank + a sysadmin-configurable **permission matrix** (`role_permissions`, sane defaults) enforced centrally (`has_perm`/`PERM_BY_ENDPOINT`).
2. **Module gating** (`modules.require_enabled`, plan-gated).
3. **Usage quotas** (`access.enforce_*`).
4. **Segregation of duties** (e.g. no self-approval of expenses).
All decisions use server-derived user + tenant context, fail-closed.

## Alternatives considered
- **Full RBAC/ABAC engine (e.g. OPA)** — powerful, but heavy for four roles; premature.
- **Hard-coded role checks scattered in routes** — unconfigurable, easy to drift.
- **A single "admin" role** — can't express read-only auditors or the company-vs-platform distinction the product requires.

## Why appropriate
Matches the product's real personas (owner/admin/processor/read-only + operator) with the minimum machinery; the matrix gives tenants configurability; module + quota gating express commercial packaging. Centralised enforcement avoids drift.

## Risks
- Matrix misconfiguration → safe defaults + audit of changes.
- Role sprawl if we add bespoke roles → resist; extend the matrix, not the role set.

## Revisit when
Enterprise needs custom/fine-grained permissions or externalised policy (introduce a policy engine behind the same `has_perm` seam), or SCIM-driven role provisioning arrives.
