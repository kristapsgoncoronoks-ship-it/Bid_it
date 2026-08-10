# WO-95 — G2.9: fee freezing on the contingency model (board G2.9 · R13 · C10/C11)

**Effort M (3–5d). Priority P1. Milestone M3. Depends on: WO-51 (`lock.submit_claim`),
WO-54 (`freeze.freeze_claim_lines` — the frozen VAT base this fee is computed on),
WO-73 (`customer_lifecycle` — the "customer" identity this codebase actually has),
WO-93 (the client surface whose fee-free guarantee this order must not weaken),
and the owner decision recorded in `docs/DECISIONS-NEEDED.md` "Decisions taken — 2026-08-08".**

---

## RECON — verified before any code

### 1. The decision that unblocks this order, and the part of it that is still open

`docs/DECISIONS-NEEDED.md`, top section, verbatim:

> **§10 transport pricing — DECIDED: contingency fee on recovered VAT
> (no-win-no-fee).** Unblocks G2.9. The claim already carries frozen
> `fee_pct`/`fee_min`/`fee_eur` columns, so the engine has somewhere to land.
> **Still open: the actual percentage and any minimum.** Building the mechanism
> with the rate as an org-level setting that FAILS CLOSED when unset — a fee
> figure is what a client is charged, so no default may be invented (the excise
> placeholder precedent does not transfer: a labelled indicative rate on an
> advisory figure is not the same as a live charge).

So the MODEL is decided and the NUMBER is not. Everything below follows from that
split: the mechanism is built in full, and the one thing nobody has decided is the
one thing this order refuses to supply.

### 2. What the spec actually says about the fee — every definition, cited

`docs/plan/shared/specs/BA_fleet_fuel.md`:

**§1 (line 41)** — the business model: *"…on behalf of transport companies, and
charges a **contingency fee**"*.

**§2.2 (lines 110–113)** — the fee's place in the lifecycle, verbatim:

```
payout_route = 'customer' → we invoice our fee (receivable)
payout_route = 'us'       → we deduct fee, remit net
fee = max(fee_pct% × base, fee_min)   [customer_master.compute_fee]
rate FROZEN at submission; fee CHARGED on the PAID amount
```

**C10 (lines 489–497)** — FEE FREEZING, verbatim:

> **C10. FEE FREEZING — the rate freezes at submission; the fee is charged on the
> PAID amount.**
> - On first entry to a locking state: freeze `vat_eur` **and** `vat_local`
>   computed over **exactly the locked `claim_set`** via `invoice_lines` — *not* a
>   raw `SUM(vat_eur)` over the period, which would wrongly include period
>   invoices not in this claim. Freeze `fee_pct`, `fee_min`, `fee_eur`.
> - On `paid`: recompute `fee_eur = compute_fee(paid_amount or vat_eur, frozen
>   fee_pct, frozen fee_min)` and stamp `fee_billed_date`. **Only the fee BASE
>   changes (claimed → paid); the frozen rate/minimum are never re-derived.** % /
>   minimum changes only affect *un-submitted* declarations.
> - `record_payment` stamps `paid_amount` and drives the claim to `3A` in **ONE
>   transaction** so a crash can never leave `paid_amount` stamped while the
>   status/fee lag.

**C11 (lines 499–502)** — the FORMULA and the RATE RESOLUTION, verbatim:

> **C11. Fee formula (`customer_master.compute_fee`):** `% fee` takes priority; if
> it falls below the per-declaration minimum, the **minimum** is charged. Returns
> `(fee, basis)` where basis ∈ {`percent`, `minimum`}. Resolution order for the
> rate (`fee_for`): per-(customer, country) override → customer default → (0, 0).

**R13 (line 1370)**, the M-priority rule and its acceptance test, verbatim:

> **Fee rate FROZEN at first submission; fee CHARGED on the PAID amount.** The
> frozen VAT base is computed over **exactly the locked claim set**, not a period
> SUM. `paid_amount` + the paid transition commit atomically.
> *Acceptance:* Change the customer fee % after submission ⇒ the claim's fee is
> unchanged. Record a partial payment ⇒ the fee recomputes on that amount at the
> frozen rate.

**R40 (line 1412)** — the second place a rate is defined, and the only place an
ORG-LEVEL rate appears in the harvest, verbatim:

> **Transparent pricing page + refund calculator** … reusing the same fee function
> the real claim uses. **Standard fee is admin-editable; a per-client fee overrides
> it.** *Acceptance:* The calculator and a real claim produce the same fee for the
> same inputs.

**R39 (line 1411) / §3.D (line 550) / §1.2 (line 79)** — the absence this order
must not break: *"**No internal codes, no actions, no fees** are exposed to the
client role."*

**Appendix B (line 1730)** — `Contingency default: pricing_fee_pct 15%,
pricing_fee_min (admin-editable)`. **This number is deliberately NOT harvested** —
see "The governing constraint" below.

**C12 (lines 504–508)** — settlement routes, `payout_to`, `fee_billed_date`, fee
invoice numbering `F<year>-<NNNN>`. **Out of scope** (see Scope).

