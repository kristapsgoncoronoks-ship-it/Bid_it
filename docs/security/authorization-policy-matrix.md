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

```python
from app.core import authz

authz.require(current, authz.Permission.EXPORT_RUN)   # 403 if not granted
```

Migrated so far: `GET /export/accounting` (was an ad-hoc `is_admin_or_above`
check — behaviour-preserving: only Owner/Administrator/platform hold `export.run`).
Remaining ad-hoc role checks migrate onto `authz.require` incrementally; each
migration is behaviour-preserving for the four stored roles by construction.
