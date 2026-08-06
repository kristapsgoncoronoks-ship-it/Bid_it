# WO-80 — transport UI slice 2: the admin/config workspace

> The second half of the M3 **UI batch**. WO-78 shipped the CLAIM workspace
> and WO-79 its submit pick-list; WO-78's own anti-scope clause named what it
> deliberately left out — *"any SPA surface for the WO-77 admin/config routes
> … They are a second UI slice."* This is that slice: every one of WO-77's
> org-level configuration surfaces and the R44 customer-activation ladder get
> a screen, on the existing routes, permissions and refusal vocabulary.

**WORK ORDER 80 — transport UI slice 2: the admin/config workspace
(`frontend/src/pages/VatAdmin.tsx` — a tabbed configuration page over the
WO-77 `admin.py` routes — plus `frontend/src/pages/VatCustomers.tsx` for the
R44 lifecycle/country ladder, and the claim-scoped receipt waivers added to
the existing `VatClaimDetail.tsx`), permission-gated in the UI exactly as the
API gates it (VAT_READ reads / VAT_WRITE config mutations / VAT_SUBMIT the
claim-status verb), with every WO-77 refusal code mapped to an actionable
human sentence. Effort M 3–5d. Priority P1. Milestone M3. Depends on: WO-77
(the 27 admin/config routes), WO-78 (the SPA conventions: `lib/types.ts`,
`decimalMoney`, `hasVatPerm`, `claimRefusal`, `RefusalNotice`, the nav `perm`
flag), WO-79 (the fuel read surface + its spec conventions), WO-49 (the VAT
permission vocabulary), I1.2 (the live nav IA / `AppShell`).**

### Objective and business value

The gap, with verified evidence. `backend/app/api/routes/transport/admin.py`
(14 routes) and `customers.py` (7 routes), plus the three claim-scoped waiver
routes in `claims.py`, are live, structurally gated and covered by
`tests/transport/test_wo77_admin_routes.py` — and `grep -rn "checklist-rules\|
cadences\|receipt-controls\|note-overrides\|tie-out-expectations\|
transport/customers" frontend/src` returns **nothing**. WO-78 consumed exactly
one route from `admin.py` (`GET /transport/status-codes`, for the ladder
vocabulary) and nothing from `customers.py`. Every other admin lever is
reachable only with `curl`.

Who stops losing money: the same FINANCE_MANAGER who files, one step earlier.
Three of WO-78's D5 refusal sentences tell an operator to do something that has
no screen — `customer_not_active` says "set the entity active", and
`country_not_activated` says "request the country first, then activate it":
both are `customers.py` routes with no UI, so the sentence is currently a
dead end and the claim misses the 30 September Art. 15 deadline while nobody
can act on it. `unresolved_invoice_refs` says "map the statement note to an
invoice reference" — that is `PUT /transport/note-overrides`, also with no UI.
The checklist that WO-78 renders as advisory is only adjustable through
`POST /transport/checklist-rules/{key}/active`, and its rules do not even
EXIST until a committing caller runs the seed (WO-77 decision 4). And the R25
tie-out expectations HALT the monthly close when the typed figures disagree —
an operator who cannot see or correct them cannot restart their own close.

### Scope