### 3. Rule-number verification against `docs/transport/rules.md`

| Claim in the order | Verified |
|---|---|
| R13 is the fee-freezing rule | ✅ `BA_fleet_fuel.md` line 1370. **No R13 row exists in `docs/transport/rules.md` yet** — this order writes the first one. |
| R5/D7 govern withdrawal | ✅ ledger row R5 (WO-51 + WO-94), `BA_fleet_fuel.md` §3.C7, §3.D D7. |
| G2.5 is the frozen VAT base | ✅ ledger row "G2.5 (ADR-P3; strengthens R30)". |
| R44 is the customer-lifecycle gate | ✅ ledger row R44 (WO-73). |
| R40 names a standard + per-client rate | ✅ line 1412. R40 has **no ledger row** — it belongs to the `/fees` pricing board, not to this one; this order does not claim it. |

### 4. The code being completed — verified, symbol by symbol

| Symbol | State at `076355f` |
|---|---|
| `app/models/transport/vat_claim.py::VatRefundClaim.fee_pct/fee_min/fee_eur` | Present, `Numeric(5,2)` / `Numeric(14,2)` / `Numeric(14,2)`, all nullable, shipped empty by WO-49. Docstring: *"Carries the fields that get FROZEN at submission (R13) … populated by a future submission service, never by this order."* **Nothing in `app/` reads or writes any of the three** (`grep -rn "fee_pct\|fee_min\|fee_eur" app/` returns only the model, `app/schemas/transport_claim.py:64-66` and `app/api/routes/transport/claims.py:107-109`). |
| `app/services/transport/freeze.py::freeze_claim_lines` | Freezes the lines and stamps `claim.vat_eur/vat_local/currency` from EXACTLY those lines (C10 first bullet). Does **not** flush; the caller does. `preview_vat_base` is its read-only twin. Neither touches a fee. |
| `app/services/transport/lock.py::submit_claim` | The gate stack (module → draft → non-empty → R7 → R8 → R44 → R3 → R6 → R15 note → R10 → freeze → locks → status flip → `status_code="2"` → one `TRANSPORT_CLAIM_SUBMIT` audit event). This is C10's "first entry to a locking state". |
| `app/services/transport/lock.py::withdraw_claim` | Deletes the locks, sets `status="withdrawn"`, NULLs `status_code` (WO-94/D7). **Does not clear `vat_eur`/`vat_local`/`currency`.** |
| `app/services/transport/status.py` | Module docstring, verbatim: *"Building that mapping FOR REAL would mean this order also builds the 'money received'/'decision received'/'rejected' transitions — which collide with G2.9's fee freezing (`compute_fee`, `record_payment`, `payout_to`), explicitly decision-gated."* `set_status_code` therefore writes the LABEL only and never `claim.status`. |
| `app/services/transport/claim.py` | `get_or_create_claim` / `list_claims(year=…)` / `get_claim`. No fee concept. |
| `app/models/transport/customer_lifecycle.py` | `VatCustomerLifecycle` is keyed `(org, entity_id)` and is NAMED "customer": WO-73 already settled that **in this codebase the claimant entity IS the customer**. `VatCountryActivation` adds the `(entity, country)` grain. |
| `app/models/transport/excise_rate.py` | The admin-override precedent: a transport-local tenant table, audited `set_rate`/`remove_rate`, absence → a code default. Its docstring records that *"This codebase has no per-org key/value settings table — `app/models/` contains no settings model at all."* |

### 5. The blocker DECISIONS-NEEDED §10 recorded — and why it is now gone

§10's M3 update reads: *"this codebase has no established mapping from a
`VatRefundClaim` to a billable 'customer' distinct from the claimant `entity_id`
(`issuer_profiles`) itself, and `app.models.customer.Customer` (the AR sales-customer
master) has no fee-rate concept today. Building G2.9 now would mean inventing BOTH
the customer-identity mapping and the fee-rate storage shape."*

The first half is **no longer true**: WO-73 (G2.11, R44) shipped
`VatCustomerLifecycle`, a table literally named for the customer and keyed on
`(org, entity_id)`, plus `VatCountryActivation` on `(org, entity_id, country)` — and
`lock.submit_claim` already gates every submission on both. The claimant entity IS
the customer, decided and shipped, not invented here. C11's
`per-(customer, country) → customer default` chain therefore maps onto
`per-(entity, country) → per-entity` with no new identity concept.

The second half — the fee-rate storage shape — is what this order builds, and the
owner's 2026-08-08 decision is what authorises it.

### 6. The WO-93 client-surface guarantee — is it vacuous today?

**Yes, partially, and the fix is a fixture, not an assertion.**

* `tests/transport/test_wo93_client_surface.py::test_wo93_the_service_never_reads_a_fee_or_currency_ambiguous_column`
  is an **AST scan** over `client_status.py`'s source for attribute access to
  `{fee_pct, fee_min, fee_eur, vat_local, paid_amount}`. This is **structural** and
  holds whether or not fees are populated. Not vacuous.
