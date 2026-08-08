# WO-92 — transport UI slice 5: the diesel-excise screen

> WO-91 shipped G4.6's whole backend — the analysis, the rate registry, the
> customs packet, five test files — and named its own missing half in its scope
> section, verbatim: *"**An `/excise` SPA page.** Board G4.6's UI half; R42's
> acceptance line (*"the UI shows the … caveats"*) is served on the wire by this
> order … and the page is a follow-up UI slice with the WO-90 precedent. This
> order adds **no SPA page**."* This is that slice.

**WORK ORDER 92 — transport UI slice 5: the diesel-excise screen
(`frontend/src/pages/Excise.tsx` over WO-91's five routes `GET /transport/excise`,
`GET /transport/excise/packet`, `GET /transport/excise/rates`,
`PUT`/`DELETE /transport/excise/rates`), carrying R42's eligibility
non-assertion into the UI structurally — the statement rendered VERBATIM off the
wire, no entitlement vocabulary anywhere on the screen, and a country with no
rate presented as no finding rather than as €0.00. Effort M 3–5d. Priority P1.
Milestone M5. Depends on: WO-91 (the service, its schemas, its routes, its
constants and its refusal codes), WO-90 (the SPA conventions: `lib/types.ts`,
`decimalMoney`, `hasVatPerm`, `claimRefusal`/`periodShape`, `RefusalNotice`, the
nav `perm` flag, the `page.route`-mocked Playwright harness, the source-scan +
seeded-violation pattern).**

### Objective and business value

The gap, with verified evidence. `grep -rn "transport/excise" frontend/src`
returns **nothing**. WO-91 built `app/api/routes/transport/excise.py` — five
routes, three of them on the reserved `TRANSPORT_READ` and two on the existing
`VAT_WRITE` — and shipped `app/services/transport/excise.py` with 79 new test
functions behind it. Every one of those routes is reachable today only with
`curl`. The deliverable a haulier actually needs is
`GET /transport/excise/packet`: one spreadsheet per period to hand to a customs
authority. A download endpoint with no button is a feature nobody can use.

Who stops losing money: the finance lead of a haulier that already pays this
platform to capture its fuel lines. Around seven EU states refund part of the
commercial-diesel excise on litres the client has already burned and already has
invoices for, and this is the **second** recoverable-cash stream over data the
client has already paid to have captured. Three concrete failures the missing
screen causes. (a) **The packet is never filed.** The workbook exists and is
formula-injection-safe and reconciles cell-for-cell with the JSON, and no
operator can obtain it. (b) **The rate stays a placeholder forever.**
`DEFAULT_RATE_EUR_PER_1000L` is EUR 30.0000 and `BA_fleet_fuel.md` §2.4 calls it
an *"explicit PLACEHOLDER"* in a reported EUR 25–33 band; `set_rate` exists so an
operator can type what customs actually applies, and there is no surface to type
it into, so every figure the platform holds is computed from a placeholder that
nobody can correct. (c) **The one number most likely to be misread has no
reader.** R42's acceptance line is *"The UI shows the indicative-rate and
eligibility caveats on **every surface that shows the number**"* — an acceptance
criterion about a UI, which has had no UI to be true of.

And the risk this order is really about: **the eligibility non-assertion**. WO-91
made it structural on the backend — one `ELIGIBILITY_STATEMENT` constant rendered
by every result shape and both workbook sheets, a required literal
`eligibility_asserted: false`, no claim vocabulary in any field name, and a
vocabulary scan that **imports** WO-87's `CLAIM_WORDS` so the two surfaces cannot
drift. **A UI is exactly where that gets undone**: one "Recoverable" column
heading, one "€0.00" in a country row we hold no rate for, one paraphrase of the
statement that shortens it into a footnote, and this platform is asserting to a
haulier that a customs authority owes it money — under conditions
(vehicle ≥ 7.5 t, carrier registration) that the product deliberately does not
model. This order therefore treats the non-assertion as the governing constraint
and proves its preservation the way WO-90 proved R53's: by asserted absence, at
source, with a seeded-violation self-test, plus a DOM assertion that the
statement is actually on the screen.

### Scope

