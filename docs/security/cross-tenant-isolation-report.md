# Cross-Tenant Isolation — Test Report

**Property proven:** a user authenticated to **Organization B cannot READ, UPDATE,
EXPORT, or DOWNLOAD** any data belonging to **Organization A** — and cannot learn
whether A's objects even exist.

**Status: PASS** — `tests/test_cross_tenant_isolation.py` (5 tests) + the existing
`test_isolation.py` (5), `test_rls.py` (2), `test_tenancy.py` (9). Run:

```
python -m pytest tests/test_cross_tenant_isolation.py tests/test_isolation.py \
                 tests/test_rls.py tests/test_tenancy.py -v
```

## How isolation is enforced (defence in depth)

Every tenant request carries the caller's `org_id`, resolved once at the auth
choke point (`app/api/deps.py::get_current_user`) and never from client input:

1. **Per-route filter** — handlers load objects through a tenant-scoped query
   (`_load(db, org_id, id)`); a foreign id simply isn't found.
2. **ORM guard** (`app/core/tenant.py::_apply_tenant_scope`) — a `do_orm_execute`
   hook ANDs `org_id == current_org` onto *every* SELECT over a tenant model, so
   even a handler that forgets to filter cannot return another org's rows.
3. **Postgres RLS** — a `tenant_isolation` policy on every tenant table (GUC
   `app.current_org`), enforced at the database, backstops the app entirely.

Because the object is *not found* (not *forbidden*), a cross-tenant id returns an
**opaque 404**, identical to a nonexistent id — defeating object-id guessing.

## Test matrix — Org B against Org A's objects

Org A is seeded with one object of each protected kind; Org B is **fully
activated** (same modules enabled) so each assertion isolates the *tenant* check,
not a module gate.

| Verb | Surface exercised (Org A's id, called as Org B) | Expected | Result |
|---|---|---|---|
| **READ** | `GET /invoices/{id}`, `GET /issued/{id}`, `GET /expenses/{id}` | 404 | ✅ |
| **UPDATE** | `PATCH /invoices/{id}`, `POST /invoices/{id}/validate`, `PATCH /expenses/{id}`, `PATCH /expenses/{id}/items/{item}` | 404 | ✅ |
| **EXPORT** | `GET /issued/{id}/xml` (by-id e-invoice) | 404 | ✅ |
| **EXPORT** | `GET /export/accounting` (org-scoped ledger) — B's export contains **none** of A's rows (404 = empty) while A's contains them | scoped | ✅ |
| **DOWNLOAD** | `GET /issued/{id}/pdf`, `GET /expenses/{id}/pdf`, `GET /expenses/{id}/items/{item}/receipt` | 404 | ✅ |
| **ID-guessing** | cross-tenant id vs. random UUID → indistinguishable | both 404 | ✅ |
| **Integrity** | after B's attempts, A's invoice is byte-for-byte unchanged | unchanged | ✅ |

### Corroborating existing coverage (already in the suite)
- `test_isolation.py` — a fresh registrant sees zero of another company's invoices,
  expenses, analytics, team, or audit; delete of a foreign id is 404; joining a
  company requires an invitation; same-company-name registration never takes over.
- `test_rls.py` — the RLS migration covers **every** tenant table (asserted against
  `TENANT_MODELS`); a raw cross-tenant query is blocked at the DB (Postgres marker).
- `test_tenancy.py` — invite/accept lands the member in the same tenant; a
  suspended org blocks login; the last owner cannot be demoted.

## Scope & known boundaries (honest limits)
- **Background jobs** run unscoped and re-enter each tenant's scope per job
  (`set_current_org(job.org_id)`); a dedicated per-job isolation assertion is a
  recommended follow-up (see the gap analysis).
- **RLS-at-DB** is proven on Postgres; the CI run is on SQLite (the ORM guard is
  the enforced layer there), so `test_rls_blocks_cross_tenant_raw_query` is skipped
  off-Postgres by design.
- This report covers the primary financial objects (invoices, issued invoices,
  expense reports + receipts). Extending the same by-id matrix to every remaining
  resource route is mechanical and tracked as a follow-up.
