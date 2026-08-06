# WO-76 — transport routes slice 1: the claim lifecycle over HTTP

> The head of the M3 route batch (`ARCH_plan.md` ADR-P3: `app/api/routes/
> transport/ — fuel.py, claims.py, recovery.py, excise.py, overcharges.py`;
> the TODO M3 cell's own "Remaining in M3: … the `api/routes/transport/*` +
> UI surface (every transport service is now built and route-ready)"). This
> slice ships `claims.py` — the claim lifecycle — and nothing else of that
> file list.

**WORK ORDER 76 — transport routes slice 1: the claim-lifecycle services
(get-or-create / list / detail, line materialization + read, submit,
withdraw, checklist + stage read) exposed as thin controllers under
`app/api/routes/transport/claims.py`, structurally gated on the EXISTING
`VAT_READ`/`VAT_WRITE`/`VAT_SUBMIT` permissions (ADR-P3 rule 5 — shipped by
WO-49, consumed by a route for the first time here). Effort M 3–5d.
Priority P1. Milestone M3. Depends on: WO-49 (grain + permissions), WO-52
(claim lines), WO-58/WO-73/WO-75 (the D5 gate chain), WO-60 (checklist),
WO-59 (stage), WO-1 (structural authz), WO-10 (tenancy parity).**

### Objective and business value

The gap, with verified evidence: every claim-lifecycle service is built and
self-gated (`app/services/transport/claim.py::get_or_create_claim`,
`claim_lines.py::build_claim_lines`, `lock.py::submit_claim`/
`withdraw_claim`, `checklist.py::submission_checklist`,
`status.py::derive_stage` — each opens with the `modules.is_enabled` →
`PermissionError(code="module_not_enabled")` block and an org-scoped opaque
404 fetch), but `app/api/routes/` contains no `transport/` package: `grep
-rn "transport" app/api/router.py` returns nothing, and sixteen
`tests/test_tenancy_parity.py` EXEMPT entries all carry the same reason —
*"no api/routes/transport/* route exists yet to drive an HTTP-level probe
through. Gains a probe with the first transport route."* This order is that
first transport route.

Who stops losing money: a VAT refund claim that can only be driven from a
Python shell is not a product — the operator who prepares, gates and files
the claim (the FINANCE_MANAGER of `authz.ROLE_PERMISSIONS`) needs the
lifecycle over HTTP before any M3 UI can exist, and the whole D5 refusal
vocabulary (`period_not_ended`, `below_minimum`, `customer_not_active`,
`unresolved_invoice_refs`, `duplicate_invoice_lock`,
`invoice_document_missing`) is only worth its fail-closed design if the
person fixing a draft claim can SEE the refusal code. Slice 1 covers
exactly the lifecycle a filing runs: create → materialize lines → read
checklist/stage → submit → (if needed) withdraw.

### The wire contract (verified against the services — no invented codes)

Every refusal below is raised by the SERVICE as an `AppError` and rendered
by the one `app.main` handler as `{"detail", "code"}` + `X-Request-ID`
(master-context §4.20 — the routes map nothing themselves, so the wire
shape cannot drift from the service vocabulary):

| Condition | HTTP | `code` (verbatim from the service) |
|---|---|---|
| transport module off | 403 | `module_not_enabled` |
| cross-tenant / unknown claim id | 404 | `claim_not_found` |
| unknown entity id | 404 | `entity_not_found` |
| malformed period | 422 | `invalid_period` |
| claim not draft (rebuild/submit) | 409 | `claim_not_draft` |
| period not ended (R7) | 409 | `period_not_ended` |
| below Art. 17 minimum (R8) | 409 | `below_minimum` |
| lifecycle not active (R44) | 409 | `customer_not_active` |
| country not activated (R44) | 409 | `country_not_activated` |
| synthetic line in claim (R3) | 409 | `unresolved_invoice_refs` |
| overlap with an existing lock (R6) | 409 | `duplicate_invoice_lock` |
| annual mop-up left empty (R6) | 422 | `empty_claim_set` |
| resolved line without a document (R10) | 409 | `invoice_document_missing` |
| mixed-currency line bucket (§4.14) | 422 | `claim_line_mixed_currency` |
| withdraw on a non-locking status | 409 | `claim_not_locked` |

(The prompt's suggested `activation_required`/`duplicate_claim`/
`missing_documents` slugs do not exist in the services; per master-context
§9 the ACTUAL codes above are used and the discrepancy is reported, not
papered over.)

### Design decisions (recorded, with rationale)

1. **Permissions: the EXISTING `VAT_READ`/`VAT_WRITE`/`VAT_SUBMIT`, no new
   members.** ADR-P3 rule 5 / WO-49 already added the transport permission
   vocabulary to `app/core/authz.py` and all 8 `ROLE_PERMISSIONS` rows,
   with the deliberate WRITE/SUBMIT split ("submitting a claim acquires
   invoice locks … mirroring how ISSUED_SEND is split from ISSUED_WRITE" —
   the ACCOUNTANT books claim data but cannot submit). Inventing a second
   `TRANSPORT_CLAIM_*` vocabulary would violate §10 (no invented
   functionality) and orphan the existing matrix rows. Declaration is
   structural (ADR-0024): router-level `require_perm(VAT_READ)`, per-route
   `VAT_WRITE` overrides on create/build-lines, `VAT_SUBMIT` on
   submit/withdraw — the `payment_runs.py` pattern, and
   `tests/test_authz_coverage.py` picks the declarations up unchanged.
2. **Router package with an aggregating `__init__` router.** ADR-P3 names
   `app/api/routes/transport/claims.py`; `tests/test_authz_coverage.py`
   enumerates `pkgutil.iter_modules(app.api.routes.__path__)` and reads
   each module's `router` attribute — so `app/api/routes/transport/
   __init__.py` exposes `router = APIRouter()` including `claims.router`,
   keeping the package's routes inside the structural-coverage net (a
   package without a top-level `router` would silently escape the CI
   check). `app/api/router.py` includes the package router once; future
   slices (`fuel.py`, `recovery.py`, …) add themselves to the package
   `__init__`, not to `api/router.py`.
3. **Thin routes; the two missing READ accessors land in the service
   layer.** No list/detail/lines-read service existed (`lock._get_claim`
   is private and un-gated by design — its callers gate first). Rather
   than put org-scoped selects + a route-local module gate into the
   controller, `claim.py` gains `list_claims`/`get_claim` and
   `claim_lines.py` gains `list_claim_lines` — each opening with the
   identical `modules.is_enabled` gate and opaque-404 fetch every other
   transport service entry point carries, so the transport rule "every
   service entry point gates the module itself" (WO-49's defense-in-depth
   note) stays true with routes now in front. Routes parse → authorize
   (structurally) → call the service → shape the response; zero business
   logic (engineering-rules §3).
4. **POST /transport/claims returns 200, not 201.** `get_or_create_claim`
   is deliberately idempotent on the grain (R1 — "same key upserts, never
   duplicates") and does not report whether it created; a 201 on the
   second call would be a lie on the wire. 200 + the (one) claim row both
   times, the body identical — the idempotency is the contract.
5. **Checklist/stage are GETs and commit nothing.** `submission_checklist`
   auto-seeds `DEFAULT_RULES` idempotently (flush, not commit); on a pure
   read the request session is discarded uncommitted, so a GET stays
   side-effect-free on the wire (the seed re-runs harmlessly until the
   first committing write persists it). This preserves "advisory never
   blocks/mutates" (§4.19) in its strongest observable form: reading the
   checklist or stage of a claim changes NOTHING about the claim — proven
   by test.
6. **`submit_claim`'s `today` test seam is NOT exposed over HTTP.** It
   exists so service tests can pin period-end deterministically; a client
   that could post its own `today` could bypass R7. The route always lets
   the service default to the real clock. Route tests use genuinely-past
   periods (2026-Q2) and genuinely-future ones (2027-Q4) instead.
7. **Mutating routes commit AFTER the service returns** (the
   `payment_runs` pattern); the services already audit in the same
   transaction (§4.16), so a refused submit rolls everything back —
   including the audit row that was never warranted — and the
   nothing-mutated D5 guarantee holds over HTTP exactly as at the service
   level (asserted by test: a 409 leaves status `draft`, zero locks, zero
   frozen lines).
8. **Decimal on the wire per the platform convention**: response schemas
   type money fields `Decimal` (`RunOut.total_eur` precedent) — pydantic
   v2 serializes them as JSON strings, never float (§4.9).
9. **Tenancy-parity EXEMPT → HTTP probes** for the two tables this slice
   makes route-readable: `vat_refund_claims` (list + by-id routes) and
   `vat_claim_lines` (lines read route, rows seeded directly per the
   `reimbursement_batches` precedent — the READ path is what the probe
   proves). The other transport EXEMPT entries stay (their rows are still
   not route-readable) but their now-false "no api/routes/transport/*
   route exists yet" wording is trued up to name what IS missing (a route
   that reads those rows).

### Scope

**In scope:**
- `app/api/routes/transport/__init__.py` + `claims.py` (**new**): 
  - `GET    /transport/claims` — list (VAT_READ)
  - `POST   /transport/claims` — get-or-create (VAT_WRITE)
  - `GET    /transport/claims/{id}` — detail (VAT_READ)
  - `GET    /transport/claims/{id}/lines` — materialized lines (VAT_READ)
  - `POST   /transport/claims/{id}/lines` — build/rebuild lines (VAT_WRITE)
  - `GET    /transport/claims/{id}/checklist` — advisory checklist (VAT_READ)
  - `GET    /transport/claims/{id}/stage` — derived 1A/1B/1C/1E stage (VAT_READ)
  - `POST   /transport/claims/{id}/submit` — the D5 gate chain (VAT_SUBMIT)
  - `POST   /transport/claims/{id}/withdraw` — release locks (VAT_SUBMIT)
- `app/schemas/transport_claim.py` (**new**) — `ClaimCreateIn`,
  `ClaimSubmitIn` (+`SubmitInvoiceIn`), `ClaimOut`, `ClaimLineOut`,
  `ChecklistItemOut`, `StageOut`.
- `app/services/transport/claim.py` — additive `list_claims`/`get_claim`.
- `app/services/transport/claim_lines.py` — additive `list_claim_lines`.
- `app/api/router.py` — include the transport package router.
- `tests/transport/test_wo76_claim_routes.py` (**new**) — the matrix below.
- `tests/test_tenancy_parity.py` — probes for `vat_refund_claims` +
  `vat_claim_lines`; wording truth-up on the remaining transport EXEMPT
  entries.
- Boards: `TODO.md` (WO-76 row + M3 cell + suite line),
  `docs/transport/rules.md` (R rows gaining a route consumer), README
  collected-test count.

**Out of scope (the anti-scope-creep clause):**
- `fuel.py` / `recovery.py` / `excise.py` / `overcharges.py` and any other
  transport route (later slices of the M3 route batch own them) — incl.
  routes for waivers, checklist-rule admin (`set_active`), note-overrides,
  status-code stamping (`set_status_code`), tie-out expectations,
  cadences, lifecycle transitions, claim workbook/evidence-pack downloads
  (WO-74's builders keep their "no route yet" status).
- Any SPA page (the M3 UI batch).
- G2.9 fee freezing/settlement (decision-gated).
- New permissions, new tables, new migrations — none needed.
- Exposing `override_minimum` behind a stricter permission than
  VAT_SUBMIT (no harvested rule assigns it one; recorded, not decided).

### Files to touch

| File | Change |
|---|---|
| `app/api/routes/transport/__init__.py` | **new** — package router aggregating the slice |
| `app/api/routes/transport/claims.py` | **new** — the nine thin routes |
| `app/schemas/transport_claim.py` | **new** — request/response schemas |
| `app/services/transport/claim.py` | additive `list_claims`, `get_claim` |
| `app/services/transport/claim_lines.py` | additive `list_claim_lines` |
| `app/api/router.py` | include `transport.router` |
| `tests/transport/test_wo76_claim_routes.py` | **new** — route tests |
| `tests/test_tenancy_parity.py` | 2 EXEMPT→probe conversions + reason truth-ups |
| `TODO.md`, `docs/transport/rules.md`, `README.md` | boards (final commit) |

### Implementation guidance

1. Service accessors first (`list_claims`/`get_claim`/`list_claim_lines`),
   each: module gate (`is_enabled` → `PermissionError`
   `module_not_enabled`, fails CLOSED — an un-entitled org must stay
   byte-identical to before the vertical existed, ADR-P3 rule 3) →
   org-scoped select (opaque 404 for by-id, §4.4). `list_claim_lines`
   orders `(invoice_ref, product_group)` — the build/freeze order.
2. Schemas: `ClaimOut` mirrors the model's own field vocabulary; money
   fields `Decimal | None` read from the frozen `Numeric(14,2)` columns —
   no recomputation anywhere in the route layer (§4.10 stays with the
   services that already enforce it).
3. Routes: router-level `Depends(require_perm(Permission.VAT_READ))`;
   `_WRITE`/`_SUBMIT` per-route override lists (the `payment_runs`
   convention). Handlers: unpack body → call service → build `*Out` —
   `AppError` propagates to the global handler untouched. Mutations
   `await db.commit()` on success only.
4. `POST {id}/submit` body: `invoices: list[{supplier, invoice_ref,
   fuel_transaction_id}]` (schema `min_length=1` — shape at the boundary;
   the service's own `empty_claim_set` refusal stays as the in-depth
   check, DoD §3) + `override_minimum: bool = False`.
5. Parity probes: enable the transport module for BOTH orgs via
   `modules.set_enabled` on `ctx.db` (the module is deliberately in no
   plan yet — `PLANS` excludes it, so the HTTP toggle would 402; direct
   enablement is test setup, the read path under proof stays HTTP), then
   drive claims entirely over HTTP with IDENTICAL bodies; seed
   `vat_claim_lines` rows directly (the `reimbursement_batches`
   precedent) and read them through the route.

### Invariants this order must preserve

- **§4.4 (opaque 404):** every by-id route reaches the service's
  org-scoped fetch; cross-tenant probes assert 404-never-403 over HTTP for
  detail, lines, checklist, stage, submit and withdraw.
- **§4.6/§4.7 (deny-by-default, structural):** all nine routes declare
  permissions structurally; `test_authz_coverage.py` sees them via the
  package `__init__` router; granted/denied role pairs proven by test
  (EMPLOYEE denied VAT_READ; ACCOUNTANT granted VAT_WRITE, denied
  VAT_SUBMIT; OWNER full).
- **§4.9 (Decimal):** money crosses the wire as pydantic-serialized
  Decimal strings; no float appears in any new module.
- **§4.16 (audit):** unchanged — the services already audit every
  mutation in-transaction; the routes add no unaudited write (list/detail/
  checklist/stage reads mutate nothing and audit nothing).
- **§4.19 (advisory):** checklist/stage reads block nothing and change
  nothing — proven by re-reading the claim after the GET.
- **§4.20 (additive wire):** brand-new endpoints only; no existing route,
  schema or error shape is touched.
- **D5 nothing-mutated over HTTP:** a 409 submit leaves `draft`, no
  status_code, no frozen lines, no locks, no vat_eur — asserted through
  the API plus a direct DB read.
- **Layering:** routes import services + schemas only; services keep
  raising `AppError`, never `HTTPException`; `test_boundaries.py` green.

### Database / migration impact

None. No table, no column, no RLS change. (RLS/model set-equality is
untouched; the two parity conversions REPLACE exemptions with stronger
HTTP probes.)

### Testing requirements

`tests/transport/test_wo76_claim_routes.py`:
- `test_wo76_create_claim_is_idempotent_on_the_grain` — POST twice → 200
  both, same id (R1 over HTTP).
- `test_wo76_create_claim_unknown_entity_is_404` / malformed period → 422
  `invalid_period`.
- `test_wo76_list_and_detail_read_back_the_claim`.
- `test_wo76_build_lines_then_read_them` — POST lines → R2-grain rows with
  goods codes; GET lines identical.
- `test_wo76_checklist_and_stage_read_are_advisory_and_mutate_nothing` —
  failing checklist named; claim unchanged after both GETs; stage `1A`.
- `test_wo76_stage_reaches_1e_when_ready`.
- `test_wo76_submit_happy_path_freezes_locks_and_flips_status` — 200;
  `submitted`, `status_code == "2"`, frozen `vat_eur` exact; lock rows +
  frozen lines exist (DB).
- `test_wo76_submit_refusals_surface_the_service_codes_with_nothing_mutated`
  — `period_not_ended`, `customer_not_active`, `unresolved_invoice_refs`,
  `below_minimum` (then `override_minimum=True` succeeds and stamps
  `status_note`), `invoice_document_missing`, `duplicate_invoice_lock`:
  each 409 + code + the D5 nothing-mutated assertion.
- `test_wo76_submit_empty_invoice_set_is_422_at_the_boundary`.
- `test_wo76_withdraw_releases_the_locks` — 200, `withdrawn`, zero lock
  rows; withdraw on a draft → 409 `claim_not_locked`.
- `test_wo76_module_disabled_is_403_module_not_enabled_on_every_route`.
- `test_wo76_missing_permission_is_403_structural` — EMPLOYEE → 403 on
  read; ACCOUNTANT → 2xx create / 403 submit+withdraw (the WRITE/SUBMIT
  split live on the wire).
- `test_wo76_cross_tenant_claim_id_is_an_opaque_404_on_every_route`.

`tests/test_tenancy_parity.py`: `_p_vat_refund_claims` (list + by-id 404),
`_p_vat_claim_lines` (seeded rows read through the route + cross-tenant
404) — EXEMPT entries removed; remaining transport reasons trued up.

### Acceptance criteria (verifiable checklist)

- [ ] `POST /api/v1/transport/claims` twice with one body → 200 twice,
      one row, same id (test named above green).
- [ ] A submit refused at each named gate returns 409 with the SERVICE's
      code, and a follow-up `GET .../{id}` still shows `status="draft"`
      with zero lock rows in the DB.
- [ ] `POST .../submit` happy path → `status="submitted"`,
      `status_code="2"`, frozen `vat_eur` equal to the line sum; withdraw
      → `withdrawn` + zero lock rows.
- [ ] EMPLOYEE gets 403 on `GET /transport/claims`; ACCOUNTANT gets 2xx on
      `POST /transport/claims` and 403 on `.../submit` (matrix live).
- [ ] Org with the module off gets 403 `module_not_enabled` on all nine
      routes; org B's claim id under org A yields 404, never 403, on all
      six by-id routes.
- [ ] `tests/test_authz_coverage.py` passes with the new routes counted
      (no PUBLIC_ROUTES entry added).
- [ ] `vat_refund_claims` + `vat_claim_lines` are PROBED, not EXEMPT, in
      the parity suite.
- [ ] Full suite green from the 1795-passed baseline; pii-scan clean;
      boards updated in the final commit.

### Rollback strategy

Pure code revert (new package + schema module + two additive service
functions + tests + doc rows). No migration, no data effect. Narrow
mitigation without revert: unregister the package router in
`app/api/router.py` (the services keep their own gates).

### Documentation to update

- `docs/transport/rules.md` — R-rows that gain a route consumer (R1, R2,
  R7/R8/R44/R3/R6/R10 via the submit route, R5 via withdraw, R45/R17 via
  the checklist/stage reads).
- `TODO.md` — WO-76 row + M3 cell + suite line.
- `README.md` — collected-test count in the scale line (route-module
  count is top-level by convention — `tests/test_docs_truth.py` counts
  `app/api/routes/*.py` non-recursively, exactly as `app/models/transport`
  is already outside the model count; unchanged at 39).
- No ADR contradicted: ADR-0024's structural-authz mechanism and ADR-P3's
  route-layer plan are being followed, not changed.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo76_claim_routes.py -q
python -m pytest tests/test_tenancy_parity.py tests/test_authz_coverage.py tests/test_boundaries.py -q
python -m pytest -q                                   # full baseline, once, at the end
# the demonstration — the routes exist, declare permissions, and the claim tables are probed not exempt:
grep -n "transport" app/api/router.py
grep -rn "require_perm" app/api/routes/transport/claims.py | head
grep -n "vat_refund_claims\|vat_claim_lines" tests/test_tenancy_parity.py | head
cd .. && python scripts/pii_scan.py --tree
```
