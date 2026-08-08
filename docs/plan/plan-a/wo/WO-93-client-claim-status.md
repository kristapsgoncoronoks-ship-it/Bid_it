# WO-93 — the client claim-status portal (G4.4 / R39)

> `ARCH_plan.md` line 1646 lists **G4.4 — Client claim-status portal
> (plain-language stages only)**, P1, M, depending on G2.7, rule **R39**.
> `docs/transport/rules.md` carries **no R39 row today** — verified with
> `grep -o "^| R[0-9]*" docs/transport/rules.md | sort -u`, which returns
> R1…R9, R10, R11, R12, R15, R16, R17, R20, R21, R22, R25, R26, R30, R31, R38,
> R41, R42, R44, R45, R50, R53, R56, R60 and no R39. The rule is unharvested,
> so this order is its first implementation and adds its row.

**WORK ORDER 93 — the client claim-status portal (board G4.4, rule R39):
`app/services/transport/client_status.py` +
`app/api/routes/transport/claim_status.py` +
`app/schemas/transport_client_status.py` +
`frontend/src/pages/ClaimStatus.tsx`, translating the internal 1A..5 workflow
codes into the spec's own plain-language client stages
(`prep → ready → filed → awaiting → refunded`, plus *needs attention*) and
making the "no codes, no actions, no fees" constraint a property of the wire
shape rather than of careful template-writing. Effort M 3–5d. Priority P1.
Milestone M5. Depends on: WO-59 (G2.7 — `status.derive_stage`, `AUTO_CODES`,
`MANUAL_CODES`, the codes this order maps FROM), WO-76 (`claim.list_claims`,
the one claims-listing query, and its `year` filter added by WO-81), WO-81 (the
per-claim evaluation pattern: `derive_stage` + `freeze.preview_vat_base` walked
per claim, and the route/schema shape), WO-90/WO-92 (the SPA conventions:
`decimalMoney`, `hasVatPerm`, `RefusalNotice`, the nav `perm`/`module` gating,
the `page.route`-mocked Playwright harness, the source-scan + seeded-violation
pattern).**

### Objective and business value

The gap, with verified evidence. `grep -rn "claim-status\|client_status"
backend/app frontend/src` returns **nothing**: the surface does not exist in any
layer. What exists is everything it must be built from —
`app/services/transport/status.py` derives the four AUTO codes
(`AUTO_CODES = ("1A","1B","1C","1E")`) and stamps the eleven manual ones
(`MANUAL_CODES = ("2","2A","2B","3","3A","3B","3C","3D","4","4A","5")`), and
`status.list_status_codes` already says in as many words that this codebase has
**no label mapping at all**: *"Fleet Fuel's `STATUS_LABELS` was never harvested
as data (master-context §10 …), so the two code tuples ARE the vocabulary a
caller can offer."* Today the only transport surfaces a `READ_ONLY` session can
reach are the operator ones: `/recovery` renders the six readiness-state slugs
(`ready · deadline · missing · below · submitted · paid`) and `/vat-claims`
renders `status_code` verbatim — `frontend/src/lib/transportClaims.ts` maps
`"1A"` to *"Missing documents"* for an operator. A client logging in today
either sees nothing about their own claims, or sees the operator's internal
vocabulary.

Who stops losing money. `BA_fleet_fuel.md` §2.4 records this surface as a
**deliberate competitive differentiator** — *"incumbents rarely self-serve a
live client status view"* — and §1.2 defines the `user` role as *"**The
client.** Read-only base. Sees only 'open' read-only pages: `/value`, `/fees`,
`/claim-status`, home. **No codes, no fees, no actions.**"* A VAT refund takes
months to arrive and the client's question the whole time is one sentence:
*"where are my claims?"*. Every time that question is asked by email, an
operator answers it by hand. The commercial cost of the missing screen is
churn during the wait; the commercial risk of building it CARELESSLY is worse
and is what this order is actually about — a client-facing screen that leaks
`3B` ("Rejection"), or `fee_eur`, or a "Submit" button, does three things at
once: it exposes an internal workflow the client cannot act on, it puts the
service fee in front of the client on a page about their refund, and it offers
a control that the server would refuse. R39's acceptance line is a single
absence — *"A client-role session cannot see a status code or a fee
anywhere."*