* `test_wo93_no_field_name_carries_code_fee_or_action_vocabulary` scans dataclass
  and Pydantic field names against `FEE_WORDS = ("fee", "commission", "payout",
  "billed")`. Also structural. Not vacuous.
* `test_wo93_no_internal_status_code_reaches_the_wire` serialises a REAL response
  and asserts no leaf string equals an internal code. **This one is value-based**,
  and every `fee_*` column in its fixture portfolio is NULL, so it has never been
  exercised against a claim that actually carries a fee figure.

The strengthening is therefore: make WO-93's own `_submitted_claim` fixture produce
a claim whose three fee columns are genuinely populated, **without editing a single
line of any WO-93 file**, and then assert the absence over that portfolio from a
WO-95 test. This falls out of the design for free: `_org_with_entity` calls
`tests/transport/conftest.py::enable_transport`, so seeding the standard rate there
(the WO-73 `activate_entity` / WO-60 `make_entity` fixture-raising precedent) makes
every existing submission fixture in the tree freeze a real fee.

### 7. Blast radius of a fail-closed gate inside `submit_claim`

18 test modules call `lock.submit_claim`. All but one obtain their org through
`tests/transport/conftest.py::enable_transport`; the exception is
`tests/test_transport_lock_concurrency.py` (the Postgres gate), which calls
`modules.set_enabled` directly and needs one added line. Seeding the standard rate
inside `enable_transport` therefore costs **one conftest helper + one line in the
Postgres concurrency test**, and modifies no assertion anywhere.

---

## THE GOVERNING CONSTRAINT — no invented rate, and why the excise precedent does not transfer

`BA_fleet_fuel.md` Appendix B carries a number: `pricing_fee_pct 15%`. This order
**does not use it**, and neither does it use C11's third resolution rung `(0, 0)`.
Both are deliberate deviations from the harvest, and the reasoning belongs in the
record because a future reader will find the 15% in the spec and wonder.

**Why not the 15%.** The owner decision that unblocked this board says the
percentage is *still open*. A harvested figure from a retired single-tenant system
is not a decision about what this product charges; master-context §10 forbids
inventing commercial facts and §9 forbids substituting a name the order did not
give. A percentage that reaches a fee invoice is a live charge against a real
client.

**Why the excise precedent does not transfer.** `excise.py` ships
`DEFAULT_RATE_EUR_PER_1000L = 30.00` as an explicit placeholder, and that is
correct there for two reasons this board has neither of: (a) the excise figure is
**advisory** — R42's own words, *"the figure asserts NO eligibility"*, and every
surface that shows it says so loudly; (b) the number is **the state's**, not ours —
getting it wrong misstates a third party's published rate, which a customs filing
would immediately correct. A fee percentage is **ours**, it is **binding**, and the
first place a wrong one surfaces is an invoice a client pays.

**Why not C11's `(0, 0)` rung.** Fleet Fuel resolved an unconfigured customer to a
zero rate and a zero minimum, producing `fee_eur = 0.00`. Once frozen, that value is
indistinguishable from a deliberate zero-fee arrangement — the claim carries a
positive assertion *"this filing earns no fee"* which nobody made. Under
no-win-no-fee the fee is the entire revenue of the vertical, so a silent zero either
forfeits it or is discovered later and "corrected" by rewriting a frozen figure,
which is the one thing R13 exists to prevent. **The engine refuses instead.**

**And why an explicitly typed `(0, 0)` IS accepted.** The refusal is about
*absence*, not about zero. A human who types 0% / €0 for a client has made a
decision, it is audited with an actor, and it is a legitimate commercial
arrangement. Absence is not a decision. That distinction is the whole design, and
the storage layer permits `fee_pct = 0` / `fee_min = 0` precisely so it can be
stated rather than defaulted into.

---

## Objective and business value

`VatRefundClaim` has carried `fee_pct`/`fee_min`/`fee_eur` since WO-49 and **no code
has ever written one** — verified by grep at `076355f`: the only occurrences outside
the model are the read-only claim schema and the route that copies them onto the
wire. C10 places the freeze at "first entry to a locking state", which in this
codebase is `lock.submit_claim`, immediately after `freeze.freeze_claim_lines`
stamps the VAT base the fee is a percentage of. Today that function freezes the base
and stops, so the platform files a claim without ever recording what the client
agreed to pay for it — and `status.py`'s own docstring names this gap as the reason
its engine-state transitions are still unbuilt.

