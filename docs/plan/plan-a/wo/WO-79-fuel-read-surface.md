# WO-79 — the fuel-transaction read surface + the submit pick-list it unlocks

> Closes WO-78's two recorded deviations. WO-50 shipped `fuel_transactions`
> (the typed line item every claim is built from) and WO-76/WO-77 routed the
> claim lifecycle over it — but no route has ever RETURNED a fuel transaction.
> That single gap is why `tests/test_tenancy_parity.py` still carries
> `fuel_transactions` as an EXEMPT row, and why WO-78's submit dialog asks an
> operator to TYPE a supplier and a UUID by hand. This order routes the rows
> and turns the typed entry into a pick-list.

**WORK ORDER 79 — the fuel-transaction read surface (`app/services/transport/
fuel.py` + `app/api/routes/transport/fuel.py` + `app/schemas/transport_fuel.py`)
and the submit PICK-LIST it unlocks in `frontend/src/pages/VatClaimDetail.tsx`,
on the existing VAT_READ permission, with `fuel_transactions` converted from a
tenancy-parity EXEMPT row to a real HTTP probe. Effort M 3–5d. Priority P1.
Milestone M3. Depends on: WO-50 (the `fuel_transactions` model + ingestion),
WO-76 (the claim lifecycle routes + the submit contract), WO-78 (the SPA
workspace this extends).**

### Objective and business value

The gap, with verified evidence. `backend/app/models/transport/fuel_transaction.py`
has existed since WO-50 and `app/services/transport/fuel_ingest.py::ingest_transaction`
is its only writer; `grep -rn "FuelTransaction" backend/app/api` returns
NOTHING. Three consequences are live in the tree today:

1. `backend/tests/test_tenancy_parity.py::EXEMPT["fuel_transactions"]` says, in
   its own words, *"Still no route RETURNS these rows after WO-77 … Gains a
   probe when the fuel-analytics route slice exposes them"*. The table's
   behavioural isolation is proven only by the ORM guard and Postgres RLS — not
   over the real HTTP read path every other claim table is proven on.
2. `frontend/src/pages/VatClaimDetail.tsx` (WO-78, lines 59–64 of its module
   docstring) records: *"THE INVOICE SET IS OPERATOR-SUPPLIED, and that is a
   recorded limitation of the current wire … no route enumerates fuel
   transactions yet (`api/routes/transport/fuel.py` does not exist)"*. The
   submit dialog therefore asks a human to type a **UUID** — the
   `fuel_transaction_id` third element of the lock tuple
   (`app/services/transport/lock.py::submit_claim`, `invoices:
   list[tuple[str, str, str]]`).
3. WO-78's second deviation: the claim-line table can show no supplier, because
   `ClaimLineOut` carries none.

Who stops losing money: the FINANCE_MANAGER who files. Typing a UUID per
invoice is not a workflow — it is a transcription error waiting to become a
`duplicate_invoice_lock` on the wrong invoice, or a submission abandoned before
the 30 September Art. 15 deadline. The lock tuple is the ONE place in the whole
D5 chain where the operator supplies data the service cannot derive; making
that entry a selection over the period's real transactions removes the only
hand-keyed field from the filing path. And the tenancy probe is not paperwork:
`fuel_transactions` carries every litre, station and amount a tenant owns —
the moment a route returns it, invariant §4.4/§4.1 must be proven over that
route, not adjacent to it.

### Scope

**In scope:**
- `backend/app/services/transport/fuel.py` (**new**) — `list_fuel_transactions()`,
  the additive READ accessor: transport module gate → opaque-404 entity fetch →
  org-scoped, filtered, paginated query. Follows the WO-76/WO-77 accessor
  pattern exactly (`claim_lines.list_claim_lines`, `waiver.list_waivers`,
  `receipt_control.list_controls`).
- `backend/app/schemas/transport_fuel.py` (**new**) — `FuelTransactionOut` +
  `FuelTransactionListOut{items,total,page,page_size}`. Money and quantity as
  `Decimal` fields (strings on the wire, §4.9), naming per `transport_claim.py`.
- `backend/app/api/routes/transport/fuel.py` (**new**) — one thin controller,
  `GET /api/v1/transport/fuel-transactions`, router-level `VAT_READ`.
