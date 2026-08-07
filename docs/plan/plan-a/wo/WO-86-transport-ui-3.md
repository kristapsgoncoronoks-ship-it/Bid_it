# WO-86 — transport UI slice 3: the recovery intelligence workspace

> The third and last UI slice of the transport batch. WO-78 shipped the CLAIM
> workspace, WO-79 its submit pick-list, WO-80 the ADMIN/CONFIG screens — and
> WO-80's own closing note recorded exactly what was left: *"what remains of
> the UI batch is the ANALYTICS surface, which has no backing service yet."*
> Since then WO-81 (`recovery.py`), WO-82 (`overcharges.py` + `contract_audit`),
> WO-83 (the two send-ready artifacts) and WO-84 (`rebates.py`) all landed. The
> service half exists; the analytics surface is now the only thing missing.

**WORK ORDER 86 — transport UI slice 3: the recovery intelligence workspace
(`frontend/src/pages/RecoveryDashboard.tsx` over `GET /transport/recovery-dashboard`,
`frontend/src/pages/Overcharges.tsx` over the WO-82/WO-83 overcharge routes, and
`frontend/src/pages/Rebates.tsx` over the WO-84 rebate routes), permission-mirrored
exactly as the routes declare (`TRANSPORT_READ` reads / `VAT_WRITE` mutations), with
every refusal code mapped to an actionable human sentence and the two honest-nulls
— `median_days_to_refund` and `currency_mismatch_claims` — presented as facts rather
than as zeroes. Effort M 3–5d. Priority P1. Milestone M5. Depends on: WO-81 (the
dashboard route + schema), WO-82 (the contract-term/detection/claim-back routes),
WO-83 (the packet + letter artifacts and their refusals), WO-84 (the rebate registry
route + the §3.H close boundary), WO-78/WO-80 (the SPA conventions: `lib/types.ts`,
`decimalMoney`, `hasVatPerm`, `claimRefusal`, `RefusalNotice`, the nav `perm` flag,
the `page.route`-mocked Playwright harness).**

### Objective and business value

The gap, with verified evidence. Four backend orders shipped nine client-reachable
route surfaces that no pixel consumes:
`grep -rn "recovery-dashboard\|contract-terms\|overcharges\|rebates" frontend/src`
returns **nothing**. The routes are live and structurally gated —
`backend/app/api/routes/transport/recovery.py` (1 route),
`overcharges.py` (9 routes: 3 contract-term verbs, the detection read, the
claim-back list/total/detail/open/advance, and the two WO-83 artifact
downloads) and `rebates.py` (2 routes) — and every one of them is reachable
today only with `curl`. WO-81's own objective paragraph states the point of the
dashboard in one line: *"there is no surface anywhere that answers 'how much
money can we still recover this year, and what is stopping each euro of it?'"*
That is still true, because the answer has no screen.

Who stops losing money: the FINANCE_MANAGER who files, and the same one who
demands a supplier credit. Three concrete failures the missing screens cause.
(a) **The deadline.** `docs/plan/shared/specs/BA_fleet_fuel.md` A4 sets the
north-star KPI at *"deadline misses = 0"* and the harvested risk register scores
the missed 30-September Art. 15 filing deadline at the maximum (CJEU C-294/11
*Elsacom* makes it a permanent forfeiture). `deadline_risk_claims` is computed
and served today, and nobody can see it. (b) **The supplier credit.** WO-83
renders a formal PDF demand letter and an Excel evidence packet from a frozen
claim-back figure; with no download button the letter is never sent, so
`recovered_eur` — §2.4's booked-cash north star — stays zero by construction.
(c) **The wrong demand.** WO-84's whole objective paragraph is that a rebated
supplier gets a letter demanding money it has **already paid**; the fix is a
recorded rebate document plus a close, and `POST /transport/rebates` has no
form. The `overcharge_evidence_drift` 409 exists precisely for the window
between those two events, and today it would reach an operator as an
untranslated slug.

### Scope

