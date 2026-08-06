# WO-77 — transport routes slice 2: admin/config surfaces + filing artifacts

> Slice 2 of the M3 route batch, built exactly on WO-76's pattern (thin
> controllers, existing VAT_* permissions, service-side module gate +
> opaque-404 via additive read accessors, `AppError` propagation, Decimal
> wire, parity EXEMPT→probe conversions). WO-76 shipped the claim
> lifecycle; this slice ships every ADMIN/CONFIG surface the already-built
> transport services carry plus the WO-74 filing-artifact downloads.

**WORK ORDER 77 — transport routes slice 2: receipt-control waivers
(WO-58), checklist-rule admin (WO-60), note→invoice-ref overrides (WO-52),
manual status codes (WO-59), engine tie-out expectations (WO-66 regime 2),
receipt-control cadences + the persisted control grid (WO-72), customer
lifecycle + per-country activation (WO-73), and the claim workbook /
evidence-pack downloads (WO-74) — exposed as thin controllers under
`app/api/routes/transport/`, structurally gated on the EXISTING
`VAT_READ`/`VAT_WRITE`/`VAT_SUBMIT` permissions. Effort M 3–5d. Priority
P1. Milestone M3. Depends on: WO-76 (the route package + conventions),
WO-52/WO-58/WO-59/WO-60/WO-66/WO-72/WO-73/WO-74 (the services exposed),
WO-1 (structural authz), WO-10 (tenancy parity).**

### Objective and business value