The vertical's entire revenue is this fee: the owner priced the transport module at
**€0/month** (`BA_fleet_fuel.md` line 125: *"`tax_refund` is **€0/mo** —
deliberately monetised as the `/fees` contingency"*) and the 2026-08-08 decision
confirmed no-win-no-fee. Until the fee freezes with the base, there is no auditable
record of the rate a filing was made under, and the only alternative — recomputing
it later from whatever the configuration says that day — is exactly R13's forbidden
case (*"Change the customer fee % after submission ⇒ the claim's fee is
unchanged"*). Freezing it here makes the revenue of every filed claim a stored,
audited, immutable fact, and it removes the last **service** row blocking M3's G2.9.

---

## Scope

**In scope**
- A new tenant table `vat_fee_rates` — the configured contingency rate, at C11's
  three rungs, none of them defaulted.
- `app/services/transport/fee.py` — `compute_fee` (C11's formula, `(fee, basis)`),
  `resolve_fee_rate` (C11's chain, **fail-closed** at the end instead of `(0,0)`),
  the audited `set_rate`/`remove_rate`/`list_rates` CRUD, and `freeze_fee` (C10's
  first bullet, over the already-frozen base).
- `lock.submit_claim` — resolve the rate as the LAST gate; stamp the three columns
  in the same flush as the line freeze, the locks and the status flip; carry the fee
  old→new in the existing `TRANSPORT_CLAIM_SUBMIT` audit event (§4.16).
- Two audit actions for the rate CRUD.
- `tests/transport/conftest.py::enable_transport` seeds a synthetic standard rate
  (opt-out kwarg) — the fixture-raising precedent, which also de-vacuums WO-93's
  value-based scan for free.
- Docs: `docs/transport/rules.md` (R13's first row + R5 gaining a consumer),
  `docs/DECISIONS-NEEDED.md` §10, `TODO.md`, `README.md` (tables 83→84, revisions
  90→91), `tests/test_docs_truth.py`'s pinned table literal.

**Out of scope** — named, with the board that owns each
- **C10's second bullet — the `paid` recompute on `paid_amount`, and
  `fee_billed_date`.** It requires the `3A`/"money received" ENGINE transition,
  which `status.py`'s docstring already records as unbuilt and entangled with this
  board. A documented seam only (see below).
- **Partial rejection.** `docs/DECISIONS-NEEDED.md`'s 2026-08-08 §13 note calls it
  *"a capability that does not exist yet — partial rejection, which `status.py`
  already names as an unbuilt transition colliding with G2.9"*, and the owner has
  confirmed it as a separate follow-up. **This order builds none of it.** A seam,
  not an implementation.
- **C12 — `payout_to`, fee receivable vs deduct-and-remit, `issue_fee_invoice`,
  `F<year>-<NNNN>` numbering.** A separate board (§7 "invoicing for work"); nothing
  here creates an invoice or moves money.
- **R40's `/fees` pricing page and refund calculator.** A different surface; it will
  consume `fee.compute_fee` when it is built, which is why the formula is a pure,
  importable function.
- **Any route or SPA screen for editing a rate.** The service is the surface this
  order ships; the table is classified EXEMPT in `tests/test_tenancy_parity.py` with
  the reason, exactly like `departments`/`cost_centers`, and gains a probe in the
  commit that gives it a route.
- **Deciding the percentage or the minimum.** Explicitly the owner's.

---

## Files to touch

| File | Change |
|---|---|
| `backend/app/models/transport/fee_rate.py` | **new** — `VatFeeRate`, the three-rung configured rate. |
| `backend/app/models/transport/__init__.py` | export `VatFeeRate`. |
| `backend/app/core/tenant.py` | register `VatFeeRate` in `TENANT_MODELS`. |
| `backend/alembic/versions/<rev>_fee_rates.py` | **new** — create the table + FORCE RLS in the SAME migration. |
| `backend/app/services/transport/fee.py` | **new** — the formula, the resolution chain, the CRUD, the freeze. |
| `backend/app/services/transport/lock.py` | resolve the rate as the last gate; stamp the fee in the freeze flush; extend the submit audit meta. |
| `backend/app/services/audit.py` | `TRANSPORT_FEE_RATE_SET` / `TRANSPORT_FEE_RATE_REMOVE`. |
| `backend/tests/transport/conftest.py` | `enable_transport(..., fee_rate=True)` seeds the standard rate. |
| `backend/tests/test_transport_lock_concurrency.py` | seed the standard rate beside its direct `modules.set_enabled`. |
| `backend/tests/test_tenancy_parity.py` | `vat_fee_rates` → `EXEMPT` with its reason. |
| `backend/tests/test_docs_truth.py` | pinned table literal 83 → 84. |
| `backend/tests/transport/test_wo95_fee_freeze.py` | **new** — the freeze, the boundary, the fail-closed refusal, the re-rate immunity, the audit, org scoping. |
| `backend/tests/transport/test_wo95_fee_rates.py` | **new** — `compute_fee`'s arithmetic and the three-rung resolution. |
| `backend/tests/transport/test_wo95_client_surface_with_fees.py` | **new** — R39 re-proven with fees actually populated. |
| `README.md` | tables 83 → 84, Alembic revisions 90 → 91 (SAME commit as the migration). |
| `docs/transport/rules.md` | R13's first row; R5 gains a consumer. |
| `docs/DECISIONS-NEEDED.md` | §10 status; record precisely what remains open. |
| `TODO.md` | WO-95 row, M3 cell, suite line. |

---

## Implementation guidance

1. **`VatFeeRate` — the three rungs in one table.** Columns: `org_id`;
   `entity_id` (GUID, **nullable** — NULL is C11's/R40's org-level STANDARD rate,
   non-NULL is a per-customer rate, composite `(org_id, entity_id)` FK to
   `issuer_profiles` RESTRICT); `country` (`String(2)`, NOT NULL, **`''` is the
   sentinel for "every country"**, i.e. the customer-default rung); `fee_pct`
   `Numeric(5,2)` NOT NULL; `fee_min` `Numeric(14,2)` NOT NULL.

   Uniqueness, deliberately not left to NULL semantics: `UNIQUE(org_id, entity_id,
   country)` covers the two customer rungs, and a **partial unique index on
   `(org_id) WHERE entity_id IS NULL`** guarantees exactly one org standard on both
   SQLite and PostgreSQL (a plain UNIQUE would not — SQL treats NULLs as distinct,
   so two org standards would both be storable). The `''` country sentinel exists so
   the customer-default rung needs no second partial index; it is documented as a
   sentinel, never as a country.

   CHECKs (defense-in-depth behind the service gate, the `excise_rate.py`
   discipline): `fee_pct BETWEEN 0 AND 100`; `fee_min >= 0`; `country = '' OR
   length(country) = 2`; and `entity_id IS NOT NULL OR country = ''` — **no
   org-level per-country rung is harvested**, and a shape the spec does not describe
   is not stored. Zero is permitted for both amounts on purpose (see "The governing
   constraint").

   Precision follows the claim columns exactly — `Numeric(5,2)` for a percentage and
   `Numeric(14,2)` for a euro minimum — so the frozen value is byte-identical to the
   configured one and no rounding happens at the copy.

2. **`compute_fee(base, fee_pct, fee_min) -> (Decimal, str)` — C11 verbatim.**
   `percent_fee = q2(base * fee_pct / 100)`. If `percent_fee < fee_min` → `(q2(fee_min),
   "minimum")`, else `(percent_fee, "percent")`. Basis is C11's own two-value
   vocabulary, no third value. **The exact tie belongs to `percent`**: C11 says the
   minimum applies when the percentage *"falls below"* it, and an equal figure has
   not fallen below — the boundary test asserts both sides one cent apart.
   Decimal throughout, `money.q2`, ROUND_HALF_UP, never `float` (§4.9). Currency
   basis: **NET EUR**, the same `vat_eur` the claim froze; `fee_min` is EUR, so no
   cross-currency sum ever occurs (§4.14) and `vat_local` is never a fee base.

3. **`resolve_fee_rate(db, org_id, *, entity_id, country) -> FeeRate` — C11's chain,
   fail-CLOSED.** `(entity, country)` → `(entity, '')` → `(NULL, '')` →
   `ConflictError(code="fee_rate_not_configured")` with a message naming the entity
   and country and telling the operator what to configure. Read-only; module-gated
   first like every transport entry point. This replaces C11's `(0, 0)` rung; the
   deviation and its reasoning go in the module docstring, not only here.

4. **`freeze_fee(claim, rate) -> (Decimal, str)` — pure, synchronous, no DB.** Reads
   `claim.vat_eur` (already frozen by `freeze_claim_lines`), computes, and stamps
   `fee_pct`/`fee_min`/`fee_eur`. Refuses `ConflictError(code="fee_already_frozen")`
   if any of the three is already set, and `ConflictError(code="vat_base_not_frozen")`
   if `vat_eur` is None — a fee computed off an unfrozen base is exactly the drift
   R13 forbids. Being pure means the `/fees` calculator (R40) and any future
   `paid`-recompute call the SAME arithmetic; nothing forks it.

5. **`lock.submit_claim` — where the two halves go.** The **rate resolution** is a
   GATE and runs LAST in the gate stack, after R10's document-presence check and
   **before** `freeze_claim_lines`. Two reasons, both stated in the code: it is a
   pure read, so a missing rate refuses while the session is still unmutated
   (`submit_claim`'s own contract — *"nothing is mutated before the LAST gate
   passes"* — is preserved exactly); and the statutory gates come first so an
   operator is told about a legal problem before a commercial one. The **stamp**
   then happens immediately after `freeze_claim_lines`, in the same flush as the
   locks and the status flip — C10's "freeze `vat_eur` and `vat_local` … freeze
   `fee_pct`, `fee_min`, `fee_eur`" is one atomic act, and a lost lock race must roll
   back the fee with everything else.

   **This is a behaviour change, stated loudly:** an org with the transport module
   enabled and no fee rate configured can no longer submit a claim. That is the
   fail-closed decision the work order requires, and the refusal carries an
   actionable code. It is not silent and it is not a data change.

6. **Audit (§4.16).** The rate CRUD audits old→new per field, in-transaction,
   idempotent-no-op on an unchanged write (`excise.set_rate`'s convention). The
   freeze rides the EXISTING `TRANSPORT_CLAIM_SUBMIT` event rather than adding a
   third action — WO-94's precedent for the withdrawal (*"the existing audit event
   now carries old→new under `set_status_code`'s own field names"*): submission IS
   the moment the fee freezes, and one event with `old_fee_*`/`new_fee_*` plus the
   basis and the resolved rung is one shape for a consumer to learn, not two.

7. **Withdrawal — does it clear the fee? No. The spec's answer, cited.**
   §3.D **D7** enumerates exactly what a withdrawal changes: *"Only `withdraw_claim`
   releases, and it also NULLs `status_code`."* It names the locks and the code and
   nothing else, and WO-94 implemented precisely that. Nothing anywhere in the
   harvest clears a frozen figure on withdrawal, and `freeze_claim_lines`'
   `vat_eur`/`vat_local`/`currency` are correspondingly left standing by
   `withdraw_claim` today. Clearing the fee but not the base it was computed from
   would leave a claim in a shape the spec never describes. Decisive confirmation:
   `submit_claim` refuses any claim whose status is not `draft`, so a withdrawn
   claim can never be re-submitted (pinned by
   `test_wo94_a_withdrawn_claim_cannot_be_resubmitted_or_recoded`) — the frozen fee
   can therefore never be re-frozen, stale or contradicted. **`withdraw_claim` is not
   touched by this order**, and a test asserts the fee survives a withdrawal
   unchanged so the decision is pinned rather than merely argued.

8. **The rejection seam — documented, not implemented.** `status.py` records that
   the "decision received"/"rejected" transitions collide with this board, and the
   owner has confirmed partial rejection as a separate follow-up. `fee.py`'s
   docstring states what such a transition would need from this module (a recompute
   over a REDUCED base at the FROZEN rate — C10's *"only the fee BASE changes … the
   frozen rate/minimum are never re-derived"*), and that `compute_fee` being pure and
   `fee_pct`/`fee_min` being stored on the claim is exactly what makes that possible
   without re-reading configuration. **No function, no column and no status
   transition for it is added.**

---

## Invariants this order must preserve

- **§4.9 Decimal-only money.** `compute_fee` is pure Decimal via `money.q2`; storage
  is `Numeric`; `tests/test_money_invariants.py::test_money_never_uses_float` covers
  the new module, and an AST test asserts no `float(` in `fee.py`.
- **§4.10 the server computes.** The fee is derived from the frozen base and the
  configured rate; no caller supplies a fee, and no route accepts one.
- **§4.14 no cross-currency sums.** The base is `vat_eur` and the minimum is EUR.
  `vat_local` is never a fee input — asserted by an AST scan over `fee.py`, the
  `client_status.py` precedent.
- **§4.16 audit old→new in-transaction.** Both the rate CRUD and the freeze.
- **§4.19 fail-closed gates with the reasoning in the docstring.** The refusal, and
  why zero-when-typed is different from zero-when-absent.
- **§4.20 additive.** A new table, a new module, one gate added to one function; no
  column dropped, no existing figure rewritten, no migration backfill.
- **§4.1/§4.2 tenancy.** Org-scoped queries + `TENANT_MODELS` registration + FORCE
  RLS in the same migration; `(org_id, id)` composite unique per §4.3.
- **§4.4 opaque 404.** The rate CRUD reuses the entity lookup that already 404s
  across tenants.
- **§9/§10 vocabulary and nothing invented.** `basis ∈ {percent, minimum}` is C11's;
  the resolution chain is C11's; the org rung is R40's and the 2026-08-08 decision's;
  the percentage is nobody's, and stays that way.
- **R39 (WO-93).** No WO-93 file is edited; the guarantee is re-proven with fees
  populated from a WO-95 test.

---

## Database / migration impact

One new tenant table, `vat_fee_rates`, with `ENABLE`/`FORCE ROW LEVEL SECURITY` and
the `tenant_isolation` policy **in the same migration** (§4.2). No backfill: absence
of a row is the fail-closed state, which is the correct state for every existing
org, and no existing row anywhere is read or rewritten. Downgrade drops the table
and loses every configured rate — safe in the sense that no money figure is
rewritten, but a downgraded deployment cannot submit a claim until rates are
re-entered, which the migration docstring states.

Postgres gate REQUIRED (a new tenant table): `tests/test_rls.py::
test_rls_migration_covers_every_tenant_table` must run on a real NOSUPERUSER role.

---

## Testing requirements

`tests/transport/test_wo95_fee_rates.py`
- `test_wo95_compute_fee_is_the_percentage_when_it_clears_the_minimum` — hand-computed Decimal.
- `test_wo95_compute_fee_is_the_minimum_when_the_percentage_falls_below_it` — hand-computed Decimal, basis `"minimum"`.
- `test_wo95_the_exact_boundary_where_the_percentage_equals_the_minimum_is_percent` — plus its one-cent-below twin returning `"minimum"` (both sides of the boundary, one cent apart).
- `test_wo95_compute_fee_rounds_half_up_to_cents` / `..._never_uses_float`.
- `test_wo95_a_zero_rate_typed_by_a_human_is_accepted_and_produces_a_zero_fee`.
- `test_wo95_resolve_prefers_the_per_country_rate_over_the_customer_default` / `..._over_the_org_standard`.
- `test_wo95_resolve_falls_back_to_the_customer_default_then_the_org_standard`.
- `test_wo95_resolve_refuses_when_no_rate_is_configured` — code `fee_rate_not_configured`.
- `test_wo95_only_one_org_standard_can_exist` (the partial unique index) and `test_wo95_an_org_level_rate_cannot_carry_a_country` (the CHECK).
- `test_wo95_set_rate_audits_old_to_new` / `..._is_a_no_op_when_unchanged` / `..._is_org_scoped`.
- `test_wo95_a_second_org_rate_is_never_visible_to_the_first` — overlapping data.

`tests/transport/test_wo95_fee_freeze.py`
- `test_wo95_submission_freezes_the_fee_from_the_frozen_base` — the three columns against a hand-computed Decimal.
- `test_wo95_submission_freezes_the_minimum_when_it_bites`.
- `test_wo95_a_per_customer_rate_overrides_the_org_standard_on_a_real_claim`.
- `test_wo95_submission_refuses_when_no_rate_is_configured_and_writes_nothing` — code `fee_rate_not_configured`, and after rollback: claim still `draft`, `vat_eur` NULL, zero lock rows, zero frozen lines, no submit audit event.
- `test_wo95_changing_the_rate_after_submission_never_re_rates_the_claim` — R13's acceptance test verbatim.
- `test_wo95_the_submit_audit_event_carries_the_fee_old_to_new`.
- `test_wo95_a_withdrawn_claim_keeps_its_frozen_fee` — the D7 reading, pinned.
- `test_wo95_freeze_fee_refuses_an_already_frozen_claim` / `..._refuses_an_unfrozen_vat_base`.
- `test_wo95_the_fee_gate_runs_after_every_legal_gate` — a claim failing R7/R10 reports the legal code, not the fee code.
- `test_wo95_the_frozen_fee_reaches_the_operator_claim_route` (granted role) and `test_wo95_a_denied_role_cannot_read_it` (403) — the schema already carries the fields.

`tests/transport/test_wo95_client_surface_with_fees.py`
- `test_wo95_a_submitted_claim_really_carries_a_frozen_fee` — proves the WO-93 fixture is no longer vacuous.
- `test_wo95_no_fee_figure_reaches_the_client_wire_with_fees_populated` — the serialized portfolio contains no string or number equal to any frozen fee value.
- `test_wo95_the_fee_column_scan_still_holds` — re-imports WO-93's AST assertion over the post-change module.

Plus: `tests/test_tenancy_parity.py` classification and `tests/test_docs_truth.py`
re-pin in the migration's own commit.

---

## Acceptance criteria

- [ ] `vat_fee_rates` exists with FORCE RLS created in the same migration; `alembic heads` is one; `alembic check` reports no drift.
- [ ] `fee.compute_fee(Decimal("1000.00"), Decimal("15.00"), Decimal("250.00"))` returns `(Decimal("250.00"), "minimum")`; at `fee_min = 150.00` it returns `(Decimal("150.00"), "percent")`.
- [ ] `lock.submit_claim` on an org with **no** configured rate raises `fee_rate_not_configured`; after rollback the claim is still `draft`, `vat_eur` is NULL, and `vat_claimed_invoices` holds zero rows for it.
- [ ] A claim submitted under a 15%/€0 rate stores `fee_pct=15.00`, `fee_min=0.00`, `fee_eur = 15% of its frozen `vat_eur``; changing the configured rate afterwards leaves all three unchanged.
- [ ] An `AuditEvent` with `action="transport.claim_submit"` carries `old_fee_eur=None` and the new fee; an event with `action="transport.fee_rate_set"` carries old→new for a changed rate.
- [ ] `tests/transport/test_wo93_client_surface.py` and `test_wo93_client_status.py` pass **unmodified**, and `test_wo95_a_submitted_claim_really_carries_a_frozen_fee` proves their submitted fixture now carries a real fee.
- [ ] `python scripts/pii_scan.py --tree` is clean.
- [ ] `ruff check` / `ruff format --check` / `mypy app` clean; full suite green with the baseline explained line by line.

---

## Rollback strategy

Code revert plus `alembic downgrade -1`. The downgrade is written and exercised
(`upgrade → downgrade → upgrade`). What is lost: every configured rate. Nothing is
one-way — no claim figure is rewritten by the migration in either direction, and a
claim frozen while the table existed keeps its `fee_pct`/`fee_min`/`fee_eur` (they
live on `vat_refund_claims`, untouched by this migration). Narrow mitigation without
a full revert: remove the `resolve_fee_rate` call from `submit_claim`'s gate stack —
submissions resume immediately and simply stop freezing a fee.

---

## Documentation to update

`docs/transport/rules.md` (R13's first ledger row; R5 gains this order as a
consumer), `docs/DECISIONS-NEEDED.md` §10 (status + precisely what remains open),
`TODO.md` (WO-95 row, M3 cell, suite line), `README.md` (tables 83 → 84, Alembic
revisions 90 → 91, in the migration's own commit). No ADR is contradicted: ADR-P3's
rules 1–3 are followed (no column on another domain's table, no raw cross-domain
join, an un-entitled org is byte-identical).

---

## Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check
python -m pytest tests/transport/test_wo95_fee_rates.py \
                 tests/transport/test_wo95_fee_freeze.py \
                 tests/transport/test_wo95_client_surface_with_fees.py \
                 tests/transport/test_wo93_client_surface.py \
                 tests/transport/test_wo93_client_status.py -q
python -m pytest -q
python ../scripts/pii_scan.py --tree
# DEMONSTRATION: the refusal is real and the freeze is real.
grep -rn "fee_pct\|fee_min\|fee_eur" app/services/transport/lock.py app/services/transport/fee.py
grep -rn "15\b" app/services/transport/fee.py   # must return NO rate constant
```

---

## As built — what changed while implementing, and why

**1. The customer-rung storage shape landed exactly as planned, but the
uniqueness needed a partial index the plan only half-anticipated.** The plan
named it; implementing it confirmed why it is not optional. `UNIQUE(org_id,
entity_id, country)` is satisfied by two rows that both carry `entity_id IS
NULL`, because SQL treats NULLs as distinct — so without
`uq_vat_fee_rates_org_standard` an org could hold two standard rates and
`resolve_fee_rate`'s walk would return whichever the query planner surfaced
first. `test_wo95_only_one_org_standard_can_exist` drives a raw INSERT at it.

**2. The minimum-bites test could not be written the obvious way.** The plan
assumed a small VAT base would make the minimum bite. It cannot: R8's Art. 17
gate refuses a quarterly LV claim below €400 long before the fee is reached, so
a €120 base fails on `below_minimum` and never sees the fee at all. The test
instead makes the RATE small (2% of €420.00 = €8.40 against a €25.00 minimum),
which exercises the same branch on a claim that legitimately passes every
statutory gate. Recorded rather than quietly adjusted, because it is a real fact
about the gate stack: the two "minimums" in this vertical (Art. 17's statutory
threshold and C11's per-declaration fee floor) constrain each other, and a fee
minimum above ~10% of the Art. 17 threshold can only ever bite via a low rate.

**3. The "nothing written" test needed its ids captured before the refusal.**
`db_session.rollback()` expires every ORM object in the session, so reading
`claim.id` afterwards is a lazy load against dead state and raises
`MissingGreenlet` rather than failing the assertion. The ids are read up front.
This is a test-mechanics note, not a product one, but it is the kind of thing
that reads as a flaky test later if it is not written down.

**4. A positive control was added beside the fail-closed test.** Asserting that
a submission refuses proves nothing on its own — a broken fixture refuses too.
`test_wo95_the_same_claim_submits_once_a_rate_is_configured` takes the SAME
claim that just refused, configures a rate, and submits it successfully. The
refusal is therefore provably the fee gate.

**5. Two gate-ORDER tests, not one.** The plan asked for a test that the fee
gate runs after the legal gates. It is asserted twice — against R7 (period not
ended) and against R10 (missing document, the last statutory gate before this
one) — because "after the legal gates" is a claim about a position in a
sequence, and one probe only pins one end of it.

**6. The no-invented-rate constraint is asserted structurally, and the first
attempt was wrong.** The initial scan banned every numeric literal in `fee.py`
outside `{0, 100}` and failed on `True` (a `bool` is an `int` in Python's AST)
and on `2` (the country-code length check) — noise, not rates. It was replaced
by a scan of MODULE-LEVEL numeric constants, which is the only shape a default
rate could actually take (`DEFAULT_FEE_PCT = Decimal("15.00")`, exactly the form
`excise.py` legitimately uses for its advisory placeholder), plus a ban on any
`Decimal("...")` literal in the module other than the `"100"` divisor. Both ship
with a seeded-violation self-test.

**7. `withdraw_claim` was not touched, and that is the deliverable.** The order
asked whether withdrawal should clear a frozen fee. The answer harvested from
D7 is no, so the code change is nothing and the artifact is a test
(`test_wo95_a_withdrawn_claim_keeps_its_frozen_fee`) plus the citation in
`fee.py`'s docstring and in R5's ledger row. A decision that results in no code
still has to be recorded, or the next order re-litigates it.

**8. Deviation from the plan's testing list: no dedicated route test was
written.** The plan listed `test_wo95_the_frozen_fee_reaches_the_operator_claim_route`
and a denied-role case. Neither is a WO-95 test: this order changes no route,
adds no field to `ClaimOut` (WO-49 put the three fields there) and creates no
new permission, so those cases are already covered by
`tests/transport/test_wo76_claim_routes.py`'s existing granted/denied matrix
over the same endpoint. Writing them again here would assert WO-76's contract in
WO-95's name. What IS asserted is the schema exposure itself
(`test_wo95_the_frozen_fee_is_still_visible_to_the_operator`), which is the fact
this order actually depends on — the fee is hidden from the CLIENT surface, not
from the platform.