**In scope:**
- `frontend/src/pages/Excise.tsx` (**new**) — `/excise`, two panels over WO-91's
  five routes:
  1. **Monthly figures** (`GET /transport/excise?period=&country=`) — the
     per-(entity × country) `rows` field-for-field (`entity_name`, `entity_id`,
     `country`, `litres`, `rate_eur_per_1000l`, `rate_is_override`,
     `indicative_excise_eur`, `lines`), the report totals (`litres`,
     `indicative_excise_eur`, `lines_examined`), the scope echo (`period`,
     `entity_id`, `country`, `currency`, `product_group`), and
     `skipped_countries` (`country`, `litres`, `lines` — and deliberately **no
     euro**, because the shape carries none). The rate SOURCE is visible per row
     off `rate_is_override`, in the workbook's own two words (*verified
     override* / *indicative default*), so a placeholder is never presented as a
     statutory figure. Plus the **evidence-workbook download**
     (`GET /transport/excise/packet`) — a read, on the router-level
     `TRANSPORT_READ`, so it is not hidden behind a write permission.
  2. **Country rates** (`GET /transport/excise/rates`) — every state in
     `countries` with its resolved `rate_eur_per_1000l`, `is_override` and the
     `default_rate_eur_per_1000l` beside it, and the two write controls
     (`PUT`/`DELETE /transport/excise/rates`) gated cosmetically on the
     permission the routes actually declare (**`vat.write`** — verified in
     `app/api/routes/transport/excise.py`'s `_WRITE`; no permission is invented).
- **The eligibility non-assertion, carried structurally into the UI:**
  - `excise.ELIGIBILITY_STATEMENT`, `excise.RATE_CAVEAT`, `excise.LEGAL_FRAMING`,
    `excise.FILED_WITH` and `excise.LITRE_BASIS` are rendered **verbatim from the
    wire** (`eligibility`, `rate_caveat`, `legal_framing`, `filed_with`,
    `litre_basis`) — never paraphrased, never shortened, never restated. The
    frontend holds **no framing string of its own**, exactly as
    `lib/transportSavings.ts` deliberately holds none.
  - `eligibility_asserted` is rendered as an explicit statement, not dropped: the
    boolean exists precisely so the denial survives a surface that truncates
    prose, and a surface that ignored it would be that surface.
  - **No entitlement vocabulary anywhere in the new modules.** No `recover*`,
    `owed`, `owes`, `claim*`, `demand*`, `due`, `debt*`, `payable`, `entitle*` in
    any identifier, string, comment or class name of `Excise.tsx`,
    `lib/transportExcise.ts` or the new wire interfaces in `lib/types.ts`. The
    word list is WO-87's `CLAIM_WORDS` (the same list WO-91's backend scan
    imports) **plus `entitle`**, which this order's own constraint names.
  - **A country with no configured rate is no finding, never €0.00.**
    `skipped_countries` gets its own panel, its own heading and a sentence saying
    what it means; the string `€0.00` never describes one, and the shape carries
    no euro field for the page to render even if it wanted to.
- `frontend/src/lib/transportExcise.ts` (**new**) — PURE helpers, no React, no
  network, no arithmetic: the tab definitions, the no-rate copy, the advisory
  copy, the placeholder-rate copy and the `rate_is_override` label pair. It holds
  **no framing string**. `isPeriodShape`/`isDecimalShape` are IMPORTED from
  `transportRecovery.ts` and `isCountryShape` from `transportSavings.ts` rather
  than re-declared: one shape check, one source.
- `frontend/src/lib/types.ts` — additive wire types, field-for-field from
  `app/schemas/transport_excise.py`: `ExciseRate`, `ExciseRates`, `ExciseCell`,
  `ExciseSkippedCountry`, `ExciseReport`. Every euro, rate and litre figure is
  `string` (`litres` included — it is the figure's multiplicand and a float
  round-trip would move the euro computed from it).