- `backend/app/api/routes/transport/__init__.py` — the aggregator includes it
  (its own docstring already names `fuel.py` as a future includer HERE).
- `backend/tests/transport/test_wo79_fuel_routes.py` (**new**) — the route
  matrix below, in the `test_wo76_claim_routes.py` pattern.
- `backend/tests/test_tenancy_parity.py` — `fuel_transactions` EXEMPT → a real
  HTTP probe; the three remaining transport exemptions' "after WO-77" reason
  text trued up to "after WO-77/WO-79".
- `frontend/src/lib/types.ts` — `FuelTransaction` + `FuelTransactionList`,
  field-for-field from `transport_fuel.py`.
- `frontend/src/pages/VatClaimDetail.tsx` — the submit dialog gains a PICK-LIST
  over the period's fuel transactions; the typed path is preserved.
- `frontend/e2e/vat-claims.spec.ts` — the pick-list specs.
- Boards: `TODO.md`, `docs/transport/rules.md`, `README.md` scale line.

**Out of scope (the anti-scope-creep clause):**
- Any fuel/toll ANALYTICS surface — €/L benchmarking, per-country recovery,
  excise, overcharges (`recovery.py`/`excise.py`/`overcharges.py`, none of
  which has a backing service). This order routes the ROWS, not a derived
  figure. The `TRANSPORT_READ` permission stays reserved for that surface (see
  Implementation guidance §2).
- Any WRITE surface over `fuel_transactions`. Ingestion stays a
  service/parser-tier concern (`fuel_ingest`, `statement_ingest`); no route
  creates, edits or deletes a transaction, so no audit event and no SoD
  question arises.
- Deriving the submit invoice set SERVER-side from the frozen lines. `lock.py`'s
  own docstring records that as *"a future slice of G2.6"*; this order makes the
  caller-supplied list selectable, it does not replace the contract.
- The SUPPLIER column on the claim-line table (WO-78 deviation 1) — see
  Deviations: it is not honestly derivable from the wire and is therefore NOT
  built.
