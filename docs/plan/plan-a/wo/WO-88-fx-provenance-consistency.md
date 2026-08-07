**WORK ORDER 88 — FX provenance consistency at the row writer (board G4.7 follow-up; R56, R49; master-context §4.15/§4.14). Effort S. Priority P1. Milestone M5. Depends on: WO-84 (`vat_off_invoice_rebates`), WO-87 (the analysis-boundary refusal this order backs with a writer gate).**

### 0. Current-state recon (done BEFORE any design — "who can write a row that lies about its own euro?")

Five findings, each verified in the tree at `8c2a2e2`, not taken on trust from
the WO-87 write-up.

**Finding 0 — the pointer in the brief does not resolve, and the finding it
names is real anyway.** The commissioning brief cites *"WO-87 recon finding 3"*
in `docs/plan/plan-a/wo/WO-87-overpay-benchmark.md`. That file has no recon
section at all (`grep -n recon` returns three hits, all of them the word
"reconcile"). The finding is recorded in **`TODO.md:1294-1299`**, where it is
numbered the **second** recon finding of WO-87, and it is restated in
`app/services/transport/savings.py:105-121`. Per master-context §9 the symbol
named in the order does not exist, so it was re-derived from the code below
rather than replaced with an invented citation.

**Finding 1 — `fuel_ingest.ingest_transaction` can be told to write a row that
denies its own euro, and nothing refuses.** `app/services/transport/fuel_ingest.py:62`
declares `fx_source: str | None = None` and line 140 passes it straight to the
model. Between them there is no comparison of `fx_source` against `net_eur`,
`net_eur_eff`, `vat_eur` or `currency` — the whole gate list is module
entitlement (line 89) and entity existence (line 105). So both of these are
writable today:

| `currency` | `fx_source` | `net_eur` | what the row asserts |
|---|---|---|---|
| `EUR` | `unknown` | `1400.00` | *"no rate was available"* **and** *"the euro figure is 1400.00"* |
| `PLN` | `NULL` | `1400.00` | a foreign amount labelled EUR with no recorded conversion at all |

`app/models/fx.py:27` defines `unknown` as *"no rate available → EUR figure is
NULL"*, and `FuelTransaction.net_eur` (`app/models/transport/fuel_transaction.py:233`)
is `nullable=False`. The two statements cannot both hold: on this table the
honest outcome of "no rate" is **no row**, which is exactly the branch
`statement_ingest._resolve_line` already takes (`app/services/transport/statement_ingest.py:225`,
`ValidationError(code="fx_rate_unavailable")`). The production path is right;
the writer underneath it is unguarded.

**Finding 2 — the model and the database agree with the writer.**
`ck_fuel_transactions_fx_source` (model line 174, migration
`alembic/versions/fc45baaf3283_fuel_transactions.py:120`) constrains only the
*value domain* — `fx_source IS NULL OR fx_source IN ('eur','stated','ecb','unknown')`.
It says nothing about the *combination*. Both rows in the table above satisfy
every constraint on the table today.

**Finding 3 — WO-87 closed it at the analysis boundary only, and that is one
layer, not two.** `savings._require_eur_basis` (`savings.py:441-478`) refuses
exactly these two combinations with `fx_rate_unavailable`. It is real
protection for the three overpay/benchmark analyses and it stays. It protects
nothing else: `contract_audit.audit()`, `recovery`, `claim_lines`,
`tie_out`, `close` and every export read the same `net_eur`/`net_eur_eff`
columns with no such check, and `contract_audit.py:63` argues from
*"ingestion refuses a line it cannot convert"* — an argument that is true of
`statement_ingest` and false of the row writer.

**Finding 4 — no existing row violates the invariant, but a live test fixture
does, repeatedly.** Verified:

* the dev database carries **zero** `fuel_transactions` and **zero**
  `vat_off_invoice_rebates` rows (`sqlite3 backend/invoiceiq.db`);
* **no migration inserts a `fuel_transactions` row** — `op.bulk_insert` /
  `INSERT INTO` appear in exactly three revisions
  (`d99c826e4767`, `efd78f4cbe2e`, `deb447b02296`), none of them transport;
* `app/seed.py` writes no fuel transaction at all (its one `fx_source="stated"`
  at line 240 is an AP `Invoice`);
* the ONLY production writer is `fuel_ingest.ingest_transaction`, called from
  `statement_ingest.py:325`, which always supplies `eur` / `stated` / `ecb`;