- `frontend/src/lib/transportClaims.ts` — three ADDITIVE refusal entries for the
  three codes WO-91 raises that no page has yet rendered
  (`excise_country_not_supported`, `invalid_excise_rate`, `no_excise_findings`).
  No existing entry is edited; `invalid_period`, `invalid_country` and
  `module_not_enabled` are already mapped and are reused with
  `periodShape="month"` (WO-91's own split).
- `frontend/src/lib/nav.ts` — one destination in the existing Transport group,
  gated `module: "transport"` + `perm: "transport.read"` (the permission
  `routes/transport/excise.py` declares at router level), exactly as WO-86's and
  WO-90's are.
- `frontend/src/App.tsx` — `/excise` (lazy), beside the other transport routes.
- `frontend/e2e/excise.spec.ts` (**new**) — the matrix below, in the
  `page.route`-mocked live-app pattern of `savings.spec.ts`.
- `frontend/package.json` — the new spec joins the `test:e2e` list CI runs.
- Boards: `README.md` (SPA page count 53 → 54, machine-checked by
  `backend/tests/test_docs_truth.py::test_readme_scale_numbers_match_the_live_tree`,
  moved in the SAME commit that adds the page), `TODO.md` (WO-92 row + M5 cell +
  suite line), `docs/transport/rules.md` (R42 / G4.6 gains its UI consumer).

**Out of scope (the anti-scope-creep clause):**
- **Any BACKEND change.** No route, schema field, permission member, constant or
  error code. If the screen wants a figure the wire does not carry, it is
  reported as a gap and left unbuilt (§10).
- **An entity picker.** `GET /transport/excise` takes an optional `entity_id`,
  but resolving ids to names needs `GET /issuer/registry`, which is gated on a
  DIFFERENT permission (`ISSUED_READ`) that a `transport.read`-only role need not
  hold. The report already carries `entity_name` per row, so the grain is fully
  legible without one; the filter is reported as a follow-up rather than built on
  a permission this surface cannot assume.
- **Modelling eligibility, or hinting at it.** §9.2 item 14 is an open owner
  question. The screen states what is not modelled and offers no control, badge
  or checkbox suggesting the product can decide it.
- **Real statutory rates.** §9.2 item 13 is an open owner question. The screen
  shows the placeholder, labels it as one on every row, and lets an operator type
  what customs actually applies. It ships no rate table of its own.
- **An excise claim lifecycle**, a "file this" action, a send verb, or any link
  into the VAT-claim or supplier claim-back flows. WO-91 shipped no lifecycle and
  none is invented here; the customs regime is a separate one and the page says
  so in the service's own `filed_with` words.
- **Charting.** No new dependency and no chart: these are litres beside a rate
  beside a euro, and a chart would add pixels without adding an answer (WO-86's
  decision, unchanged).
- Reworking `Savings.tsx` / `RecoveryDashboard.tsx` / `Overcharges.tsx` /
  `Rebates.tsx` or any existing lib entry beyond the three additive refusal rows.

### Files to touch

| File | Change |
|---|---|
| `frontend/src/pages/Excise.tsx` | **new** — the two-panel screen |
| `frontend/src/lib/transportExcise.ts` | **new** — pure copy + label helpers |
| `frontend/src/lib/types.ts` | 5 additive wire interfaces |
| `frontend/src/lib/transportClaims.ts` | 3 additive refusal entries |
| `frontend/src/lib/nav.ts` | one Transport-group destination |
| `frontend/src/App.tsx` | one lazy route |
| `frontend/e2e/excise.spec.ts` | **new** — the spec matrix |
| `frontend/package.json` | the spec joins `test:e2e` |
| `README.md` | scale line: SPA pages 53 → 54 |
| `TODO.md` | WO-92 row, M5 cell, suite line |
| `docs/transport/rules.md` | R42 / G4.6 gains its UI consumer |

### Implementation guidance

1. **Read the wire off `app/schemas/transport_excise.py` and
   `app/api/routes/transport/excise.py`, never off a work order.** Every field a
   panel renders must appear in those modules; every refusal code the page maps
   must be raised by `app/services/transport/excise.py`. A field that does not
   exist is not rendered and not invented (§10).
2. **Money never becomes a number** (§4.9). Every euro, the rate and `litres`
   arrive as decimal STRINGS. Euros render through `decimalMoney` (string
   surgery, no arithmetic); rates and litres render as received — they are not
   currency and `decimalMoney` would pad a 4-dp rate into a currency symbol. The
   page performs NO arithmetic: not a sum, not a product, not a percentage. The
   server has already multiplied and quantized (§4.10) — a UI-side
   `litres/1000 × rate` would be a second, forkable source of truth for a figure
   that goes to a customs authority. Grep-provable, and the suite greps.
3. **The eligibility statement is rendered, not summarised.** One block, high on
   the page, carrying `eligibility` verbatim, and `rate_caveat` verbatim beside
   it. Both are ALSO rendered inside the figures panel from that response's own
   fields, because R42's acceptance is *"every surface that shows the number"*
   and a panel scrolled away from the page header is such a surface.
4. **No rate is no finding.** `skipped_countries` is presented as *"countries we
   hold no rate for"* with its litres and line count, and the copy states in
   words that this is not a figure of zero. The euro column does not exist in
   that table because the shape has no euro field.
5. **The rate source is always visible.** Every place a rate appears —
   the figures table and the rates table — shows whether it is the org's typed
   override or the harvested placeholder, in the workbook's own two words. A rate
   shown without its source presents a placeholder as a statutory figure, which
   is the exact confusion §9.2 item 13 exists to record.
6. **The four absences, mirrored from WO-90 and proven, not promised.** (a) every
   framing string comes off the wire and is rendered verbatim; (b) the forbidden
   vocabulary is absent from the new modules — enforced by a source-level
   word-boundary scan in the spec with a seeded-violation self-test, so the scan
   itself cannot silently stop working; (c) `€0.00` never describes a
   no-rate country; (d) the eligibility statement is asserted PRESENT in the DOM,
   not merely absent-of-bad-words. Every one is an assertion about tomorrow's
   file, not a description of today's.
7. **Advisory means advisory** (§4.19). The analysis and the packet gate nothing,
   mutate nothing and persist nothing; the copy says so and describes neither as
   blocking. The rate CRUD is the one mutation and it is configuration, audited
   server-side — the copy says what changing a rate does (it re-computes the
   indicative figure) and does not suggest it changes anything filed.
8. **Loading / empty / error on every panel** via `QueryState` / `EmptyState` /
   `Skeleton`, module gating via `useModules` + `ModuleInactive`, refusals via
   `RefusalNotice` with `periodShape="month"`, exactly as the WO-90 page does.
9. **Permission mirroring is cosmetic** (master-context §6). The nav entry and
   both reads mirror `transport.read`; the two rate controls mirror `vat.write`,
   the permission `_WRITE` actually declares. A 403 from the server still renders
   through the refusal path — the mirror hides a dead button, it is never the
   control.
10. **The refusal map gains three rows and edits none.** `invalid_period`,
    `invalid_country` and `module_not_enabled` already exist and are reused;
    `excise_country_not_supported`, `invalid_excise_rate` and
    `no_excise_findings` are new and additive (§4.20). Their wording explains the
    fail-CLOSED reason the service gives — a rate for an eighth state would
    assert a regime the spec records nowhere; a zero rate is not "this state
    refunds nothing"; an empty packet looks like a filing and supports nothing.

### Invariants this order must preserve

- **§4.9 — money is a string end to end.** No `Number()`, `parseFloat`,
  `toFixed`, `Math.` or arithmetic operator touches a euro, a rate or a litre
  figure on the new page or in the new helper module. Proven by a source grep in
  the spec and by a fixture carrying `99999999999999.99`, which an IEEE-754
  round-trip would destroy.
- **§4.10 — the server computes every total.** `indicative_excise_eur` per row
  and in total, and `litres` per row and in total, are rendered exactly as
  received. No panel re-derives `litres/1000 × rate`, a subtotal or a difference.
- **§4.14 — no cross-currency sum is presented.** The response's own `currency`
  ("EUR") is rendered beside its figures. WO-91's service reads no currency
  amount at all, so there is nothing to convert and nothing to mislabel; the page
  adds no second currency of its own.
- **§4.19 — an advisory surface must not imply it gates.** The analysis and the
  packet are described as read-only; nothing on the screen is described as
  blocking, approving or filing anything.
- **§4.20 — additive.** One new page, one new pure-helper module, additive types,
  three additive refusal rows, one nav row, one route, one spec file. No existing
  page, type, refusal entry, helper or permission value is changed or removed.
- **R42 / G4.6 — the eligibility non-assertion.** See §6 above; the absences and
  the asserted presence of the statement are the deliverable.
- **R53's third framing** — `legal_framing` ("Indicative / advisory — verify
  before relying") rendered verbatim, never merged with either other framing.
- **R49 / §3.G G1** — `litre_basis` rendered verbatim on the figures panel.
- **§9/§10 — actual vocabulary, nothing invented.** `eligibility`,
  `rate_caveat`, `legal_framing`, `filed_with`, `litre_basis`, `product_group`,
  `skipped_countries` and the field names are the server's own; no label is
  invented for a state the server does not have.

### Database / migration impact

**None.** This order touches no backend file. No table, no column, no migration,
no RLS policy, no permission member.

### Testing requirements

`frontend/e2e/excise.spec.ts`, the `page.route`-mocked live-app harness of
`savings.spec.ts` (synthetic fixtures — fictional entity names and figures; no
Fleet Fuel bytes, no literal shaped like a VAT id, IBAN or registration number).

Monthly figures:
- `figures: each row renders its entity, country, litres, the rate applied and the indicative euro`
- `figures: the rate source is visible on every row (override vs placeholder)`
- `figures: the totals render exactly as the wire strings`
- `figures: a month with no qualifying litres renders the zero-state, not an error`
- `figures: the scope echo renders the period, the product group and the currency`
- `figures: a loading state renders before the API resolves`
- `figures: a 500 renders the error state`
- `figures: invalid_period renders the MONTH instruction, not the claim one`
- `figures: invalid_country renders its sentence, not the slug`

No rate is no finding (the governing presentation rule):
- `no-rate: a country with no configured rate is listed with its litres and no euro`
- `no-rate: the page states that no rate held is not a figure of zero`
- `no-rate: the string €0.00 never describes a country with no rate`

The workbook:
- `packet: the download button issues the packet GET with the current scope`
- `packet: no_excise_findings renders its sentence, not the slug`
- `packet: a refused download does not blank the figures already on screen`

Country rates:
- `rates: every state renders with its resolved rate and whether it is an override`
- `rates: the harvested default is rendered as a placeholder, not as a statutory rate`
- `rates: setting a rate PUTs the typed string and refreshes the table`
- `rates: clearing an override DELETEs for that country`
- `rates: excise_country_not_supported renders its sentence, not the slug`
- `rates: invalid_excise_rate renders its sentence, not the slug`
- `rates: a role without vat.write sees the rates but no write control`

The eligibility non-assertion (the governing constraint):
- `eligibility: the statement is rendered verbatim from the wire on the page`
- `eligibility: the statement is rendered on the figures panel too`
- `eligibility: the rate caveat is rendered verbatim from the wire`
- `eligibility: eligibility_asserted false is stated, not dropped`
- `eligibility: the legal framing is rendered verbatim from the wire`
- `eligibility: the customs addressee is rendered from the wire`
- `eligibility: the new modules carry no entitlement vocabulary` (source scan
  over `pages/Excise.tsx` and `lib/transportExcise.ts`)
- `eligibility: the new wire interfaces carry no entitlement vocabulary` (field
  names only)
- `eligibility: the vocabulary scan detects a seeded violation` (self-test — the
  scan must be able to fail)
- `eligibility: the frontend holds no framing string of its own` (the statement
  text appears in no source file under `src/`)

Permission, module and money:
- `perm: a read-only role sees every figure and no write control` (granted)
- `perm: transport.read gates the nav entry (granted / denied)`
- `perm: a server 403 renders through the refusal path`
- `module: transport off renders the module notice and no nav entry`
- `money: every euro renders from the wire string with no float round-trip` (a
  `99999999999999.99` fixture asserted character-for-character)
- `money: the new modules perform no float arithmetic` (source grep for
  `parseFloat`, `Number(`, `toFixed`, `Math.`)

### Acceptance criteria (verifiable checklist)

- [ ] `/excise` renders two panels; the figures panel renders `eligibility`,
      `rate_caveat`, `legal_framing`, `filed_with` and `litre_basis` verbatim
      from its own response.
- [ ] With `skipped_countries: [{country: "LV", litres: "8100.000", lines: 4}]`
      the page lists LV with its litres, renders no euro for it, and the string
      `€0.00` appears nowhere describing it.
- [ ] Every rate on both panels is rendered beside its source — *verified
      override* or *indicative default* — and the page states that the default is
      a placeholder in a reported band.
- [ ] `PUT /transport/excise/rates` is issued with the typed rate as a STRING;
      `DELETE /transport/excise/rates?country=FR` is issued by the clear control;
      neither control renders for a role without `vat.write`.
- [ ] `excise_country_not_supported`, `invalid_excise_rate` and
      `no_excise_findings` each render a sentence, and their raw slugs appear
      nowhere on the screen.
- [ ] `grep -nEi "\brecover|\bowed\b|\bowes\b|\bclaim|\bdemand|\bdue\b|\bdebt|\bpayable\b|\bentitle"
      frontend/src/pages/Excise.tsx frontend/src/lib/transportExcise.ts` returns
      nothing, and the spec's seeded-violation self-test proves the scan can fail.
- [ ] `grep -nE "parseFloat|Number\(|toFixed|Math\." frontend/src/pages/Excise.tsx
      frontend/src/lib/transportExcise.ts` returns nothing.
- [ ] The eligibility statement text exists in no file under `frontend/src/` —
      it is on the wire only.
- [ ] A `user_free` (READ_ONLY) session sees every figure and no write control; a
      `user` (EMPLOYEE) session sees no nav entry for the surface.
- [ ] `npm run build` (tsc + vite) clean; `npm run test:e2e` green at 209 + the
      new specs with zero pre-existing specs modified.
- [ ] `python -m pytest -q` unchanged from the WO-91 baseline (2273 passed / 10
      skipped — a frontend-only order), with `README.md`'s SPA page count moved
      53 → 54 in the SAME commit that adds the page.

### Rollback strategy

Code revert only — one new page, one new helper module, additive types, three
additive refusal rows, one nav row, one App route, one spec file. No backend
file, no migration, no data. Narrower mitigation: remove the one entry from
`frontend/src/lib/nav.ts` and the destination disappears from the IA while the
route stays reachable by URL; remove the `<Route>` line and it is gone entirely.
The only mutation the page can cause is a rate override, which is an audited
configuration change the server already accepts today and which
`DELETE /transport/excise/rates` reverses to the harvested default.

### Documentation to update

- `README.md` — the scale line, SPA pages 53 → 54 (machine-checked).
- `TODO.md` — the WO-92 row, the M5 cell, the suite line.
- `docs/transport/rules.md` — R42 / G4.6 gains its UI consumer, with the
  eligibility non-assertion named as what the UI preserves and how.
- No ADR is contradicted. ADR-0024's structural authorization is mirrored, not
  re-decided; the frontend mirror stays cosmetic (master-context §6).

### Self-verification block

```bash
cd /home/user/Bid_it/frontend
npm run build                       # tsc --noEmit + vite build
npx playwright test e2e/excise.spec.ts
npm run test:e2e                    # 209 -> 209 + N, zero pre-existing specs touched

# the money-discipline proof (must print nothing)
grep -nE "parseFloat|Number\(|toFixed|Math\." src/pages/Excise.tsx src/lib/transportExcise.ts

# the eligibility proofs (must print nothing)
grep -nEi "\brecover|\bowed\b|\bowes\b|\bclaim|\bdemand|\bdue\b|\bdebt|\bpayable\b|\bentitle" \
  src/pages/Excise.tsx src/lib/transportExcise.ts
grep -rn "asserts NO eligibility" src/ ; echo "^ must be empty — the statement is on the WIRE only"

# every field the page reads must exist on the wire schema
cd /home/user/Bid_it/backend && . .venv/bin/activate
python -c "
from app.schemas.transport_excise import ExciseReportOut, ExciseRatesOut, ExciseCellOut, SkippedCountryOut
for m in (ExciseReportOut, ExciseRatesOut, ExciseCellOut, SkippedCountryOut):
    print(m.__name__, sorted(m.model_fields))
"
python -m pytest -q                 # frontend-only order: unchanged baseline (2273/10)
cd /home/user/Bid_it && python scripts/pii_scan.py --tree
```
