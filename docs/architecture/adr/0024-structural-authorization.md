# ADR-0024 — Structural authorization: declared router permissions + both-direction CI coverage

**Status:** Accepted — implemented across all 38 route modules (WO-1).

## Context
Authorization used to be an **imperative call inside each handler** — `authz.require(current, Permission.X)` written by hand, route by route. Coverage was therefore a per-route discipline with no structural guarantee, and the discipline had already failed in the highest-consequence place in the system: `POST /vendors` and `PATCH /vendors/{id}` carried **no permission check at all**, and they set the `iban` that `services/sepa.py::payment_run_sepa` pays out to. Fixing routes by hand fixes an instance; this ADR fixes the class: an unclassified route cannot ship.

## Decision
1. **Declaration is a dependency.** `app/api/deps.py::require_perm(*permissions)` returns an introspectable dependency (`PermissionDependency`) used as `APIRouter(dependencies=[Depends(require_perm(Permission.X))])`, with per-route overrides for stricter verbs (both run — AND semantics, so an override can only tighten). Enforcement calls the existing `authz.require`, which resolves through `authz.permissions_for` — the **single resolver**; nothing is duplicated. A denial is the same `403 {"detail","code"}` wire shape as before.
2. **Placement.** The factory lives in `app/api/deps.py`, NOT in `app/core/authz.py`: a FastAPI dependency must resolve `CurrentUser` (an `app.api.deps` symbol) in its signature, and `core` must not import `app.api` (`tests/test_boundaries.py`, AST-based — even a function-local import would be flagged). What `core/authz.py` owns is the **introspection contract**: `PERMISSIONS_ATTR` (the attribute every gate dependency carries), `declared_permissions()`, the `PLATFORM_ADMIN` sentinel, and `PUBLIC_ROUTES`. CI and tooling depend only on core.
3. **Platform gate.** `routes/platform.py::require_platform_admin` (and the in-handler check on `PUT /access/matrix/{role}`) is **stricter than any tenant permission** — an org owner holds every `Permission` yet must never pass. It therefore declares the `PLATFORM_ADMIN` sentinel via the same `PERMISSIONS_ATTR`, so the coverage test recognises the route as classified without pretending a tenant permission exists.
4. **`PUBLIC_ROUTES`** (`app/core/authz.py`) is the small, explicitly-reviewed allow-list of `(method, full path)` pairs that legitimately carry no permission. Each entry carries a one-line **reason in the same structure** — an entry with no reason fails CI. Three legitimate categories only: (a) public bootstrap / token-credentialed endpoints (register, login, reset, invite, SSO — the token is the credential); (b) webhook receivers with their own authentication (Stripe signature, EveryPay reference verification, the inbound-email mandatory shared secret, SCIM's own bearer); (c) authenticated **self-service** endpoints that touch only the caller's own account/session/usage (`/auth/me`, sessions, `/access/usage`, own bank details).
5. **Both-direction CI coverage** (`tests/test_authz_coverage.py`):
   - *Forward:* every route either declares a permission or has a reasoned `PUBLIC_ROUTES` entry — an unclassified route fails CI **by name**.
   - *Reverse:* every `PUBLIC_ROUTES` entry resolves to a live route — the allow-list can never hold stale/phantom classifications (the Fleet Fuel `share_revoke` defect: classified in two structures, existing in none).
   - The checker proves itself on a scratch app (never a fixture route on the real app), and asserts a minimum route count so a silently-broken enumeration cannot vacuously pass.
6. **Test discipline.** When declaring a permission breaks a test, the triage order is: (1) endpoint legitimately open → reasoned `PUBLIC_ROUTES` entry; (2) the declared permission contradicts the matrix for a role that genuinely should pass → fix the **declaration**; (3) the fixture's role should never have had access → **raise the fixture's privilege — never weaken the assertion**, and record the raise. Assertions are never lowered, skipped or deleted to go green.

## Deliberate declaration choices (behaviour-preserving by design)
- **Metered capture stays open to every tier.** `POST /invoices`, `POST /invoices/upload` (+ status/retry/captures) and the email-intake inbox/confirm are the documented, quota-governed capture funnel — open to every billing tier including `user_free` (see the in-code note on `create_invoice` and `test_access`). They declare `INVOICE_READ`, which **every** business role holds: the route is classified and authenticated, and behaviour is unchanged. The privileged operations (`validate` → `INVOICE_APPROVE`, `DELETE` → `INVOICE_DELETE`, `PATCH` → `INVOICE_WRITE`, payments → `PAYMENT_*`) declare their stricter permission.
- **Shared reference data declares `INVOICE_READ`** (currency/tax-code catalogues, FX rates/convert): held by every role, i.e. "any authenticated member", while catalogue management declares `SETTINGS_MANAGE`.
- **`GET /access/usage` stays self-service** (allow-listed): the free tier reads its own quota — asserted by `test_access.py::test_free_user_invoice_limit_enforced`, which is correct behaviour (triage rule 2). The limits **matrix** read/write is `SETTINGS_MANAGE` + the platform gate on write.
- **Analytics KPIs are `REPORT_READ`**, which READ_ONLY legitimately holds in the (unchanged) matrix — so the denied-role proof for those endpoints is EMPLOYEE, not `user_free`.
- **In-handler checks that are stricter than any permission remain**: expense approval keeps the assigned-approver + segregation-of-duties checks; `/access/matrix` write keeps the platform-operator check; expense/approval-policy admin keeps `is_admin_or_above` as defence in depth. Removed in-handler `authz.require` calls are only those exactly equal to (or weaker than) the declared dependency. **Nothing became more permissive.**

## Alternatives considered
- **Factory in `core/authz.py` with a local `app.api` import** — rejected: the boundary test walks the AST, so even a function-body import erodes the layering rule it exists to protect.
- **A middleware permission map** (path-pattern → permission table) — rejected: a parallel routing table drifts from the real router; the declaration belongs on the router object the framework actually serves.
- **Marker-only declarations** (attribute on the handler, enforcement still in-handler) — rejected: introspection without enforcement re-creates the original gap.

## Rollback / incident mitigation
The change is additive at the router layer and behaviour-preserving at the service layer — rollback is a revert. If a production issue appears and a revert is not immediately possible, the narrow mitigation is moving the offending `(method, path)` into `PUBLIC_ROUTES` with a reason of `"INCIDENT-<id>: temporarily de-gated, re-gate by <date>"` — CI stays honest, the route stays enumerated, and the exception leaves an audit trail. Never delete the coverage test to unblock a deploy.

## Revisit when
A1.5 makes the four currently-unreachable business roles storable (fixtures can then act as ACCOUNTANT/AUDITOR/… directly); A1.4 adds audit-coverage enforcement over the same introspection hook; any new transport (websocket, RPC) needs an equivalent structural gate.

## Follow-on
WO-2 (ADR-0025) builds the vendor bank-detail control on these gates: protected
vendor fields (`iban`, `tax_id`) are a **workflow, not a write** — a change
request approved by a *different* `SETTINGS_MANAGE` holder — and the approval
routes declare their permission structurally like every other route.
