# WO-83 — R41's send-ready artifacts: the evidence packet and the claim letter

> The follow-up slice WO-82 named in its own "out of scope" section: *"**The two
> send-ready ARTIFACTS.** R41 also requires 'an Excel evidence packet and a
> formal PDF claim letter (with a credit/refund demand and a deadline) built
> from the SAME line source', and R53 requires the framing text on each. Both
> are a FOLLOW-UP SLICE of G4.5. This order ships the detection + lifecycle +
> the single `_lines_for`-shaped line source they must both render from, and
> records the artifact slice as the recommended next one."*
>
> `TODO.md`'s M5 row names it as *"**G4.5's two send-ready ARTIFACTS** (the
> Excel evidence packet + the formal PDF claim letter with a 30-day credit/refund
> demand, both off the SAME line source `contract_audit.audit()` already is —
> the recommended next slice)"*. This is that slice.

**WORK ORDER 83 — R41's two send-ready artifacts over the ONE line source
`contract_audit.audit()` already is: `app/services/transport/overcharge_pack.py`
(the Excel evidence packet + the formal PDF claim letter with its 30-day
credit/refund demand) and the two download routes on the existing
`app/api/routes/transport/overcharges.py`. Effort M 3–5d. Priority P1.
Milestone M5 (board G4.5, rules R41 + R53). Depends on: WO-82 (`contract_audit.
audit()` — the line source; `overcharge.py` — the claim-back the artifacts are
rendered FOR), WO-74 (`claim_pack.py` — the binding one-source/two-renderers
precedent), WO-77 (the artifact-download route precedent on
`api/routes/transport/claims.py`), WO-61 (`supplier_entity` — the per-country
supplier legal entity the letter is addressed to).**

### Objective and business value

The gap, with verified evidence. `grep -rn "evidence_workbook\|claim_letter\|
packet" backend/app/services/transport/` returns nothing;
`app/services/transport/contract_audit.py` line 620-624 names the hole in its own
`audit()` docstring — *"This is the ONE line source R41 requires both send-ready
artifacts (the Excel evidence packet and the PDF claim letter — a follow-up slice
of G4.5) to render from"* — and `app/models/transport/overcharge.py`'s closing
paragraph records the composite `(org_id, id)` unique as *"the FK target a future
evidence/letter artifact table would use"*. `docs/transport/rules.md`'s R41 row
ends with **DEFERRED, not skipped**, naming exactly this slice. Today the
platform can detect a contract breach, freeze the euro and track the chase — and
then the operator has to retype the whole thing into Word and Excel by hand to
actually ask the supplier for the money.

Who stops losing money: the FINANCE_MANAGER who signs the fuel-card contracts.
`BA_fleet_fuel.md` §2.4 is emphatic that this analysis — **alone** among the
price analyses — is *"Money the supplier owes"*, backed by *"a claim letter with
a 30-day demand"*, in deliberate contrast to the same-day overpay figure which is
*"negotiation evidence, NOT a contractual claim-back"* (R53). A detected
€8,000 rebate breach that never leaves the screen is worth exactly €0; the
artifacts are the step that converts `detected_eur` into a demand a supplier's
accounts department can act on, and `recovered_total` — the north star — can only
move after one has been sent. Hand-retyping is also where the figure gets
corrupted: a letter quoting a total its own attached evidence does not sum to is
a letter a supplier's credit controller rejects on sight, and R41's acceptance
("both artifacts for the same (supplier, period) show identical lines and
totals") exists precisely to make that impossible.

### The harvested definitions — every element, with its citation

Everything below is quoted from `docs/plan/shared/specs/BA_fleet_fuel.md`. No
Fleet Fuel code, constant table, fixture or datum is read or reproduced.

**1. The two artifacts and the one-source rule** — R41 (line 1413), verbatim:

> *"…with an **Excel evidence packet and a formal PDF claim letter (with a
> credit/refund demand and a deadline) built from the SAME line source**, and a
> `recovered_total` that feeds the north star. Read-only over the analytics."*
> Acceptance: *"Both artifacts for the same (supplier, period) show identical
> lines and totals."*

**2. The deadline is 30 days** — §2.4's `/overcharges` row (line 221), verbatim:

> *"Two artifacts off ONE source: an Excel evidence packet and a **formal PDF
> claim letter with a 30-day credit/refund demand**."*

and §2.4's legal-framing table (line 236), verbatim:

> *"| `contract_audit` / `overcharge` | **"Money the supplier owes"** — a claim
> letter with a 30-day demand |"*