- Any new permission, error shape, or change to an existing route's behaviour.
- The WO-77 admin/config SPA screens (the second UI slice).

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/fuel.py` | **new** — `list_fuel_transactions()` |
| `backend/app/schemas/transport_fuel.py` | **new** — `FuelTransactionOut` / `FuelTransactionListOut` |
| `backend/app/api/routes/transport/fuel.py` | **new** — one `GET`, router-level `VAT_READ` |
| `backend/app/api/routes/transport/__init__.py` | include the new router |
| `backend/tests/transport/test_wo79_fuel_routes.py` | **new** — the route matrix |
| `backend/tests/test_tenancy_parity.py` | `fuel_transactions` EXEMPT → probe; reason text trued up |
| `frontend/src/lib/types.ts` | additive `FuelTransaction` / `FuelTransactionList` |
| `frontend/src/pages/VatClaimDetail.tsx` | the submit pick-list |
| `frontend/e2e/vat-claims.spec.ts` | pick-list specs |
| `TODO.md`, `docs/transport/rules.md`, `README.md` | boards (final commit) |

### Implementation guidance

1. **Verify the field names first, then type them once.** `FuelTransaction`'s
   columns are read off `app/models/transport/fuel_transaction.py`; the schema
   mirrors them field-for-field. `qty` is `Numeric(14,3)` and DELIBERATELY not
   money-quantized (the €/L denominator — that model's own docstring, and
   master-context §4.9's carve-out); it is still a `Decimal` field, so it too
   crosses the wire as a string. The four FX-provenance columns
   (`fx_rate`/`fx_ecb_rate`/`fx_ecb_date`/`fx_source`) are carried verbatim —
   §4.15's provenance is worthless if the read surface drops it.
2. **Permission: `VAT_READ`, and the judgment is recorded.** `app/core/authz.py`
   carries BOTH `VAT_READ` and `TRANSPORT_READ = "transport.read"  # fuel/toll
   analytics, excise (advisory)`. This route is gated `VAT_READ` because these
   rows ARE the claim's evidence base — `claim_lines.build_claim_lines` reads
   exactly this set to materialize the lines `GET /transport/claims/{id}/lines`
   already serves under `VAT_READ`, and the surface's consumer is the SUBMIT
   pick-list, not an analytics dashboard. `TRANSPORT_READ`'s own comment scopes
   it to the derived analytics/excise surfaces this order explicitly leaves out.
   The choice changes NO effective access: `ROLE_PERMISSIONS` grants
   `VAT_READ` and `TRANSPORT_READ` to exactly the same six roles and denies both
   to APPROVER and EMPLOYEE (verified by enumeration, and asserted in the new
   route test). No permission member is added (§10).
3. **Filtering = what the pick-list genuinely needs, all of it a real column.**
   `entity_id` (required — the claim's own grain), `period` (required),
   `supplier` (optional — `FuelTransaction.supplier`), `country` (optional —
   `FuelTransaction.country`, the country of SUPPLY = the refund jurisdiction,
   so a pick-list scoped to a claim must be able to ask for it; without it the
   dialog would offer transactions that `build_claim_lines` provably excluded).
4. **`period` accepts both shapes, via the CANONICAL mapping — no new one.**
   `fuel_transactions.period` is `"YYYY-MM"`; a claim's `ref_period` is
   `"YYYY-Qn"`/`"YYYY-YEAR"`. The accessor takes either: a `"YYYY-MM"` value
   filters that month directly, and a claim reference period is expanded through
   the EXISTING public helper `claim_lines.period_months()` — the same function
   `build_claim_lines` and `close` already share, documented there as
   centralized precisely so the mapping lives in one place. A malformed value
   refuses 422 `invalid_period` (the vocabulary `claim.validate_ref_period` and
   `receipt_control._validate_period` already raise). Nothing new is invented.
5. **Pagination follows the established convention, it does not create one.**
   `page`/`page_size` query params with a `{items,total,page,page_size}`
   response model — the `InvoiceListOut` shape (`app/schemas/invoice.py`,
   `GET /api/v1/invoices`), with the `page_size` bound of the LARGE-list
   precedents (`expenses.py`, `email.py`: `default=100, ge=1, le=500`), because
   a quarter of fuel-card lines for one entity is hundreds of rows, not tens.
   The count and the page come from the SAME filter set, in the service.
6. **The gate order is the transport-wide one, and it fails CLOSED.** Module
   entitlement first (`modules.is_enabled`, never `require_enabled` — a service
   raises `AppError`), then the org-scoped entity fetch through `issuer.get_by_id`
   (`entity_not_found`, 404 — indistinguishable from a cross-tenant id, §4.4),
   then period validation, then the query. A cross-tenant `entity_id` therefore
   404s BEFORE any row is read; an in-tenant entity with a foreign org's data is
   impossible because the query itself carries `org_id`.
7. **The route is a thin controller.** Parse → structural gate → call the
   already-gated service → shape the response. No filtering logic, no refusal
   mapping — every code on the wire is the service's own (§4.20).
8. **The pick-list mirrors the lock tuple, and nothing else.** The dialog fetches
   `GET /transport/fuel-transactions?entity_id=<claim.entity_id>&period=<claim.
   ref_period>&country=<claim.refund_country>` — the claim's own grain — and each
   selected transaction contributes one `{supplier, invoice_ref,
   fuel_transaction_id}` row. `supplier` and `fuel_transaction_id` come straight
   off the transaction (never typed again); `invoice_ref` is pre-filled from the
   transaction's own raw `invoice_ref` and stays EDITABLE, because that column is
   nullable by design (the model's own docstring: "often unresolved AT INGESTION
   time") while `SubmitInvoiceIn.invoice_ref` is `min_length=1`. The typed path
   survives as an explicit "Add a row manually" control — WO-78's contract, kept.
9. **Advisory never blocks, and the UI still computes nothing (§4.19/§4.10).**
   The pick-list is a selection aid: it never disables Submit, never filters a
   transaction out on a rule of its own, and never sums a column. Every amount
   renders through `decimalMoney` from the wire string.

### Invariants this order must preserve

- **§4.1/§4.4 (tenant isolation, opaque 404):** the accessor filters `org_id`
  and fetches the entity org-scoped; a cross-tenant `entity_id` yields 404
  `entity_not_found`, never 403. Proven by the new route test AND by converting
  `fuel_transactions` from an EXEMPT row to a real overlapping-data HTTP probe
  in `test_tenancy_parity.py`.
- **§4.6/§4.7 (deny-by-default, structural authz):** the permission is declared
  on the router (`APIRouter(dependencies=[...])`), so `test_authz_coverage.py`
  classifies it automatically; a granted/denied role pair is asserted live.
- **§4.9 (Decimal, never float):** every amount and `qty` is a `Decimal` schema
  field; the test asserts the exact digit string on the wire, and the SPA renders
  it with `decimalMoney` (no `Number()`/`parseFloat`).
- **§4.10 (the server recomputes every total):** this order adds a READ only —
  the pick-list neither sums nor derives a claim figure.
- **§4.19 (advisory never blocks):** selecting transactions changes nothing on
  the server; the checklist still never disables Submit.
- **§4.20 (additive):** no existing route, schema, permission or error code
  changes. The aggregator gains one `include_router` line; every WO-76/WO-77
  test passes unmodified.
- **§9/§10 (actual vocabulary only, nothing invented, zero Fleet Fuel bytes):**
  every field name comes from the ORM model, every refusal code from an existing
  service, the period mapping from the existing shared helper; fixtures are
  synthetic.

### Database / migration impact

None. No column, table, index or constraint changes; `fuel_transactions` is
already a registered tenant model with an RLS policy from its own WO-50
migration.

### Testing requirements

`backend/tests/transport/test_wo79_fuel_routes.py`:
- `test_wo79_lists_the_periods_transactions_for_an_entity` — happy path,
  ordering, `total`.
- `test_wo79_money_and_qty_cross_the_wire_as_exact_decimal_strings` — §4.9.
- `test_wo79_supplier_and_country_filters_narrow_the_set`.
- `test_wo79_a_claim_reference_period_expands_to_its_months`.
- `test_wo79_an_unparseable_period_is_422_invalid_period`.
- `test_wo79_pagination_pages_the_set_without_losing_a_row`.
- `test_wo79_module_disabled_refuses_403_module_not_enabled`.
- `test_wo79_employee_is_denied_and_accountant_is_granted` — the authz pair.
- `test_wo79_a_cross_tenant_entity_id_is_an_opaque_404`.
- `test_wo79_an_org_scoped_list_returns_zero_rows_of_the_other_org` —
  overlapping data (identical supplier, period, amounts) in both orgs.

`backend/tests/test_tenancy_parity.py`: `fuel_transactions` becomes
`_p_transport_fuel_transactions`.

`frontend/e2e/vat-claims.spec.ts`:
- `submit: the pick-list renders the period's fuel transactions`
- `submit: selecting a transaction posts its supplier and id verbatim`
- `submit: the pick-list money renders exactly from the wire string`
- `submit: an empty fuel response shows the empty copy and the manual row`
- `submit: a fuel 500 shows the error state and Submit still works manually`
- `submit: the manual row path still posts what was typed`