* **but** `tests/transport/test_g3_3_tie_out.py:28-45` builds every one of its
  lines as `currency="SEK", net_eur=Decimal("90.00")` with **no `fx_source`**,
  and the same shape recurs in `test_g2_5_freeze.py`, `test_g2_6_submission_gates.py`,
  `test_wo83_overcharge_artifacts.py`, `test_wo82_contract_audit.py`,
  `test_wo85_canonical_queries.py` and — found by CI rather than by this recon,
  see the correction below — `test_wo81_recovery.py`. Those fixtures are the
  defect in miniature: a Swedish krona line asserting €90.00 that no rate ever
  produced.

**Finding 4, CORRECTED — it is SEVEN fixtures, not six, and the seventh was
found by CI.** The first sweep piped its grep through `head -30` and the
`test_wo81_recovery.py:604` hit sat below the cut. The full backend suite found
it (`test_wo81_a_cross_currency_draft_is_excluded_from_the_euros_and_counted`,
the only failure in 2172 tests) exactly where the gate was designed to bite: a
SEK line built with no provenance in order to construct a genuine cross-currency
claim for the §4.14 dashboard proof. Recorded here rather than quietly fixed,
because the miss is instructive — **a truncated grep is not a sweep**, and the
regression net, not the recon, is what proved the fixture list complete. The
re-run sweep is unbounded and is quoted in the *Testing requirements* section.

**Finding 5 — the same inconsistency is representable on the OTHER money-bearing
transport table, and worse.** `vat_off_invoice_rebates` (WO-84) carries
`amount_eur` (NOT NULL, `> 0`) plus the same FX quadruple — and its model
(`app/models/transport/off_invoice_rebate.py:126-129`) and its migration
(`b3d8f1c04e97:92-95`) declare **no `fx_source` CHECK of any kind**, not even
the value-domain one every other FX-bearing table has had since WO-8
(`ck_expense_items_fx_source`, `ck_invoices_fx_source`,
`ck_fuel_transactions_fx_source`). A raw writer can store
`fx_source='banana'`, or `'unknown'` beside a positive `amount_eur`, on that
table today. Its service (`rebate._resolve_eur`) is already correct — it
returns only `eur`/`ecb` and raises `fx_rate_unavailable` otherwise — so this
half is purely the missing database floor.

### Objective and business value

A `fuel_transactions` row can be written that asserts, in one row, both *"no
exchange rate was available"* and *"the euro value is €1,400.00"* (finding 1).
Nothing in the model, the ingestion service or the storage layer refuses it
(findings 1-2); WO-87 refuses it at the analysis boundary of three read-only
analyses only (finding 3). Every other consumer of `net_eur` / `net_eur_eff` —
the claim lines that become a filed VAT refund, `contract_audit`'s overcharge
euro that WO-83 prints on a client's letterhead as a 30-day payment demand,
the tie-out, the close, the recovery dashboard's north-star euros — sums that
figure as if it were a real conversion. Master-context §4.15 exists to make
exactly that impossible: *"`unknown` yields NULL, never a guessed number"*.

Who pays. A haulier files a cross-border VAT refund on these euros and a
finance lead negotiates with a supplier using them. A fabricated conversion in
that stack is not a rounding complaint: it is a wrong number on a legal filing
under Dir. 2008/9/EC and a false assertion sent to a counterparty. The cheapest
possible defence is to make the bad row **unrepresentable** — refuse it at the
one service that writes it, and put a CHECK constraint underneath so no
future script, fixture, migration or hand-rolled `INSERT` can create what the
service refuses. This order costs one gate and one migration and removes an
entire class of wrong money from every downstream consumer at once, including
the ones that do not exist yet.

### Scope

**In scope:**
- `app/services/transport/fuel_ingest.py` — a fail-CLOSED provenance gate
  refusing both inconsistent combinations with the tree's existing
  `fx_rate_unavailable` code (the `statement_ingest` / `rebate` /
  `savings` vocabulary, verified, not invented). Nothing is written on refusal.
- `app/models/transport/fuel_transaction.py` — `ck_fuel_transactions_fx_provenance`,
  the same invariant as a table CHECK.
- `app/models/transport/off_invoice_rebate.py` — the missing
  `ck_vat_off_invoice_rebates_fx_source` (value domain, the WO-8 constant) plus
  `ck_vat_off_invoice_rebates_fx_provenance` (finding 5).