The gap, with verified evidence: after WO-76 the ONLY transport routes are
the nine claim-lifecycle endpoints in `app/api/routes/transport/claims.py`.
Every admin/config service this slice exposes is built, self-gated and
audited, but unreachable except from a Python shell:
`waiver.set_waiver/remove_waiver` (no read accessor returns the rows),
`checklist.list_rules/set_active/seed_default_rules`,
`invoice_match.set_note_override` (no list accessor),
`status.set_status_code` (+ the `AUTO_CODES`/`MANUAL_CODES` vocabulary),
`tie_out.set_expectation/remove_expectation` (no list accessor),
`receipt_control.set_cadence/remove_cadence/list_cadences/list_controls/
set_control_override`, `customer_lifecycle.add_prospect/promote_prospect/
set_activation/set_inactive/request_country/set_country_activation` (no
route-facing read), and `claim_pack.build_workbook/build_evidence_pack`
(whose own docstring says "No route exists yet — the
`api/routes/transport/*` batch serves and permission-gates these bytes
later"). Ten `tests/test_tenancy_parity.py` EXEMPT entries name exactly
this absence.

Who stops losing money: the operator who FILES the claim needs the
workbook and the evidence ZIP over HTTP — those two artifacts ARE the
product's deliverable to the refunding member state; a filing bundle that
requires a shell is not a filing bundle. And every gate the D5 chain
enforces is only operable if its admin lever is reachable: a waiver for a
genuinely uninvoiced supplier, a note→invoice override for an unmatched
ref, a typed tie-out expectation, an activation click, the post-decision
status-code stamp with its R12 `action_deadline` — each currently dead-ends
in a service function no UI can ever call.

### The wire contract (verified against the services — no invented codes)

Every refusal is raised by the SERVICE as an `AppError` and rendered by
the one `app.main` handler as `{"detail","code"}` + `X-Request-ID`
(§4.20). New codes surfacing over HTTP for the first time:

| Condition | HTTP | `code` (verbatim from the service) |
|---|---|---|
| transport module off (every route) | 403 | `module_not_enabled` |
| cross-tenant / unknown claim id | 404 | `claim_not_found` |
| cross-tenant / unknown entity id | 404 | `entity_not_found` |
| waive/unwaive on a non-draft claim | 409 | `claim_not_draft` |
| waived supplier has registered invoices (R15) | 422 | `waiver_supplier_has_invoices` |
| unknown checklist rule key | 404 | `checklist_rule_not_found` |
| manual code is an AUTO code (R17) | 409 | `status_code_system_controlled` |
| unknown status code | 422 | `unknown_status_code` |
| manual code on a draft claim (D4) | 409 | `claim_not_submitted` |
| malformed period (tie-out / controls) | 422 | `invalid_period` |
| malformed currency (tie-out) | 422 | `invalid_currency` |
| negative expected_lines | 422 | `invalid_expected_lines` |
| tolerance outside [0.02, 0.05] | 422 | `invalid_gross_tolerance` |
| deleting an untyped expectation | 404 | `tieout_expectation_not_found` |
| cadence outside the harvested three | 422 | `invalid_cadence` |
| unknown receipt-control row id | 404 | `receipt_control_not_found` |
| synthetic note key on an override | 422 | `override_note_is_synthetic` |
| override target not registered | 422 | `override_target_not_registered` |
| promote on a non-prospect | 409 | `not_a_prospect` |
| illegal lifecycle edge | 409 | `lifecycle_transition_invalid` |
| bad ISO country | 422 | `invalid_country` |
| activating a never-requested country | 409 | `country_not_requested` |
| illegal country edge | 409 | `country_transition_invalid` |
| artifacts on a claim with no frozen lines | 409 | `claim_not_frozen` |
| synthetic frozen line (R3) | 409 | `synthetic_line_in_pack` |
| frozen header ≠ its lines (§4.10) | 409 | `claim_totals_drift` |
| frozen set spans currencies (§4.14) | 409 | `claim_currency_mismatch` |
| vaulted bytes gone at assembly | 409 | `evidence_document_unavailable` |

### Design decisions (recorded, with rationale)

1. **Permissions — the existing three, assigned by consequence.** Reads →
   `VAT_READ`. Config mutations (waivers, checklist-rule toggle/seed, note
   overrides, tie-out expectations, cadences, control-grid overrides,
   customer lifecycle + country activation) → `VAT_WRITE`: they configure
   how claims are BUILT and gated, the same tier as booking claim data —
   none of them flips a claim's status or acquires/releases a lock.
   `set_status_code` → `VAT_SUBMIT`: it is the post-submission claim-status
   workflow (stamps `status_code` + R12 `action_deadline` on a LOCKED
   claim) — the claim-status surface the prompt routes to VAT_SUBMIT.
   Artifact downloads → `VAT_READ`, not `EXPORT_RUN`: the workbook/ZIP are
   read-only renderings of claim data (the same data `GET .../lines`
   already serves under VAT_READ); `EXPORT_RUN` guards the accounting-
   ledger export hub, a different product surface. Judgment calls recorded
   here, none inventing a permission.
2. **Route modules: `admin.py` + `customers.py` + additions to
   `claims.py`.** ADR-P3's file list (`fuel.py`, `recovery.py`,
   `excise.py`, `overcharges.py`) names ANALYTICS surfaces that have no
   backing services yet; the admin/config surfaces get their own module
   (`admin.py` — org-level configuration) and the customer-lifecycle
   ladder its own (`customers.py` — entity-scoped), while claim-scoped
   surfaces (waivers, status-code, artifacts) join `claims.py`. All
   aggregate through the package `__init__.router` (WO-76 decision 2), so
   `test_authz_coverage.py` sees every route.
