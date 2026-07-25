# Authorization Policy Matrix

The single source of truth for **what a role may do**. Enforced in the backend by
`app/core/authz.py` (`authz.require(user, Permission.X)`), never by the UI alone.
**Deny-by-default:** a role grants exactly the permissions listed below and
nothing else; an unlisted permission is denied.

Tenant isolation (which *org* a user may touch) is a separate, always-on layer
(request auth + the ORM tenant guard + Postgres RLS — see
`cross-tenant-isolation-report.md`). This matrix answers only "may this user
perform this action", assuming the object is already in their org.

## Roles

| Role | Purpose |
|---|---|
| Organization Owner | The company's principal — full administration incl. billing |
| Administrator | Full business administration; no billing |
| Finance Manager | Runs finance: full money surfaces + approve, send, export, audit-read |
| Accountant | Books invoices/expenses/issuing + export; no approve/send/admin |
| Approver | Approves expenses; otherwise read-only |
| Employee | Submits own expenses; reads invoices |
| Auditor | Read-everything for assurance + audit log + export |
| Read-only User | Pure read of the money surfaces |

## Matrix (✓ = granted)

| Permission | Owner | Admin | Finance Mgr | Accountant | Approver | Employee | Auditor | Read-only |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| invoice.read     | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| invoice.write    | ✓ | ✓ | ✓ | ✓ |   |   |   |   |
| invoice.delete   | ✓ | ✓ | ✓ |   |   |   |   |   |
| expense.read     | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| expense.write    | ✓ | ✓ | ✓ | ✓ |   | ✓ |   |   |
| expense.approve  | ✓ | ✓ | ✓ |   | ✓ |   |   |   |
| issued.read      | ✓ | ✓ | ✓ | ✓ |   |   | ✓ | ✓ |
| issued.write     | ✓ | ✓ | ✓ | ✓ |   |   |   |   |
| issued.send      | ✓ | ✓ | ✓ |   |   |   |   |   |
| report.read      | ✓ | ✓ | ✓ | ✓ | ✓ |   | ✓ | ✓ |
| export.run       | ✓ | ✓ | ✓ | ✓ |   |   | ✓ |   |
| audit.read       | ✓ | ✓ | ✓ |   |   |   | ✓ |   |
| member.read      | ✓ | ✓ |   |   |   |   |   |   |
| member.manage    | ✓ | ✓ |   |   |   |   |   |   |
| role.assign      | ✓ | ✓ |   |   |   |   |   |   |
| settings.manage  | ✓ | ✓ |   |   |   |   |   |   |
| billing.manage   | ✓ |   |   |   |   |   |   |   |

The runtime source is `ROLE_PERMISSIONS` in `app/core/authz.py`; this table is
generated from the same data (`GET /api/v1/auth/authz-matrix`) and the test
`test_every_role_is_in_the_matrix` keeps them in lock-step.

## How it works today (no schema change yet)

The stored role is still the 4-tier ladder (`owner/admin/user/user_free`). It is
mapped onto the business roles so this layer works on current accounts:

| Stored role | Business role |
|---|---|
| owner | Organization Owner |
| admin | Administrator |
| user | Employee |
| user_free | Read-only User |

Plus two bridges:
- `is_platform_admin` (cross-tenant operator) → **all** permissions.
- `is_expense_approver` (per-user flag) → additively grants `expense.approve`,
  bridging today's approver onto the Approver role.

The resolver is **forward-compatible**: once the role model expands to store the
eight business-role values directly (the multi-org membership slice), they resolve
as-is with no change to this matrix or the route guards.

**Directly assignable today:** Owner, Administrator, Employee, Read-only (the four
stored values), plus Approver via the flag. Finance Manager, Accountant, and
Auditor are fully defined and enforced here, and become directly assignable when
the stored role vocabulary expands.

## Using it in a route

Authorization is **structural** (ADR-0024): a route declares its permission on
the router, and CI proves total coverage in both directions.

```python
from fastapi import APIRouter, Depends

from app.api.deps import require_perm
from app.core.authz import Permission

router = APIRouter(
    prefix="/vendors",
    tags=["vendors"],
    dependencies=[Depends(require_perm(Permission.INVOICE_READ))],  # router default
)


@router.post("", dependencies=[Depends(require_perm(Permission.INVOICE_WRITE))])
async def create_vendor(...):  # stricter per-route override — both gates run
    ...
```

## How it is enforced

- **The factory** — `app/api/deps.py::require_perm(*permissions)` returns an
  introspectable dependency that calls `authz.require` (which resolves through
  `authz.permissions_for`, the single resolver). It carries its declaration
  under `authz.PERMISSIONS_ATTR` so tooling can read it without executing
  anything. A denial is a `403 {"detail","code"}`.
- **The allow-list** — `app/core/authz.py::PUBLIC_ROUTES` is the explicitly
  reviewed set of `(method, path)` pairs that legitimately carry no permission
  (auth bootstrap, signature-authenticated webhooks, SCIM's own bearer, and
  authenticated self-service endpoints). Every entry carries a reason **in the
  structure itself**; a reason-less entry fails CI.
- **The coverage test** — `backend/tests/test_authz_coverage.py` asserts BOTH
  directions on every CI run: (forward) every route declares a permission or is
  allow-listed with a reason — an unclassified route fails CI by name; (reverse)
  every allow-list entry resolves to a live route — no stale classifications.
  The checker is proven by a self-test on a scratch app.
- **Platform-operator routes** (`/platform/*`, the `/access/matrix` write) are
  stricter than any tenant permission and declare the `authz.PLATFORM_ADMIN`
  sentinel through the same introspection attribute.
- **Fixture discipline** — when gating breaks a test whose role should never
  have had access, the fixture's privilege is raised; an assertion is never
  weakened (ADR-0024 §6).

## Beyond the matrix: two-person controls (permission ∧ different user)

Some actions are deliberately **stricter than any single permission** — holding
the permission is necessary but not sufficient (segregation of duties, §4.8):

| Action | Permission required | Additional rule |
|---|---|---|
| Approve a vendor bank-detail / tax-id change request | `settings.manage` | Approver **must not** be the requester — `requested_by == approver` is 403 `maker_is_checker`, enforced in `services/vendors.py::approve_change` regardless of role (even the Owner cannot approve their own request). |
| Approve a supplier invoice | `invoice.approve` | Submitter cannot approve their own invoice. |
| Approve an expense report | `expense.approve` | Claimant cannot approve their own report. |

The vendor rule exists because a single compromised account must never be able
to both **plant** a new payee IBAN and **activate** it — the whole point of the
WO-2 payment-redirection control. The UI hides the Approve button for the
requester, but the server check is the control; the frontend is cosmetic.
