**WORK ORDER 89 — FX provenance honesty: the WRONG-provenance rule (board G4.7 follow-up; R56, R49; master-context §4.15/§4.14/§4.20). Effort S. Priority P1. Milestone M5. Depends on: WO-88 (the `_require_fx_provenance` gate and the two CHECK constraints this order extends), WO-84 (`vat_off_invoice_rebates`), WO-87 (the analysis boundary that stays).**

### 0. Current-state recon (done BEFORE any design — "who can write a row that lies about *needing* a rate?")

Six findings, each verified in the tree at `554e9c2`, several of them by running
code rather than reading it. The commissioning brief's framing was **not** taken
on trust; where it is imprecise, the correction is recorded here.

**Finding 0 — the framing is right about the rule and stale about the fixture.**
The brief says *"WO-88 found a live fixture instance, so the shape is
reachable."* WO-88 did find one — `tests/transport/test_g3_4_capture_checks.py`
labelled a PLN line `fx_source="eur"` — but it **also fixed it in the same
order** (WO-88 *Testing requirements*, second sweep bullet; the line now reads
`fx_source="eur" if str(currency).upper() == "EUR" else "ecb"`,
`test_g3_4_capture_checks.py:85`). So the fixture evidence for reachability is
historical, not current. Reachability is nevertheless real and worse than a
fixture — see finding 2, which demonstrates it through the **production
writer**.

**Finding 1 — exactly four tables in the tree carry an `fx_source` column, and
only two of them carry the `(currency, fx_source, <EUR amount>)` quadruple the
rule is about.** `grep -rn "fx_source" app/models/` returns four models. The
distinction that matters is *what the converted column is denominated in*:

| table | `currency` | the converted column | `fx_source` | is it a EUR column? |
|---|---|---|---|---|
| `fuel_transactions` | NOT NULL, stored **verbatim** by the writer (not upper-cased) | `net_eur` / `net_eur_eff` / `vat_eur`, all NOT NULL | nullable | **yes** |
| `vat_off_invoice_rebates` | NOT NULL, `.upper()`-normalised by `record_rebate` (`rebate.py:319`) | `amount_eur`, NOT NULL, `> 0` | nullable | **yes** |
| `invoices` | NOT NULL, `.upper()`-normalised in the route (`invoices.py:163`) | `total_eur`, **nullable** | nullable | **yes** |
| `expense_items` | **nullable**, and it is the *original* currency | `amount` — denominated in the **report currency**, which need not be EUR (`expense.py:154-163`) | nullable | **no — there is no EUR column on this table at all** |

That last row is the recon's first real correction to the brief's framing: on
`expense_items` the wrong-provenance rule *as stated* does not even typecheck.
`fx_source='eur'` there means *"identity: the original currency and the report
currency are the same, and that currency is the euro"*
(`expenses.py:114`), not *"this amount is euros"*. A SEK receipt on a NOK report
converts correctly and stores `fx_source = None`, never `eur`. See finding 5.

**Finding 2 — the combination is representable on all four tables, and on
`fuel_transactions` the LIVE PRODUCTION WRITER stores it.** Not inferred from
the CHECK expression — executed. A throwaway probe (three tests, run and then
deleted) against the real fixtures:

```
RECON fuel_transactions: STORED PLN eur 1400.00      <- via fuel_ingest.ingest_transaction
RECON vat_off_invoice_rebates: STORED [('PLN', 'eur')]
RECON invoices: STORED [('PLN', 'eur', Decimal('100.00'))]
3 passed in 2.93s
```

The first line is the finding. WO-88's `_require_fx_provenance` refuses a
*missing* provenance and says nothing about a *wrong* one, and its CHECK
```sql
(fx_source IS NULL OR fx_source <> 'unknown' OR net_eur IS NULL)
AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)
```
is satisfied by `('PLN', 'eur')`: clause 1 passes (`fx_source` is not
`unknown`), clause 2 passes (`fx_source IS NOT NULL`). So the ONE service that
writes the ONE table every VAT claim line, every overcharge demand letter, every
tie-out and the whole recovery dashboard are built from will accept and store a
PLN line asserting `net_eur = 1400.00` with the provenance *"this amount was
already EUR, identity, rate 1"* (`app/models/fx.py:21`). This is strictly worse
than the shape WO-88 closed: WO-88's finding 1 was a row no production caller
produced; this one is a row the production writer **stores on request**.