### Scope

**In scope:**

- `backend/app/services/transport/client_status.py` (**new**) — the read-only
  service `client_claim_status(db, org_id, year, *, today=None)`:
  - **The stage vocabulary, verbatim from the spec (§9).**
    `BA_fleet_fuel.md` §3.D: *"Client-facing translation (`CLIENT_STAGES`,
    `/claim-status`): internal 1A..5 codes map to plain-language stages **prep →
    ready → filed → awaiting → refunded** (plus "needs attention")."* Restated
    identically at §4.5 (*"VAT claim (client-facing): `prep → ready → filed →
    awaiting → refunded` (+ needs attention)"*) and in R39. `CLIENT_STAGES`
    carries exactly those six slugs in that order. No seventh stage, and no
    friendlier synonym for any of them.
  - **`STAGE_BY_CODE`** — a pure, TOTAL mapping over
    `status.AUTO_CODES + status.MANUAL_CODES` (fifteen codes, every one the
    spec's §3.D table defines), plus `ENGINE_STAGE`, the fallback for a claim
    whose engine status is filed but whose `status_code` column is NULL or
    unrecognised. The mapping is an INTERPRETATION where the spec names the
    stages but not the per-code assignment — recorded as such in the module
    docstring, the way `recovery.py` records its four bucket interpretations.
  - **The sources, combined, never forked.** The claim set comes from
    `claim.list_claims(db, org_id, year=year)` — the ONE claims-listing query,
    whose `year` filter WO-81 added to it *"because R38's own acceptance line
    forbids forking the claims query: one claim-listing query, one filter,
    every consumer."* A draft's stage comes from `status.derive_stage` (D3, the
    canonical AUTO-code evaluator) and its figure from
    `freeze.preview_vat_base` (the same preview `lock.submit_claim` gates on);
    a filed claim's figure is its own FROZEN `vat_eur` (R13/C10). This is
    exactly the "in-preparation claims combined with the filed/awaiting/
    refunded ones" the spec describes, expressed in this codebase's canonical
    functions. This module holds **no `select()` of its own** over
    `vat_refund_claims` — asserted structurally.
  - Module-gated FIRST and fail-CLOSED (ADR-P3 rule 3), `year`-validated
    through `recovery.validate_year` (the same 422 `invalid_year`, imported not
    re-written), org-scoped, and read-only: no `db.add`, no attribute
    assignment, no `audit.record`, no commit.
  - Never raises on missing data: a year with no claims returns the six empty
    stages and zeroes. A draft whose lines span currencies (`preview_vat_base`
    → `claim_currency_mismatch`, §4.14) yields `vat_eur = None` for that claim
    — *"we cannot state a figure for this one yet"* — rather than a EUR-labelled
    foreign amount or a false `0.00`.
- `backend/app/schemas/transport_client_status.py` (**new**) —
  `ClientClaimStatusOut`, `ClientStageOut`, `ClientClaimOut`. Decimal-typed, so
  every euro crosses the wire as an exact STRING (§4.9).
- `backend/app/api/routes/transport/claim_status.py` (**new**) — one thin
  controller, `GET /api/v1/transport/claim-status?year=`, registered in
  `app/api/routes/transport/__init__.py`'s aggregating router (the package
  docstring requires it: a slice that registers itself in `app/api/router.py`
  instead escapes `tests/test_authz_coverage.py`).
- **The client-surface constraint, made structural** (see the dedicated
  section below).
- `frontend/src/pages/ClaimStatus.tsx` (**new**) — `/claim-status`, plus
  `frontend/src/lib/transportClaimStatus.ts` (**new**, pure: a stage→tone map
  and the empty-state copy — and NO stage label, which comes off the wire),
  additive wire types in `lib/types.ts`, one nav destination, one lazy route in
  `App.tsx`, `frontend/e2e/claim-status.spec.ts` (**new**) added to
  `package.json`'s `test:e2e` list.
- Boards, last: `TODO.md` (the WO-93 row, the M5 cell, the suite line),
  `docs/transport/rules.md` (a **new R39 row**, and R17 gaining its
  client-facing consumer), `README.md` (SPA pages 54 → 55 and the collected-test
  count, moved in the SAME commit as the page / the tests).

**Out of scope (the anti-scope-creep clause):**

- **A new permission member or a new role.** The surface uses the existing
  permission that fits; if none did, the order would STOP and report rather
  than invent a role model (§9). See the permission section.
- **Any change to `status.py`, `claim.py`, `recovery.py`, `freeze.py` or the
  claim routes.** This order is purely additive (§4.20). It maps FROM the
  existing vocabulary; it does not adjust it. In particular it does NOT fix
  `lock.withdraw_claim` leaving a stale `status_code` behind (D7 says the
  withdraw *"also NULLs `status_code`"* and this codebase's `withdraw_claim`
  does not) — that is a G2.7 defect, reported below, and this order is
  structurally immune to it because it dispatches on the engine status first.
- **`/value` and `/fees`** — the other two open client pages named in
  `BA_fleet_fuel.md` §1.2. Boards G4.7 and R40; neither is this order.
- **The engine-state transitions that would stamp `status_code`** — 2A/2B/3/3A
  and the fee ladder are G2.9, decision-gated in `docs/DECISIONS-NEEDED.md` §10.
  This order maps codes the model can already carry; it creates none of them.
- **Per-claim documents, lines, checklists or deadlines.** The client sees a
  stage and a figure. `action_deadline` is deliberately not exposed — the spec
  does not name it on this surface, and its field name is itself in the
  forbidden vocabulary family.
- **A claim-detail drill-down, any link into `/vat-claims`, any button.**
  A read-only view means no control at all, not a disabled one.
- Reworking `RecoveryDashboard.tsx` / `VatClaims.tsx` or any existing lib entry.

### THE GOVERNING CONSTRAINT — R39's absence, made structural

R39's acceptance line is an ABSENCE: *"A client-role session cannot see a status
code or a fee anywhere."* An absence is trivially true of the file you just
wrote and worthless as a guarantee about the next edit. Three families are
therefore forbidden from the wire shape AND the page, and the ban is enforced by
scans that ship with a seeded-violation self-test (WO-87/WO-90/WO-92 precedent —
`test_wo87_the_scan_would_catch_a_claim_shaped_field_name`):

1. **Internal status codes.** No string VALUE anywhere in the serialized
   response may equal any member of `status.AUTO_CODES + status.MANUAL_CODES`
   (exact equality over every leaf string, recursively — a substring test would
   be meaningless when `"2"` is a code and `"2026-Q2"` is a period). No field
   name may contain `code`. The scan IMPORTS the two tuples from `status.py`, so
   a code added there is covered without touching this test.
2. **Fee figures.** No field name may contain `fee`, `commission`, `payout` or
   `billed`; and `fee_pct`/`fee_min`/`fee_eur` are asserted never read by the
   service (an AST attribute scan, the shape
   `test_wo81_the_service_never_sums_a_local_or_paid_amount` already uses for
   `vat_local`/`paid_amount`).
3. **Action verbs.** No field name, and no rendered string the SERVICE owns, may
   contain `submit`, `withdraw`, `approve`, `freeze`, `lock`, `waive`,
   `override`, `package`, `send` or `confirm`. The stage slug `filed` and the
   stage slug `refunded` are the spec's own vocabulary and are NOT verbs in this
   list — the list names the app's action vocabulary, not the English language.

Two positive assertions accompany the three absences, because a page can satisfy
every ban by rendering nothing:

4. **The stage vocabulary is the spec's, verbatim** — `CLIENT_STAGES ==
   ("prep","ready","filed","awaiting","refunded","needs_attention")`, asserted
   literally, and the six stages are ALWAYS present in the response in that
   order (an empty stage that vanished would make "you have nothing in
   preparation" and "the page forgot" identical).
5. **The plain-language labels live on the SERVER and cross the wire.** The SPA
   holds no stage label of its own (asserted: the label text appears in no file
   under `frontend/src/`), exactly as `lib/transportSavings.ts` holds no framing
   string. A re-wording in the SPA therefore cannot invent friendlier words, and
   the label the client reads is scanned by the backend's own vocabulary tests.

### Permission — the existing one that fits

Verified against `app/core/authz.py`. The spec's `user` ("The client. Read-only
base") is this codebase's `Role.READ_ONLY` (stored `user_free`;
`authz._LEGACY_ROLE` maps `user_free → READ_ONLY`, and WO-92's acceptance list
uses exactly that equivalence). `ROLE_PERMISSIONS[Role.READ_ONLY]` holds
`VAT_READ` **and** `TRANSPORT_READ`; `Role.APPROVER` and `Role.EMPLOYEE` hold
neither. So a client-role session can already reach this surface under either
permission, and **no permission member is added** (§10).

The choice is **`VAT_READ`**, and it is a semantic one. WO-79 recorded the
reservation that has governed every transport route since: *"`TRANSPORT_READ`
stays reserved for the derived analytics/excise slices"*, and WO-81 claimed it
for `/recovery-dashboard` on the stated ground that *"it returns no claim row,
no line, no object id, only aggregates"*. This surface returns **claim rows** —
one per claim, with the claimant entity, the refund country and the period. It
is the claims themselves, translated; it is not portfolio analytics. Putting it
on `VAT_READ` is what keeps the two permissions from becoming synonyms by drift.
The nav entry mirrors `vat.read`, exactly as `/vat-claims` and `/vat-customers`
already do. Effective access is unchanged either way today, and
`test_wo79_vat_read_and_transport_read_have_identical_role_coverage` still
pins that.

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/client_status.py` | **new** — the service, `CLIENT_STAGES`, `STAGE_BY_CODE`, `ENGINE_STAGE`, the labels |
| `backend/app/schemas/transport_client_status.py` | **new** — three Decimal-typed response models |
| `backend/app/api/routes/transport/claim_status.py` | **new** — one GET, router-level `VAT_READ` |
| `backend/app/api/routes/transport/__init__.py` | register the new router in the aggregator |
| `backend/tests/transport/test_wo93_client_status.py` | **new** — the mapping + service tests |
| `backend/tests/transport/test_wo93_client_surface.py` | **new** — the three absences + two presences, with self-tests |
| `backend/tests/transport/test_wo93_claim_status_routes.py` | **new** — the HTTP half |
| `frontend/src/pages/ClaimStatus.tsx` | **new** — the page |
| `frontend/src/lib/transportClaimStatus.ts` | **new** — pure tone map + empty-state copy |
| `frontend/src/lib/types.ts` | 3 additive wire interfaces |
| `frontend/src/lib/nav.ts` | one Transport-group destination, `perm: "vat.read"` |
| `frontend/src/App.tsx` | one lazy route `/claim-status` |
| `frontend/e2e/claim-status.spec.ts` | **new** — the spec matrix |
| `frontend/package.json` | the spec joins `test:e2e` |
| `README.md` | scale line: SPA pages 54 → 55, collected tests |
| `TODO.md` | WO-93 row, M5 cell, suite line |
| `docs/transport/rules.md` | a new R39 row; R17 gains its client-facing consumer |

### Implementation guidance

1. **Read the codes off `status.py`, never off this document.**
   `AUTO_CODES`/`MANUAL_CODES` are imported by both the service and its tests;
   a code added there must fail a test rather than silently fall through.
2. **The mapping, and the interpretation in it.** The spec names the six stages
   and the fifteen codes but never assigns one to the other. Recorded reading:

   | Code (§3.D label) | Stage | Why |
   |---|---|---|
   | 1A Missing documents | `needs_attention` | Blocked on something a human must supply |
   | 1B Documents received — period not ended | `prep` | Nothing to fix; it becomes fileable when the period closes |
   | 1C Can be submitted (with a caveat) | `prep` | A caveat is not "ready"; the client is not shown WHICH caveat |
   | 1E Ready to submit | `ready` | The stage the spec names for exactly this |
   | 2 / 2A Submitted | `filed` | With the refunding member state |
   | 2B Document request received | `needs_attention` | The member state has asked for something (D2: carries `action_deadline`) |
   | 3 Decision received | `awaiting` | Decided, money not yet in — the distinction the arrow order implies |
   | 3A Money received | `refunded` | |
   | 3B Rejection / 3C Confiscation | `needs_attention` | An adverse outcome is neither awaiting nor refunded |
   | 3D Under appeal | `needs_attention` | Off the happy path and carrying its own deadline |
   | 4 / 4A / 5 | `refunded` | The money has arrived (all three are engine `paid`); these are FEE-ladder codes and the fee is exactly what the client must not be shown |

   Dispatch order in the service: **engine status first**, then the code. A
   `draft` claim gets `derive_stage`; a `submitted`/`approved`/`paid` claim gets
   its `status_code` if the code is known, else `ENGINE_STAGE`; anything else
   (`withdrawn`, `rejected`, or a value this module has not met) is NOT SHOWN
   and is counted in `not_shown_claims`, never silently dropped and never given
   an invented seventh stage. Status-first is also what makes the order immune
   to `withdraw_claim`'s stale `status_code`.
3. **Money: NET EUR, and `None` is a real answer.** A draft has no frozen
   figure by construction (WO-49), so its euro comes from
   `freeze.preview_vat_base`; a filed claim's comes from its own frozen column.
   Either can be unavailable — a currency-spanning draft (§4.14) or a filed
   claim whose column is NULL — and `vat_eur: Decimal | None` reports that as
   `null`, which the page renders as `—`. A per-stage total sums only the
   figures that exist. `0.00` would be a wrong number; refusing the whole year
   would hide the rest of the portfolio.
4. **Read-only, fail-closed, never raises.** Module entitlement inside the
   service before any query (ADR-P3 rule 3); `invalid_year` refuses 422 for the
   reason `recovery.validate_year` already documents (an empty year for a typo
   reads as "you have nothing"). Missing data returns empty, not 404.
5. **Entity names are resolved through `issuer.get_by_id`**, once per DISTINCT
   entity id (ADR-P3 rule 2 — a service call, never a cross-domain join), the
   same way `excise.py` puts `entity_name` on its rows. The client should read
   their own legal entity's name, not a UUID. No claim `id` is exposed at all:
   there is nothing to link to, and the response's natural key is the R1 grain
   (entity × country × period).
6. **The page computes nothing** (§4.9/§4.10). Every euro arrives as a decimal
   string and renders through `decimalMoney`; no `Number(`, `parseFloat`,
   `toFixed` or `Math.` appears in the new modules, grep-proven in the spec.
7. **Loading / empty / error on every panel** via `QueryState`/`EmptyState`/
   `Skeleton`, module gating via `useModules` + `ModuleInactive`, refusals via
   `RefusalNotice`. `invalid_year` and `module_not_enabled` are ALREADY mapped
   in `lib/transportClaims.ts`; no refusal entry is added or edited.
8. **Nav-label locator hazard.** WO-92's nav label collided with a bare
   `getByText` in a pre-existing spec. Before finishing: grep every existing
   spec for bare `getByText(` on short strings the new nav label ("Claim
   status") or the new page could also match, and SCOPE any collision to its own
   container — never delete or weaken an assertion (WO-80 decision 2). Re-run
   the FULL `npm run test:e2e` list.

### Invariants this order must preserve

- **§4.1/§4.4 — tenancy.** Every read is org-scoped through
  `claim.list_claims`, which filters on `org_id`; `issuer.get_by_id` is
  org-scoped too. `vat_refund_claims` is already a probed (non-EXEMPT) row in
  `tests/test_tenancy_parity.py` (line 1482), so no exemption is converted;
  isolation over this new route is proven with deliberately OVERLAPPING data.
- **§4.6/§4.7 — structural authorization.** Router-level `VAT_READ` declared
  with `require_perm`, no per-route override (there is one route and it is a
  read), and the declaration is pinned by a test that reads
  `authz.PERMISSIONS_ATTR` back off the router.
- **§4.9 — Decimal on the wire.** Every euro is `Decimal | None` in the schema,
  so pydantic v2 emits a JSON string; asserted with a
  `99999999999999.99`-class fixture that a float round-trip would destroy.
- **§4.10 — the server computes every total.** Per-stage totals are computed in
  the service through `money.q2`; the SPA re-derives nothing.
- **§4.14 — no cross-currency sum.** `vat_local`, `currency` and `paid_amount`
  are never read (AST-asserted); a currency-spanning draft yields `null`, never
  a EUR-labelled foreign amount.
- **§4.19 — read-only.** No write of any kind; reading the portal twice leaves
  every claim column byte-identical (the `test_wo81_reading_the_dashboard_
  mutates_nothing` shape, reused).
- **§4.20 — additive.** Three new backend modules, one router registration, one
  page, one helper, additive types, one nav row, one route, one spec. Nothing
  existing is edited except the aggregator, `types.ts`, `nav.ts`, `App.tsx`,
  `package.json` and the boards.
- **§9/§10 — the spec's vocabulary, nothing invented.** Six stages, named
  verbatim; no seventh; no label the spec does not license; no permission
  member; no lifecycle.
- **R39 / G4.4** — the three absences and the two presences above.
- **R17 / G2.7** — the AUTO codes stay system-derived and this surface only
  READS them; it stamps nothing and offers no code-setting control.

### Database / migration impact

**None.** No table, no column, no migration, no RLS policy, no tenant-model
registry change, no permission member.

### Testing requirements

`backend/tests/transport/test_wo93_client_status.py` — the service, each stage
produced by a claim GENUINELY CONSTRUCTED in that state (the
`test_wo81_recovery.py` seeding pattern: real vendors, real invoices, real
documents, `claim_lines.build_claim_lines`, a controlled `today`), never by
stubbing the mapper:

- `test_wo93_the_six_client_stages_are_the_spec_vocabulary_in_order`
- `test_wo93_every_internal_code_maps_to_exactly_one_client_stage` (over
  `AUTO_CODES + MANUAL_CODES`, imported)
- `test_wo93_a_draft_missing_documents_reads_needs_attention` (1A, built by
  leaving the invoice unregistered)
- `test_wo93_a_draft_whose_period_has_not_ended_reads_prep` (1B, via `today`)
- `test_wo93_a_below_minimum_draft_reads_prep` (1C, €2.10 of VAT)
- `test_wo93_a_clean_fileable_draft_reads_ready` (1E)
- `test_wo93_a_submitted_claim_reads_filed` (through `lock.submit_claim`, which
  is what actually stamps `status_code = "2"`)
- `test_wo93_each_manual_code_reads_its_stage` (2A/2B/3/3A/3B/3C/3D/4/4A/5 via
  `status.set_status_code` on a genuinely submitted claim)
- `test_wo93_a_filed_claim_with_no_status_code_falls_back_to_the_engine_state`
- `test_wo93_a_withdrawn_claim_is_not_shown_and_its_stale_code_is_never_read`
- `test_wo93_every_claim_is_accounted_for_exactly_once` (Σ stages +
  `not_shown_claims` == `total_claims`)
- `test_wo93_a_year_with_no_claims_renders_the_empty_state_not_an_error`
- `test_wo93_a_cross_currency_draft_reports_a_null_figure_not_zero`
- `test_wo93_stage_totals_match_hand_computed_decimals`
- `test_wo93_the_service_forks_no_claims_query` (AST: no `select(` /
  `VatRefundClaim` query in the module — the claim set comes from
  `claim.list_claims`)
- `test_wo93_reading_the_portal_mutates_nothing`
- `test_wo93_an_un_entitled_org_is_refused_module_not_enabled` (403)
- `test_wo93_an_invalid_year_is_refused` (422 `invalid_year`)

`backend/tests/transport/test_wo93_client_surface.py` — R39's absences, each
with the self-test that proves the scan can fail:

- `test_wo93_no_internal_status_code_reaches_the_wire` (every leaf string of a
  real response, over a portfolio carrying EVERY code)
- `test_wo93_the_code_scan_detects_a_seeded_code_value`
- `test_wo93_no_field_name_carries_code_fee_or_action_vocabulary` (service
  dataclasses + response schemas)
- `test_wo93_the_field_name_scan_detects_a_seeded_violation`
- `test_wo93_the_service_never_reads_a_fee_column` (AST over `fee_pct`,
  `fee_min`, `fee_eur`)
- `test_wo93_no_service_owned_string_carries_action_vocabulary` (the labels and
  descriptions the wire carries)
- `test_wo93_the_route_path_and_query_carry_no_internal_vocabulary`
- `test_wo93_the_stage_labels_are_server_owned` (present, non-empty, one per
  stage)

`backend/tests/transport/test_wo93_claim_status_routes.py` — the HTTP half
(the `test_wo81_recovery.py` fixture strategy):

- `test_wo93_route_returns_the_portal`
- `test_wo93_amounts_cross_the_wire_as_exact_decimal_strings` (including the
  `null` case)
- `test_wo93_an_empty_year_is_200_with_the_six_empty_stages`
- `test_wo93_an_invalid_year_is_422_invalid_year`
- `test_wo93_module_disabled_refuses_403_module_not_enabled`
- `test_wo93_employee_is_denied_and_a_read_only_client_is_granted` (the denied
  role is EMPLOYEE, which holds no `VAT_READ`; the granted one is the READ_ONLY
  client this surface exists for)
- `test_wo93_the_route_is_gated_on_vat_read` (structural, off
  `authz.PERMISSIONS_ATTR`)
- `test_wo93_one_orgs_portal_shows_none_of_another_orgs_claims` (overlapping
  data: same entity name, same country, same period, same amounts). There is no
  by-id fetch on this surface — the route takes no object id — so the opaque-404
  case has no form here; the isolation proof is the zero-rows probe, and
  `vat_refund_claims`' own opaque-404 path is already proven by
  `test_wo76_claim_routes.py`.

`frontend/e2e/claim-status.spec.ts` — the WO-92 `page.route`-mocked harness,
synthetic fixtures only:

- `stages: each stage renders its server-owned label and count`
- `stages: all six stages render even when empty`
- `claims: each row renders its entity, country, period, stage and amount`
- `claims: a claim with no stateable figure renders a dash, never €0.00`
- `empty: a year with no claims renders the empty state, not an error`
- `loading: a loading state renders before the API resolves`
- `error: a 500 renders the error state`
- `refusal: invalid_year renders its sentence, not the slug`
- `module: transport off renders the module notice and no nav entry`
- `perm: vat.read gates the nav entry (granted / denied)`
- `perm: a server 403 renders through the refusal path`
- `constraint: no internal status code appears anywhere on the page`
- `constraint: no fee figure or fee word appears anywhere on the page`
- `constraint: no action control is rendered`
- `constraint: the page-source scan detects a seeded violation`
- `constraint: the SPA holds no stage label of its own`
- `money: every euro renders from the wire string with no float round-trip`

### Acceptance criteria (verifiable checklist)

- [ ] `GET /api/v1/transport/claim-status?year=2026` returns 200 with six
      stages named `prep, ready, filed, awaiting, refunded, needs_attention` in
      that order, and every euro as a JSON string.
- [ ] A claim built through `lock.submit_claim` reads stage `filed`; the same
      claim after `set_status_code("3A")` reads `refunded`; after `("3B")`,
      `needs_attention`.
- [ ] A 1A draft reads `needs_attention`, a 1B draft `prep`, a 1C draft `prep`,
      a 1E draft `ready` — each built through the real services, none stubbed.
- [ ] For every code in `status.AUTO_CODES + status.MANUAL_CODES`, no response
      leaf string EQUALS that code, and the seeded-violation self-test proves
      the scan can fail.
- [ ] No field name in `client_status.py`'s dataclasses or
      `transport_client_status.py`'s models contains `code`, `fee`, `commission`,
      `payout`, `billed`, `submit`, `withdraw`, `approve`, `freeze`, `lock`,
      `waive`, `override`, `package`, `send` or `confirm`.
- [ ] `grep -nE "fee_pct|fee_min|fee_eur|vat_local|paid_amount"
      backend/app/services/transport/client_status.py` returns nothing.
- [ ] `year=2019` with claims only in 2026 returns 200 with `total_claims: 0`
      and six zeroed stages; `year=99999` returns 422 `invalid_year`.
- [ ] A `user` (EMPLOYEE) session gets 403; a `user_free` (READ_ONLY) session
      gets 200 — the client this surface is for.
- [ ] `npm run build` clean; `npm run test:e2e` green at 248 + the new specs
      with zero pre-existing specs weakened (any collision is SCOPED).
- [ ] `python -m pytest -q` green with the baseline explained
      (2273 passed / 10 skipped + the new tests), `README.md` moved in the same
      commit as the page/tests, `python scripts/pii_scan.py --tree` clean.

### Rollback strategy

Code revert only — three new backend modules, one router-registration line, one
page, one helper, additive types, one nav row, one App route, one spec file. No
migration, no data, no permission change, and the surface performs no write of
any kind, so nothing it did can need undoing. Narrower mitigation: remove the
one entry from `frontend/src/lib/nav.ts` and the destination disappears from the
IA while the route stays reachable by URL; remove the
`router.include_router(claim_status.router)` line and the endpoint is gone while
every other transport route is untouched.

### Documentation to update

- `docs/transport/rules.md` — a new **R39** row (module, test, legal/spec
  source), and R17's row gains its client-facing consumer.
- `TODO.md` — the WO-93 row, the M5 cell, the suite line.
- `README.md` — the scale line (SPA pages 54 → 55, collected tests).
- No ADR is contradicted. ADR-0024's structural authorization is mirrored, not
  re-decided; the frontend permission mirror stays cosmetic (master-context §6).

### Recorded defect, not fixed here

`app/services/transport/lock.withdraw_claim` sets `status = "withdrawn"` but
leaves `status_code` populated, while `BA_fleet_fuel.md` §3.D **D7** says
withdrawal *"also NULLs `status_code`"*. That is a G2.7 gap, out of scope here
(§4.20). This order is immune to it by construction — the stage dispatch reads
the engine status first and a withdrawn claim never reaches the code map — and
`test_wo93_a_withdrawn_claim_is_not_shown_and_its_stale_code_is_never_read`
pins that immunity so a future fix cannot silently change this surface.

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo93_client_status.py \
                 tests/transport/test_wo93_client_surface.py \
                 tests/transport/test_wo93_claim_status_routes.py -q
python -m pytest -q                       # full baseline

# the constraint proof (must print nothing)
grep -nE "fee_pct|fee_min|fee_eur|vat_local|paid_amount" \
  app/services/transport/client_status.py

# every stage the page renders must exist on the wire schema
python -c "
from app.schemas.transport_client_status import ClientClaimStatusOut, ClientStageOut, ClientClaimOut
for m in (ClientClaimStatusOut, ClientStageOut, ClientClaimOut):
    print(m.__name__, sorted(m.model_fields))
"
cd ../frontend && npm run build
npx playwright test e2e/claim-status.spec.ts
npm run test:e2e                          # 248 -> 248 + N, zero pre-existing specs weakened
grep -nE "parseFloat|Number\(|toFixed|Math\." src/pages/ClaimStatus.tsx src/lib/transportClaimStatus.ts
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```
