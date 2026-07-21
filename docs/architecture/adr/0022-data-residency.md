# ADR-0022 — Data residency / region-pinning

**Status:** Accepted — app-layer seam implemented; the multi-region data plane is a deployment concern (see boundary).

## Context
Enterprise/EU buyers require a tenant's data to stay in a named region (EU vs US, etc.). True residency is delivered by the **infrastructure** — a region-local database, object storage, and backups per region, with the load balancer routing each tenant to its region. But the application needs a first-class notion of a tenant's region and a **backstop** so a misrouted request fails closed instead of reading/writing data in the wrong jurisdiction.

## Selected approach
- **`organizations.region`** — every tenant is pinned to a region, assigned at registration from `default_tenant_region` (→ `service_region` by default). Region is exposed on the org (`/auth/me`, UI badge).
- **`service_region`** — the region THIS deployment's data plane serves.
- **Enforcement backstop** (`core/residency.assert_region`, off by default via `enforce_region_pinning`): on every authenticated request (`get_current_user`) and at login, a tenant pinned to a **different** region than `service_region` is refused with **421 Misdirected Request** + an `X-Tenant-Region` header. The load balancer does the real routing; this guarantees a bug/misroute can't silently serve cross-region.
- **Off by default** = single-region, byte-identical behaviour; the check short-circuits before any extra work, so single-region deployments pay nothing.

Object-storage residency is achieved by each regional deployment pointing at its **own region-local bucket** (`storage_s3_bucket` / `storage_local_path`); combined with the enforcement backstop (only same-region tenants are served), a region's bucket only ever holds that region's data — no per-key region tagging needed.

## Alternatives considered
- **Single global region** — simplest, but fails EU residency requirements outright. This ADR keeps it as the *default* while making multi-region a config flip + infra.
- **Per-key region prefix in one shared bucket/DB** — weaker isolation (one blast radius, one jurisdiction physically), and doesn't satisfy "data does not leave the region." Region-local infra is the real control.
- **Region in the JWT** (avoid the per-request org fetch) — possible optimization, but region can change (a tenant relocation) and a stale token would then bypass the backstop; the PK fetch (only when enforcement is on) is cheap and always-correct.

## Risks / boundary
- **This is the app seam, not the data plane.** Real residency needs region-local Postgres + object storage + backups and LB routing per region — **deployment/infra work**, out of scope here. The backstop assumes the LB is doing the primary routing.
- **Tenant relocation** (moving a tenant between regions) is a deliberate data-migration operation, not a field flip; not modelled yet.
- **Cross-region platform operations** (a global operator view) must be designed to respect residency; today platform-admin queries are single-DB.

## Revisit when
Standing up the second region (wire region-local DB/storage/backups + LB routing, then flip `enforce_region_pinning`), building tenant relocation, or a global control plane over region-local data planes.