- One new Alembic revision creating all three constraints, single head
  preserved, with a pre-flight violation report (see *Database / migration
  impact*).
- The SEVEN test fixtures of finding 4 (six found by recon, the seventh by the
  full suite), raised to record the provenance a real ingestion would have
  recorded (`fx_source="ecb"`). No assertion is weakened, and the one
  scenario that could plausibly have been broken by the raise — WO-81's
  cross-currency dashboard proof — is verified still reachable and still
  asserting the same six outcomes.
- `docs/transport/rules.md` — **R56 gains its first ledger row** (it has none
  today: `grep -n "^| R56" docs/transport/rules.md` returns nothing) naming
  both enforcement layers.
- `TODO.md` — the WO-88 row, the M5 cell, the suite line.
- `README.md` — the pinned Alembic revision count 86 → 87, in the SAME commit
  as the migration (`tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`).

**The other transport tables — checked, and named so the check is on the record.**
`grep -l currency app/models/transport/*.py` returns six modules; only TWO can
represent this inconsistency, and both are in scope above:

| table | EUR column | `fx_source`? | verdict |
|---|---|---|---|
| `fuel_transactions` | `net_eur`/`net_eur_eff`/`vat_eur` NOT NULL | yes | **in scope** (findings 1-2) |
| `vat_off_invoice_rebates` | `amount_eur` NOT NULL | yes | **in scope** (finding 5) |
| `vat_claim_lines` | `net_eur`/`vat_eur` NOT NULL, `currency` nullable | **no column** | nothing to contradict; the figures are DERIVED from `fuel_transactions` by `claim_lines.build_claim_lines`, so they inherit the provenance this order now guarantees upstream |
| `vat_refund_claims` | `vat_eur`/`fee_eur` nullable | no | derived totals, no document currency pair |
| `vat_overcharge_claims` | `detected_eur`/`recovered_eur` | no | *"Both are EUR by construction… the document-currency columns are never read"* (`overcharge.py:55-58`) |
| `fuel_tieout_expectations` | `expected_*_eur` nullable | no | operator-entered expectations, per currency, nullable — a missing figure is already NULL |
| `fuel_extraction_baselines` | — | no | LOCAL currency only, by design (`extraction_baseline.py:24`) |

**Out of scope (named, with the reason):**
- **A non-EUR row claiming `fx_source='eur'`** — a third representable
  inconsistency (`currency='PLN'` with the identity provenance). It is a
  *wrong* provenance rather than a *missing* one, no writer in the tree can
  produce it, and refusing it is a different rule with a different acceptance
  test. Recorded here as a follow-up rather than smuggled in (§10).
- **`invoices.total_eur` / `expense_items.amount`** (the AP/AR core). The
  invariant already holds there BY CONSTRUCTION — `total_eur` is nullable and
  `fx.eur_total` returns `(None, "unknown")` when no rate exists
  (`tests/test_sepa.py:135-142`, `tests/test_budget.py:154-159`). Adding the
  combination CHECK there is a platform order, not a transport one.
- **Any change to `savings.py`'s refusal** — it stays, unmodified. Defence in
  depth means both layers, and the analysis layer is the only one that can
  explain *why* a comparison is refused rather than merely blocked.
- **Any analysis, route, schema or SPA change.** This order adds no endpoint
  and changes no wire shape.
