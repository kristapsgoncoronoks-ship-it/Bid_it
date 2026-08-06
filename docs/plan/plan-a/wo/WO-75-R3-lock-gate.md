# WO-75 — R3: the synthetic-line refusal as a LOCK-GATE consumer in `submit_claim`

> The small, precisely-scoped gap WO-74's design decision 8 recorded and
> deliberately did not smuggle into G2.12's scope: "`lock.submit_claim` does
> not yet apply the R3 synthetic block itself … Wiring the submit-side
> synthetic gate is reported as a follow-up, not smuggled into this order's
> scope." This order is that follow-up. No board G-row of its own — it
> completes R3's named consumer set inside the already-closed G2.6/G2.12
> machinery.

**WORK ORDER 75 — R3 lock gate: `submit_claim` refuses a claim whose
materialized lines include ANY synthetic line, via THE one
`claim_gates.is_synthetic()` predicate (rules ledger R3; spec §3.C C2 / D5).
Effort S 1–2d. Priority P1. Milestone M3. Depends on: WO-49 (the predicate),
WO-52 (materialized lines), WO-58 (R10's read-the-claim's-own-lines
precedent + R15 waiver exclusion), WO-74 (recorded the gap).**

### Objective and business value

The gap, with verified evidence: `docs/plan/shared/specs/BA_fleet_fuel.md`
§3.C C2 (lines 433–443) — *"A pack containing ANY synthetic line CANNOT be
filed. The same predicate is used by: **the lock gate** (`set_status`,
`bad = [... if _synthetic(r)]` → `BLOCKED - unresolved invoice refs`), the
checklist gate …, the readiness check …, the workbook builder"* — and the R3
requirements row (line 1360): *"used by the lock gate, the checklist gate,
the readiness check AND the workbook builder."* In this codebase the
predicate exists (`backend/app/services/transport/claim_gates.py::
is_synthetic`, WO-49), the workbook/evidence builders block on it (WO-74,
`claim_pack._load_pack` → `synthetic_line_in_pack`), and the checklist/stage
surfaces NAME the rule advisorily (WO-59/WO-60) — but
`backend/app/services/transport/lock.py::submit_claim` applies no R3 gate at
all: a claim whose materialized lines include an `UNMATCHED` row currently
FREEZES AND LOCKS (`document_gate.py`'s own docstring says so explicitly:
*"R3's `is_synthetic()` is still not wired as an actual submission-blocking
consumer anywhere"*), and the operator only discovers the problem later when
`build_workbook`/`build_evidence_pack` refuse.

Who stops losing money: an UNMATCHED line means "we cannot prove which real
invoice this euro amount belongs to" — filing it risks the whole claim
(claim_gates' own fail-closed rationale). Today that claim gets FROZEN and
its invoices LOCKED (R4/R5) before anyone finds out; unwinding requires an
explicit withdraw, and the frozen/locked interval is exactly the window in
which a competing legitimate claim over the same invoices would be refused
as a duplicate (R6). Refusing at submit — before anything mutates — keeps
the claim in `draft` where the fix (note-matching, an override, a waiver)
is still cheap, and preserves Fleet Fuel's defence-in-depth: all four
surfaces block independently, with the same predicate.

### Where the gate sits in the D5 order (the harvested position, cited)

D5 (spec §3.D, line ~543): *"checklist (1A) → period-end (1B) →
national-currency minimum (Art. 17) → then `set_status(engine)` which
applies **synthetic/duplicate/document** gates"* — synthetic is named FIRST
of the three engine gates. C9 (lines ~480–484) confirms the internal
`set_status` order by listing what a waived supplier is dropped before:
*"dropped from `invs` before the **`bad` gate**, `claim_set`, **locks**,
**doc-gate** and the frozen VAT base"* — i.e. bad(synthetic) → claim_set/
locks (R6 machinery) → doc-gate (R10) → freeze. WO-73 placed the R44
activation gate at the ENTRY of the engine-gate group ("layered on top" of
`set_status`, §3.E). The gate therefore lands:

> draft → non-empty → period-end (R7) → minimum (R8) → activation (R44) →
> **synthetic (R3, THIS ORDER)** → mop-up/duplicate (R6) → waiver stamp
> (R15) → document-presence (R10) → freeze → lock → status flip.

Like R10 (WO-58's recorded reasoning), the gate reads the claim's OWN
materialized, currently-unfrozen lines (`frozen_at IS NULL`) — the thing
that actually gets frozen — never `submit_claim`'s caller-supplied
`invoices` tuples. A claim with no materialized lines passes trivially
(same semantics as R10; the R8 minimum gate already refuses a zero-base
claim unless overridden). Waived suppliers never became lines at all
(WO-58, "excluded by construction"), so a fully-waived synthetic supplier
does not trip this gate — proven by a dedicated interaction test.

### One scan, not three

- `claim_gates` gains the DB-backed pair mirroring `document_gate`'s
  established shape: a non-raising `unfrozen_synthetic_refs()` (sorted
  distinct offending refs) + a raising `enforce_no_synthetic_lines()`
  (`ConflictError`, `code="unresolved_invoice_refs"` — the harvested
  message "BLOCKED - unresolved invoice refs" made a stable slug), both on
  THE one predicate. The R3 material stays in ONE module.
- `claim_pack._load_pack`'s scan stays inline: it scans FROZEN lines it has
  already loaded for rendering (a second query would be pure waste) and
  already imports the one predicate — R3's "one predicate" invariant is
  about `is_synthetic`, which remains single.
- The checklist's `unresolved_refs` item deliberately keeps its
  transaction-level scan — it must NAME the offending suppliers, which the
  collapsed `UNMATCHED` line cannot yield (`checklist.py`'s documented
  "WHY THE … SPLIT RE-QUERIES `fuel_transactions`" rationale). Advisory
  surface names suppliers; the hard gate names refs; both fire on the same
  predicate-defined set.

### Scope

**In scope:**
- `backend/app/services/transport/claim_gates.py` — add
  `unfrozen_synthetic_refs` + `enforce_no_synthetic_lines`; update the
  module docstring (consumers now exist).
- `backend/app/services/transport/lock.py` — call the gate after R44,
  before R6; update the module/function gate-order docstrings.
- `backend/tests/transport/test_r3_lock_gate.py` (**new**) — the matrix
  below.
- Fixture raises in existing suites whose successful submissions ride
  UNMATCHED lines (each listed in the final report with rationale; zero
  assertions weakened). `tests/transport/conftest.py` gains a shared
  `register_documented_invoice` helper so each raise is two lines, not a
  copy-pasted vendor/invoice/document block.
- Boards: `docs/transport/rules.md` R3 row (lock-gate consumer wired),
  `TODO.md` (WO-75 row + suite line), `docs/plan/plan-a/README.md` scale
  line if the collected count changes.

**Out of scope:**
- The readiness-check consumer as a distinct surface (the stage view
  `derive_stage` already returns 1A off the checklist's `unresolved_refs`
  item — WO-59/WO-60; no fourth surface exists to wire).
- G2.9 fee freezing/settlement (decision-gated).
- `api/routes/transport/*` + UI (the M3 route batch).
- Any change to `claim_pack`'s frozen-line scan or the checklist's
  supplier-naming scan (both documented, both stay).

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/claim_gates.py` | add the scan/enforce pair; docstring truth-up |
| `backend/app/services/transport/lock.py` | wire the gate; docstring gate order |
| `backend/tests/transport/test_r3_lock_gate.py` | **new** — gate tests |
| `backend/tests/transport/conftest.py` | shared `register_documented_invoice` fixture helper |
| `backend/tests/transport/test_*.py` (as measured) | fixture raises only |
| `backend/tests/transport/test_r3_is_synthetic.py` | docstring truth-up ("consumers exist now") |
| `docs/transport/rules.md`, `TODO.md`, `docs/plan/plan-a/README.md` | boards |

### Implementation guidance

1. `unfrozen_synthetic_refs(db, org_id, claim_id) -> list[str]`: select
   `(invoice_ref, vat_id)` of `VatRefundClaimLine` where org/claim match and
   `frozen_at IS NULL`; return `sorted({ref for ref, vat_id … if
   is_synthetic(ref, vat_id)})`. Org-scoped like every line query.
2. `enforce_no_synthetic_lines(db, org_id, claim_id) -> None`: raise
   `ConflictError` naming every offender,
   `code="unresolved_invoice_refs"`. Fails CLOSED — the claim_gates
   docstring already states why (a false-negative here is a silent
   forfeiture-of-money bug).
3. Wire into `submit_claim` between `enforce_activation` (R44) and
   `_apply_annual_mop_up_or_duplicate_block` (R6), with the C9/D5 citation
   in the comment. Nothing is mutated before the freeze, so the
   nothing-mutated proof pattern holds automatically.
4. Fixture raises: where a test's successful submit rode an UNMATCHED line,
   register a real vendor+invoice (+ vaulted document, since a resolved
   line then meets R10) via the new conftest helper — the WO-73
   `activate_entity` precedent. Where a test deliberately NEEDED a frozen
   synthetic line (`test_g2_12_synthetic_unmatched_frozen_line_refuses_
   both_artifacts`), seed the corruption directly on the frozen row (the
   file's own `line.vat_id = "INPUT"` tamper precedent) — the assertion set
   is unchanged and the test now proves defence-in-depth (the pack blocks
   even when the frozen set is corrupted post-submit).

### Invariants this order must preserve

- **R3 (one predicate):** the new scan calls `claim_gates.is_synthetic`;
  a structural test asserts `lock.py` defines no rival predicate
  (no `"INPUT" in` / `startswith("ALL:")` / `== "UNMATCHED"` literals).
- **§4.4 (tenancy):** the scan is org-filtered; `submit_claim`'s existing
  opaque-404 fetch runs first.
- **D5 nothing-mutated:** a refused submit leaves status `draft`,
  `status_code` NULL, zero lock rows, zero frozen lines, `vat_eur` NULL —
  asserted explicitly.
- **§4.16 (audit):** a refused submit audits nothing (no mutation
  happened); the success path's existing audit event is untouched.
- **R15 interaction:** waived suppliers are excluded by construction, so
  waiving remains the legitimate path past this gate for a
  genuinely-uninvoiced supplier — tested.

### Database / migration impact

None. No table, no column, no RLS change.

### Testing requirements

`backend/tests/transport/test_r3_lock_gate.py`:
- `test_r3_submit_refuses_a_claim_with_an_unmatched_line_nothing_mutated`
  — UNMATCHED line → `AppError` 409 `unresolved_invoice_refs` naming
  `UNMATCHED`; claim still `draft`, `status_code` None, zero
  `VatClaimedInvoice` rows, zero `frozen_at`, `vat_eur` None.
- `test_r3_submit_refuses_an_input_vat_id_on_an_otherwise_resolved_line` —
  the predicate's second input is live at submit.
- `test_r3_gate_precedes_the_duplicate_block` — overlapping lock AND an
  UNMATCHED line → `unresolved_invoice_refs`, not
  `duplicate_invoice_lock` (C9's bad-gate-before-locks order).
- `test_r3_gate_precedes_the_document_gate` — undocumented resolved line
  AND an UNMATCHED line → `unresolved_invoice_refs`, not
  `invoice_document_missing`.
- `test_r3_fully_waived_supplier_never_trips_the_gate` — genuinely
  uninvoiced supplier (synthetic `INPUT` refs) waived per R15 + one
  resolved documented supplier → submit succeeds (WO-58's
  by-construction exclusion verified against this gate).
- `test_r3_claim_with_no_materialized_lines_passes_trivially` — same
  semantics as R10 (with `override_minimum=True`), documented.
- `test_r3_unfrozen_synthetic_refs_is_sorted_distinct_and_org_scoped`.
- `test_r3_lock_module_defines_no_rival_predicate` — structural
  (the WO-51 grep-test style).
- Module-disabled / cross-tenant submits are already covered by the
  existing lock suites (the gate adds no new entry path).

### Acceptance criteria (verifiable checklist)

- [ ] `submit_claim` on a claim whose unfrozen lines include `UNMATCHED`
      raises 409 `unresolved_invoice_refs`; afterwards `status == "draft"`,
      zero lock rows, zero frozen lines (the named test passes).
- [ ] The refusal fires BEFORE R6 and R10 (both positional tests pass).
- [ ] A fully-waived synthetic supplier does not trip the gate; the claim
      submits (the named test passes).
- [ ] `claim_gates.py` contains the ONLY line-scan the lock gate uses;
      `lock.py` contains no synthetic-pattern literal (structural test).
- [ ] `docs/transport/rules.md` R3 row lists the lock gate as a wired
      blocking consumer.
- [ ] Full suite green; every fixture raise listed with rationale; zero
      assertions weakened; pii-scan clean.

### Rollback strategy

Pure code revert (two service edits + tests + doc rows). No migration, no
data effect. Narrow mitigation without revert: none needed — the gate only
ever refuses earlier what `claim_pack` already refuses later.

### Documentation to update

- `docs/transport/rules.md` — R3 row.
- `TODO.md` — WO-75 row + suite line.
- `docs/plan/plan-a/README.md` — scale line if counts change.
- No ADR contradicted: ADR-P3's freeze semantics gain an earlier guard,
  unchanged in meaning.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_r3_lock_gate.py -q
python -m pytest tests/transport tests/test_boundaries.py -q
python -m pytest -q                                   # full baseline, once, at the end
# the demonstration — the lock gate blocks what the pack would have blocked later:
grep -n "enforce_no_synthetic_lines" app/services/transport/lock.py
cd .. && python scripts/pii_scan.py --tree
```
