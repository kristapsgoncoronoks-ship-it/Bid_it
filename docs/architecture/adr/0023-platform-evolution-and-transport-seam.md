# ADR-0023 — Platform evolution: 8 bounded contexts, 2 projection layers, and the transport-vertical seam

**Status:** Accepted (contexts + projection rules are in effect today; the transport
vertical itself is future work — this ADR fixes its binding rules *before* it is built).
Extends ADR-0001 (modular monolith), ADR-0004 (tenant isolation).

**Implementation status (WO-49, M3 opener):** rules 1-5 are now CI-enforced, not
just documented. `app/models/transport/vat_claim.py` (`VatRefundClaim`/
`VatRefundClaimLine`, the `(org, entity, refund_country, ref_period)` grain,
R1), `app/services/transport/claim_gates.py::is_synthetic()` (R3, the single
predicate every future gate must call), the `transport` module entitlement
(default off, `app/services/modules.py`), and the four new `Permission`
members (rule 5) have landed. `tests/test_boundaries.py::
test_transport_services_do_not_import_other_domain_models` makes rule 2 an
enforced CI assertion, not a promise. Still future work (tracked as ADR-P3's
G2.2 onward in `docs/plan/plan-a/ARCH_plan.md`): the lock table
(`vat_claimed_invoices`, R4/R5), the checklist/period-end/minimum/deadline
gate stack, fee freezing, status derivation, and every `api/routes/transport/*`
route — none of rules 1-5 above required them to exist first.

