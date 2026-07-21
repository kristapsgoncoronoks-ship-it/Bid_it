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
| Logout | 🟡 | JWT is stateless → client discards token. **No server-side session** to invalidate |
| Email verification | ❌ | No `email_verified` / token flow |
| Password reset | ❌ | No forgot/reset flow |
| Session management | 🟡→❌ | Stateless JWT only; no session store, no "your active sessions" list |
| Session revocation | ❌ | Cannot revoke a live token (needs a session/`jti` denylist or a session table) |
| SSO prep (no enterprise SSO yet) | ✅ (exceeds) | `SsoConnection` model; **OIDC implemented**, SAML deliberately stubbed at the ACS boundary |

## Organization & membership

| Requested | State | Where / gap |
|---|---|---|
| Organization creation | 🟡 | Created at registration; **no "create another org"** for an existing user |
| Legal-entity creation | 🟡 | `issuer_profiles` = the selling legal entity (multi-issuer registry exists). No distinct multi-legal-entity-per-org tree |
| Organization invitations | ✅ | `team/invites` (+ preview, seat limits) |
| Invitation acceptance | ✅ | `auth/accept-invite` |
| Invitation **email** flow | 🟡 | Invitation row + token created, but **not emailed** — token is returned to the inviter (share-a-link). Needs a `notify` send |
| **Organization switching** | ❌ | **Architectural fork** — see below |
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
| Empty / loading / invalid-token / expired / permission-denied states | 🟡 `AcceptInvite.tsx` handles invalid; others partial |

## Recommended sequence (each an additive, tested slice)

1. **✅ done** — cross-tenant isolation proof (tests + report).
2. **✅ done** — authorization service + policy matrix (`app/core/authz.py`, 8-role
   grid, deny-by-default; export guard migrated; `GET /auth/permissions` for the UI).
   Remaining ad-hoc role guards migrate onto `authz.require` incrementally.
3. **Email verification + password reset** — a `verification_tokens`/`password_resets`
   table, `notify` email send, endpoints + FE states. Additive.
4. **Sessions + revocation** — a `sessions` table (or `jti` denylist) so logout,
   "your sessions", and revoke-all work. Additive.
5. **Invitation email send** + the FE empty/expired/permission-denied states.
6. **Multi-org membership + org switching** *(the fork)* — the `Membership` split.
   Biggest change; do last, with its own migration plan.

Slice 2 is the highest-value next step: it makes deny-by-default explicit and is
the backbone the rest of the roles work hangs on.