**In scope:**
- `frontend/src/pages/RecoveryDashboard.tsx` (**new**) — `/recovery`, one read
  over `GET /transport/recovery-dashboard?year=`:
  - the six readiness buckets in the service's own order
    (`ready · deadline · missing · below · submitted · paid`), each with its
    claim count and `vat_eur`, all six always rendered including the empty ones;
  - the north-star euros `recovered_eur` / `awaiting_eur` / `claimable_eur`, and
    `overcharges_eur` rendered as the SECOND, separate cash stream (the schema
    comment is explicit that folding it into the VAT reconciliation would make
    that identity false);
  - `deadline_risk_claims` kept **visually separate** from the buckets — WO-81
    interpretation 3 deliberately did not make it a seventh bucket ("the bucket
    says WHAT TO DO, the count says HOW URGENT");
  - the `excluded` block (withdrawn/rejected) with its reason and count, so
    `Σ buckets + Σ excluded == total_claims` is legible on screen;
  - **`median_days_to_refund === null` renders as "not yet measurable" with its
    `days_to_refund_sample`**, never as 0 — the service docstring's own reason
    ("never 0, which would claim refunds arrive the same day they are filed");
  - **`currency_mismatch_claims > 0` renders a named advisory notice** stating
    that those claims contribute €0.00 to every euro above, so a total that
    looks short is explained rather than mysterious (§4.14).
- `frontend/src/pages/Overcharges.tsx` (**new**) — `/overcharges`, three tabs
  over the WO-82/WO-83 routes:
  1. **Detection** — `GET /transport/overcharges/audit?period=&supplier=`: the
     breach lines with `flag`, `agreed_eur_l`, `actual_eur_l`, `gap_eur_l`, `qty`
     and `recover_eur`, plus `lines_audited` / `lines_without_terms` /
     `lines_skipped_zero_qty`, the response's own `price_basis` and
     `legal_framing` strings rendered verbatim, and `source_warnings` as an
     advisory notice that links to the rebates screen. "Open a claim-back"
     (`POST /transport/overcharges`, VAT_WRITE) sits here.
  2. **Claim-backs** — `GET /transport/overcharges` worklist +
     `GET /transport/overcharges/total`, the `detected → packaged → claimed →
     {recovered, rejected, written_off}` ladder driven by
     `POST /transport/overcharges/{id}/advance` (VAT_WRITE), offering only the
     edges `TRANSITIONS` allows from the current state, with the `recovered`
     amount input appearing for that target only. Both artifact downloads live
     on the row.
  3. **Contract terms** — `GET/PUT/DELETE /transport/contract-terms`, the two
     harvested term types (`expected_discount_eur_l`, `max_net_eur_l`) and the
     `active` flag, with the copy recording that a LAPSED contract is
     deactivated rather than deleted (the route docstring's own warning).
- `frontend/src/pages/Rebates.tsx` (**new**) — `/rebates`:
  `GET/POST /transport/rebates`, the recorded documents with their FX provenance
  (`fx_source`, `fx_ecb_rate`, `fx_ecb_date`) and the server-resolved
  `amount_eur`; the form takes `amount_local` + `currency` and **never** an EUR
  figure (`RebateIn` has no `amount_eur` — the server resolves it); and two
  statements of boundary in the copy, both asserted as text by the suite:
  - **§3.H** — recording a rebate changes no transaction figure until the close
    runs. **No preview is faked**: WO-84 shipped no preview endpoint, so the page
    says the effect applies at the next close and offers no "merge now" verb
    (the route module docstring: *"There is therefore no 'merge now' verb here,
    by design"*).
  - **§4.15** — a non-EUR rebate with no ECB rate for its own document date
    refuses `fx_rate_unavailable` and **records nothing**.
- `frontend/src/lib/transportClaims.ts` — the refusal map gains every code these
  three surfaces can raise, read off the services (verified, not guessed):
  `invalid_year`, `invalid_product_group`, `term_has_no_figure`,
  `invalid_term_rate`, `no_overcharge_detected`, `overcharge_claim_not_found`,
  `invalid_overcharge_status`, `overcharge_transition_invalid`,
  `recovered_amount_required`, `recovered_amount_invalid`,
  `recovered_amount_not_applicable`, `overcharge_evidence_drift`,
  `overcharge_claim_closed`, `issuer_profile_incomplete`,
  `pdf_renderer_unavailable`, `rebate_source_required`, `rebate_amount_invalid`,
  `fx_rate_unavailable`. Additive only — every WO-78/WO-80 entry is untouched.
  `overcharge_evidence_drift` gets the longest sentence in the file: it must say
  what actually happened (a rebate merged after the claim-back froze its figure)
  and what to do (re-run the audit; the frozen demand is deliberately not
  silently re-snapshotted), never the slug.
- `frontend/src/lib/transportRecovery.ts` (**new**) — PURE helpers, no React, no
  network: `BUCKET_COPY` (what each of the six readiness states means and what to
  do about it), `EXCLUDED_COPY`, `OVERCHARGE_TRANSITIONS` (a verbatim mirror of
  `app/services/transport/overcharge.py::TRANSITIONS`) plus
  `overchargeActions(status)`, and `OVERCHARGE_STATUS_TONE`. Mirrors only; the
  server remains the control, exactly like `transportAdmin.ts::lifecycleActions`.
- `frontend/src/lib/types.ts` — additive wire types, field-for-field from
  `app/schemas/transport_recovery.py`, `transport_overcharge.py` and
  `transport_rebate.py`: `RecoveryBucket`, `RecoveryExcluded`, `RecoveryDashboard`,
  `ContractTerm`, `OverchargeBreach`, `ContractAudit`, `OverchargeClaim`,
  `OverchargeTotal`, `OffInvoiceRebate`.
- `frontend/src/lib/roles.ts` — the permission mirror gains `"transport.read"`,
  the permission these three route modules actually declare
  (`app/core/authz.py::Permission.TRANSPORT_READ`), with the same six roles the
  matrix grants it and the two it denies. Additive: every existing entry and the
  `hasVatPerm` signature are unchanged.
- `frontend/src/lib/nav.ts` — three destinations in the existing Transport group,
  gated `module: "transport"` + `perm: "transport.read"`.
- `frontend/src/App.tsx` — `/recovery`, `/overcharges`, `/rebates` (lazy).
- `frontend/e2e/recovery.spec.ts` (**new**) — the matrix below, in the
  `page.route`-mocked live-app pattern of `vat-claims.spec.ts` / `vat-admin.spec.ts`.
- `frontend/package.json` — the new spec joins the `test:e2e` list CI runs.
- Boards: `TODO.md` (WO-86 row + M3/M5 cells + suite line), `README.md` (SPA page
  count 49 → 52, machine-checked by
  `backend/tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`),
  `docs/transport/rules.md` (the R-rows gaining a UI consumer).

**Out of scope (the anti-scope-creep clause):**
- **Any BACKEND change.** No new route, schema field, permission member or error
  code. If a screen wants a field the wire does not carry, it is reported as a
  gap and left unbuilt (§10). In particular: no rebate PREVIEW endpoint, no
  "merge now" verb, no overcharge re-snapshot verb, no supplier column on a
  claim line (`docs/DECISIONS-NEEDED.md` §11 still owns that).
- **`/excise`** (board G4.6) — `excise.py` still has no backing service; there is
  nothing to render.
- **`/value`, `/claim-status`, the refund-estimate funnel** (G4.4 / G4.8) — each
  its own board row, each with no route today.
- **Charting.** No new dependency, and no chart is introduced on these screens;
  the buckets are a table + figure tiles in the existing primitives. `recharts`
  is already in the tree and is deliberately not reached for here — the six
  buckets are six numbers, and a chart would add pixels without adding an answer.
- **G2.9 fee freezing** — decision-gated (`docs/DECISIONS-NEEDED.md` §10). No fee
  figure appears on any of these screens.
- Reworking `VatClaims.tsx` / `VatClaimDetail.tsx` / `VatAdmin.tsx` /
  `VatCustomers.tsx`. This order adds pages; it does not refactor the three that
  exist.

### Files to touch

| File | Change |
|---|---|
| `frontend/src/pages/RecoveryDashboard.tsx` | **new** — the six buckets + north-star euros |
| `frontend/src/pages/Overcharges.tsx` | **new** — detection · claim-backs · contract terms |
| `frontend/src/pages/Rebates.tsx` | **new** — record + list the rebate registry |
| `frontend/src/lib/transportRecovery.ts` | **new** — pure copy/transition mirrors |
| `frontend/src/lib/transportClaims.ts` | 18 additive refusal entries |
| `frontend/src/lib/types.ts` | 9 additive wire interfaces |
| `frontend/src/lib/roles.ts` | `"transport.read"` added to the mirror |
| `frontend/src/lib/nav.ts` | three Transport-group destinations |
| `frontend/src/App.tsx` | three lazy routes |
| `frontend/e2e/recovery.spec.ts` | **new** — the spec matrix |
| `frontend/package.json` | the spec joins `test:e2e` |
| `README.md` | scale line: SPA pages 49 → 52 |
| `TODO.md` | WO-86 row, M3/M5 cells, suite line |
| `docs/transport/rules.md` | R38/R41/R50/R53 gain a UI consumer |

### Implementation guidance

1. **Read the wire off the schemas, never off the work orders.** Every field name
   used by a page must appear in `app/schemas/transport_recovery.py`,
   `transport_overcharge.py` or `transport_rebate.py`. A field that does not
   exist is not rendered and not invented (§10).
2. **Money never becomes a number.** Every amount and every €/L rate arrives as a
   decimal STRING and is rendered by `decimalMoney` (§4.9). The pages perform NO
   arithmetic on money — not a sum, not a percentage, not a difference. The
   server already totals everything these screens show (§4.10); a UI-side total
   would be a second, forkable source of truth. This is grep-provable and the
   suite greps for it.
3. **The two honest nulls.** `median_days_to_refund: null` renders as *"Not yet
   measurable"* plus the sample size; it is never coerced to `0`, and
   `decimalMoney` is not used for it (it is a day count, not money).
   `currency_mismatch_claims > 0` renders a named notice; at `0` the notice is
   absent, so its presence always means something.
4. **Advisory vs blocking, stated correctly on every surface** (§4.19). The
   detection read is read-only and gates nothing; `source_warnings` is advisory
   and never blocks; the receipt-control precedent from WO-80 is the wording
   model. Conversely, the artifact refusals are fail-CLOSED and the copy says so:
   an `overcharge_evidence_drift` means *no letter is produced*, and the page
   must not imply the operator can send it anyway.
5. **Permission mirroring is cosmetic** (§6 / master-context). Reads are gated on
   `transport.read`; every mutating control (open claim-back, advance, set/remove
   term, record rebate) is gated on `vat.write` — the permission the routes
   actually override to. The artifact DOWNLOADS are reads (`TRANSPORT_READ`, the
   router default) and are therefore NOT hidden from a read-only role. A 403 from
   the server still renders through `RefusalNotice`.
6. **Transitions come from the mirror, not from a hand-typed list.**
   `overchargeActions(status)` returns `TRANSITIONS[status]` verbatim; a terminal
   state offers nothing. The server re-validates the edge and
   `overcharge_transition_invalid` renders its sentence if the mirror ever drifts.
7. **Downloads reuse `downloadFile`** from `lib/api.ts` — it already re-inflates a
   blob error body into the `{detail, code}` shape, which is exactly why the
   artifact refusals can render human sentences at all.
8. **Loading / empty / error on every panel** via `QueryState` / `EmptyState` /
   `Skeleton`, and module gating via `useModules` + `ModuleInactive`, exactly as
   the three existing transport pages do.

### Invariants this order must preserve

- **§4.9 — money is a string end to end.** No `Number()`, `parseFloat`, `+`, `-`,
  `*`, `/` or `toFixed` touches an amount on any of the three pages or in
  `transportRecovery.ts`. Proven by a grep in the spec file and by a fixture
  carrying `99999999999999.99`, which an IEEE-754 round-trip would destroy.
- **§4.10 — the server computes every total.** The pages render
  `recovered_eur`, `awaiting_eur`, `claimable_eur`, `overcharges_eur`,
  `recover_eur`, `detected_eur`, `recovered_eur` and `amount_eur` exactly as
  received. No screen re-derives a bucket total, a grand total or a net-of-fee.
- **§4.14 — no cross-currency sum is ever presented.** The dashboard states
  `currency` ("EUR") beside its figures and surfaces `currency_mismatch_claims`
  rather than hiding the shortfall. A breach line's `document_currency` is
  rendered as PROVENANCE beside the line and is never totalled. A rebate's
  `amount_local` is shown with its own `currency` and never added to anything.
- **§4.15 — never a guessed number.** The rebate form posts a document, not a
  converted total; `fx_rate_unavailable` is presented as "nothing was recorded".
- **§4.19 — advisory surfaces must not imply they gate.** Detection,
  `source_warnings` and the dashboard are read-only and say so; the artifact
  refusals block and say so. Neither is described as the other.
- **§4.20 — additive.** Three new pages, one new pure-helper module, additive
  entries in four existing lib modules, three nav rows, three routes. No existing
  page, type, refusal entry or permission value is changed or removed.
- **§9/§10 — actual vocabulary, nothing invented.** Bucket names, excluded
  reasons, claim-back states, flags (`short discount` / `over ceiling`),
  `price_basis` and `legal_framing` are rendered as the server's own strings or
  from a verbatim mirror. No status LABELS are invented (the WO-77 decision-5
  precedent), no seventh bucket, no preview.

### Database / migration impact

**None.** This order touches no backend file. No table, no column, no migration,
no RLS policy, no permission member.

### Testing requirements

`frontend/e2e/recovery.spec.ts`, the `page.route`-mocked live-app harness of
`vat-admin.spec.ts` (fixtures synthetic, fictional supplier codes and figures —
no Fleet Fuel bytes, no PII-shaped literal).

Recovery dashboard:
- `dashboard: renders all six buckets in the service's order with counts and euros`
- `dashboard: the north-star euros render exactly as the wire string`
- `dashboard: overcharges is presented as a separate cash stream, not inside the VAT total`
- `dashboard: deadline risk is shown outside the six buckets`
- `dashboard: the excluded block names withdrawn and rejected with their counts`
- `dashboard: a null median renders "not yet measurable" with its sample size, never 0`
- `dashboard: a measurable median renders the days figure`
- `dashboard: currency_mismatch_claims renders a named notice explaining the €0 contribution`
- `dashboard: no mismatch notice when the count is zero`
- `dashboard: an empty year renders zero-state copy, not an error`
- `dashboard: a 500 renders the error state`
- `dashboard: a loading state renders before the API resolves`
- `dashboard: invalid_year renders its sentence, not the slug`

Overcharges:
- `overcharges: detection renders each breach with its flag, gap, litres and recoverable euro`
- `overcharges: the price basis and legal framing are rendered verbatim from the response`
- `overcharges: source_warnings render as an advisory notice that gates nothing`
- `overcharges: a period with no configured term is not reported as a clean supplier`
- `overcharges: the claim-back ladder offers only the transitions legal from the state`
- `overcharges: a terminal claim-back offers no transition`
- `overcharges: advancing to recovered posts the typed amount as the wire string`
- `overcharges: overcharge_transition_invalid renders its sentence, not the slug`
- `overcharges: overcharge_evidence_drift explains the rebate/freeze race and what to do`
- `overcharges: overcharge_claim_closed says the packet is still available`
- `overcharges: issuer_profile_incomplete names the missing letterhead fields`
- `overcharges: pdf_renderer_unavailable renders its sentence`
- `overcharges: no_overcharge_detected renders its sentence`
- `overcharges: a contract term posts both €/L figures as typed strings`
- `overcharges: term_has_no_figure and invalid_term_rate render their sentences`
- `overcharges: the booked-cash total is labelled distinctly from the detected exposure`

Rebates:
- `rebates: the recorded documents render with their FX provenance and server-resolved euro`
- `rebates: the form never asks for a EUR amount`
- `rebates: recording states plainly that the effect applies at the next close`
- `rebates: no merge/preview control exists`
- `rebates: fx_rate_unavailable says nothing was recorded`
- `rebates: rebate_source_required and rebate_amount_invalid render their sentences`

Permission + gating pairs (granted/denied, every screen):
- `perm: a read-only role sees the figures and no mutating control`
- `perm: a VAT_WRITE role sees every mutating control`
- `perm: artifact downloads stay visible to a read-only role (they are reads)`
- `perm: a role without transport.read sees no Transport analytics nav entry`
- `perm: an EMPLOYEE gets the nav hidden and the server 403 still renders`
- `module: the transport module being off renders the module notice on all three pages`

Money discipline:
- `money: every amount renders from the wire string with no float round-trip`
  (a `99999999999999.99` fixture asserted on screen character-for-character)
- `money: no page performs float arithmetic on an amount` (a grep over the three
  page sources + the helper module, asserting the absence of `parseFloat`,
  `Number(`, `toFixed` and `+`/`-` on a money field)

### Acceptance criteria (verifiable checklist)

- [ ] `/recovery` renders exactly six buckets, labelled `ready`, `deadline`,
      `missing`, `below`, `submitted`, `paid`, in that order, all six present when
      three of them are zero.
- [ ] With `median_days_to_refund: null` and `days_to_refund_sample: 0`, the page
      shows "Not yet measurable" and its sample size, and the string `"0 days"`
      appears nowhere.
- [ ] With `currency_mismatch_claims: 2`, a notice names the count and states those
      claims contribute `€0.00`; with `0`, no such notice exists in the DOM.
- [ ] `deadline_risk_claims` is rendered outside the bucket table (not as a seventh row).
- [ ] `GET /transport/overcharges/{id}/packet` returning
      `{"code":"overcharge_evidence_drift"}` renders a sentence naming the frozen
      demand vs the re-summed evidence and the action to take; the string
      `overcharge_evidence_drift` appears nowhere on screen.
- [ ] A claim-back in `claimed` offers exactly `recovered`, `rejected`,
      `written_off`; one in `recovered` offers none.
- [ ] `POST /transport/rebates` refused with `fx_rate_unavailable` renders a
      sentence stating that no rebate was recorded.
- [ ] `/rebates` contains no control that claims to merge, preview or apply the
      rebate now, and states that the effect applies at the next close.
- [ ] A `user_free` (READ_ONLY) session sees the figures and the artifact download
      buttons, and sees no open/advance/set/record control.
- [ ] `grep -nE "parseFloat|Number\(|toFixed" frontend/src/pages/RecoveryDashboard.tsx
      frontend/src/pages/Overcharges.tsx frontend/src/pages/Rebates.tsx
      frontend/src/lib/transportRecovery.ts` returns nothing.
- [ ] `npm run build` (tsc + vite) is clean and `npm run test:e2e` is green at
      117 + the new specs, with zero pre-existing specs modified.
- [ ] `python -m pytest -q` is unchanged at 2073 passed / 10 skipped (frontend-only
      order), with `README.md`'s SPA page count moved 49 → 52 in the same commit
      that adds the pages.

### Rollback strategy

Code revert only — three new pages, one new helper module, additive edits to four
lib modules, three nav rows, three App routes, one spec file. No backend file, no
migration, no data. Narrower mitigation: remove the three entries from
`frontend/src/lib/nav.ts` and the destinations disappear from the IA while the
routes stay reachable by URL; remove the three `<Route>` lines and they are gone
entirely. Nothing this order ships can change a stored figure, a claim status, a
lock, a rebate row or an audit chain — every mutation it exposes is an existing,
already-audited service verb reached through its existing route.

### Documentation to update

- `README.md` — the scale line, SPA pages 49 → 52 (machine-checked).
- `TODO.md` — the WO-86 row, the M3/M5 cells, the suite line.
- `docs/transport/rules.md` — R38, R41, R50 and R53 gain their first UI consumer.
- No ADR is contradicted. ADR-0024's structural-authorization rule is mirrored,
  not re-decided; the frontend mirror remains cosmetic per master-context §6.

### Self-verification block

```bash
cd /home/user/Bid_it/frontend
npm run build                       # tsc --noEmit + vite build
npx playwright test e2e/recovery.spec.ts
npm run test:e2e                    # 117 -> 117 + N, zero pre-existing specs touched

# the money-discipline proof (must print nothing)
grep -nE "parseFloat|Number\(|toFixed" \
  src/pages/RecoveryDashboard.tsx src/pages/Overcharges.tsx src/pages/Rebates.tsx \
  src/lib/transportRecovery.ts

# every field the pages read must exist on the wire schemas (spot proof)
cd /home/user/Bid_it/backend && . .venv/bin/activate
python -c "
from app.schemas.transport_recovery import RecoveryDashboardOut
from app.schemas.transport_overcharge import ContractAuditOut, OverchargeClaimOut
from app.schemas.transport_rebate import RebateOut
for m in (RecoveryDashboardOut, ContractAuditOut, OverchargeClaimOut, RebateOut):
    print(m.__name__, sorted(m.model_fields))
"
python -m pytest -q                 # frontend-only order: 2073 passed, 10 skipped
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```
