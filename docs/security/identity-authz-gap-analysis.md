# Identity, Org Management & Tenant Isolation — Audit & Gap Analysis

Ground-truth audit of the existing system against the requested first-version
scope. **Much already exists** (JWT auth, org registration, invitations, a
3-layer tenant guard, OIDC SSO, audit chain). This maps each requested item to
its real state so the remaining work is built as gaps, not duplicates.

Legend: ✅ present · 🟡 partial · ❌ missing

## Authentication & session

| Requested | State | Where / gap |
|---|---|---|
| User registration | ✅ | `auth/register` → creates org + owner |
| Login | ✅ | `auth/login` (bcrypt, region + org-status gates) |
| Logout | ✅ **Slice 4** | `POST /auth/logout` revokes the server-side session — the token dies immediately (FE logout calls it) |
| Email verification | ✅ **Slice 3** | `users.email_verified` + `auth_tokens` (hashed, single-use); `POST /auth/verify-email` + `/resend-verification`; opt-in login gate `require_email_verification`; FE `/verify-email` |
| Password reset | ✅ **Slice 3** | `POST /auth/forgot-password` (no user enumeration) + `/reset-password` (single-use, 1h); FE `/forgot-password` + `/reset-password` |
| Session management | ✅ **Slice 4** | `sessions` table; token carries `jti`; every request validates it; `GET /auth/sessions` lists active devices (FE `/sessions` page) |
| Session revocation | ✅ **Slice 4** | `POST /auth/logout`, `/sessions/revoke-others`, `DELETE /sessions/{id}`; password reset revokes ALL sessions |
| SSO prep (no enterprise SSO yet) | ✅ (exceeds) | `SsoConnection` model; **OIDC implemented**, SAML deliberately stubbed at the ACS boundary |

## Organization & membership

| Requested | State | Where / gap |
|---|---|---|
| Organization creation | 🟡 | Created at registration; **no "create another org"** for an existing user |
| Legal-entity creation | 🟡 | `issuer_profiles` = the selling legal entity (multi-issuer registry exists). No distinct multi-legal-entity-per-org tree |
| Organization invitations | ✅ | `team/invites` (+ preview, seat limits) |
| Invitation acceptance | ✅ | `auth/accept-invite` |
| Invitation **email** flow | ✅ **Slice 5** | `team.send_invitation_email` mails the accept link via `mailer`; invites now expire (+14d), preview returns 410 (expired) vs 404 (invalid/used) |
| **Organization switching** | ✅ **Slice 6c** | `GET /auth/organizations` + `POST /auth/switch-org/{id}` (membership-verified) + FE switcher; isolation preserved. The `Membership` fork (below) is landed through 6c |
| Membership management | 🟡 | List members, change role, deactivate — all within a single org |
| Role assignment | 🟡 | Works, but only 4 roles (see below) |
| Account deactivation | ✅ | `is_active` + `USER_DEACTIVATE` audit |

### The architectural fork: single-org-per-user vs. multi-org membership
Today `User` has **one `org_id`** and a **globally-unique email** — a user
belongs to exactly one org, and identity == membership. The requested
**"organization switching"** and rich **"membership management"** require
splitting these:

- `User` = global identity (email, password, verification, sessions)
- `Membership` = `(user_id, org_id, role, status)` join table
- the active org moves into the **session**, resolved per request

This is a large, high-blast-radius migration (`user.org_id` / `user.role` are
read across the whole codebase). It is the single biggest gate on the requested
scope and a genuine product decision — recommend a dedicated slice with its own
plan. Everything else below can proceed without it.

## Roles

Requested 8 business roles vs. existing 4-tier ladder + flag:

| Requested | Maps to today |
|---|---|
| Organization Owner | `owner` ✅ |
| Administrator | `admin` ✅ |
| Finance Manager · Accountant · Approver · Employee · Auditor · Read-only | ❌ — collapse into `user`/`user_free` + `is_expense_approver` |