So `DEMAND_DAYS = 30` is harvested, not chosen. The demand is for a **credit or a
refund** — both words are in the spec, and both appear on the letter.

**3. The framing text must ride the artifacts** — R53 (line 1430), verbatim:

> *"**Legal framing per analysis must not be flattened:** contract breach =
> *"money the supplier owes"* (claim letter); same-day overpay = *"negotiation
> evidence, NOT a contractual claim-back"* (printed on every sheet); peer/excise/
> estimate = *"indicative, verify"*."* Acceptance: *"Each workbook carries its
> framing text."*

WO-82 already materialised the framing as `contract_audit.LEGAL_FRAMING` (*"Money
the supplier owes — a contractual claim-back, not negotiation evidence"*) and the
basis as `contract_audit.PRICE_BASIS`. This order does not restate them — it
**prints those two constants** on both artifacts, so R53's "each workbook carries
its framing text" is satisfied by the same string the API already returns.

**4. What a line IS** — §2.5's "Contract audit" row, already implemented by
WO-82 as `contract_audit.Breach`: the flag (`"short discount"` / `"over
ceiling"`), the agreed and actual €/L, the gap, the litres and
`recover_eur = gap × litres`. The artifacts render `Breach` objects; they define
no line shape of their own.

**5. The price basis** — §3.G G1 / R49: *"Prices everywhere are NET EUR/L, FINAL
— VAT excluded, rebates applied. This basis **must be stated on any new report
surface**."* Both artifacts are new report surfaces; both print
`contract_audit.PRICE_BASIS`.

**6. The supplier legal entity** — §3.B / R20-R22, as implemented by WO-61's
`supplier_entity`: a multi-entity fuel-card network issues a **different local
legal entity per country**. The letter is addressed to the registered
per-country entity (marker-only, exact `(supplier, country)` lookup, R21 — never
a fuzzy match, never an invented name).

**7. Our letterhead** — the existing `IssuerProfile` registry (`app/services/
issuer.py`), which `claim_pack.py` already reuses for the claimant entity. §2.8
(C8) records the same registry as the source of *"multiple of the client's own
companies"*. No new company table.

### Documented interpretations (stated because the spec does not settle them)

1. **The artifacts render the LIVE line source and refuse when it no longer
   reproduces the frozen `detected_eur`** (409 `overcharge_evidence_drift`).
   R41 names one line source (`contract_audit.audit()`); WO-82 froze the TOTAL
   of that source onto the claim-back at `open_claim` (*"the euro the demand
   letter quotes"*, `overcharge.py`'s own docstring). Those two can disagree
   after a re-ingest or a corrected term. A demand letter whose enclosed
   evidence does not sum to the euro it demands is exactly the misleading
   document R41's acceptance exists to prevent, so the pack refuses rather than
   choosing one of the two figures — the fail-CLOSED twin of WO-74's
   `claim_totals_drift` (*"a filing artifact must never paper over a corrupted
   frozen set with a header figure its own lines cannot reproduce"*). The live
   figure stays visible at `GET /transport/overcharges/audit`; **re-snapshotting
   a drifted claim-back is a lifecycle edge that was never harvested** and is
   recorded in `docs/DECISIONS-NEEDED.md` rather than invented here (§10).
2. **A terminal claim-back yields the evidence packet but NOT a new demand
   letter** (409 `overcharge_claim_closed`). The packet is EVIDENCE and stays
   reproducible in every state — WO-74's own precedent (*"a WITHDRAWN claim keeps
   its frozen lines … reproducing what WAS filed is audit-trail behaviour, not a
   leak"*). The letter is a **live demand** carrying a 30-day payment deadline;
   generating one for a claim-back already `recovered`, `rejected` or
   `written_off` would assert an open debt that the org's own ledger says is
   closed. The three terminal states are `overcharge.TRANSITIONS`' own empty
   tuples — no new vocabulary.
3. **The letter needs a complete letterhead** (409 `issuer_profile_incomplete`).
   `issuer.REQUIRED_FIELDS` is the EN 16931 / Art. 226 minimum the codebase
   already enforces for an issued invoice; a formal demand from an unnamed,
   unaddressed party is not a formal demand. The packet does not need one — it
   is an attachment, not a letter. Reported with the missing field names, as
   `issuer.missing_fields` already returns them.
4. **The 30-day deadline runs from the letter's generation date** (UTC), printed
   as both "date of this letter" and "payment due by". The spec fixes the
   INTERVAL (30 days) and not an anchor; the only honest anchor for a letter
   that is generated on demand is the letter's own date, which is the date the
   supplier reads. Recorded rather than assumed.
5. **Which of our companies signs it**: the org's DEFAULT issuer, with an
   optional explicit `issuer_id` — the `issued.py` `_render_pdf` resolution
   order (*"the invoice's OWN issuer entity (fallback: default)"*) applied to a
   claim-back that carries no issuer of its own. Resolution is READ-ONLY:
   `issuer.list_issuers` + first row, **never** `issuer.get_or_create`, which
   commits a new row and would make a read-only artifact a writer.
6. **The addressee block lists one entity per country the evidence covers.** A
   claim-back is per (supplier × period) and a month can span countries, each
   with its own registered seller (R20). Listing them all is the truthful
   rendering; a country with no registration prints the supplier code alone and
   nothing is invented (R21 marker-only).
7. **No new PDF or Excel dependency.** `openpyxl` (claim_pack, report_writers)
   and `reportlab` (invoice_pdf, report_writers) are both already pinned in
   `backend/requirements.txt`. The degradation path mirrors
   `invoice_pdf.PdfUnavailable` → `issued.py`'s 503, expressed as an `AppError`
   (503 `pdf_renderer_unavailable`) because a service must not raise
   `HTTPException` (master-context §3) and the route must stay a thin
   controller. **No wkhtmltopdf, no HTML fallback surface** — that was the
   retired system's rendering path, not this codebase's, and adding a second
   renderer would break the one-source guarantee this order exists to prove.

### Scope

**In scope:**
- `backend/app/services/transport/overcharge_pack.py` (**new**) — `_load_packet`
  (the ONE loader), `_evidence_rows` (the ONE table builder both renderers
  consume), `_render_evidence_workbook`, `_render_claim_letter`,
  `build_evidence_packet`, `build_claim_letter`, `DEMAND_DAYS`,
  `PdfRendererUnavailable`-shaped refusal.
- `backend/app/api/routes/transport/overcharges.py` — two GET download routes
  (`/overcharges/{claim_id}/packet`, `/overcharges/{claim_id}/letter`) on the
  router-level `TRANSPORT_READ`, the WO-77 `Response`+`content_disposition`
  +`nosniff` precedent.
- `backend/tests/transport/test_wo83_overcharge_artifacts.py` (**new**) — the
  service matrix: the parsed bytes of both artifacts asserted cell-for-cell
  against hand-computed Decimals, the structural one-source proof, the
  identical-lines-and-totals cross-check, every refusal, formula-injection
  safety, §4.14, org scoping.
- `backend/tests/transport/test_wo83_overcharge_artifact_routes.py` (**new**) —
  the route matrix: content types, `Content-Disposition`, granted/denied roles,
  module-disabled, cross-tenant opaque 404.
- Boards: `TODO.md`, `docs/transport/rules.md` (R41 CLOSES; R53 gains its first
  consumer), `docs/DECISIONS-NEEDED.md` (§13 — re-snapshotting a drifted
  claim-back).

**Out of scope (named, with the board id that owns them):**
- **Any change to detection or the lifecycle.** `contract_audit.py` and
  `overcharge.py` are read as they are (G4.5, shipped in WO-82). In particular
  no re-snapshot edge is added to `TRANSITIONS` — see interpretation 1.
- **Persisting an artifact.** Nothing is stored: no artifact table, no vault
  write, no migration (the WO-74 precedent — artifacts are generated on demand
  and are reproducible from the ledger).
- **Emailing the letter.** `mailer.py` exists, but "send it" is a workflow
  decision (who signs, from which address, with what audit) the spec does not
  settle. Recorded, not built.
- **Any SPA screen.** Service + route only, the WO-79/WO-81/WO-82 shape; the
  analytics UI batch owns the download buttons.
- The other C5 analyses and their artifacts (board **G4.7**), `/excise`
  (**G4.6**), `/value` and `/claim-status` (**G4.4**), the estimate funnel
  (**G4.8**).

### Files to touch

| File | Change |
|---|---|
| `backend/app/services/transport/overcharge_pack.py` | **new** — one loader, one table builder, two renderers |
| `backend/app/api/routes/transport/overcharges.py` | two GET download routes |
| `backend/tests/transport/test_wo83_overcharge_artifacts.py` | **new** — service/artifact matrix |
| `backend/tests/transport/test_wo83_overcharge_artifact_routes.py` | **new** — route matrix |
| `docs/transport/rules.md` | R41 row: DEFERRED → CLOSED; R53 row gains its consumer |
| `docs/DECISIONS-NEEDED.md` | §13 — re-snapshotting a drifted claim-back |
| `TODO.md` | WO-83 row, M5 cell, suite line |
| `README.md` | only if a pinned count moves (verified, not assumed) |

> Count check performed BEFORE handover: `test_docs_truth.py` counts
> `app/services/*.py` and `app/api/routes/*.py` **non-recursively**, so a new
> module under `app/services/transport/` and a route added to an existing
> transport route module move NO pinned figure. No new table, no migration ⇒
> the pinned table count (81) and revision count are unchanged.

### Implementation guidance (execution order)

1. **`_load_packet(db, org_id, claim_id, *, issuer_id=None)`** — the ONE loader,
   every refusal fail-CLOSED, in this order: the `transport` module entitlement
   (ADR-P3 rule 3) → `overcharge.get_claim` (org-scoped, opaque 404) →
   `contract_audit.audit(period=claim.period, supplier=claim.supplier)` (THE line
   source) → 422 `no_overcharge_detected` when it found nothing → 409
   `overcharge_evidence_drift` when `result.recover_eur != q2(claim.detected_eur)`
   → the per-country supplier registrations (marker-only) → our letterhead
   (read-only, may be incomplete; the LETTER enforces completeness). The packet
   carries `contract_audit.Breach` objects **unchanged** — not a copy, not a
   remapped shape.
2. **`_evidence_rows(packet)`** — the ONE table builder: header tuple + one
   typed row per breach + the TOTAL row. Both renderers consume its output and
   neither is allowed to read `packet.lines` directly (a structural test pins
   that). Money stays `Decimal`; the renderers differ only in how they FORMAT it
   (openpyxl writes the Decimal into a numeric cell; reportlab formats
   `f"{v:,.2f}"`).
3. **`_render_evidence_workbook(packet)`** — openpyxl, two sheets, the
   `claim_pack._render_workbook` shape: `Claim-back` (the header block including
   `PRICE_BASIS` and `LEGAL_FRAMING` — R53) and `Evidence` (the shared table with
   its bold TOTAL row). Free-text cells go through the ONE shared
   `core.csv_safety.sanitize_cell`; numeric money cells deliberately do NOT
   (`csv_safety`'s own rule — a leading `-` is a negative amount).
4. **`_render_claim_letter(packet)`** — reportlab, the `report_writers.to_pdf`
   shape: our letterhead, the letter date and the +30-day due date, the
   addressee block (supplier + the registered per-country legal entities), the
   subject, the R53 framing sentence, the SAME table, the credit/refund demand
   naming the total and the deadline, and the `PRICE_BASIS` footer. Paragraph
   text is escaped (`xml.sax.saxutils.escape`) because reportlab parses a mini
   markup in `Paragraph`; Table cells take plain strings.
5. **Money**: every figure comes from `Breach.recover_eur` (already `q2`-ed by
   WO-82) and the total is `q2(Σ)` — recomputed by the renderer path and
   asserted equal to both `result.recover_eur` and the frozen `detected_eur`
   (§4.10). No float anywhere; a `grep -n "float"` over the new module returns
   nothing, asserted by a test.
6. **Routes**: two GETs, router-level `TRANSPORT_READ` (generation is read-only
   analytics; no write verb, so no `VAT_WRITE` override and **no new permission**
   — §10). `Response` + `content_disposition(...)` + `X-Content-Type-Options:
   nosniff`, exactly the WO-77 `download_workbook`/`download_evidence_pack`
   shape. No `db.commit()` — nothing is written.

### Invariants this order must preserve

- **§4.9 Decimal-only.** Every euro is a `Decimal` from `Breach`/`q2`; the PDF
  formats with `f"{Decimal:,.2f}"`, never `float()`. Structural test.
- **§4.10 the server recomputes.** The renderer path sums the lines and refuses
  when the frozen header figure disagrees (`overcharge_evidence_drift`). Nothing
  client-supplied reaches either artifact except the claim id and the optional
  issuer id, both org-scoped.
- **§4.14 no cross-currency sums.** The artifacts total in **EUR only**; a
  line's own document currency is printed as PROVENANCE in its own column and is
  never summed. `net_local`/`vat_local`/`gross_local` are never read (asserted
  by name over the new module). A supplier month spanning EUR and PLN produces
  one correct EUR total, proven by a test.
- **§4.16 audit old→new.** Nothing mutates, so nothing is audited — the
  `claim_pack`/`erp_export`/`report_writers` precedent (audit is required on
  *mutations*). A test asserts no `AuditEvent` is written by either builder.
- **§4.19 advisory never blocks or mutates.** Generating either artifact writes
  no row, changes no claim-back status, no fuel transaction, no VAT claim, no
  lock. A test compares the claim-back and every fuel-transaction column before
  and after both builds.
- **§4.20 additive.** One new service module, two new routes. No existing
  behaviour changes; no schema, no migration, no permission member.
- **§4.4 tenancy.** The claim id is fetched through `overcharge.get_claim`
  (org-scoped) — a cross-tenant id is an opaque 404, never 403; an issuer id
  from another org is likewise 404.
- **§9 actual vocabulary.** The flags, the states, the framing string and the
  basis string are WO-82's harvested constants, printed verbatim. The
  30-day interval is §2.4's own number.
- **§10 nothing invented.** Every artifact element traces to a citation above;
  the four readings the spec does not settle are in the interpretations list,
  and the missing re-snapshot edge is recorded in DECISIONS-NEEDED rather than
  guessed.

### Database / migration impact

**None.** No table, no column, no migration — artifacts are generated on demand
and never persisted (the WO-74 precedent). The pinned table count (81) and the
Alembic revision count are unchanged.

### Testing requirements

`backend/tests/transport/test_wo83_overcharge_artifacts.py`
- `test_wo83_the_evidence_packet_renders_every_line_cell_for_cell` (openpyxl
  parse, hand-computed Decimals)
- `test_wo83_the_evidence_packet_total_row_equals_the_hand_computed_sum`
- `test_wo83_the_claim_letter_prints_the_same_lines_and_the_same_total` (pypdf
  text extraction)
- `test_wo83_both_artifacts_show_identical_lines_and_totals` (R41's acceptance,
  cross-parsed)
- `test_wo83_both_renderers_consume_the_one_shared_row_builder` (structural)
- `test_wo83_the_renderers_cannot_query_the_database` (structural — both are
  sync functions over a loaded packet)
- `test_wo83_the_letter_carries_the_thirty_day_credit_or_refund_demand`
- `test_wo83_both_artifacts_carry_the_r53_framing_and_the_price_basis`
- `test_wo83_the_letter_is_addressed_to_the_registered_per_country_entity`
- `test_wo83_an_unregistered_country_prints_the_supplier_code_and_invents_nothing`
- `test_wo83_a_claim_with_no_live_findings_refuses_no_overcharge_detected`
- `test_wo83_a_drifted_detected_total_refuses_overcharge_evidence_drift`
- `test_wo83_a_terminal_claim_back_refuses_a_letter_but_still_yields_the_packet`
- `test_wo83_an_incomplete_letterhead_refuses_the_letter_naming_the_fields`
- `test_wo83_generating_both_artifacts_mutates_nothing_and_audits_nothing`
- `test_wo83_a_station_name_starting_with_equals_is_neutralised_in_the_packet`
- `test_wo83_mixed_currency_lines_total_in_eur_and_no_local_column_is_read`
- `test_wo83_the_module_is_float_free`
- `test_wo83_a_cross_tenant_claim_id_is_an_opaque_404`
- `test_wo83_an_issuer_from_another_org_is_an_opaque_404`

`backend/tests/transport/test_wo83_overcharge_artifact_routes.py`
- `test_wo83_the_packet_downloads_as_xlsx_with_a_filename`
- `test_wo83_the_letter_downloads_as_pdf_with_a_filename`
- `test_wo83_accountant_is_granted_and_employee_is_denied` (both artifacts)
- `test_wo83_an_auditor_can_download_both` (TRANSPORT_READ, no VAT_WRITE needed
  — generation is a read)
- `test_wo83_module_disabled_refuses_403_module_not_enabled`
- `test_wo83_a_cross_tenant_claim_id_is_an_opaque_404_over_http`
- `test_wo83_a_drifted_claim_refuses_409_over_http`
- `test_wo83_an_unknown_claim_id_is_404`

### Acceptance criteria (verifiable checklist)

- [ ] For a claim-back over one 1,000 L line whose applied discount is €0.05/L
      against an agreed €0.20/L, the packet's `Evidence` sheet has exactly one
      data row with `Gap €/L = 0.1500`, `Litres = 1000.000` and
      `Recoverable EUR = 150.00`, and a TOTAL row of `150.00`.
- [ ] The claim letter's extracted text contains the same `150.00` total, the
      same station, the same flag string `short discount`, and the sentence
      demanding a credit note or refund **within 30 days**, with the due date
      equal to the letter date + 30 days.
- [ ] `test_wo83_both_artifacts_show_identical_lines_and_totals` parses BOTH
      artifacts and asserts the row set and total are equal — R41's acceptance.
- [ ] Both artifacts print `contract_audit.LEGAL_FRAMING` and
      `contract_audit.PRICE_BASIS` verbatim (R53 / §3.G G1).
- [ ] `GET /transport/overcharges/{id}/packet` returns
      `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` with a
      `Content-Disposition` naming a `.xlsx`; `…/letter` returns
      `application/pdf` naming a `.pdf`; both carry `X-Content-Type-Options:
      nosniff`.
- [ ] Tampering `detected_eur` to a figure the live lines cannot reproduce makes
      BOTH artifacts refuse 409 `overcharge_evidence_drift`.
- [ ] A `recovered` claim-back returns 409 `overcharge_claim_closed` on the
      letter and 200 with a valid workbook on the packet.
- [ ] An org with no complete issuer profile gets 409 `issuer_profile_incomplete`
      on the letter, with the missing field names in the message.
- [ ] A station named `=cmd|' /C calc'!A0` is written to the packet with a
      leading `'` (neutralised) and the money cells are unquoted numbers.
- [ ] An org whose lines span EUR and PLN produces one EUR total equal to the
      hand-computed Decimal sum, and `grep -n "net_local\|vat_local\|gross_local"
      app/services/transport/overcharge_pack.py` returns nothing.
- [ ] EMPLOYEE gets 403 on both downloads; ACCOUNTANT and AUDITOR get 200.
- [ ] Tenant B's claim id passed to a tenant-A session returns 404, never 403.
- [ ] `python -m pytest -q` is green at 1971 + the new tests, 10 skipped, zero
      pre-existing tests modified.

### Rollback strategy

Pure code revert — there is no migration and nothing is persisted, so a revert
loses only the ability to download the two artifacts; every claim-back, term,
figure and audit row is untouched. Narrower mitigation without a revert: delete
the two route handlers from `overcharges.py` — the surface disappears while the
service stays intact and importable.

### Documentation to update

- `docs/transport/rules.md` — **R41**'s row: the DEFERRED artifact clause becomes
  SHIPPED (with the one-source proof and the refusal set); **R53**'s row gains
  its first real consumer (the framing text is now printed, not just returned).
- `TODO.md` — the WO-83 row, the M5 cell (G4.5 fully closed), the suite line.
- `docs/DECISIONS-NEEDED.md` — §13: re-snapshotting a drifted claim-back (the
  harvested chain has no such edge; the artifacts refuse rather than choose).
- `README.md` — only if a pinned count moves (verified above: it does not).
- No ADR is contradicted; ADR-P3's rules 1/2/3/5 hold (transport-local service,
  org-scoped resolution, entitlement inside the service, existing permissions
  only).

### Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/transport/test_wo83_overcharge_artifacts.py \
                 tests/transport/test_wo83_overcharge_artifact_routes.py -q
python -m pytest tests/transport/test_wo82_contract_audit.py \
                 tests/transport/test_wo82_overcharge_lifecycle.py \
                 tests/transport/test_wo82_overcharge_routes.py -q   # WO-82 untouched
python -m pytest tests/test_authz_coverage.py tests/test_boundaries.py \
                 tests/test_docs_truth.py -q
python -m pytest -q                                   # full baseline: 1971 -> 1971+N
python -c "
import inspect
import app.services.transport.overcharge_pack as p
src = inspect.getsource(p)
assert 'float' not in src, 'float in a money path'
for col in ('net_local', 'vat_local', 'gross_local'):
    assert col not in src, f'{col} read in a EUR-only artifact (§4.14)'
assert 'get_or_create' not in src, 'a read-only artifact must not create an issuer row'
for fn in (p._render_evidence_workbook, p._render_claim_letter):
    fsrc = inspect.getsource(fn)
    assert '_evidence_rows(' in fsrc, f'{fn.__name__} does not use the ONE table builder'
    assert 'db' not in inspect.signature(fn).parameters
print('overcharge_pack.py: one source, two renderers, EUR-only, float-free, writes nothing')
"
grep -n "packet\|letter" app/api/routes/transport/overcharges.py | head
python ../scripts/pii_scan.py --tree
```