**Implementation status (WO-50, G1.2):** the typed `fuel_transactions` model
has landed — `app/models/transport/fuel_transaction.py` (`FuelTransaction`,
natural key `(org, entity, supplier, period, line_seq)` so ingestion is
insert-or-no-op, never Fleet Fuel's DELETE-by-period; the overloaded `note`
column split into `invoice_ref`/`provenance_note`), `app/services/transport/
product_group.py::derive_product_group()` (the centralized PROMO → HVO →
{AdBlue,Parking,Toll/Fees} → Diesel → Service/Other precedence, mirroring
`is_synthetic()`'s centralization for the same drift-prevention reason),
`app/services/transport/fuel_ingest.py::ingest_transaction()` (module-gated,
`q2`-quantized money, `qty` deliberately unquantized). Rule 1's nullable FK
from transport into the AP invoice table is now proven twice
(`vat_claim_lines.invoice_id` from WO-49, `fuel_transactions.invoice_id` from
this order) — both target `invoices`, never each other, so the two transport
tables "relate" only through the shared AP invoice, never a new transport-
internal cross-reference. Still future work: the lock table
(`vat_claimed_invoices`, R4/R5, G2.2), claim-line materialization from
transactions (G2.4/G2.5), the monthly close (G1.3/G1.4), and the goods-code
mapping table itself (G2.8 — `product_group` is derived now; the
`product_group -> goods_code` lookup is not).
**Implementation status (WO-51, G2.2):** the one-invoice-one-submission lock
has landed — `app/models/transport/lock.py` (`VatClaimedInvoice`,
`UNIQUE(org_id, entity_id, refund_country, supplier, invoice_ref)` IS the
lock, R4; `entity_id`/`refund_country` are denormalized so the constraint
spans every claim, not just the one that currently holds a row), `app/
services/transport/lock.py::submit_claim` (a minimal stub `draft`->
`submitted` transition that acquires one lock row per invoice via a plain
ORM INSERT in the SAME flush as the claim's status mutation — a lost race
raises `IntegrityError` and rolls back the whole transition, proven on real
Postgres with two genuinely concurrent submissions racing the same invoice
key) and `withdraw_claim` (R5 — the ONLY function that deletes a lock row,
proven both structurally, via a grep-based test, and behaviorally, via a
test that directly mutates a claim's `status` and asserts no lock release
cascades). The composite RESTRICT FK from `vat_claimed_invoices` into
`fuel_transactions` is now proven end to end (one representative transaction
row per lock; protecting every row sharing an `invoice_ref` is a future
close/re-close guard's job, not this FK's). Still future work: the
checklist/period-end/minimum/deadline gate stack (G2.6), wiring
`is_synthetic()` into the lock path (G2.3's consumer side), claim-line
materialization/freezing (G2.4/G2.5), fee freezing and status derivation
(G2.9/G2.7), and every `api/routes/transport/*` route.
**Implementation status (WO-52, G2.4):** claim-line construction + note→
invoice resolution has landed. `app/services/transport/invoice_match.py`
implements C3's ONE resolution order (`resolve_invoice_ref`): two note-
matching heuristics (prefix / stem-contained, a documented interpretation of
an underspecified BA phrase — see the module docstring), then the admin-
curated override (`app/models/transport/note_override.py`'s
`VatNoteInvoiceOverride`, C4/R16 — never displaces a successful heuristic
match, `ondelete=CASCADE` on the target FK rather than `SET NULL` because a
composite-FK `SET NULL` would try to null the NOT-NULL `org_id` column too,
caught live while writing this order's own de-registration test), then the
sole-registered fallback, else UNMATCHED. `app/services/transport/
claim_lines.py::build_claim_lines` MATERIALIZES the live (unfrozen)
`VatRefundClaimLine` rows a `draft` claim's underlying `fuel_transactions`
resolve to (R2 — one row per (invoice, product_group), never an `ALL:`
aggregate) — rebuildable, refuses a non-draft claim, and only ever touches
`frozen_at IS NULL` rows (future-proofing for G2.5's freeze). Two new
read-only AP-domain seams landed alongside it, filling the `invoice_service`
gap this ADR's rule 2 always named but the codebase didn't yet have:
`app.services.invoices` (`list_by_vendor`, `get_by_id`) and
`app.services.vendors.get_by_name` — so `services/transport/*` never has to
import `app.models.invoice`/`app.models.vendor` directly (rule 2 stays CI-
enforced, `test_transport_services_do_not_import_other_domain_models`).
Still future work: the checklist/period-end/minimum/deadline gate stack
(G2.6), wiring `is_synthetic()` into the lock path (G2.3's consumer side),
freezing claim lines at submission (G2.5), fee freezing and status
derivation (G2.9/G2.7), the goods-code mapping table (G2.8), and every
`api/routes/transport/*` route.
**Implementation status (WO-53, G1.3/G1.4):** the monthly close has landed
as a durable job on the PRE-EXISTING `app.services.jobs` framework — no new
mechanism, since that framework already provides everything R31/R60 ask for
(idempotent-by-key enqueue, dead-letter + manual retry, rollback-then-fail on
any handler exception). `app/services/transport/close.py::run_close`
(re)builds live claim lines (G2.4) for every `draft` claim in scope for a
closed `"YYYY-MM"` period; `enqueue_close` keys the job on
`idempotency_key=f"transport.close:{period}"` (R31's "idempotent... hand-off"
verbatim) and the handler is registered in `app.services.job_handlers`
(`transport.close`) — there is still no `api/routes/transport/*` route, so
the close cannot run inline in a web request even by mistake (R60). G1.4
("locked lines are protected from a re-close") turned out to already be
STRUCTURALLY true from G2.4/G2.2 alone: `run_close` only ever queries
`status == "draft"` claims (a submitted claim is invisible to its own filter,
inherited from `build_claim_lines`'s existing refusal), and
`vat_claimed_invoices`' pre-existing `RESTRICT` FK into `fuel_transactions`
(WO-51) independently refuses a raw delete of a locked transaction row at
the database level — proven directly for the first time by this order's own
test (`test_g1_4_a_locked_transaction_cannot_be_deleted_the_database_refuses_it`),
since no prior order had exercised that FK's actual delete-time behavior.
Fleet Fuel's own `consolidate -> build_master -> history -> run_control ->
backup` ETL pipeline is deliberately NOT ported — there is nothing to
consolidate FROM, since `fuel_transactions` ingestion (G1.2) is already
insert-or-no-op on a structural natural key, not Fleet Fuel's DELETE-by-
period-then-reinsert. Still future work: everything G1.3/G1.4 were never
meant to cover — the checklist/period-end/minimum/deadline gate stack (G2.6),
freezing claim lines at submission (G2.5), and every `api/routes/transport/*`
route.
**Implementation status (WO-54, G2.5 — "the linchpin", `ARCH_plan.md`'s own
word):** frozen claim lines + frozen VAT base at submission have landed.
`app/services/transport/freeze.py::freeze_claim_lines` stamps `frozen_at` on
every currently-unfrozen `VatRefundClaimLine` for a claim and sets
`claim.vat_eur`/`vat_local`/`currency` from EXACTLY that claim's own lines
(C10 — never a raw period `SUM`, which a claim's own G2.4-scoped lines make
unnecessary here, unlike Fleet Fuel's live `invoice_lines()` query);
`app/services/transport/lock.py::submit_claim` calls it in the SAME flush as
lock acquisition + the status flip, so a lost lock race rolls back the
freeze too. Refuses (`code="claim_currency_mismatch"`/
`"claim_line_mixed_currency"`) rather than sum raw local-currency amounts
across more than one currency, at both the per-line-bucket level
(`build_claim_lines`) and the whole-claim level (`freeze_claim_lines`) —
master-context invariant §4.14. `vat_claim_lines` gained 3 additive nullable
columns (`net_local`/`vat_local`/`currency`, migration `bc783e1ec7c2`) so the
local-currency figure is captured PER LINE by `build_claim_lines` (G2.4)
rather than re-derived from `fuel_transactions` at freeze time. Fee freezing
(R13/G2.9) is explicitly NOT this order's scope — `claim.fee_pct`/`fee_min`/
`fee_eur` stay NULL. Still future work: the checklist/period-end/minimum/
deadline gate stack (G2.6), fee freezing (G2.9), status derivation (G2.7),
and every `api/routes/transport/*` route.
**Implementation status (WO-55, G2.8):** the Art. 9 goods-code mapping has
landed — `app/services/transport/goods_code.py::GOODS_CODE` is harvested
VERBATIM from `BA_fleet_fuel.md` A6 (Diesel/HVO/Promo adj -> "1", Toll/Fees
-> "4", AdBlue/Parking/Service/Other -> "10"); `derive_goods_code()` defaults
an unrecognised `product_group` to `"10"`, never `"9"` (R11), on top of the
pre-existing DB CHECK constraint from WO-49
(`ck_vat_claim_lines_goods_code_never_9`) — two independent layers, not one.
`build_claim_lines` (G2.4) now populates every line's `goods_code` at
construction time. This task depended on G1.2 ONLY (`ARCH_plan.md`'s own
scoping) — it is a pure function of `product_group`, unrelated to the
claim-freeze/submission machinery, and landed independently of G2.6/G2.7/
G2.9. Still future work: fee freezing (G2.9, decision-gated —
see `docs/DECISIONS-NEEDED.md` §10), status derivation (G2.7), the checklist/
annual-mop-up/document-presence/receipt-control-waiver remainder of the gate
stack (G2.6 slices 2+, R6/R10/R15), and every `api/routes/transport/*` route.
**Implementation status (WO-56, G2.6 slice 1 — the highest-risk rule in the
whole M3 plan, `ARCH_plan.md` risk register L-1, score 6):** the hard
period-end gate (R7) and the Art. 17 minimum-amount gate (R8) have landed as
REAL legal gates inside `lock.submit_claim` — the first two rules in the
G2.6 stack to move past "schema exists" into "actually blocks a bad
submission." `app/services/transport/deadline.py::period_ended` refuses
(409 `period_not_ended`) before anything mutates; `app/services/transport/
minimum.py::below_minimum` is previewed (`freeze.preview_vat_base`, a new
read-only twin of `freeze_claim_lines`'s own arithmetic) BEFORE the freeze —
matching Fleet Fuel's own D5 submission-gate order (checklist -> period-end
-> minimum -> lock/freeze) — so a below-minimum claim never freezes or
locks, unless explicitly overridden (`override_minimum=True`, recorded in
`status_note`, not yet a permission-gated route param since no route exists
yet). `deadline.py::filing_deadline`/`deadline_status` (R9, the 60-day risk
scanner) are pure functions NOT wired into the submission gate — this
software never blocks a late filing attempt; only a future dashboard (G4.3)
consumes them. Still future work: fee freezing (G2.9, decision-gated —
see `docs/DECISIONS-NEEDED.md` §10), status derivation (G2.7), the checklist/
document-presence/receipt-control-waiver remainder of the gate stack (G2.6
slice 3+, R10/R15), and every `api/routes/transport/*` route.
**Implementation status (WO-57, G2.6 slice 2):** R6 (the annual mop-up /
quarterly duplicate-block) has landed —
`lock.py::_apply_annual_mop_up_or_duplicate_block` runs after the R7/R8
gates and before the freeze: for an ANNUAL claim, an invoice already locked
by a QUARTERLY claim is silently excluded from the set to lock (the
"mop-up"); an invoice already locked by ANOTHER ANNUAL claim is treated as a
fail-closed blocking duplicate (an interpretation beyond the harvested
text, which only describes the quarter-exclusion case); a QUARTERLY claim
treats ANY existing lock overlap as a duplicate and blocks the WHOLE
submission before any mutation; an annual claim left with nothing after
exclusion is refused (`code="empty_claim_set"`, C6's "nothing to claim
annually" verbatim). One pre-existing WO-51 test
(`test_g2_2_submit_claim_on_an_already_locked_invoice_...`) was updated to
expect the NEW, cleaner `ConflictError`/`duplicate_invoice_lock` instead of
the raw DB-level `IntegrityError` it used to prove — a real improvement
(catching an already-known duplicate before any mutation, rather than
after a failed insert), not a weakened assertion; the DB constraint itself
stays independently proven by `test_g2_2_natural_key_uniqueness_
constraint_rejects_a_raw_duplicate_insert` and the genuine-concurrent-race
case by `tests/test_transport_lock_concurrency.py` on real Postgres
(re-verified green on a fresh scratch cluster after this order's changes).
Still future work: fee freezing (G2.9, decision-gated —
see `docs/DECISIONS-NEEDED.md` §10), status derivation (G2.7), the checklist/
document-presence/receipt-control-waiver remainder of the gate stack (G2.6
slice 3+, R10/R15), and every `api/routes/transport/*` route.
**Implementation status (WO-58, G2.6 slice 3):** R10 (document-presence)
and R15 (receipt-control waivers) have landed — `document_gate.
enforce_document_presence` checks every real, resolved `vat_claim_lines`
row (never an `UNMATCHED` one) has >=1 captured document with real stored
bytes, via a new batch seam `extraction.invoice_ids_with_documents` (one
query, no N+1 — the AP-domain read seam transport is allowed to add,
mirroring the existing `app.services.invoices`/`app.services.vendors`
imports); this reads the claim's own MATERIALIZED lines, not `submit_claim`'s
still-caller-supplied `invoices` tuple, since the lines are what actually
gets frozen. A new tenant table `vat_receipt_waivers` (grain `(org, claim,
supplier)`, narrower than the harvested `(entity, refund_country,
ref_period, supplier)` since a claim's own grain already fixes those three
fields) backs `waiver.set_waiver`/`remove_waiver`/`waived_suppliers`:
`set_waiver` refuses a supplier with any registered invoice for the claim's
refund country (reusing `invoice_match.registered_invoices`, never a second
implementation of that check); `claim_lines.build_claim_lines` excludes a
waived supplier's transactions from grouping BEFORE the resolution step
(C9's "excluded from the claim by construction" — never even an `UNMATCHED`
line); `submit_claim` stamps every active waiver into `status_note` at
submission. Both gates run after the R6 mop-up/duplicate-block gate and
before the freeze, extending D5's order. Still NOT wired anywhere: R3's
`is_synthetic()` as an actual submission-blocking gate over a REMAINING,
un-waived `UNMATCHED` line — a real, pre-existing gap this order does not
close (flagged explicitly in WO-58's own scope). Still future work: fee
freezing (G2.9, decision-gated), status derivation (G2.7), the adjustable
checklist as data (G2.10), and every `api/routes/transport/*` route.
**Implementation status (WO-59, G2.7):** the status lifecycle 1A->5 has
landed, narrowly scoped. `status.derive_stage` computes the correct
system-derived `AUTO_CODES` value (`1A`/`1B`/`1C`/`1E`) for a `draft`
claim in D3's literal order, reusing `claim_gates.is_synthetic` (unresolved
lines) and `document_gate.missing_document_invoice_ids` (a new non-raising
twin of WO-58's own R10 gate, extracted so the blocking check and this
read-only preview share ONE query) as documented stand-ins for the still-
unbuilt G2.10 checklist, and `minimum.below_minimum`/`waiver.
waived_suppliers` as the documented "verdict caveat" (1C) signal.
`status.set_status_code` is the ONE writer of `status_code`: refuses every
`AUTO_CODES` value ("system-controlled") and refuses every `MANUAL_CODES`
value — INCLUDING `"2"` itself — while the claim is still `draft`
(`code="claim_not_submitted"`), R17 verbatim. `lock.submit_claim`
additively stamps `status_code="2"` in the same flush as its existing
lock/freeze/status writes. Deliberately NOT built: the `ENGINE_OF`-driven
engine-state transitions for 3/3A/3B/3C/3D/4/4A/5 (moving the coarse
`status` column itself to `approved`/`paid`/`rejected`) — entangled with
G2.9's fee-freezing/settlement-route logic, which stays decision-gated;
`set_status_code` manages ONLY the workflow-code LABEL (+ `action_deadline`,
R12's soft `2B`/`3D` reminder) in this order, a documented, tested
limitation rather than a silent gap. Still future work: G2.9 (decision-
gated), G2.10 (the adjustable checklist, which will REPLACE `derive_stage`'s
two-check proxy), and every `api/routes/transport/*` route.
**Implementation status (WO-60, G2.10 slice 1):** the adjustable
submission checklist has landed as DATA — a new tenant table
`vat_checklist_rules` (key/label/scope/check_type/reference/active/sort)
backs `checklist.seed_default_rules`/`set_active`/`submission_checklist`.
Only `customer_data` and `bank_account` (`check_type="data"`,
`scope="customer"`, evaluated against the claimant `IssuerProfile` —
`registration_number`+`vat_number`+`address_line1`, and `iban`
respectively) are seeded/evaluable in this slice — a deliberate PARTIAL
harvest of the six-rule `DEFAULT_CHECKLIST`, documented rather than
silently short; `contract`/`nace`/`trade_register`/`power_of_attorney`
need a document-requirements-with-expiry concept this codebase does not
yet have, or a new `nace_code` column, both flagged for a follow-up slice.
The four claim-level items (receipt control, unresolved refs, documents
attached, period ended) reuse WO-56/58's own pure checks — a materialized
`vat_claim_lines` row collapses every unresolved transaction under one
literal `"UNMATCHED"` ref with no supplier retained, so naming "the
missing supplier" re-queries `fuel_transactions` directly (one
duplicated SELECT, zero duplicated resolution/waivability logic).
`status.derive_stage` (G2.7) now consults this evaluator, replacing
WO-59's own two-check proxy exactly as that order's docstring anticipated.
Still future work: G2.9 (decision-gated), G2.10 slice 2 (the `document`
check_type, `nace_code`, country-scope rules), and every
`api/routes/transport/*` route.
**Implementation status (WO-61, G3.1 slice 1):** the per-country supplier
legal-entity registry has landed — a new tenant table `supplier_vat_
registrations` (`(org, supplier, country)` -> `vat_number`/`entity_name`/
`source`) backs `app.services.transport.supplier_entity`:
`get_registration` (a single exact-key SELECT, R21 — marker-only, no
fuzzy matching anywhere), `set_registration` (the only admin-curated
writer, ALWAYS wins), `learn_registration` (R22 — seeds a NEW `"capture"`
row only when none exists; never overwrites an existing row of either
source, and never touches a `Vendor`/group-primary row or queues a
pending-change request, a deliberate contrast with A2.3's vendor-bank-
detail dual control since this is diagnostic/filing metadata, not a
payment-redirection vector). **R20 (capture actually reading the seller
off a real invoice document — the Eurowag per-country footer, the E100
anchor) is explicitly NOT closed by this slice** — that is text/PDF
extraction, `G3.2` (the fuel-card parser registry, a separate XL-effort,
7-network build); `learn_registration` has no real caller yet, proven
correct at the function level with a synthetic "just-captured" input,
mirroring `is_synthetic()`'s own WO-49 debut with zero consumers wired in.
Still future work: G3.2 (the actual parsers, which would call
`learn_registration` for real), G2.9 (decision-gated), G2.10 slice 2, and
every `api/routes/transport/*` route.
**Implementation status (WO-62, G3.2 slice 1):** the fuel-card parser
registry has landed — `app.services.transport.fuel_card_parser` (the
deterministic-first `FuelCardParser` registry, mirroring the AP-domain
`extraction_provider.py` PATTERN over a fuel-transaction-shaped output,
not its invoice-shaped type) plus the FIRST of seven networks,
`parsers/eurowag.py` (`EurowagParser`): reads a statement's transaction
CSV block into `fuel_transactions` rows and anchors the per-country seller
footer (`"Pārdevējs / Verkoper:"` + the harvested legal-form token set,
verbatim from `BA_fleet_fuel.md` §3.B1) into detected entities — the Czech
"W.A.G. Issuing Services, a.s." factoring entity is structurally
unreachable since the anchor only inspects lines carrying the literal
seller label. `app.services.transport.statement_ingest.ingest_statement`
is the REAL caller `fuel_ingest.ingest_transaction` (WO-50) and
`supplier_entity.learn_registration` (WO-61) were both built for but never
had — it resolves every line's EUR figure via `app.services.fx.to_eur`
BEFORE writing anything (a statement with one unconvertible line writes
ZERO rows, not "all but one"), reads the network off the PARSED statement
(never a caller-supplied string, so a mislabeled upload can't be
miscategorized), and is gated on the `transport` entitlement first,
identical discipline to every other transport service. **R20 is now
CLOSED for Eurowag** — the remaining six networks (E100, Q8/Port One, DKV,
TFC, Moeve, BP/Aral) are explicit named future slices in
`docs/plan/plan-a/wo/WO-62-G3.2-slice1.md`'s "Out of scope", each with its
own real, learned format quirk (E100's VAT-inclusive gross and buyer-VAT-
annexe hazard; Q8's off-invoice Port One rebate, the reason `net_eur_eff`
exists as a distinct column; DKV's 5.63% service fee; TFC's hub-only
discount; Moeve's 6-dp VAT-inclusive maths; BP's Polish split-payment).
No migration — this order composes WO-50/WO-61's existing tables through
their existing write services. Still future work: G3.2's remaining six
networks, G3.3 (the two independent validation regimes — line-count tie-
out + capture review gate — explicitly DEPENDS on G3.2 per `ARCH_plan.md`),
a persisted statement review-queue (this slice's review surface is the
returned `warnings` list only), G2.9 (decision-gated), G2.10 slice 2, and
every `api/routes/transport/*` route.
**Implementation status (WO-63, G3.2 slice 2):** the SECOND network,
`parsers/e100.py` (`E100Parser`), has landed in the same registry —
`fuel_card_parser._default_parsers()` now returns `[EurowagParser(),
E100Parser()]`. E100 is R20's OWN SECOND worked example
(`BA_fleet_fuel.md` §3.B1): `_detect_entities` anchors ONLY to lines
carrying the literal `"E100 International Trade"` marker string — a
co-present, unrelated buyer-VAT annexe line ("repeats on every annexe
page") is never inspected. This order also proves the registry handles a
structurally DIFFERENT money model for the first time: E100's statement
supplies only `gross_local` (VAT-inclusive) and a per-line `vat_rate`, so
`net_local`/`vat_local` are DERIVED by the reverse calculation
(`net_local = gross_local / (1 + vat_rate/100)`, `vat_local = gross_local -
net_local`), entirely in `Decimal`, with `vat_rate` bounded to `[0, 100]`
(a `ValueError` outside that range, never silently clamped) — every
subsequent VAT-inclusive network (Moeve is next) needs this exact code
path. `statement_ingest.ingest_statement` required ZERO changes — proven
directly by re-running WO-62's own `test_g3_2_fuel_card_parser.py` and
`test_g3_2_statement_ingest.py` byte-for-byte unmodified alongside the new
E100 suites, and by a registry-level test dispatching a well-formed file of
each network to the correct parser in the same test. R22 (learning never
clobbers a curated value) and the two-phase FX guarantee are both re-proven
end to end for E100 specifically. No migration — pure parser addition.
**R20 is now CLOSED for Eurowag AND E100** — the remaining five networks
(Q8/Port One, DKV, TFC by Moya, Moeve, BP/Aral) are named future slices in
`docs/plan/plan-a/wo/WO-63-G3.2-slice2.md`'s "Out of scope". Still future
work: unchanged from the WO-62 note above, now with E100 struck off.
**Implementation status (WO-64, G3.2 slice 3):** the THIRD network,
`parsers/q8.py` (`Q8Parser`), has landed in the same registry —
`fuel_card_parser._default_parsers()` now returns `[EurowagParser(),
E100Parser(), Q8Parser()]`. Q8's own money model reuses Eurowag's shape
(independently-given `net_local`/`vat_local`/`gross_local` — Q8 invoices
at LIST price, not VAT-inclusive gross like E100), so no new arithmetic
was needed; what this order proves for the first time is that a SINGLE
statement can legitimately carry lines for more than one country and
currency (`BA_fleet_fuel.md` §5.1's own "per-line country + currency"
quirk for Q8) — neither `eurowag.py` nor `e100.py`'s fixtures ever
exercised that before. `Q8Parser` deliberately attempts NO seller-entity
detection at all (`entities` is unconditionally `[]`, with one
explanatory warning distinguishing "never attempted" from
`eurowag.py`/`e100.py`'s "attempted, none found") — `BA_fleet_fuel.md`
gives no footer label or anchor marker for Q8 the way it does for
Eurowag/E100, so R20 stays **CLOSED at exactly Eurowag and E100**,
unchanged by this order; an adversarial test plants a "Kuwait
Petroleum ... VAT: ..."-shaped decoy line in the raw file and confirms it
is never picked up (no scan exists to accidentally match it). Q8's
`net_eur_eff` (the Port One off-invoice-rebate figure — "the entire
reason `net_eur_eff` exists", per the WO-62 note above) is likewise
deliberately left at `ingest_transaction`'s existing default
(`= net_eur`); reconciling Q8's list-price statement against a SEPARATE
Port One rebate export is a cross-statement merge with no worked column
layout given anywhere in the harvested spec, and `ARCH_plan.md` already
scopes it as its own, later board item — **G4.2, "Price basis +
`net_eur_eff` source guard"** (R49/R50, milestone M5) — not a G3.2 slice.
`statement_ingest.ingest_statement` required ZERO changes — proven
directly by re-running WO-62/WO-63's own suites byte-for-byte unmodified
alongside the new Q8 suites, and by a registry-level test dispatching a
well-formed file of each of the three networks to the correct parser in
the same test. No migration — pure parser addition. **R20 stays CLOSED
for Eurowag AND E100 only** — Q8 is G3.2 progress, not an R20 claim. The
remaining four networks (DKV, TFC by Moya, Moeve, BP/Aral) are named
future slices in `docs/plan/plan-a/wo/WO-64-G3.2-slice3.md`'s "Out of
scope". Still future work: unchanged from the WO-62/WO-63 notes above,
now with Q8 struck off.
The Insight projection rule has its first composed endpoint: the home dashboard
(`GET /dashboard`, `services/dashboard.py`, WO-16 / I1.1) — it consumes only
canonical services (`approval_policy.waiting_for`, `expense_approval.pending_report_count`,
`payment_run.runs_awaiting_check`, `vendors.pending_change_count`,
`extraction.review_queue_summary`, `ap_aging`, `issued_reports`, `cash_position`),
owns no tables and adds no arithmetic on amounts.

## Context

The product charter names ten "core modules". Modelling all ten as bounded contexts
would repeat two known failure modes: (a) "Dashboard" and "Reports" own no domain
concept, no lifecycle and no writes — treating projections as modules is exactly how
math gets forked (the codebase has already exhibited the seed of this: the Explore
pivot engine and the fixed by-dimension report carry different dimension registries —
see ADR-0026); and (b) a transport vertical (EU VAT refunds under Dir. 2008/9/EC,
fuel/toll line-item analytics, excise) is being added, harvested as *specification*
from the retired Fleet Fuel system, and its intensely specific domain (litres,
`net_eur_eff`, per-country seller entities, Art. 9 goods codes) must not leak into
`invoices`, `line_items` or `vendors`, or every non-transport tenant pays the
complexity tax forever.

## Selected approach

### 1. Eight domain contexts, two projection layers

Domain contexts (own tables, own lifecycle, own invariants): **Intake & Capture**,
**AP Record**, **AR / Issuing**, **Settlement & Banking**, **Expenses**, **Money &
Compliance kernel** (pure), **Organization & Identity**, and (future) **Transport**.

Cross-cutting projection layers (own **no** domain tables; may own recomputable read
models): **Insight** (analytics/explore/benchmark/budget/cash/dashboards) and
**Export & Reporting** (CSV/Excel/PDF/SAF-T/ERP/e-invoice/audit export). Projection
rules: every figure derives from one canonical query registry — no surface may fork
the math; a materialised rollup must be recomputable through the same code path and
drift-checked; exports are read-only, formula-injection-safe, never invent a figure
and never sum across currencies.

**Integrations is a register of adapters, not a context.** Each adapter is owned by
the context it feeds, behind an existing Protocol seam (`ExtractionProvider`,
`BillingProvider`, the storage backend Protocol, the email-intake payload contract,
the ERP exporter registry, `sso_connections`). New integrations add an adapter,
never a module. **SaaS Administration** splits into Entitlements & Metering
(`org_modules`, `plans`, `usage_counters`, `plan_policies` — WO-47: quota keyed by
the org's plan) and Subscription Billing (`billing_*` behind `BillingProvider`).

### 2. The transport seam — six binding rules

1. **Transport owns only transport tables.** It never adds a column to `invoices`,
   `line_items` or `vendors`; fuel line detail lives in its own tables with a
   nullable FK to the AP invoice it was captured from.
2. **Transport reads the core through services, never through joins.** A boundary
   test (extending `tests/test_boundaries.py`) will assert no transport service
   imports another domain package's models.
3. **Transport is an entitlement** — an `org_modules` key `transport`, default
   **off**, plan-gated exactly like `issuing`/`expenses`. A tenant that does not buy
   it sees no nav, no routes, and pays no query cost.
4. **Transport reuses the platform floor unchanged** — `core/money` (Decimal
   ROUND_HALF_UP), the hash-chained audit, tenancy (`org_id` + composite FK + RLS),
   the durable jobs queue, `filesec` at the single upload choke point, `documents`
   for the vault, `keyvault` for any stored credential.
5. **Transport adds permissions, not roles** — new `Permission` members join
   `app/core/authz.py` with rows for all 8 business roles; no new role tier.
6. **Transport never gates a core figure and the core never gates a claim.** The
   advisory covenant is preserved: excise, overcharge, benchmark and any AI seam are
   advisory and cannot mutate a legal figure.

### 3. The one Fleet Fuel invariant that is *translated*, not copied

Fleet Fuel kept its VAT-claim data in a **physically separate database** so a
monthly reload (DELETE-by-period + INSERT) could never corrupt filed claims — a
SQLite-shaped solution to a real problem. Copied into Postgres it would fight the
tenancy model, RLS and the single-transaction audit commit. The translation:

- A claim **materialises and freezes its own lines at submission** (alongside the
  frozen VAT amounts and fee terms). Fleet Fuel derived claim lines live at read
  time, which is precisely why it needed the separate database.
- Once frozen, a re-close of the period **cannot change what was filed** — nothing
  reads through to the transaction rows any more.
- Transaction rows locked into a submitted claim are protected **at the database
  level**: the period-scoped delete in the close excludes locked rows, and a
  `RESTRICT` FK from the claim-lock table makes accidental deletion an error rather
  than silent data loss.
- The close runs as a **durable idempotent job** (`jobs` kind, tenant-scoped,
  idempotent by `(org, period)`) on the existing queue.

This is **strictly stronger** than the original: Fleet Fuel's separation protected
the claim *store* but still recomputed claim lines from live transaction data on
every read — a reload changed what a claim *showed* even if not what it stored.
Freezing at submission protects the *content*; the FK + delete-guard protect the
*inputs*; the idempotent job protects the *process* — and all three live inside the
same transactional, RLS-guarded, audit-chained database instead of beside it.

## Alternatives considered

- **Ten modules as ten contexts** — rejected: projections-as-modules fork math;
  Integrations-as-a-context centralises what belongs at each context's edge.
- **Transport columns on the AP tables** (a `fuel_*` column family on
  `invoices`/`line_items`) — rejected: every tenant pays; the AP record is the most
  reusable asset in the platform.
- **A separate transport database / read-only replica** (copying Fleet Fuel §3.H
  literally) — rejected: fights RLS, composite-FK tenancy and the atomic
  audit-commit; the freeze-at-submission translation is stronger (see above).
- **A separate transport service** — rejected per ADR-0001; one deployable, one
  regression net.

## Why appropriate

It converts "build 10 modules" into "close gaps in 8 contexts that mostly exist and
build 1 new one", keeps the most valuable asset (the AP/AR record) vertical-neutral,
and fixes the hardest structural decision — how a legally-sensitive vertical plugs in
— before any of its code exists, when the rules are cheapest to enforce.

## Risks

- The projection rule is discipline until the canonical query registry is complete —
  drift is possible in the interim (tracked as C1.6/C1.7 in ADR-0026).
- The transport rules are asserted here before the code exists; the boundary test in
  rule 2 must land in the same PR as the first transport module, or the seam is
  fiction.

## Revisit when

The transport vertical's first implementation PR opens (the six rules become CI
assertions); a second vertical arrives (the seam pattern generalises); or a
projection needs its own writes (it is then a context and must say so).