**Finding 3 — the rule is sound, and it is not already enforced anywhere.**
`FxSource.eur` is documented as *"the amount was already EUR (identity, rate
1)"*. If the document currency is not EUR, the amount was demonstrably not
already EUR, so there is no legitimate reading under which the combination is
honest. Searched for an existing enforcement, and there is none: neither
`_require_fx_provenance` (both clauses quoted above), nor
`ck_fuel_transactions_fx_provenance` / `ck_vat_off_invoice_rebates_fx_provenance`
(same expression), nor `savings._require_eur_basis` (`savings.py:441-478` —
its predicate is `source == FX_SOURCE_UNKNOWN or (currency != CURRENCY and
source is None)`, the missing case only), nor any schema (`fx_source` appears
only on `FuelTransactionOut` / `RebateOut`, never on an `In` shape — the wire
cannot set it at all on either transport table).

**Finding 4 — no stored row, seed, migration or fixture exhibits the shape
today.** Verified, and the fixture half was verified with an **unbounded** sweep
(WO-88's own recorded lesson — its first pass piped a grep through `head -30`
and missed the seventh fixture; that miss is why this order's sweep is quoted in
full in *Testing requirements*):

* the dev database holds zero rows in all four tables, and
  `SELECT COUNT(*) … WHERE upper(currency) <> 'EUR' AND fx_source = 'eur'`
  returns 0 on each;
* `grep -rln "op.bulk_insert\|INSERT INTO\|exec_driver_sql" alembic/versions/*.py`
  returns three revisions (`d99c826e4767`, `deb447b02296`, `efd78f4cbe2e`), none
  of them FX-bearing;
* `app/seed.py:240` writes `fx_source="stated"` on a foreign-currency AP
  `Invoice` — legal, and the only `fx_source` in the seeder;
* every one of the 118 `fx_source` occurrences under `backend/tests` was listed
  and classified; the only non-EUR line carrying `eur` was
  `test_g3_4_capture_checks.py`, already corrected by WO-88 (finding 0).

So this order needs **no fixture repair at all** — a deliberate difference from
WO-88, stated up front so its absence is not read as a skipped sweep.

**Finding 5 — WO-88's second follow-up (`invoices` / `expense_items`) reaches
the right verdict from partly the wrong evidence.** WO-88 recorded the AP/AR
core as *"the invariant already holds BY CONSTRUCTION — `total_eur` is nullable
and `fx.eur_total` returns `(None, "unknown")` when no rate exists"*. That
argument is about the *missing*-provenance rule; a nullable `total_eur` says
nothing whatever about a non-EUR row claiming the EUR identity. The **verdict**
survives, but on different evidence:

* `invoices.fx_source` has exactly two writers, both of which take it from
  `fx.eur_total` and nowhere else — `app/api/routes/invoices.py:165` and
  `app/api/routes/invoice_review.py:330` (`grep -rn "Invoice("` over `app/`
  returns only those two plus `app/seed.py`). `fx.eur_total`
  (`app/services/fx.py:349-363`) returns `"eur"` on exactly one branch,
  `if currency == "EUR"`, reached only after `currency = (currency or
  "EUR").upper()`. It is structurally incapable of stamping `eur` on anything
  else. `InvoiceCreate` carries no `fx_source` field (`app/schemas/invoice.py` —
  it is on `InvoiceOut` only), so the wire cannot supply one.
* `expense_items.fx_source` is set only by `expenses.apply_item_fx`
  (`expenses.py:99-148`). It DOES accept a client-supplied value into the ORM
  object first (`item_from`, `expenses.py:176`) — but `apply_item_fx` runs on
  every item built through `build_items` and assigns `fx_source` on **every one
  of its six branches**, so the client's value is always overwritten. The only
  branch that emits `eur` is `item.fx_source = FxSource.eur.value if ccy ==
  "EUR" else None` (line 114), inside `if ccy == report_ccy`.