### Acceptance criteria (verifiable checklist)

- [ ] `GET /api/v1/transport/fuel-transactions?entity_id=…&period=2026-05`
      returns 200 with `{items,total,page,page_size}` and `items[0].net_eur ==
      "2000.00"` (a string, not a number).
- [ ] The same request with `period=2026-Q2` returns the same row (expanded via
      `claim_lines.period_months`), and `period=2026-13` returns 422 with
      `code="invalid_period"`.
- [ ] A cross-tenant `entity_id` returns **404** `entity_not_found`, never 403.
- [ ] A stored-role `user` (EMPLOYEE) gets **403**; an ACCOUNTANT gets **200**.
- [ ] With the `transport` module off the route returns **403**
      `module_not_enabled`.
- [ ] Two orgs holding identical supplier/period/amount rows each see ONLY their
      own — asserted by `test_tenancy_parity.py`, with `fuel_transactions` no
      longer in `EXEMPT` and `test_exemption_list_has_no_stale_entries`
      still green.
- [ ] The submit dialog lists the mocked transactions; ticking one and filing
      posts `{"supplier":"Q8","invoice_ref":"INV-0001","fuel_transaction_id":
      "<the mocked id>"}` — asserted on the captured request body.
- [ ] `npm run build`, `npm run test:e2e`, `ruff check`, `ruff format`,
      `mypy app`, `python scripts/pii_scan.py --tree` all clean, and the backend
      suite grows only by the new tests (1853 → 1853 + N, 10 skipped).