- **Correcting a violating row.** There are none (finding 4); if one ever
  exists, restating a stored money figure is a business decision (§9), not
  something a migration decides.

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/fuel_ingest.py` | `_require_fx_provenance()` gate + docstring rationale |
| `backend/app/models/transport/fuel_transaction.py` | `ck_fuel_transactions_fx_provenance` |
| `backend/app/models/transport/off_invoice_rebate.py` | `ck_vat_off_invoice_rebates_fx_source` + `..._fx_provenance` |
| `backend/alembic/versions/<rev>_wo88_fx_provenance_consistency.py` | **new** — the three constraints + the pre-flight report |
| `backend/tests/transport/test_wo88_fx_provenance.py` | **new** — the service gate, the legal combinations, the DB refusal, defence in depth |
| `backend/tests/test_migrations.py` | `test_wo88_fx_provenance_migration_refuses_to_run_over_a_violating_row` |
| `backend/tests/transport/test_g3_3_tie_out.py` | fixture: SEK lines gain `fx_source="ecb"` |
| `backend/tests/transport/test_g2_5_freeze.py` | same |
| `backend/tests/transport/test_g2_6_submission_gates.py` | same |
| `backend/tests/transport/test_wo82_contract_audit.py` | same |
| `backend/tests/transport/test_wo83_overcharge_artifacts.py` | same |
| `backend/tests/transport/test_wo85_canonical_queries.py` | same |
| `backend/tests/transport/test_wo81_recovery.py` | same (the seventh fixture — the cross-currency dashboard proof; scenario verified intact) |
| `backend/tests/transport/test_g3_4_capture_checks.py` | a PLN line labelled `fx_source="eur"` (the identity provenance) now carries `ecb` |
| `backend/tests/transport/test_wo87_savings.py` | the two §4.15 tests keep their names and their assertion; the now-unstorable row is exercised against the guard directly (see *Documented interpretations*) |
| `backend/tests/transport/test_wo87_savings_routes.py` | same, for the wire test |
| `README.md` | 86 → 87 Alembic revisions (same commit as the migration) |
| `docs/transport/rules.md` | the R56 row |
| `TODO.md` | WO-88 row, M5 cell, suite line |

### Implementation guidance

1. **State the invariant once, in words, before writing SQL.** A stored fuel
   line asserts a euro figure. Exactly two combinations make that assertion
   false:
   * `fx_source = 'unknown'` while a EUR figure is present — the writer
     positively recorded *"no rate available"* (`app/models/fx.py:27`). Since
     `net_eur` is NOT NULL on this table, the honest outcome of "no rate" is
     **no row**, which is the branch `statement_ingest` already takes.
   * a non-EUR document currency with `fx_source IS NULL` — a foreign amount
     with no recorded conversion, labelled EUR. Master-context §4.14: *"it
     never labels a foreign amount EUR."*

   A **EUR-currency row with a NULL `fx_source` stays legal**: EUR is the
   identity and involves no rate. That carve-out is deliberate and it is what
   keeps every EUR fixture and every EUR production line working unchanged
   (`savings._require_eur_basis` makes the same carve-out for the same reason).
2. **The service gate, fail CLOSED.** `_require_fx_provenance(currency,
   fx_source)` is a pure function raising
   `ValidationError(code="fx_rate_unavailable")` — the code
   `statement_ingest.py:223`, `rebate.py:259` and `savings.py:476` already
   raise for the same class of failure, verified before reuse; no new code
   slug is invented. It runs after the module entitlement and the country
   normalization and **before** the entity fetch and the natural-key lookup:
   the argument tuple is refused on its own terms, before the database is
   touched at all, so a refused call provably writes nothing (no row, no audit
   event, not even a read). Currency is compared case-insensitively
   (`(currency or "").strip().upper()`), matching how `savings` and `rebate`
   read it.
3. **The database CHECK, same invariant, both dialects.** Follow the
   `FX_SOURCE_CHECK` precedent's form — plain portable SQL, no
   `IS DISTINCT FROM` (SQLite gained it only in 3.39), no dialect-specific
   syntax:

   ```sql
   (fx_source IS NULL OR fx_source <> 'unknown' OR net_eur IS NULL)
   AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)
   ```

   `upper()` is immutable on both SQLite and PostgreSQL and is therefore legal
   in a CHECK on both. The `net_eur IS NULL` disjunct is *deliberately kept
   even though the column is NOT NULL today*: it states the invariant as
   ADR-0010 states it, so if a future order makes the EUR columns nullable the
   constraint keeps meaning the right thing instead of silently becoming
   wrong. The rebate table gets the same expression over `amount_eur`.
4. **Declare the constraint in the model as well as the migration** (the WO-8
   pattern: `ck_expense_items_fx_source` lives in both). The models are what
   `create_all` builds in the test suite, so the CHECK is live in every SQLite
   test database, and `tests/test_migrations.py::test_models_match_migration_head`
   keeps the two definitions honest on tables and columns.
5. **The migration refuses rather than guesses.** See *Database / migration
   impact*.
6. **Fixture repair is a privilege raise, not a weakening (§9).** The finding-4
   fixtures assert a SEK/PLN line with a EUR figure and no provenance. The
   correct fixture is the one a real ingestion produces: the same figures
   carrying `fx_source="ecb"`. Every existing assertion in those files is left
   exactly as it is.

### Documented interpretations (stated, never silently assumed)

- **After this order the inconsistent row is UNSTORABLE, so WO-87's two §4.15
  tests can no longer seed one through storage.** They keep their names, their
  `fx_rate_unavailable` assertion and their reason for existing; what changes
  is how the untrustworthy row reaches the guard — the writer's own refusal is
  asserted first (the new first layer), and `savings._require_eur_basis` is
  then exercised on a detached, never-persisted `FuelTransaction` (the second
  layer, unchanged code). The route test keeps its wire assertion by feeding
  that same detached row into the real guard through the real route and the
  real exception handler. This is recorded as an interpretation because
  "test through the real path" (`WORK_ORDER_TEMPLATE.md`) normally forbids it:
  here the real path is *precisely what is being removed*, and a test that can
  no longer construct its input is evidence the fix worked, not a licence to
  delete the assertion.
- **The gate runs before the entity fetch**, so a call that is wrong about BOTH
  the entity and the provenance reports `fx_rate_unavailable`, not
  `entity_not_found`. Either ordering is defensible; this one is chosen because
  the provenance check is pure and touches no database, and because a refused
  call must be provably free of side effects.
- **`vat_off_invoice_rebates` gets the database floor but no new service gate.**
  `rebate._resolve_eur` already refuses (`fx_rate_unavailable`) and can only
  emit `eur` or `ecb`; adding a second service-layer check there would be dead
  code. The missing CHECK constraints are the real gap (finding 5).

### Invariants this order must preserve

- **§4.15 (one FX convention, `unknown` ⇒ NULL, never a guessed number)** — this
  is the invariant the order *installs*, at two layers.
- **§4.14 (no aggregate across currencies without a recorded conversion)** — a
  foreign amount can no longer become a EUR summand without provenance.
- **§4.9 (Decimal money)** — untouched; this order compares provenance strings
  and quantizes nothing new.
- **§4.20 (frozen wire contract)** — the refusal reuses an existing `code`
  slug; no route, schema or status code changes.
- **§4.16 (every mutation audited)** — a refused ingestion writes no row and
  therefore emits no audit event; a successful one is audited exactly as before.
- **§4.1/§4.2 (tenancy, RLS/model set-equality)** — no new table, so
  `TENANT_TABLES` and the RLS parity test are untouched; the migration adds
  constraints to two tables that already carry FORCE RLS.
- **R56** (`BA_fleet_fuel.md:1433`) — *"No coverage ⇒ NULL, never a fabricated
  pass"* — enforced at the writer and in storage, not merely at one reader.
- **R49** — the NET EUR/L basis stays the basis; this order guarantees the EUR
  half of it is real.

### Database / migration impact

One new revision, `down_revision = b3d8f1c04e97` (the current single head),
adding **three CHECK constraints** and **no column, no table, no index, no RLS
policy** (both tables already have theirs — `fuel_transactions` from
`fc45baaf3283`, `vat_off_invoice_rebates` from `b3d8f1c04e97`).

**Backfill safety.** The migration is fail-CLOSED and self-reporting, following
the WO-8 precedent (`b1c3e5a7f9d1`: *"DATA migration runs BEFORE the constraints
so no legacy value can violate them"*, printing the reconciliation report):

1. It SELECTs every violating row from both tables and prints one line per row
   (`id`, supplier/source, period, currency, `fx_source`, the euro figure).
2. If the count is zero — the state of this tree, verified in finding 4 — it
   creates the constraints and the print is a single "0 violating rows" line.
3. If the count is non-zero it **raises and refuses to migrate**, naming the
   rows. It does not delete them (deleting validated transaction history is not
   a migration's decision), does not NULL the euro (the column is NOT NULL),
   and above all does not invent a rate. The operator's remedy is stated in the
   error: re-ingest the affected statement through `statement_ingest`, which
   refuses the unconvertible line properly, or remove the rows deliberately.
   This is the §9 rule — a migration never takes a business decision
   unilaterally — expressed as a refusal instead of a guess.

**Downgrade** drops the three constraints and nothing else: no value is
destroyed and no column type changes, so `upgrade → downgrade → upgrade` is
clean and lossless (asserted by the existing
`test_migrations_apply_and_roundtrip_from_empty`).

### Testing requirements

`backend/tests/transport/test_wo88_fx_provenance.py` (**new**)
- `test_wo88_ingest_refuses_an_unknown_fx_source_carrying_a_eur_figure` —
  `fx_rate_unavailable`, and **zero** `fuel_transactions` rows and **zero**
  audit events afterwards.
- `test_wo88_ingest_refuses_a_foreign_currency_with_no_recorded_conversion` —
  same code, same "nothing written" assertion.
- `test_wo88_the_gate_runs_before_any_database_read` — a refused call with a
  non-existent entity still reports `fx_rate_unavailable`.
- `test_wo88_the_three_legal_combinations_are_accepted` — EUR + `eur`,
  PLN + `ecb`, PLN + `stated`; each row stored with its provenance intact.
- `test_wo88_a_eur_line_with_no_provenance_is_still_accepted` — the identity
  carve-out (both sides of the gate, per the template's boundary rule).
- `test_wo88_the_database_refuses_a_direct_insert_that_bypasses_the_service` —
  `IntegrityError` on a raw ORM insert of each inconsistent combination. **The
  load-bearing test of this order: the database itself refuses.**
- `test_wo88_the_database_refuses_an_update_that_makes_a_stored_row_inconsistent`
  — a legal row updated to `fx_source='unknown'` is refused (this is exactly
  the tamper WO-87's route test used to perform).
- `test_wo88_the_rebate_table_refuses_the_same_inconsistency` +
  `test_wo88_the_rebate_table_refuses_a_free_text_fx_source` (finding 5, both
  new constraints).
- `test_wo88_the_recorded_rebate_writer_can_only_produce_eur_or_ecb` — the
  service half of finding 5, asserted rather than assumed.
- `test_wo88_the_analysis_boundary_still_refuses` — `savings._require_eur_basis`
  unchanged and still raising: defence in depth, both layers proven in one file.
- `test_wo88_a_second_tenants_row_is_unaffected` — the gate is per-row and
  org-agnostic; tenant B's legal rows are untouched by tenant A's refusal.

`backend/tests/test_migrations.py`
- `test_wo88_fx_provenance_migration_refuses_to_run_over_a_violating_row` —
  build the schema at `b3d8f1c04e97`, insert one violating row raw, run
  `alembic upgrade head`, assert it **fails**, prints the offending row, and
  leaves the constraint uncreated; delete the row, re-run, assert it succeeds
  and the constraint is now live (a raw insert is refused).

**The fixture sweep, unbounded** (finding 4's correction). Every non-EUR
currency literal in `backend/tests` was re-listed with no `head` and classified:

```bash
grep -rn 'currency\s*=\s*"[A-Za-z][A-Za-z][A-Za-z]"' tests --include=*.py | grep -vi '"EUR"'
```

* **reaches the writer with no provenance ⇒ raised to `ecb`** — `test_g3_3_tie_out.py`,
  `test_g2_5_freeze.py`, `test_g2_6_submission_gates.py`, `test_wo82_contract_audit.py`,
  `test_wo83_overcharge_artifacts.py`, `test_wo85_canonical_queries.py`,
  `test_wo81_recovery.py`;
* **reaches the writer with a WRONG provenance** — `test_g3_4_capture_checks.py`
  labelled a PLN line `fx_source="eur"` (the identity, which only a EUR line can
  truthfully claim). The gate accepts it by design — WO-88 refuses a MISSING
  provenance, not a wrong one — so this was not a failure, but a fixture should
  not assert what no ingestion would write, and it now carries `ecb`. It is also
  the only live instance of the out-of-scope third inconsistency, which is
  evidence that rule is worth a follow-up order rather than a shrug;
* **never reaches the writer** — ZAR/PLN statement cases (`test_g3_2_bp_*`,
  `test_g3_2_dkv_*`, `test_wo84_rebate_*`) are refused UPSTREAM by
  `statement_ingest` / `rebate` with the same `fx_rate_unavailable`; parser-row
  (`test_g3_2_tfc_*`) and baseline-row (`test_g3_3s2_extraction_baseline.py`)
  cases build no `fuel_transactions` row at all; `test_wo85:320` is a pure
  Select-builder assertion; the AP/AR hits (`test_sepa`, `test_analytics`,
  `test_expense_management`, `test_money_invariants`) are other tables entirely.

`grep -rn 'fx_source="unknown"' tests` over the same tree: the only transport
hits are WO-88's own negative tests; the rest are `invoices`/`expense_items`,
where `total_eur` is nullable and the invariant already holds.

Unchanged and re-run as the regression net: `tests/transport/test_wo87_savings.py`,
`test_wo87_savings_routes.py`, `test_wo87_r53_framing.py`,
`tests/test_money_invariants.py`, `tests/test_rls.py`, `tests/test_tenancy_parity.py`.

### Acceptance criteria (verifiable checklist)

- [ ] `ingest_transaction(..., currency="EUR", fx_source="unknown")` raises
      `ValidationError` with `code == "fx_rate_unavailable"` and
      `SELECT COUNT(*) FROM fuel_transactions` is **0** afterwards.
- [ ] `ingest_transaction(..., currency="PLN", fx_source=None)` — same code,
      same zero count.
- [ ] `ingest_transaction(..., currency="EUR", fx_source=None)` still succeeds
      (the identity carve-out), and so do `("EUR","eur")`, `("PLN","ecb")`,
      `("PLN","stated")`.
- [ ] A raw `db.add(FuelTransaction(..., fx_source="unknown"))` + `commit()`
      raises `IntegrityError` — the database refuses what the service refuses.
- [ ] `UPDATE fuel_transactions SET fx_source='unknown'` on a stored row is
      refused by the database.
- [ ] A raw insert into `vat_off_invoice_rebates` with `fx_source='banana'` is
      refused, and so is `fx_source='unknown'` beside a positive `amount_eur`.
- [ ] `alembic heads | wc -l` is **1**; `alembic upgrade head` then
      `alembic check` is clean; `alembic downgrade -1 && alembic upgrade head`
      round-trips.
- [ ] The migration prints `[WO-88] 0 violating rows` on a clean database and
      **raises** (non-zero exit) on a database seeded with one violating row,
      naming that row's id.
- [ ] `python -m pytest tests/transport/test_wo87_savings.py tests/transport/test_wo87_savings_routes.py tests/transport/test_wo87_r53_framing.py -q`
      is green with every WO-87 test name still present — the analysis-boundary
      refusal is not weakened.
- [ ] `README.md` says 87 Alembic revisions and
      `tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`
      is green.
- [ ] Full backend suite at or above the 2135 baseline, every delta explained.
- [ ] Postgres gate green on a scratch NOSUPERUSER cluster:
      `tests/test_rls.py tests/test_numbering_concurrency.py tests/test_transport_lock_concurrency.py`.

### Rollback strategy

Code revert plus `alembic downgrade -1`. The downgrade drops the three CHECK
constraints and restores nothing else, because nothing else changed: no column,
no value, no row. Nothing is one-way — the migration writes no data, and it
cannot have deleted or restated a row because it refuses rather than corrects.
The narrow mitigation short of a full revert is dropping the constraints alone
(`alembic downgrade -1`) while keeping the service gate, which leaves the
invariant enforced on every path except a raw `INSERT`.

### Documentation to update

`docs/transport/rules.md` (the R56 row — its first), `TODO.md` (WO-88 row, M5
cell, suite line), `README.md` (the pinned revision count, in the migration's
own commit). No ADR is contradicted: **ADR-0010** is the FX-provenance ADR this
order finally enforces at the writer, and its text (*"no rate available → the
EUR figure is NULL, never a guessed number"*) needs no change — the tree simply
did not implement it on this table.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo88_fx_provenance.py -q
python -m pytest tests/transport/test_wo87_savings.py \
                 tests/transport/test_wo87_savings_routes.py \
                 tests/transport/test_wo87_r53_framing.py -q   # WO-87 still green
python -m pytest tests/test_migrations.py -q
python -m pytest -q                                            # full baseline
# DEMONSTRATION: the row that lies about its own euro is now unwritable —
# refused by the service AND, one layer down, by the database itself.
python - <<'PY'
import asyncio
from decimal import Decimal
from app.core.errors import ValidationError
from app.services.transport import fuel_ingest
print(fuel_ingest._require_fx_provenance.__doc__.splitlines()[0])
for ccy, src in (("EUR", "unknown"), ("PLN", None)):
    try:
        fuel_ingest._require_fx_provenance(ccy, src)
        raise SystemExit(f"NOT REFUSED: {ccy}/{src}")
    except ValidationError as e:
        print(f"{ccy}/{src} -> {e.code}: {e}")
for ccy, src in (("EUR", None), ("EUR", "eur"), ("PLN", "ecb"), ("PLN", "stated")):
    fuel_ingest._require_fx_provenance(ccy, src)
    print(f"{ccy}/{src} -> accepted")
PY
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```