**Verdict: closed by analysis, no constraint added** — with the caveat of
finding 1 that on `expense_items` a CHECK would be enforcing a *different*
invariant (identity-claim consistency against the report currency) rather than
§4.14's *"never labels a foreign amount EUR"*, because that table has no EUR
column. Both tables remain representable at the storage layer, which is a real
platform-level gap and is reported as such (§10) rather than fixed here: this
order is transport-scoped, the hole is not live, and `invoices`/`expense_items`
are the AP/AR core with their own migration and RLS surface.

**Finding 6 — the reuse question, answered against the tree's own vocabulary.**
The tree's FX refusal codes are `fx_rate_unavailable` (5 sites),
`fx_stated_inconsistent` (2 sites), `fx_cross_currency_unsupported` and
`fx_deviation`. `fx_rate_unavailable` is raised where a rate is *needed and
missing* and every one of its messages tells the operator to go and get the rate
("refresh the ECB rates before retrying", "resolve the rate and re-ingest"). The
wrong-provenance failure is the opposite situation: a rate is needed and may
well be sitting in the cache — the caller asserted none was needed. Sending an
operator to refresh ECB rates would not fix a PLN line stamped `eur`. See
*Documented interpretations* for the decision.

### Objective and business value

`fuel_ingest.ingest_transaction` will today accept and store a fuel line whose
document currency is PLN, whose `net_eur` is 1400.00, and whose FX provenance is
`eur` — *"the amount was already EUR (identity, rate 1)"* (finding 2, proven by
executing the writer, not by reading it). Neither WO-88's writer gate nor its
two CHECK constraints nor WO-87's analysis boundary refuses it (finding 3): all
three test for a *missing* provenance. The row is a fabricated conversion
wearing a provenance flag that says no conversion was needed, and it is
indistinguishable downstream from an honest EUR line — `net_eur_eff` flows
straight into `claim_lines.build_claim_lines`, `contract_audit`'s overcharge
euro, the tie-out, the close and the recovery dashboard.

Who pays. The same people WO-88 protected, against the same failure by the other
route. A haulier files a cross-border VAT refund under Dir. 2008/9/EC on these
euros; WO-83 prints them on our own letterhead as a 30-day payment demand to a
supplier. A zloty amount asserted as euros is a wrong number on a legal filing
and a false assertion to a counterparty — and unlike WO-88's case, it survives
every layer the platform has. Closing it costs one clause in an existing
predicate and one migration, and it completes the invariant: after this order a
stored EUR figure can neither deny that a rate was used nor deny that one was
needed.

### Scope

