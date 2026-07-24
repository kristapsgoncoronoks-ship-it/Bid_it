# Multi-Org Membership & Organization Switching — Migration Plan

## The fork

Today identity and membership are **fused**: a `User` row has a single `org_id`,
a single `role`, and a **globally-unique email**. A person is one row in one org.
"Organization switching" and multi-org "membership management" require splitting:

- **`User`** = global identity (email, password, verification, name).
- **`Membership`** = `(org_id, user_id, role, is_expense_approver, status)` — the
  tenant relationship. A person can hold several.
- The **active org** moves into the request (the session), resolved and
  membership-checked per request.

`user.org_id` and `user.role` are read across the *entire* codebase (deps, the
tenant guard, authz, team, every route). A big-bang rewrite would be high-risk, so
this uses **expand/contract**: add the new structure, dual-read, migrate readers,
then drop the old columns — each step shippable and reversible.

## Invariants preserved throughout

- Tenant isolation never weakens: `current_org` still drives the ORM guard + RLS;
  the only change is *where* `current_org` comes from (session-resolved active
  membership instead of `user.org_id`).
- Deny-by-default: switching to an org you're not a member of is a 404/403, never
  a leak. The active org is always verified against a live membership.
- One login per person (email stays globally unique). Joining a second org adds a
  *membership*, never a second identity.

## Sub-slices

### 6a — `memberships` table + backfill  ✅ (this slice)
Add the table; backfill exactly one membership per existing user
(`org_id/role/is_expense_approver` copied, `status` = active/suspended from
`is_active`). Tenant-scoped → RLS + ORM guard + registration. **Nothing reads it
yet** — additive, zero behaviour change. A test proves the 1:1 backfill.

### 6b — writes create memberships
- `accept-invite` for an **existing** email creates a *membership* in the inviting
  org instead of erroring on the unique-email conflict (the multi-org join).
- Register / invite-new-user create both the user and their first membership.
- `memberships` service (`for_user`, `get`, `ensure`, role/status mutators).
Still dual-read: `user.org_id/role` remain the active projection.

### 6c — active org in the session + switching
- `sessions.org_id` already exists; make it the **active org** and add
  `POST /auth/switch-org/{org_id}` (verify membership → new session/token for that
  org) + `GET /auth/organizations` (my memberships).
- `deps` resolves `current_org` from the **session's** org (verified against a live
  membership) instead of `user.org_id`; `authz.business_role` reads the active
  membership's role. FE org switcher.

### 6d — migrate remaining readers
Sweep the few places still reading `user.org_id`/`user.role` directly onto the
active-org / active-membership accessors (most already flow through `deps`).

### 6e — contract
Drop `users.org_id` and `users.role` (now fully derived). Email stays unique.
Final migration; the isolation + authz test suites are the safety net.

## Rollback

Each sub-slice is independently revertible until 6e. 6a–6d only *add* structure and
dual-read; the old columns remain authoritative until 6e flips the source of truth.
The cross-tenant isolation suite (`test_cross_tenant_isolation.py`) and the authz
suite run at every step — a regression in isolation or permission resolution fails
the build before it ships.