### Rollback strategy

Backend: delete the three new files and the one `include_router` line — nothing
else imports them, no migration, no data effect. The tenancy-parity change
reverts to the EXEMPT row in the same commit. Frontend: revert
`VatClaimDetail.tsx` to the typed-only dialog; the pick-list is additive and the
manual path it wraps is unchanged, so a partial mitigation is simply not
rendering the pick-list section. No one-way effect anywhere: this order writes
nothing.

### Documentation to update

- `TODO.md` — WO-79 row + M3 cell + suite line.
- `docs/transport/rules.md` — the R-rows gaining a route/UI consumer.
- `README.md` — the collected-test count in the scale line (the route/service
  MODULE counts are unaffected: `test_docs_truth.py::_py_module_count` globs
  `app/api/routes/*.py` and `app/services/*.py` non-recursively, so files inside
  the `transport/` packages have never been counted).
- No ADR contradicted: ADR-P3's `api/routes/transport/` file list NAMES
  `fuel.py`; ADR-0024's structural authz is followed, not amended.

### Deviations (recorded, with the evidence)

**Deviation 1 — the claim-line SUPPLIER column is NOT built (WO-78 deviation 1
stays open, deliberately).** It cannot be derived honestly from the wire, and
faking it would be worse than leaving it absent:

- `VatRefundClaimLine.invoice_ref` is the RESOLVED AP invoice NUMBER
  (`claim_lines.build_claim_lines` sets `ref = matched.invoice_number`, where
  `matched` comes from `invoice_match.resolve_invoice_ref`), or the literal
  `"UNMATCHED"`. `FuelTransaction.invoice_ref` is the RAW statement note. They
  are different strings by construction — the whole point of
  `invoice_match._prefix_match`/`_stem_contained` is that the note is NOT the
  invoice number. Joining the two on equality would be a guess.
- `FuelTransaction.invoice_id` — the one column that WOULD tie a transaction to
  the same AP invoice a claim line points at — is never populated: the model's
  own docstring says *"No code in this order populates `invoice_id` — it stays
  NULL until the future note-matching service resolves it"*, and
  `fuel_ingest.ingest_transaction` does not set it. Verified:
  `grep -rn "invoice_id=" backend/app/services/transport/` shows only
  `claim_lines.py` writing the CLAIM LINE's own column.
- An `UNMATCHED` line aggregates EVERY unresolved supplier in the claim's scope
  into one row (`key = (ref, txn.product_group)` where `ref = "UNMATCHED"`), so
  even a correct per-supplier attribution has no single answer for it.
- The resolution itself is server-side heuristics + admin overrides; it is not
  reproducible in the browser from the two lists.

The honest fix is a backend one — carrying the supplier onto `ClaimLineOut`
would require `build_claim_lines` to record it (and to decide what an
`UNMATCHED` multi-supplier bucket carries). That is a MODEL/service question, a
schema change, and a rule decision about the aggregate bucket — a work order of
its own, not a UI detail. Recommended as the next slice.

**Deviation 2 — `period` accepts two shapes.** The order said "list a period's
transactions"; the model's `period` is `"YYYY-MM"` while the pick-list's caller
holds a claim `ref_period`. Rather than mirror the quarter→months mapping in the
browser (a second copy of a rule `claim_lines.period_months` exists to
centralize), the accessor expands it server-side through that exact helper. This
adds no new vocabulary and no new mapping.

**Deviation 3 — a `country` filter was added beyond the order's "entity, period,
supplier".** `build_claim_lines` scopes a claim's transactions by
`country == claim.refund_country`; a pick-list that ignored country would offer
rows the claim provably excludes. `country` is a real model column and the filter
is optional (absent ⇒ unchanged behaviour).

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo79_fuel_routes.py tests/test_tenancy_parity.py -q
python -m pytest -q                                   # full baseline
# the demonstration — the route exists, is classified by the authz net, and
# `fuel_transactions` is no longer an exemption:
python -c "from app.main import create_app; print([r.path for r in create_app().routes if 'fuel' in r.path])"
grep -n "fuel_transactions" tests/test_tenancy_parity.py
cd ../frontend && npm run build && npm run test:e2e
cd .. && python scripts/pii_scan.py --tree
```