Gap: a real **role → permission matrix** (the 8 roles × capability grid). Note
`RolePolicy` is the *usage-limits* matrix (monthly upload/invoice caps), **not**
an authorization matrix — authz today is ad-hoc `is_admin_or_above` checks in
routes. Deny-by-default holds structurally (unlisted → no grant) but isn't
expressed as one central policy.

## Authorization requirements

| Requirement | State |
|---|---|
| Deny by default | 🟡 structural (per-route guards); not a central matrix |
| Verify org membership every request | ✅ `deps.get_current_user` + tenant scoping |
| Enforce in backend, not just UI | ✅ |
| Prevent object-ID guessing | ✅ opaque 404 — **proven** (isolation report) |
| Prevent cross-org relationships | ✅ composite FKs `(org_id, fk)→parent(org_id,id)` |
| Protect background jobs with tenant context | ✅ jobs re-enter `set_current_org(job.org_id)` |
| Protect file downloads | ✅ **proven** |
| Protect exports | ✅ **proven** |
| Audit sensitive actions | ✅ `audit.A.*` (login, role change, deactivate, invite, downloads…) |
| Automated cross-tenant isolation tests | ✅ **delivered** — `test_cross_tenant_isolation.py` + report |

## "Create" deliverables

| Deliverable | State |
|---|---|
| Authorization policy matrix | ✅ **Slice 2** — `authorization-policy-matrix.md` + `ROLE_PERMISSIONS` (8 roles × capability grid, deny-by-default) |
| Reusable authorization service | ✅ **Slice 2** — `app/core/authz.py` (`Permission` enum, `require`/`has`/`permissions_for`); `GET /auth/permissions` + `/auth/authz-matrix` |
| Organization middleware | ✅ `TenantScopeMiddleware` + `deps` |
| Security-sensitive audit events | ✅ set exists; extend for verification/reset/session-revoke when built |
| Admin screens for members & roles | 🟡 `Team.tsx` / `Access.tsx` / `Settings.tsx` exist |
| Invitation email flow | 🟡 (token created; email send missing) |
| Empty / loading / invalid-token / expired / permission-denied states | ✅ **Slices 3+5** verify/forgot/reset (loading/invalid/expired/success); AcceptInvite distinguishes loading / invalid / **expired (410)**; sessions page (loading/error/empty) |

## Recommended sequence (each an additive, tested slice)

1. **✅ done** — cross-tenant isolation proof (tests + report).
2. **✅ done** — authorization service + policy matrix (`app/core/authz.py`, 8-role
   grid, deny-by-default; export guard migrated; `GET /auth/permissions` for the UI).
   Remaining ad-hoc role guards migrate onto `authz.require` incrementally.
3. **✅ done** — email verification + password reset (`auth_tokens` hashed/single-use,
   `mailer` send, opt-in login gate, FE verify/forgot/reset pages with
   loading/invalid/expired/success states).
4. **✅ done** — sessions + revocation (`sessions` table, `jti`-bound tokens,
   logout / revoke-others / revoke-one, reset revokes all, FE `/sessions` page).
5. **✅ done** — invitation email send + expiry (+14d, 410 preview) + FE
   loading/invalid/expired states.
6. **In progress** — multi-org membership + org switching *(the fork)*. Sequenced
   in `multi-org-membership-plan.md` (expand/contract). **6a done:** `memberships`
   table + backfill. **6b done:** every user-creation path dual-writes a membership
   (register/accept/SSO/SCIM), and an existing account can be **invited into a
   second org** (password-verified multi-org join); inviting an existing member →
   409. **6c done:** `GET /auth/organizations` + `POST /auth/switch-org/{id}`
   (membership-verified, opaque 404 otherwise) — switching repoints the active
   org/role, tenant scoping + authz follow, isolation preserved; unscoped auth
   deps for the cross-org endpoints; FE org switcher. Next: 6d (migrate readers
   off `user.org_id`/`role`), 6e (drop the columns).

Slice 2 is the highest-value next step: it makes deny-by-default explicit and is
the backbone the rest of the roles work hangs on.
