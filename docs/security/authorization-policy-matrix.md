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

## How it works today

`app.models.user.UserRole` (the stored role column) has **8** values since A1.5:
the original 4-tier ladder (`owner/admin/user/user_free`) plus the 4 remaining
business roles stored **directly** by their matrix name
(`finance_manager/accountant/approver/auditor`). The original 4 are mapped onto
the business roles so legacy accounts keep working unchanged:

| Stored role | Business role |
|---|---|
| owner | Organization Owner |
| admin | Administrator |
| user | Employee |
| user_free | Read-only User |

The 4 newly-reachable values need no mapping — `finance_manager`, `accountant`,
`approver`, `auditor` are spelled to match `Role`'s own string values, so
`business_role()`'s forward-compatible branch resolves them as-is.

Plus two bridges, unchanged by A1.5:
- `is_platform_admin` (cross-tenant operator) → **all** permissions.
- `is_expense_approver` (per-user flag) → additively grants `expense.approve`.
  This flag is **also** the *designated-approver* gate on
  `POST /expenses/{id}/decision` specifically (a stricter, in-handler check
  beyond the router's `expense.approve` dependency, matching the two-person
  control pattern below) — assigning the `approver` role grants the
  `expense.approve`/`invoice.approve` *permissions* everywhere else
  immediately, but a member still needs the flag appointed separately (same as
  an `admin` or `owner` needs it today) before they can decide a specific
  expense report. See `backend/tests/test_authz.py::
  test_approver_role_still_needs_the_expense_flag_to_decide`.

**Directly assignable today (all 8):** every business role is now reachable via
`PATCH /team/members/{id}` (`role.assign`) or `POST /team/invites` — both
endpoints are typed against the full `UserRole` enum, so the widening required
no endpoint change. The one deliberate exception: **federated identity**
(`app/services/oidc.py::_ASSIGNABLE`, SSO group→role mapping, and
`app/services/scim.py`'s SCIM default-role provisioning) still only maps to
the legacy 3 non-owner values — expanding IdP-driven default-role assignment
to the 4 new roles is a separate, un-scoped feature (a new group-naming
convention with its own tests), not a reachability gap.

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

### Master-data catalogs (WO-14)

The three cost-allocation master routers added in WO-14 follow the same pattern
as the tax-code and currency catalogs before them — reading is broad because the
masters feed pickers on invoice/expense forms, managing them is org
configuration:

| Route group | Read | Mutate |
|---|---|---|
| `/masters/departments` | `invoice.read` | `settings.manage` |
| `/masters/cost-centers` | `invoice.read` | `settings.manage` |
| `/masters/projects` | `invoice.read` | `settings.manage` |

(`/documents`, the tenant-wide registry of stored originals, remains
router-level `settings.manage` for reads too — it spans every user's uploads.)

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
| Approve a payment run (WO-9) | `payment.write` | The run's **creator** cannot approve it — 403 `maker_is_checker`, enforced in `services/payment_run.py` (immutable user id, with an email fallback covering pre-WO-9 runs), even for the Owner. |
| Mark a payment run paid (WO-9) | `payment.write` | Neither the run's **creator** nor its **approver** can pay it — three distinct people carry a payment from selection to settlement. The only exemption is a **platform admin** passing an explicit `override_sod=true`, which is audited as `payment_run.sod_override` naming the overridden control — never silent. |
| Export a payment-run bank file (CSV or SEPA, WO-9) | `payment.write` | Producing a payment instruction file is a treasury act, not a read: the run must be **approved or paid**, a **second** export needs `confirm_reexport=true` (409 `already_exported` otherwise, naming the first export's timestamp), a run with payees lacking an IBAN is refused **naming them** (409 `skipped_payees`) unless `acknowledge_skipped=true`, and every export is audited (`payment_run.exported`) with actor, MsgId, payee count and total. Reimbursement-batch exports apply the same export-once / skipped-payee rules under `expense.approve`. |

The vendor rule exists because a single compromised account must never be able
to both **plant** a new payee IBAN and **activate** it — the whole point of the
WO-2 payment-redirection control. The UI hides the Approve button for the
requester, but the server check is the control; the frontend is cosmetic.

## Permission ∧ entitlement: the partners router (WO-3)

Partner (issuing-counterparty) routes carry **two orthogonal gates, both live**:

- **Permission** — reads declare `issued.read`; every mutation (`POST /partners`,
  `PATCH /partners/{id}`, document upload and above all **document signing**,
  which unlocks whether an invoice may be issued at all) declares
  `issued.write`. Signing a contract/acceptance act is a commercial assertion:
  it requires `issued.write` and is audited (`partner.document_sign`) with the
  actor, the partner, the document kind and the signature date.
- **Module entitlement** — every partner route additionally requires the org's
  `issuing` module to be enabled. Entitlement answers "has this org bought/
  switched on the capability"; permission answers "may this member act". A
  fully-permissioned Owner in a non-issuing org is still refused (403 from the
  module gate), and an issuing-org Employee is refused by the permission gate.
  Neither check substitutes for the other.