3. **Additive read accessors, service-side gates (WO-76 decision 3).**
   New: `waiver.list_waivers` (claim-scoped, opaque-404),
   `tie_out.list_expectations` (per period),
   `invoice_match.list_note_overrides`,
   `customer_lifecycle.lifecycle_overview` (entity-scoped, opaque-404,
   returns the lifecycle row + the country-activation rows),
   `status.list_status_codes` (the vocabulary read — gated so an
   un-entitled org stays byte-identical, ADR-P3 rule 3). One EXISTING
   accessor gains the module gate: `checklist.list_rules` becomes
   route-facing and previously had no gate only because it had no external
   caller — the WO-49 convention ("no service entry point trusts its
   caller") now applies; its only internal callers gate first, so the
   change is behaviour-preserving for them.
4. **`seed_default_rules` gets a route.** The default checklist rules only
   PERSIST when a committing caller runs the seed (WO-76 deliberately made
   the checklist GET commit-free). Without a committing caller the rule
   table stays empty and `set_active` can never find a row — the admin
   surface would be inoperable. `POST /transport/checklist-rules/seed`
   (VAT_WRITE, commits) exposes the existing idempotent service function;
   the checklist GET stays pure.
5. **No `STATUS_LABELS` — the actual vocabulary is exposed.** The prompt's
   "status-label read" assumed a labels mapping; this codebase has none
   (that was Fleet Fuel vocabulary; §10 zero-bytes). The real vocabulary is
   `status.AUTO_CODES`/`MANUAL_CODES` — `GET /transport/status-codes`
   returns exactly those two lists via the gated accessor. Deviation
   recorded, nothing invented.
6. **Note-override DELETE does not exist and is not invented.** The
   service surface is `set_note_override` (idempotent upsert on the key)
   only — no remove function was harvested (R16's lifecycle is "de-register
   the target ⇒ CASCADE deletes the row"). The route surface mirrors the
   service exactly: `GET` (additive list) + `PUT` (set). A future removal
   verb is future service work, not a route-layer invention.
7. **`run_receipt_control` and `orphan_transactions` are NOT routed.** The
   control run is the close's `run_control` stage (R60's "never inline in
   a web request" posture; the grid this slice serves is what the run
   PERSISTED); the orphan check is an analytics read the prompt does not
   name — both stay for a later slice, recorded not skipped silently.
8. **Downloads mirror the existing binary-route conventions.** xlsx:
   `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
   (the `analytics.py` explore-xlsx precedent); zip: `application/zip`
   (the `issued.py` batch-PDF precedent); both with the shared
   header-injection-safe `core.security_headers.content_disposition` +
   `X-Content-Type-Options: nosniff` (the `reimbursements.py` precedent).
   Whole-bytes `Response`, not streaming — every existing download route
   ships bytes (the builders return `bytes`; a claim pack is bounded by
   its own invoice set).
9. **Tie-out DELETE keys by query params** (`entity_id`/`supplier`/
   `period`/`currency`) — the expectation's natural key; the service owns
   validation and the opaque 404 (`tieout_expectation_not_found`). 204 on
   success (the `issuer.py` precedent for a body-less success).
10. **Parity: every table this slice makes route-readable flips
    EXEMPT→probe** — `vat_receipt_waivers`, `vat_checklist_rules`,
    `vat_note_invoice_overrides`, `fuel_tieout_expectations`,
    `vat_supplier_cadences`, `vat_receipt_controls`,
    `vat_customer_lifecycles`, `vat_country_activations` (8 conversions,
    6 new probe functions). Rows with no HTTP writer reachable in test
    setup (checklist rules, control-grid rows) are seeded directly with
    identical business values and READ through the route — the
    `reimbursement_batches` precedent WO-76 already used for claim lines.
    Remaining transport EXEMPT entries (`fuel_transactions`,
    `vat_claimed_invoices`, `fuel_extraction_baselines`,
    `supplier_vat_registrations`) get their now-stale "WO-76 … read only
    claims/lines" wording trued up.

### Scope

**In scope:**
- `app/api/routes/transport/claims.py` (additions — claim-scoped):
  - `GET    /transport/claims/{id}/waivers` — list waivers (VAT_READ)
  - `POST   /transport/claims/{id}/waivers` — set waiver (VAT_WRITE)
  - `DELETE /transport/claims/{id}/waivers/{supplier}` — remove (VAT_WRITE)
  - `POST   /transport/claims/{id}/status-code` — manual code (VAT_SUBMIT)
  - `GET    /transport/claims/{id}/workbook` — xlsx download (VAT_READ)
  - `GET    /transport/claims/{id}/evidence` — ZIP download (VAT_READ)
- `app/api/routes/transport/admin.py` (**new** — org-level config):
  - `GET  /transport/status-codes` (VAT_READ)
  - `GET  /transport/checklist-rules` (VAT_READ)
  - `POST /transport/checklist-rules/seed` (VAT_WRITE)
  - `POST /transport/checklist-rules/{key}/active` (VAT_WRITE)
  - `GET  /transport/cadences` · `PUT /transport/cadences/{supplier}` ·
    `DELETE /transport/cadences/{supplier}` (READ/WRITE/WRITE)
  - `GET  /transport/receipt-controls?period=` (VAT_READ)
  - `POST /transport/receipt-controls/{control_id}/override` (VAT_WRITE)
  - `GET  /transport/note-overrides` · `PUT /transport/note-overrides`
    (READ/WRITE)
  - `GET  /transport/tie-out-expectations?period=` ·
    `PUT  /transport/tie-out-expectations` ·
    `DELETE /transport/tie-out-expectations?entity_id&supplier&period&currency`
    (READ/WRITE/WRITE)
- `app/api/routes/transport/customers.py` (**new** — entity-scoped):
  - `GET  /transport/customers/{entity_id}/lifecycle` (VAT_READ)
  - `POST /transport/customers/{entity_id}/prospect` · `/promote` ·
    `/activation` · `/inactive` (VAT_WRITE)
  - `POST /transport/customers/{entity_id}/countries/{country}/request` ·
    `/countries/{country}/activation` (VAT_WRITE)
- `app/schemas/transport_admin.py` (**new**) — the In/Out models.
- Additive service accessors: `waiver.list_waivers`,
  `tie_out.list_expectations`, `invoice_match.list_note_overrides`,
  `customer_lifecycle.lifecycle_overview`, `status.list_status_codes`;
  module gate added to `checklist.list_rules`.
- `app/api/routes/transport/__init__.py` — include the two new routers.
- `tests/transport/test_wo77_admin_routes.py` (**new**) — the matrix below.
- `tests/test_tenancy_parity.py` — 8 EXEMPT→probe conversions + wording
  truth-ups on the 4 remaining transport exemptions.
- Boards: `TODO.md`, `docs/transport/rules.md` (route-consumer notes),
  README collected-test count.

**Out of scope (the anti-scope-creep clause):**
- `fuel.py`/`recovery.py`/`excise.py`/`overcharges.py` analytics routes —
  no backing services exist yet (later M3/M5 slices own them).
- Routing `run_receipt_control`/`orphan_transactions`/`run_close`
  (decision 7; R60).
- A note-override DELETE (decision 6 — no service function exists).
- Any SPA page (the M3 UI batch); G2.9 fee freezing (decision-gated).
- New permissions, tables, migrations — none needed.

### Files to touch

| File | Change |
|---|---|
| `app/api/routes/transport/claims.py` | +6 claim-scoped routes |
| `app/api/routes/transport/admin.py` | **new** — 14 org-config routes |
| `app/api/routes/transport/customers.py` | **new** — 7 lifecycle routes |
| `app/api/routes/transport/__init__.py` | include the two new routers |
| `app/schemas/transport_admin.py` | **new** — In/Out models |
| `app/services/transport/waiver.py` | additive `list_waivers` |
| `app/services/transport/tie_out.py` | additive `list_expectations` |
| `app/services/transport/invoice_match.py` | additive `list_note_overrides` |
| `app/services/transport/customer_lifecycle.py` | additive `lifecycle_overview` |
| `app/services/transport/status.py` | additive `list_status_codes` |
| `app/services/transport/checklist.py` | module gate on `list_rules` |
| `tests/transport/test_wo77_admin_routes.py` | **new** — route tests |
| `tests/test_tenancy_parity.py` | 8 EXEMPT→probe + reason truth-ups |
| `TODO.md`, `docs/transport/rules.md`, `README.md` | boards (final commit) |

### Implementation guidance

1. Service accessors first — each opens with the identical
   `modules.is_enabled` → `PermissionError("module_not_enabled")` gate
   (fails CLOSED, ADR-P3 rule 3) and an org-scoped fetch (opaque 404 for
   by-id, §4.4). Deterministic ordering on every list.
2. Schemas mirror the models' own field vocabulary; money/litres fields
   `Decimal` (pydantic v2 → JSON strings, §4.9); tie-out set-inputs typed
   `Decimal` so the wire never passes float into the service's `q2`.
3. Routes: router/`_WRITE`/`_SUBMIT` structural declarations (the WO-76
   convention); handlers unpack → call service → shape `*Out`; `AppError`
   propagates untouched; mutations `await db.commit()` on success only;
   reads never commit.
4. Downloads: call the builder, wrap bytes per decision 8. Filenames
   `claim-{id}-workbook.xlsx` / `claim-{id}-evidence.zip` through
   `content_disposition` (injection-safe by construction).
5. Parity: enable transport for both orgs via `modules.set_enabled`
   (WO-76's recorded 402 rationale); drive writers over HTTP with
   IDENTICAL bodies wherever an HTTP writer exists; seed
   checklist-rule / control-grid rows directly (decision 10); assert
   list isolation on row ids + opaque 404 on every by-id/by-key surface.

### Invariants this order must preserve

- **§4.4 (opaque 404):** claim-scoped routes reach the services'
  org-scoped `_get_claim`; entity-scoped routes resolve via
  `issuer.get_by_id`; `control_id` via the service's org-scoped select —
  cross-tenant probes assert 404-never-403 over HTTP.
- **§4.6/§4.7 (deny-by-default, structural):** all 27 routes declare
  permissions structurally; granted/denied pairs proven (EMPLOYEE denied
  read; ACCOUNTANT granted config writes, denied `status-code`; OWNER
  full).
- **§4.9 (Decimal):** tie-out figures and workbook money cross as
  Decimal-backed strings; no float in any new module; the downloaded
  workbook's cells are written by the service from `Numeric(14,2)` reads.
- **§4.10:** untouched — the artifact totals-drift refusal IS the
  invariant's enforcement; the route adds no recomputation.
- **§4.16 (audit):** unchanged — every mutation this slice exposes already
  audits in-transaction; reads audit nothing; the artifact builders
  deliberately audit nothing (read-only renderers, WO-74).
- **§4.19 (advisory):** the receipt-control grid stays advisory — reading
  it or muting a slot (`waived`) changes no claim/line/lock (service-
  proven); no new read blocks or mutates anything.
- **§4.20 (additive wire):** brand-new endpoints only; no existing route,
  schema or error shape is touched.
- **Layering:** routes import services + schemas only; services raise
  `AppError` only; `test_boundaries.py` green.

### Database / migration impact

None. No table, no column, no RLS change. (The 8 parity conversions
REPLACE exemptions with stronger HTTP probes.)

### Testing requirements

`tests/transport/test_wo77_admin_routes.py` (matrix):
- Waivers: set→list→remove over HTTP; 422 `waiver_supplier_has_invoices`;
  409 `claim_not_draft` after submit; opaque 404 on B's claim.
- Checklist rules: seed→list→toggle; 404 `checklist_rule_not_found`;
  toggling `customer_data` off removes it from the claim checklist GET
  (R45's acceptance over HTTP).
- Status codes: vocabulary GET; happy `2A`+`3D` with `action_deadline`;
  409 `status_code_system_controlled` (AUTO code), 422
  `unknown_status_code`, 409 `claim_not_submitted` (draft) — each with
  status_code unchanged after.
- Tie-out: PUT→GET (Decimal strings); retype updates in place; 422
  `invalid_period`/`invalid_currency`/`invalid_gross_tolerance`; 404
  `entity_not_found` (cross-tenant entity), DELETE→204 then 404.
- Cadences: PUT→GET→DELETE; 422 `invalid_cadence`.
- Receipt controls: grid GET after a service-side `run_receipt_control`;
  override POST (waive + note) → grid reflects; 404 unknown/cross-tenant
  id; grid read mutates nothing.
- Customers: the full ladder over HTTP (prospect→promote→activate→
  countries request/activate→GET shows it all→inactive); 409
  `not_a_prospect`/`lifecycle_transition_invalid`/`country_not_requested`/
  `country_transition_invalid`; 422 `invalid_country`; opaque 404 on B's
  entity.
- Artifacts: workbook 200 + xlsx content-type + openpyxl parse (Claim +
  Lines sheets, TOTAL row == frozen `vat_eur`); evidence 200 +
  application/zip + zipfile parse (workbook entry + document + manifest
  whose sha256 rows match the actual entries); 409 `claim_not_frozen` on
  a draft; 409 `claim_totals_drift` after a header tamper; 409
  `synthetic_line_in_pack` after a frozen-ref tamper; 409
  `evidence_document_unavailable` on a dangling vault ref (workbook still
  200 — figures vs documents).
- Module-disabled 403 `module_not_enabled` on every new route; the
  permission matrix (EMPLOYEE/ACCOUNTANT/OWNER); cross-tenant opaque 404
  on every by-id route.

`tests/test_tenancy_parity.py`: probes `_p_transport_waivers`,
`_p_transport_checklist_rules`, `_p_transport_note_overrides`,
`_p_transport_tieout_expectations`, `_p_transport_cadences`,
`_p_transport_receipt_controls`, `_p_transport_customer_lifecycle`
(covers both lifecycle tables) — 8 EXEMPT entries removed.

### Acceptance criteria (verifiable checklist)

- [ ] `GET /transport/claims/{id}/workbook` on a submitted claim returns
      200 with the xlsx content-type and openpyxl reads a `Lines` TOTAL
      row equal to the claim's frozen `vat_eur`; on a draft → 409
      `claim_not_frozen`.
- [ ] `GET .../evidence` returns a ZIP whose `manifest.csv` sha256 rows
      match the actual bundle entries; a dangling vault ref → 409
      `evidence_document_unavailable` while the workbook still downloads.
- [ ] `POST .../status-code` with `"1E"` → 409
      `status_code_system_controlled`; with `"2A"` on a draft → 409
      `claim_not_submitted`; with `"3D"` + deadline on a submitted claim →
      200 and the deadline reads back.
- [ ] Deactivating `customer_data` via the rules route removes that item
      from the claim checklist GET (R45 acceptance over HTTP).
- [ ] A tie-out expectation PUT with tolerance `0.06` → 422
      `invalid_gross_tolerance`; with `0.05` → 200.
- [ ] The customer ladder runs end-to-end over HTTP and `POST
      .../activation` on a prospect → 409 (promotion is never skippable).
- [ ] EMPLOYEE 403 on `GET /transport/checklist-rules`; ACCOUNTANT 200 on
      `PUT /transport/cadences/{s}` and 403 on `POST .../status-code`.
- [ ] Org with the module off → 403 `module_not_enabled` on all 27 routes;
      org B probing A's claim/entity/control ids → 404, never 403.
- [ ] The 8 named tables are PROBED, not EXEMPT, in the parity suite.
- [ ] Full suite green from the 1817-passed baseline; pii-scan clean;
      boards updated in the final commit.

### Rollback strategy

Pure code revert (route modules + schema module + additive service
accessors + tests + doc rows). No migration, no data effect. Narrow
mitigation without revert: drop the two `include_router` lines in the
package `__init__` (claims.py additions need the full revert).

### Documentation to update

- `docs/transport/rules.md` — route-consumer notes on R15 (waivers), R45
  (rule admin), R16 (override set/list), R17/R12 (status-code route),
  R25 regime 2 (expectations admin), G3.5 (cadences/grid/override), R44
  (lifecycle routes), G2.12 (artifact downloads).
- `TODO.md` — WO-77 row + M3 cell + suite line.
- `README.md` — collected-test count (route-module count is top-level
  non-recursive, unchanged at 39).
- No ADR contradicted (ADR-0024 structural authz followed; ADR-P3 rule 5
  consumed further).

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo77_admin_routes.py -q
python -m pytest tests/test_tenancy_parity.py tests/test_authz_coverage.py tests/test_boundaries.py -q
python -m pytest -q                                   # full baseline, once, at the end
# the demonstration — the admin surfaces + artifacts exist and the tables are probed not exempt:
grep -rn "require_perm" app/api/routes/transport/admin.py app/api/routes/transport/customers.py | head
grep -n "workbook\|evidence" app/api/routes/transport/claims.py | head
grep -n "vat_receipt_waivers\|vat_checklist_rules\|fuel_tieout_expectations\|vat_receipt_controls" tests/test_tenancy_parity.py | head
cd .. && python scripts/pii_scan.py --tree
```