**In scope:**
- `app/services/transport/fuel_ingest.py` — **extend the existing
  `_require_fx_provenance`** with the third clause. One predicate, not a rival
  one (WO-85's registry lesson; the R3 one-predicate precedent). New code slug
  `fx_provenance_inconsistent` (finding 6, justified in *Documented
  interpretations*).
- `app/models/transport/fuel_transaction.py` — extend `FX_PROVENANCE_CHECK`
  with the third conjunct. The constraint keeps its WO-88 name
  (`ck_fuel_transactions_fx_provenance`): it is the same invariant, stated more
  completely, and a second same-shaped constraint beside it would be the
  storage-layer version of a rival predicate.
- `app/models/transport/off_invoice_rebate.py` — the same third conjunct on
  `_FX_PROVENANCE_CHECK` over `amount_eur` (parity, per WO-88's finding 5).
- One new Alembic revision **replacing** both provenance constraints (drop +
  recreate, since a CHECK cannot be altered in place on either dialect), single
  head preserved, with the same refusing pre-flight WO-88 established.
- `backend/tests/transport/test_wo89_fx_wrong_provenance.py` — **new**.
- `backend/tests/test_migrations.py` — the pre-flight refusal test for this
  revision.
- `docs/transport/rules.md` — R56 gains its **second** enforcement consumer.
- `TODO.md` — the WO-89 row, the M5 cell, the suite line.
- `README.md` — the pinned Alembic revision count 87 → 88, in the SAME commit as
  the migration (`tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`).

**Out of scope (named, with the reason):**
- **`invoices` / `expense_items`.** Assessed, verdict recorded, no constraint
  (finding 5). The invariant holds at both writers structurally; the storage
  layer is open on both, which is a platform finding reported in §10, not a
  transport order's business. `expense_items` additionally would need a
  *different* rule, because its converted column is not EUR.
- **`savings._require_eur_basis`** — unchanged, again. After this order the
  wrong-provenance row is unstorable, so the analysis boundary cannot meet one;
  widening a guard against a state its own storage layer forbids buys nothing
  and would make WO-87's tests assert something they were not written to assert.
  Its two existing refusal conditions and its `fx_rate_unavailable` code stay
  byte-identical, and are re-asserted here.
- **A service gate on `rebate.record_rebate`** — `_resolve_eur` already cannot
  emit `eur` for a non-EUR currency (`rebate.py:253-255`, `cur == "EUR"` after
  `.upper()`), so a second service-layer check would be dead code. This is
  WO-88's own reasoning for the same table, reused deliberately. The DB floor
  still lands, because storage protects the writers that do not exist yet.
- **Any fixture repair** — the sweep found nothing to repair (finding 4).
- **Any route, schema, permission or SPA change.** `fx_source` is on no `In`
  schema; the SPA's refusal map fails OPEN on an unmapped code
  (`frontend/src/lib/transportClaims.ts::claimRefusal`), so a new backend slug
  degrades to the server's own sentence with no frontend change.
- **Correcting a violating row.** There are none (finding 4); restating a stored
  money figure is a business decision (§9), not a migration's.

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/fuel_ingest.py` | third clause in `_require_fx_provenance` + `FX_SOURCE_EUR` literal + docstring rationale |
| `backend/app/models/transport/fuel_transaction.py` | third conjunct in `FX_PROVENANCE_CHECK` |
| `backend/app/models/transport/off_invoice_rebate.py` | third conjunct in `_FX_PROVENANCE_CHECK` |
| `backend/alembic/versions/<rev>_wo89_fx_wrong_provenance.py` | **new** — drop + recreate both provenance constraints, refusing pre-flight |
| `backend/tests/transport/test_wo89_fx_wrong_provenance.py` | **new** — the service gate, the legal combinations, the DB refusal, defence in depth |
| `backend/tests/test_migrations.py` | `test_wo89_wrong_provenance_migration_refuses_to_run_over_a_violating_row` |
| `README.md` | 87 → 88 Alembic revisions (same commit as the migration) |
| `docs/transport/rules.md` | the R56 row gains its second enforcement consumer |
| `TODO.md` | WO-89 row, M5 cell, suite line |

### Implementation guidance

1. **State the third case in words first.** A stored fuel line asserts a euro
   figure *and* how it got there. WO-88 closed the two ways the figure can deny
   that a rate was **used**. This is the one way it can deny that a rate was
   **needed**: a document currency that is not EUR, carrying `fx_source='eur'`,
   which `app/models/fx.py:21` defines as *"the amount was already EUR
   (identity, rate 1)"*. It was not. The honest values for such a line are `ecb`
   (converted at the reference rate) or `stated` (the document's own
   conversion); a line that can reach neither is refused entirely, which is the
   WO-88 case.
2. **Extend the ONE predicate, fail CLOSED.** `_require_fx_provenance` grows a
   third `if`, in the same pure function, raising the same way and from the same
   place in the gate order (after module entitlement, before the entity fetch),
   so a refused call still provably writes nothing — no row, no audit event, not
   even a read. Currency is compared with the existing
   `cur = (currency or "").strip().upper()` already computed at the top. Do NOT
   add a second predicate, a second helper or a second call site: one rule, one
   function, exactly as R3's lock gate and WO-85's query registry established.
3. **The new code is `fx_provenance_inconsistent`** — see *Documented
   interpretations* for why it is not `fx_rate_unavailable`. The message names
   the currency and the remedy in the caller's terms ("convert it, or record the
   document's own conversion"), never "refresh the ECB rates".
4. **The database CHECK, same invariant, both dialects.** Add a third conjunct
   to the existing expression, keeping WO-88's portable-SQL discipline — no
   `IS DISTINCT FROM`, `upper()` because it is immutable on both SQLite and
   PostgreSQL:

   ```sql
   (fx_source IS NULL OR fx_source <> 'unknown' OR net_eur IS NULL)
   AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)
   AND (upper(currency) = 'EUR' OR fx_source <> 'eur')
   ```

   The third conjunct is written as a disjunction over `upper(currency)` rather
   than `NOT (… AND …)` to match the form of the second, so the two read as one
   rule about non-EUR rows. The rebate table gets the identical conjunct over
   `amount_eur`'s constraint.
5. **The migration drops and recreates.** Neither SQLite nor PostgreSQL can
   ALTER a CHECK expression in place, so both `ck_*_fx_provenance` constraints
   are dropped and recreated with the fuller expression, inside
   `op.batch_alter_table` (SQLite rebuilds the table; the WO-88 revision proved
   that rebuild preserves the natural key, the other CHECKs and all three
   indexes). `ck_vat_off_invoice_rebates_fx_source` — WO-88's value-domain
   constraint — is NOT touched. The downgrade restores WO-88's exact
   expressions, so `upgrade → downgrade → upgrade` returns the schema to a state
   `alembic check` still calls drift-free.
6. **The migration refuses rather than guesses**, exactly as WO-88's does and
   for exactly the same reasons: the conversion rate the row should have used
   cannot be reconstructed from the row, the EUR column is NOT NULL so it cannot
   be nulled, and deleting validated transaction history (or a recorded rebate
   document) is a business decision (§9). It prints every offending row and
   raises, naming them.

### Documented interpretations (stated, never silently assumed)

- **A new code slug, `fx_provenance_inconsistent`, rather than reusing
  `fx_rate_unavailable`.** The brief permits either and asks for a
  justification. Reuse was rejected on operator-remedy grounds: every existing
  `fx_rate_unavailable` message in the tree ends in an instruction to obtain the
  rate ("refresh the ECB rates before retrying" —
  `statement_ingest.py:222`; "resolve the rate and re-ingest" —
  `fuel_ingest.py:101`; "converted at a guessed rate" — `rebate.py:258`), and
  that instruction is actively wrong here: the rate is very likely available and
  the caller simply claimed not to need one. The new slug follows the tree's own
  naming (`fx_stated_inconsistent`, `statement_ingest.py:61,198` — same `fx_`
  prefix, same `_inconsistent` suffix, same meaning of *"two things this row
  says about itself cannot both be true"*). It is additive to the wire contract,
  not a change to it (§4.20: the shape `{"detail", "code"}` is frozen; the code
  vocabulary is open — `AppError`'s docstring calls `code` *"a stable
  machine-readable slug"*, and nothing in the tree enumerates the set). The SPA
  needs no change: `claimRefusal` fails OPEN on an unmapped code and renders the
  server's own sentence.
- **The constraints keep their WO-88 names.** `ck_fuel_transactions_fx_provenance`
  after this order means *"this row's euro does not contradict its provenance"*,
  which is what it always meant; WO-88 could only state two thirds of it. A
  second constraint (`…_fx_provenance_2`) would fragment one invariant across
  two objects, which is the storage-layer form of the rival-predicate mistake
  this codebase has twice paid for. The cost is that the migration must drop and
  recreate rather than only create; that cost is one `batch_alter_table` block.
- **`savings._require_eur_basis` is deliberately NOT widened.** WO-88 kept it as
  the third layer *unchanged*, and the same argument applies with more force
  here: after this order no wrong-provenance row can exist in storage, so the
  analysis boundary can never encounter one, and a guard clause that cannot fire
  is not defence in depth — it is unreachable code with a test that proves
  nothing. What defence in depth means here is that the two layers that CAN see
  the row (the writer and storage) both refuse it. WO-87's guard is re-asserted
  byte-for-byte in this order's test file so the decision is visible rather than
  silent.
- **No fixture was raised, and that is a finding, not an omission.** WO-88's
  order raised eight; this one raises none, because the unbounded sweep
  (quoted in *Testing requirements*) found the only instance already corrected
  by WO-88 itself.

### Invariants this order must preserve

- **§4.15 (one FX convention; `fx_source` is what it says it is)** — the
  invariant this order completes. `eur` may now only appear beside a EUR
  document currency, on both transport money tables, at both layers.
- **§4.14 (no aggregate across currencies without a recorded conversion)** — a
  foreign amount can no longer become a EUR summand by *claiming it never needed
  converting*, which was the last remaining way in.
- **§4.9 (Decimal money)** — untouched; this order compares provenance strings
  and quantizes nothing new.
- **§4.20 (frozen wire contract)** — the response shape is unchanged; one
  additive `code` value, on a failure path no route can currently reach, with a
  SPA that fails open on unmapped codes.
- **§4.16 (every mutation audited)** — a refused ingestion writes no row and
  emits no audit event (the gate is still ahead of the first query); a
  successful one is audited exactly as before.
- **§4.1/§4.2 (tenancy, RLS/model set-equality)** — no new table, so
  `TENANT_TABLES` and the RLS parity test are untouched; both tables already
  carry FORCE RLS.
- **WO-88's and WO-87's layers** — the writer gate keeps both of its existing
  clauses and its `fx_rate_unavailable` code for them; the value-domain CHECKs
  are untouched; `savings._require_eur_basis` is not edited at all. Three layers
  in, three layers out.
- **R56** (`BA_fleet_fuel.md:1433`) — *"No coverage ⇒ NULL, never a fabricated
  pass"* — a provenance flag that lies about coverage is a fabricated pass.
- **R49** — the NET EUR/L basis stays the basis; this order guarantees the EUR
  half of it is real.

### Database / migration impact

One new revision, `down_revision = e4a7c1d92f08` (WO-88, the current single
head). It **drops and recreates two CHECK constraints** and adds no column, no
table, no index, no RLS policy and no data.

**Pre-flight, fail CLOSED** — WO-88's precedent, extended to the third
combination:

1. SELECT every row of both tables that either provenance constraint would now
   reject, printing one line per row (`id`, supplier, period, currency,
   `fx_source`, the euro figure).
2. Zero rows — the state of this tree, verified in finding 4 — prints
   `[WO-89] 0 violating rows` and proceeds.
3. Non-zero **raises and refuses to migrate**, naming the rows. It does not
   invent the rate the row should have used, cannot NULL a NOT NULL euro, and
   will not delete transaction history on its own authority. The error states
   the operator's remedy: re-ingest the affected statement through
   `statement_ingest` (which resolves or refuses the conversion properly), or
   remove the rows deliberately.

The pre-flight scans for ALL THREE combinations, not only the new one: a
database restored from before WO-88, or one whose constraints were dropped by
this order's own downgrade, can hold either shape, and a migration that
recreates a constraint must verify what it is about to assert.

**Downgrade** drops both constraints and recreates them with WO-88's exact
expressions. Nothing is lost: no value is written, no column changes, and a row
legal under WO-88 stays legal. `upgrade → downgrade → upgrade` is clean and
lossless (asserted by the existing `test_migrations_apply_and_roundtrip_from_empty`).

### Testing requirements

`backend/tests/transport/test_wo89_fx_wrong_provenance.py` (**new**)
- `test_wo89_ingest_refuses_a_non_eur_line_claiming_the_eur_identity` —
  `code == "fx_provenance_inconsistent"`, status 422, and **zero**
  `fuel_transactions` rows afterwards.
- `test_wo89_a_refused_ingestion_writes_no_audit_event` — §4.16's mirror image.
- `test_wo89_the_gate_still_runs_before_any_database_read` — a refused call with
  a non-existent entity reports the FX code, not `entity_not_found`.
- `test_wo89_the_module_entitlement_still_wins` — gate ordering unchanged.
- `test_wo89_the_refusal_names_the_currency_and_the_remedy` — the message
  contains the currency and does NOT tell the operator to refresh ECB rates
  (the finding-6 reason the code is distinct, asserted rather than asserted-in-prose).
- `test_wo89_wo88s_two_refusals_keep_their_own_code` — `("EUR","unknown")` and
  `("PLN",None)` still raise `fx_rate_unavailable`, not the new slug. **The
  not-weakened test**: one predicate, three cases, three correct codes.
- `test_wo89_every_legal_combination_is_still_accepted` — `("EUR","eur")`,
  `("EUR",None)`, `("PLN","ecb")`, `("PLN","stated")`, `("SEK","ecb")`; each
  stored with its provenance intact.
- `test_wo89_a_lower_case_eur_is_still_the_identity` — `currency="eur"` with
  `fx_source="eur"` is ACCEPTED (both sides of the case-folding boundary; the
  gate and the CHECK must agree).
- `test_wo89_the_database_refuses_a_direct_insert_that_bypasses_the_service` —
  `IntegrityError` on a raw ORM insert of `("PLN","eur")` and `("SEK","eur")`.
  **The load-bearing test of this order.**
- `test_wo89_the_database_refuses_an_update_that_makes_a_stored_row_inconsistent`
  — a stored `("PLN","ecb")` row updated to `fx_source='eur'` is refused.
- `test_wo89_the_database_still_accepts_every_legal_row` — the positive control;
  a constraint that refused everything would pass every negative test above.
- `test_wo89_the_rebate_table_refuses_the_same_wrong_provenance` +
  `test_wo89_the_rebate_table_still_accepts_what_its_writer_produces`.
- `test_wo89_the_rebate_writer_cannot_produce_the_wrong_provenance` — the
  service half asserted, not assumed: `_resolve_eur` returns `eur` only for a
  EUR currency, driven through the real function.
- `test_wo89_the_analysis_boundary_is_unchanged` — `savings._require_eur_basis`
  still refuses WO-88's two combinations with `fx_rate_unavailable` and still
  accepts the legal ones, verbatim.
- `test_wo89_a_second_tenants_row_is_unaffected` — tenant B's identical-looking
  legal row survives tenant A's refusal (overlapping fixtures, §8).
- `test_wo89_the_eur_literal_matches_the_platform_enum` — the module's
  `FX_SOURCE_EUR` literal pinned against `FxSource.eur.value` (the ADR-0023
  rule-2 restatement discipline, same as WO-88's `FX_SOURCE_UNKNOWN` pin).

`backend/tests/test_migrations.py`
- `test_wo89_wrong_provenance_migration_refuses_to_run_over_a_violating_row` —
  build the schema at `e4a7c1d92f08`, insert one `('PLN','eur')` row raw (legal
  at that head), run `alembic upgrade head`, assert it FAILS and names the row;
  delete the row, re-run, assert it succeeds and the constraint is live.

**The fixture sweep, unbounded** (WO-88's recorded lesson — no `head`, no
truncation):

```bash
grep -rn "fx_source" tests --include=*.py           # 118 hits, all classified
grep -rn 'currency\s*=\s*"[A-Za-z][A-Za-z][A-Za-z]"' tests --include=*.py | grep -vi '"EUR"'
```

Every hit assigning `fx_source="eur"` was checked against its currency: the only
non-EUR instance in the tree was `test_g3_4_capture_checks.py:85`, corrected by
WO-88; `tests/test_money_invariants.py:438` and `tests/test_fx.py:74` are EUR
rows, and the remaining transport helpers all key their provenance off the
currency (`None if str(currency).upper() == "EUR" else "ecb"`). **Nothing to
raise.**

Unchanged and re-run as the regression net:
`tests/transport/test_wo88_fx_provenance.py`, `test_wo87_savings.py`,
`test_wo87_savings_routes.py`, `test_wo87_r53_framing.py`,
`tests/test_money_invariants.py`, `tests/test_fx.py`, `tests/test_rls.py`,
`tests/test_tenancy_parity.py`.

### Acceptance criteria (verifiable checklist)

- [ ] `ingest_transaction(..., currency="PLN", fx_source="eur")` raises
      `ValidationError` with `code == "fx_provenance_inconsistent"`, status 422,
      and `SELECT COUNT(*) FROM fuel_transactions` is **0** afterwards.
- [ ] `ingest_transaction(..., currency="EUR", fx_source="unknown")` and
      `(..., currency="PLN", fx_source=None)` still raise
      `fx_rate_unavailable` — WO-88's codes are unchanged.
- [ ] `("EUR","eur")`, `("EUR",None)`, `("eur","eur")`, `("PLN","ecb")`,
      `("PLN","stated")` all still succeed and store their provenance verbatim.
- [ ] A raw `db.add(FuelTransaction(currency="PLN", fx_source="eur", …))` +
      `commit()` raises `IntegrityError`; so does an `UPDATE` of a stored
      `("PLN","ecb")` row to `fx_source='eur'`.
- [ ] The same raw insert into `vat_off_invoice_rebates` is refused, and
      `("EUR","eur")` / `("PLN","ecb")` still store.
- [ ] `alembic heads | wc -l` is **1**; `alembic upgrade head` then
      `alembic check` is clean; `alembic downgrade -1 && alembic upgrade head`
      round-trips.
- [ ] The migration prints `[WO-89] 0 violating rows` on a clean database and
      **raises** on one seeded with a `('PLN','eur')` row, naming that row's id.
- [ ] `python -m pytest tests/transport/test_wo88_fx_provenance.py tests/transport/test_wo87_savings.py tests/transport/test_wo87_savings_routes.py tests/transport/test_wo87_r53_framing.py -q`
      is green with every test name still present and no assertion edited.
- [ ] `README.md` says 88 Alembic revisions and
      `tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`
      is green.
- [ ] Full backend suite at or above the 2162 baseline, every delta explained.
- [ ] Postgres gate green on a scratch NOSUPERUSER cluster:
      `tests/test_rls.py tests/test_numbering_concurrency.py tests/test_transport_lock_concurrency.py`,
      plus both illegal INSERTs rejected BY CONSTRAINT NAME on real PostgreSQL.

### Rollback strategy

Code revert plus `alembic downgrade -1`, which restores WO-88's exact constraint
expressions — so a rollback lands on the WO-88 invariant, not on no invariant at
all. Nothing is one-way: the migration writes no data and cannot have restated a
row, because it refuses rather than corrects. The narrow mitigation short of a
full revert is the downgrade alone, which keeps the service gate (and therefore
the rule on every path that goes through the one writer) while relaxing storage
back to WO-88's floor.

### Documentation to update

`docs/transport/rules.md` (the R56 row — its second enforcement consumer),
`TODO.md` (WO-89 row, M5 cell, suite line), `README.md` (the pinned revision
count, in the migration's own commit). No ADR is contradicted: **ADR-0010** is
the FX-provenance ADR, and its enum documentation (*"`eur` — the amount was
already EUR"*) is precisely what this order finally enforces; it needs no change.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo89_fx_wrong_provenance.py -q
python -m pytest tests/transport/test_wo88_fx_provenance.py \
                 tests/transport/test_wo87_savings.py \
                 tests/transport/test_wo87_savings_routes.py \
                 tests/transport/test_wo87_r53_framing.py -q   # WO-87/88 still green
python -m pytest tests/test_migrations.py -q
python -m pytest -q                                            # full baseline
# DEMONSTRATION: one predicate, three failure modes, two distinct codes — and
# the wrong-provenance row that the writer stored during recon is now refused.
python - <<'PY'
from app.core.errors import ValidationError
from app.services.transport import fuel_ingest
for ccy, src in (("EUR", "unknown"), ("PLN", None), ("PLN", "eur"), ("SEK", "eur")):
    try:
        fuel_ingest._require_fx_provenance(ccy, src)
        raise SystemExit(f"NOT REFUSED: {ccy}/{src}")
    except ValidationError as e:
        print(f"{ccy}/{src:<8} -> {e.code}")
for ccy, src in (("EUR", None), ("EUR", "eur"), ("eur", "eur"), ("PLN", "ecb"), ("PLN", "stated")):
    fuel_ingest._require_fx_provenance(ccy, src)
    print(f"{ccy}/{src} -> accepted")
PY
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```
