# WO-AB — the claimant checklist's missing rules (G2.10 slice 2, R45)

**Shipped 2026-08-28.** Migration `a9c1e3f5b7d2`.

## The gap, verified against the tree before building

Every claim below was checked in the live code, not taken from the backlog —
the arc-3 lesson, where `advertised_prices` turned out to be a dead premise.

| Claim | Verified |
|---|---|
| `DEFAULT_RULES` ships two of §3.E's six rules | yes — `customer_data`, `bank_account` |
| `submission_checklist` raises on `scope="country"` / `check_type="document"` | yes — WO-60's deliberate fail-closed placeholder |
| No `nace_code` column anywhere | yes — zero occurrences |
| No claimant document store with an expiry | yes — `documents` (Slice 5d) is content-addressed on `(org, sha256, kind)`: no owner, no validity window |
| No `TAX_AUTHORITY` map | yes — one mention, in the spec; none in code |
| No document-REQUEST workflow (§3.F F4) | yes — zero occurrences of `DOC_REQUEST_STATES` |

## What shipped

**`IssuerProfile.nace_code`** (String(16), nullable) and `_verify_nace`.

It goes on the CORE claimant row, not a transport-local table, and the
distinction is deliberate. `customer_lifecycle.py`'s docstring bans transport
STATE from `issuer_profiles`; a NACE code is not state. It is neutral company
master data of exactly the class as `registration_number` and `vat_number` —
which `_verify_customer_data` already reads from that same row — and §3.F F2's
own `EDITABLE_FIELDS` list puts `nace_code` beside them. It is NOT in
`seller_snapshot` (an issued invoice has no business carrying it) and NOT in
`REQUIRED_FIELDS` (AR completeness is not VAT-refund completeness).

No format gate: `49.41`, `H49.41` and PKD `49.41.Z` are all real codes for the
same activity in different member states. A shape check would refuse valid data
and report it as a missing business activity. The rule checks PRESENCE, which
is what §3.E asks for.

**`vat_claimant_documents`** — a transport-local table on `VatCountryActivation`'s
exact precedent (composite `(org_id, entity_id)` RESTRICT FK, RLS + FORCE RLS in
the creating migration), plus `claimant_documents.py`, three routes on the
customer router, and the panel on `/vat-customers`.

Two shape decisions worth keeping:

- **`country` is `''`, not NULL, for a customer-scope document.** A contract is
  held once; a power of attorney is held per country. Both live in this table,
  so the unique key covers both — and in Postgres NULL never equals NULL, so a
  nullable column would let the same contract be inserted any number of times
  while *looking* constrained. `''` is a real value that compares.
- **The unique key carries `sha256`.** Re-uploading the same bytes is
  idempotent; a RENEWAL is different bytes and takes its own row. That is what
  keeps the lapsed document visible beside the one that replaced it, which is
  how an operator sees that a gap was *closed* rather than merely that it is
  closed now.

**`DEFAULT_RULES` completes §3.E's table** — all six, not four of six. The
mechanism now exists for all six, every one is deactivatable by an admin (R45's
own escape hatch), and a partial seed is precisely the stale-claim defect this
arc keeps finding. Adding four rules makes an unonboarded claim preview as `1A`
until its documents are on file; that is §3.E's own stated behaviour ("an
expired PoA fails `_has_doc` and the claim drops back to 1A") and it changes no
GATE — `derive_stage` is a read-only preview and `lock.submit_claim` has never
consulted this evaluator.

**`tax_authority.py`** — refund country → national authority, 23 entries pinned
by test to `capture_review.COUNTRIES` so there is one country list and never
two. An unknown country yields `""` and contributes nothing to the checklist
sentence: §3.F F5's rule, verbatim, and the same discipline `doc_templates.
render` applies to an unresolved token. A PoA naming the wrong authority is not
a cosmetic defect — it is a document the member state can refuse.

## What this order deliberately did not build

§3.E's `_open_poa_request_note` enriches a PoA item's label with an open
document REQUEST's status ("sent for signature"). There is no document-request
workflow here (§3.F F4). The note has no subject, and a status string with
nothing behind it would be worse than its absence. It lands with F4 or not at
all.

PoA document GENERATION (§3.F F5's template merge) is also out of scope:
`doc_templates.build_context` is project-scoped AR, and a transport claim
context is its own piece of work. The MAP is what was missing, and the map is
what shipped.

## The fixture was RAISED, not the assertions weakened

`tests/transport/conftest.py::make_entity` was documented as "fully clean by
default … so a claim built on this entity passes the checklist". Under two
rules that was true; under six it was not. The fixture now supplies a NACE code
and every §3.E document (WO-95's own move, when it seeded the standard fee rate
in `enable_transport` for the same reason). No assertion anywhere was loosened
— had it been, every pre-existing stage assertion would have quietly started
measuring the fixture instead of the code.

One existing test changed subject rather than expectation:
`test_g2_10_seed_default_rules_is_idempotent_and_seeds_exactly_two` asserted a
slice's subset. It now asserts §3.E's table, so a rule quietly dropped from
`DEFAULT_RULES` fails here rather than becoming a checklist that no longer
checks something the law requires.

## Certification

- **The order's headline**: a claim blocked on a missing NACE code and released
  when it is supplied. Both halves in one test — asserting only the block would
  pass just as happily against a rule hard-wired to fail.
- **The document rules behave like the existing document gate**: absent fails,
  present passes, expired fails again, and the reason says WHICH.
  `valid_until` is inclusive of its last day; a NULL expiry is a fact, not an
  absence.
- **Country scope, two-sided**: a PoA held for FR does not satisfy an LV claim,
  AND one held for LV does. The one-sided version could not tell a working rule
  apart from one that ignores `claim.refund_country` entirely.
- **R45's acceptance test over a DOCUMENT rule**: deactivate `power_of_attorney`
  and it disappears from the gate. This is what makes seeding all six safe.
- **Tenancy parity probe** for `vat_claimant_documents`, over the real HTTP
  routes, in the same commit as the table. Its second half is the one with
  teeth: a leak there would not surface as a visible row, it would surface as a
  claim quietly passing a legal gate on a document the workspace does not hold.
- **Seeded violations, both restored by inverse edit.** Ignoring `valid_until`
  in `_state` fails 2 tests; ignoring `claim.refund_country` in the evaluator
  fails 5. Neither was restored with `git checkout`.
- Postgres round-trip on a fresh database: upgrade, `relrowsecurity` and
  `relforcerowsecurity` both `t` in `pg_class` with the `tenant_isolation`
  policy present, downgrade (table and column both gone), upgrade, then
  `alembic check` → "No new upgrade operations detected".

## Hygiene carried in

`savings.py`'s "what this module deliberately does not contain" paragraph was
stale in three ways, not the one the arc-4 plan named. It still listed supplier
reliability as blocked on an `advertised_prices` table (WO-Q shipped it DERIVED
and explicitly dropped that table), still listed anomalies as unbuilt (WO-AA
shipped all six rules), and its count said six while listing five — the sixth,
contract audit, had already shipped in `contract_audit.py` when the paragraph
was written. Corrected to name what shipped and what genuinely remains.