**In scope:**
- `frontend/src/pages/VatAdmin.tsx` (**new**) — one tabbed configuration page
  at `/vat-admin`, six panels over the `admin.py` routes:
  1. **Checklist rules** — list, per-rule active toggle, the seed-defaults
     action (WO-77 decision 4: the claim checklist GET never commits, so
     seeding is its own committing route and the empty table is the real
     first-run state, not an error).
  2. **Receipt control** — the persisted cadence × activity slot grid for a
     period, with the manual override (waive / note). Presented as ADVISORY
     throughout (§4.19): the copy states that a `missing` slot is a chase-list
     row and gates no claim, and the UI offers no action that implies otherwise.
  3. **Cadences** — the admin per-supplier assignments (list / set / remove),
     with the copy recording that absence is not "no cadence" (the service
     falls back to its harvested per-network default).
  4. **Note→invoice-ref overrides** — list + set (upsert). **No delete
     control**: WO-77 decision 6 recorded that no removal function exists on
     the service (R16's lifecycle is de-register-the-target ⇒ CASCADE), and a
     route-less button would be invented functionality (§10).
  5. **Tie-out expectations** — list / upsert / delete per (entity, supplier,
     period, currency), with the consequence stated plainly: typed figures
     that disagree HALT the close (R25 regime 2), and absence is fail-open.
  6. **Status codes** — the `GET /transport/status-codes` vocabulary read
     (`auto` vs `manual`), as a reference panel that links to `/vat-claims`.
     The manual `set status-code` action is VAT_SUBMIT and already lives on
     the claim detail (WO-78); it is NOT duplicated here.
- `frontend/src/pages/VatCustomers.tsx` (**new**) — `/vat-customers`: pick a
  legal entity, see its lifecycle state (prospect → pending → active →
  inactive) and every country-activation row ((none) → requested → active),
  and drive the transitions the routes expose. The current state is rendered
  unambiguously (including the meaningful `null` = never onboarded), because
  this state is what the R44 gate reads at submission.
- `frontend/src/pages/VatClaimDetail.tsx` — additive **receipt waivers** panel
  (the claim-scoped `GET/POST/DELETE /transport/claims/{id}/waivers`), the one
  WO-77 surface that is claim-scoped rather than org-level.
- `frontend/src/lib/transportClaims.ts` — the refusal map gains the WO-77
  codes (`waiver_supplier_has_invoices`, `checklist_rule_not_found`,
  `invalid_cadence`, `receipt_control_not_found`, `override_note_is_synthetic`,
  `override_target_not_registered`, `invalid_currency`,
  `invalid_expected_lines`, `invalid_gross_tolerance`,
  `tieout_expectation_not_found`, `not_a_prospect`,
  `lifecycle_transition_invalid`, `invalid_country`, `country_not_requested`,
  `country_transition_invalid`). Additive only — every WO-78 entry is
  untouched, so `RefusalNotice` renders the new screens with no change.
- `frontend/src/lib/transportAdmin.ts` (**new**) — PURE helpers, no React, no
  network: the `CADENCES` mirror (verbatim from
  `app/models/transport/receipt_control.py`), the `RECEIPT_STATUS_COPY` map
  that says what each persisted slot status means (advisory wording), and
  `lifecycleActions`/`countryActions` — which transitions the harvested ladder
  exposes FROM a given state (`customer_lifecycle.py`'s own edges). Mirrors
  only; the server remains the control, exactly like `roles.ts::hasVatPerm`
  and `transportClaims.ts::isSyntheticRef`.
- `frontend/src/lib/types.ts` — additive wire types, field-for-field from
  `app/schemas/transport_admin.py`: `VatChecklistRule`, `VatCadence`,
  `VatReceiptControl`, `VatNoteOverride`, `VatTieOutExpectation`,
  `VatLifecycle`, `VatCountryActivation`, `VatWaiver`, `VatRemoved`.
- `frontend/src/lib/nav.ts` — two destinations in the existing Transport
  group, gated `module: "transport"` + `perm: "vat.read"` exactly as the
  VAT claims entry is.
- `frontend/src/App.tsx` — `/vat-admin` and `/vat-customers` (lazy).
- `frontend/e2e/vat-admin.spec.ts` (**new**) — the matrix below, in the
  `page.route`-mocked live-app pattern of `vat-claims.spec.ts`.
- `frontend/package.json` — the new spec joins the `test:e2e` list CI runs.
- Boards: `TODO.md` (WO-80 row + M3 cell + suite line), `README.md` (SPA page
  count 47 → 49, machine-checked by
  `backend/tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`),
  `docs/transport/rules.md` (the R-rows gaining a UI consumer).

**Out of scope (the anti-scope-creep clause):**
- Any BACKEND change. No new route, schema field, permission or error code. If
  a screen wants a field the wire does not carry, it is reported as a gap and
  not invented (§9/§10). Specifically NOT built: a note-override DELETE (no
  service function — WO-77 decision 6), a receipt-control RUN trigger (R60 —
  the run is a close stage and is deliberately unrouted, WO-77 decision 7), an
  orphan-transactions view (no route), and any entity NAME on a transport
  surface (no transport route serves one; the issuer registry is used as a
  convenience exactly as WO-78 used it).
- Any analytics surface (`recovery.py`/`excise.py`/`overcharges.py` — no
  backing service exists).
- Duplicating the claim `set status-code` action (VAT_SUBMIT, already on the
  claim detail from WO-78).
- A new UI library, state manager, styling system, navigation pattern or test
  framework. The tabbed workspace composes the existing `Tabs`/`TabPanel`
  primitives (`src/components/ui`), the same pattern the design system's own
  configuration surface uses.
- Visual-regression snapshots (`npm run test:vr` is a documented LOCAL gate).

### Files to touch

| File | Change |
|---|---|
| `frontend/src/pages/VatAdmin.tsx` | **new** — the six-tab config workspace |
| `frontend/src/pages/VatCustomers.tsx` | **new** — the R44 lifecycle + country ladder |
| `frontend/src/pages/VatClaimDetail.tsx` | additive receipt-waivers panel |
| `frontend/src/lib/transportAdmin.ts` | **new** — pure mirrors + advisory copy |
| `frontend/src/lib/transportClaims.ts` | additive refusal codes (WO-77 vocabulary) |
| `frontend/src/lib/types.ts` | additive transport-admin wire types |
| `frontend/src/lib/nav.ts` | two Transport destinations |
| `frontend/src/App.tsx` | two lazy routes |
| `frontend/e2e/vat-admin.spec.ts` | **new** — the test matrix |
| `frontend/package.json` | the new spec joins `test:e2e` |
| `TODO.md`, `README.md`, `docs/transport/rules.md` | boards (final commit) |

### Implementation guidance

1. **Verify every field against the backend first.** `ChecklistRuleOut`,
   `CadenceOut`, `ReceiptControlOut`, `ControlOverrideIn`, `NoteOverrideOut`,
   `NoteOverrideSetIn`, `TieOutExpectationOut`, `TieOutExpectationSetIn`,
   `LifecycleOut`, `CountryActivationOut`, `WaiverOut`, `RemovedOut` in
   `app/schemas/transport_admin.py`. Money/litres fields are `Decimal` on the
   backend ⇒ `string` in `types.ts`, nullable exactly where the schema says
   `| None`.
2. **Money and litres are display-only and string-exact.** The tie-out
   expectations carry four `Decimal` figures plus a tolerance; they render
   through `decimalMoney` and are POSTED BACK AS THE TYPED STRING. No
   `Number()`, no `parseFloat`, no arithmetic anywhere in the new modules —
   the service is the single point that quantizes (§4.9). `expected_lines` is
   an `int` and is the one numeric field parsed as a number, via
   `parseInt`-free `Number.parseInt` on a digits-only input, never touching a
   money path.
3. **Permission gating is cosmetic and mirrors the API exactly.** Reads render
   for VAT_READ; every config mutation (rule toggle, seed, cadence set/remove,
   control override, note override, tie-out set/delete, every lifecycle and
   country transition, waiver set/remove) renders only for `hasVatPerm(user,
   "vat.write")`. Nothing on these pages needs VAT_SUBMIT — the one
   VAT_SUBMIT verb (`set status-code`) stays on the claim detail. A user
   without the permission sees NO dead button, and the server's 403 still
   renders through `RefusalNotice` if one arrives (§6).
4. **Advisory surfaces must LOOK advisory (§4.19).** The receipt-control panel
   states in its own copy that the grid is a chase list, that a `missing` slot
   gates no claim and blocks no close, and that `waived` here is a worklist
   mute — NOT the claim-level legal waiver (R15), which lives on the claim.
   The panel offers no "block", "resolve" or "gate" verb, because the service
   has none. Conversely the tie-out panel states the opposite consequence
   plainly (typed figures that disagree HALT the close), because that one is a
   real gate.
5. **Refusal mapping is pure and additive.** The new codes extend the ONE
   `REFUSALS` map so `RefusalNotice` needs no change and there is a single
   refusal vocabulary in the SPA. Each entry says what is wrong and what to do
   next; the server's own `detail` (which names the supplier, the key, the
   band) rides underneath verbatim. An unmapped code still falls back to the
   server sentence — fail-OPEN on presentation, which gates nothing.
6. **The period-scoped panels take a period the operator controls.** The
   receipt-control and tie-out reads REQUIRE `period` (`YYYY-MM`, the route's
   own `min_length=7, max_length=7`). The page defaults it to the current
   month and lets it be edited; the query only runs with a well-formed value,
   and a malformed one is still the server's refusal (`invalid_period`) if it
   reaches it. No period arithmetic beyond formatting today's month.
7. **The entity picker is the WO-78 convenience, reused.** `GET
   /issuer/registry` (ISSUED_READ) supplies entity names; it is `retry: false`
   and falls back to a plain id input when the caller cannot read it, because
   no transport route serves an entity name. Same for the note-override target
   invoice: `GET /invoices` (INVOICE_READ, a DIFFERENT permission) is offered
   as a pick-list when readable and degrades to a typed id otherwise —
   recorded, not hidden.
8. **Lifecycle actions follow the harvested ladder, and the server still
   decides.** From `prospect` the page offers Promote; from `pending`,
   Activate; from `active`, Deactivate (`pending`) and Set inactive; from
   `inactive`, nothing (terminal in this slice — no re-onboarding edge is
   harvested, and inventing one is out of the question). Countries: `(none)` →
   Request, `requested` → Activate, `active` → Deactivate. This is the same
   state-driven action rendering WO-78 already uses (`canWrite && isDraft &&
   Build lines`); an illegal edge is still refused by the service, and its
   refusal renders.
9. **Loading, empty and error states on every panel** via
   `QueryState`/`EmptyState`/`ErrorState`/`Skeleton`, and `ModuleInactive`
   when the transport module is off — the WO-78 precedent, unchanged.

### Invariants this order must preserve

- **§4.9 (Decimal, never float):** every figure on the new screens is rendered
  by `decimalMoney` from the wire string and posted back as a string; a spec
  asserts a 16-digit tie-out figure renders and re-posts with its exact digits
  (a `Number()` round-trip would corrupt it).
- **§4.10 (the server recomputes every total):** these screens carry no
  derived figure at all — no sums, no counts of anything the server counted,
  no re-rating of a tolerance.
- **§4.19 (advisory never blocks):** the receipt-control grid is read-only
  advisory plus a worklist mute; its copy states it gates nothing, and a spec
  asserts the page never renders blocking language for a `missing` slot.
- **§4.20 (additive):** no existing route, schema, error shape or page
  behaviour changes. `transportClaims.ts` gains map ENTRIES only;
  `VatClaimDetail.tsx` gains a panel; `nav.ts` gains two items in an existing
  group.
- **§6 (frontend gating is cosmetic):** every mutating control is hidden
  without the mirrored permission AND the server's 403 still renders.
- **§9/§10 (actual vocabulary only, nothing invented, zero Fleet Fuel
  bytes):** no note-override delete, no control run, no status LABELS, no
  invented cadence or lifecycle state; every constant mirrored is quoted from
  the model/service module it mirrors. Fixtures synthetic.

### Database / migration impact

None. No backend file is touched.

### Testing requirements

`frontend/e2e/vat-admin.spec.ts`, in the mocked live-app pattern of
`vat-claims.spec.ts` (synthetic fixtures only):
- `rules: renders the seeded rules with their scope and active state`
- `rules: an empty table shows the first-run seed copy, never an alert`
- `rules: the seed action posts to the committing seed route`
- `rules: toggling a rule posts {active:false} for that key`
- `rules: a 500 shows the error state, not the empty copy`
- `rules: checklist_rule_not_found renders its sentence, not the slug`
- `controls: the grid renders each slot with its status and count`
- `controls: the advisory copy states a missing slot blocks nothing`
- `controls: an override posts waived + note for that control id`
- `controls: receipt_control_not_found renders its human message`
- `cadences: set posts the chosen cadence; remove calls DELETE`
- `cadences: invalid_cadence renders its sentence`
- `overrides: the list renders and there is NO delete control`
- `overrides: set posts the full natural key plus the target`
- `overrides: override_target_not_registered renders its sentence`
- `tieout: figures render exactly from the wire string (no float round-trip)`
- `tieout: an upsert posts the typed decimal strings verbatim`
- `tieout: invalid_gross_tolerance renders the band, not the slug`
- `tieout: delete sends the natural key as query params`
- `tieout: the copy states that a disagreeing expectation halts the close`
- `status codes: the vocabulary renders auto and manual separately`
- `lifecycle: a never-onboarded entity reads as such, not as an error`
- `lifecycle: the ladder offers only the transitions legal from the state`
- `lifecycle: promote/activate/inactive each post their own route`
- `lifecycle: countries render their activation state and request/activate`
- `lifecycle: country_not_requested renders its sentence`
- `waivers: the claim detail lists a waiver and posts a new one`
- `waivers: waiver_supplier_has_invoices renders its sentence`
- `permissions: an auditor (VAT_READ only) sees no mutating control anywhere`
- `permissions: an accountant (VAT_WRITE) sees every config mutation`
- `nav/module: the entries are permission- and module-gated; the pages render
  the module notice when transport is off`
- loading states on the tabbed page and the customers page

### Acceptance criteria (verifiable checklist)

- [ ] `/vat-admin` renders six tabs and each one reads its own WO-77 route;
      switching a tab issues that tab's GET and no other.
- [ ] With an empty `GET /transport/checklist-rules` the page shows the
      first-run copy and a "Seed the default rules" action that POSTs to
      `/transport/checklist-rules/seed`; toggling a rule POSTs
      `{"active": false}` to `/transport/checklist-rules/{key}/active`
      (asserted on the captured request body).
- [ ] The receipt-control panel renders a `missing` slot AND the sentence
      stating it blocks no claim and no close; the page renders no
      blocking/gating verb for it.
- [ ] A tie-out figure of `"99999999999999.99"` renders with those exact
      digits and re-posts with those exact digits.
- [ ] A tie-out PUT refused 422 `invalid_gross_tolerance` renders a sentence
      naming the 0.02–0.05 band and the slug appears NOWHERE on the page.
- [ ] The note-override panel renders no delete control (asserted by absence).
- [ ] `/vat-customers` on an entity with no lifecycle row reads "never
      onboarded" (not an error); a `prospect` offers Promote and NOT Activate;
      a 409 `not_a_prospect` renders its human sentence.
- [ ] With `role: "auditor"` (VAT_READ only) neither page renders any
      mutating control; with `role: "accountant"` (VAT_WRITE) every config
      mutation renders.
- [ ] With `role: "user"` (EMPLOYEE) the nav has no Transport entries; with
      the `transport` module off both pages render the module notice.
- [ ] `npm run build` and `npm run test:e2e` (including the new spec) green;
      `python scripts/pii_scan.py --tree` clean; the backend suite unchanged
      at 1865 passed / 10 skipped.
- [ ] `README.md`'s scale line says 49 SPA pages and
      `test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`
      passes.

### Rollback strategy

Pure frontend revert: two new pages, one new lib module, four additive
lib/nav/App/page edits, one new spec. No migration, no data effect, no backend
change. Narrow mitigation without a full revert: drop the two new items from
the Transport group in `LIVE_NAV` — the screens become unreachable from the
shell while the backend keeps its own gates.

### Documentation to update

- `TODO.md` — WO-80 row + M3 cell + suite line.
- `README.md` — SPA page count (47 → 49).
- `docs/transport/rules.md` — the R-rows gaining a **UI** consumer: R45
  (checklist-rule admin), G3.5 (cadences, the control grid + override), R16
  (note→invoice overrides), R25 regime 2 (tie-out expectations), R44 (the
  lifecycle + country ladder), R15 (claim-scoped waivers), R17 (the
  vocabulary read).
- No ADR contradicted: ADR-0024's structural authz is unchanged (the SPA
  mirror is explicitly cosmetic); ADR-P3's UI batch is continued, not altered.

### Self-verification block

```bash
cd /home/user/Bid_it/frontend
npm run build                       # tsc --noEmit + vite build
npx playwright test e2e/vat-admin.spec.ts
npm run test:e2e                    # the full CI e2e list, incl. the new spec
# the demonstration — the admin surfaces exist, are permission-mirrored, invent
# no delete the backend lacks, and never round-trip money through a float:
grep -n "vat-admin\|vat-customers" src/App.tsx src/lib/nav.ts
grep -rn "Number(\|parseFloat" src/pages/VatAdmin.tsx src/pages/VatCustomers.tsx \
  src/lib/transportAdmin.ts || echo "no float money path"
grep -rn "note-overrides" src/pages/VatAdmin.tsx | grep -i delete || echo "no invented delete"
cd ../backend && .venv/bin/python -m pytest -q      # 1865 passed / 10 skipped, unchanged
cd .. && python scripts/pii_scan.py --tree
```
