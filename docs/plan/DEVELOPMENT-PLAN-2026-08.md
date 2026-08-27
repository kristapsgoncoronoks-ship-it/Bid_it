# Development plan — from recorded ideas to work orders (2026-08-23)

> The queue below sequences everything currently designed and unbuilt:
> the phase-5 lifecycle remainder, the work-planning calendar
> (`docs/design/work-calendar.md`), the researched task module
> (`docs/design/tasks-module-research.md`), and supplier cost analytics
> (`docs/design/supplier-cost-analytics.md`). Order optimises for (a) making
> the product DAILY-active for a pilot customer, (b) closing the lifecycle
> loop end to end, (c) never blocking on an owner decision — decision-gated
> items sit outside the committed path.

## Standing gates (every work order)

Three-layer tenancy + parity probe in the same commit for every new tenant
table · services never commit / routes commit mutation+audit together ·
industry-neutral copy (guard list, e2e-greped) · ruff/mypy/docs-truth ·
seeded-violation checks for new tests · full backend regression + full
Playwright before certify · runbook row from actual output · push BOTH
branches.

## The committed queue

**WO-A — Calendar phase A: assignments. ✅ SHIPPED 2026-08-23.**
`project_assignments` (org, project composite-FK, assignee, starts/ends,
status planned|confirmed|done|cancelled, note) + calendar screen
(month/week, filter person/project) + "My work" list. Employees see their
own; planning needs manager rights; overlaps are advisory warnings.
Estimated: one session incl. tests/e2e. Unlocks: B, B2, B3, D-hook, tasks
integration.

**WO-B — Calendar phase B + B2: reminders and phone sync. ✅ SHIPPED 2026-08-23.**
Assign/change/cancel notifications + configurable reminder N hours before
(jobs+mailer rails, per-org default, per-assignment override) and the ICS
layer: per-user revocable feed token + .ics download — Google/Apple/
Microsoft subscribe to US, zero external calls. Estimated: one session.

**WO-C — "Next actions" v1 (the researched task module). ✅ SHIPPED 2026-08-23.**
One surface, four generators, everything self-completing/expiring: offer
follow-up nudge (N days in `sent`, default 3, ≤2 nudges, one-click send);
dunning surfaced as chase items (no second engine); recurring deadline
templates (name + recurrence + lead-time, confirm-style materialization —
"prepare VAT report"); lifecycle nudges (capture backlog, expiring offers,
all-assignments-done → suggest acceptance once WO-D lands). Rendered on
dashboard + owning record (+ calendar layer). Estimated: 1–2 sessions.
Explicitly OUT per research: workflow builder, freeform lists,
dependencies/custom fields, unbounded sequences.

**WO-D — Lifecycle close: acceptance & handover + adjustable final invoice. ✅ SHIPPED 2026-08-23.**
Acceptance as a project state between work-done and closed (generated from
the shipped acceptance template, countersign seam left for e-sign);
final invoice = plan remainder ± labelled adjustment lines (sign-flip →
credit note via existing machinery), linked-not-gated on acceptance with a
per-org gate toggle. Completes the owner's original loop end to end.
Estimated: 1–2 sessions. Includes the small `offer_prefix` settings UI.

**WO-E — Client-facing arrival notices (calendar B3, email first). ✅ SHIPPED 2026-08-24.**
48h-before notice to the customer contact (24/48/72 per org, per-assignment
override), idempotent sent-marker, quiet hours (20:00–07:00 UTC, deferred to
morning, never past the work). Brought the `projects.customer_id` link
(WO-H/WO-I build on it) and the org-level reminder default on the same
settings surface. Email only — SMS stays decision-gated (provider +
who-pays).

**WO-F — Job photos (calendar phase C). ✅ SHIPPED 2026-08-24.**
Mobile-friendly capture on the project page (camera input on phones) →
content-addressed storage → `project_documents` kind `photo`, thumbnails
served through the same authenticated download route (no second surface).
Photos are the only kind with a server-enforced image check (declared type
AND magic bytes must agree); EXIF kept as shot. They appear in the WO-D
acceptance document picker — a photo of the signed sheet is valid evidence.

**WO-G — Supplier cost analytics phase 1 (then 2). Phase 1 ✅ SHIPPED 2026-08-24.**
Phase 1: per supplier × item price history from invoice lines (item =
normalised description; qty-weighted trailing baseline; single-currency
C1.7 scope), change detection with top movers, KPI cards, per-item
price-history graph — `/supplier-costs` page, read models only, zero new
tables/migrations, current engine (Postgres; settled in design doc §2b).
Phase 2 ✅ SHIPPED 2026-08-25: `supplier_agreed_prices` tenant table (FORCE
RLS + parity probe in the same commit) — per supplier × item agreed unit
price with a validity window, item identity = phase 1's normalised
description; `agreed_price_exceeded` in the ONE validation rule registry
(advisory finding on capture); the AP submit gate refuses an overpriced
invoice only for orgs that opted into `organizations.
overcharge_block_enabled` (Settings → "Block overcharges" — open question 2
resolved as advisory-by-default); the overcharge worklist on
`/supplier-costs` prices the damage ((paid − agreed) × qty). The owner's
"cost control" half, complete. Phase 3 (external price data) stays
decision-gated behind ADR-0027.

**WO-H — CRM light (researched 2026-08-23, `docs/design/crm-module-research.md`). ✅ SHIPPED 2026-08-25.**
Customer page (notes + DERIVED activity timeline over existing audited
events), `customers.lifecycle` column (NO lead entity — documented
anti-pattern), `/pipeline` kanban over the existing offer statuses with
days-in-stage from new `offer_stage_events` rows (stamped on create/
transition/revise — cheap now, impossible to reconstruct later) and the
staleness flag on quiet sent offers. Two small tenant tables, full
three-layer tenancy + parity probes in the same commit (100 tables,
93-model registry). Twenty et al. remain design references only —
embedding was researched and rejected.

**WO-I — Client portal ("module for clients"). ✅ SHIPPED 2026-08-25.**
Magic-link access (revocable per-customer tokens; regenerate kills the old
URL): the public /portal/{token} page shows the customer's offers with
Accept/Decline (rides the ONE existing transition machinery, audited with
the portal actor, plan-seeding intact), invoices with status (drafts
hidden), and per-document-shared project files (OFF by default). Opening
the portal stamps the quote-viewed signal onto the CRM timeline. New
tenant table customer_portal_tokens with FORCE RLS + parity probe in the
same commit; three PUBLIC_ROUTES entries with reasons. Pay-in-portal
awaits the payment-rail decision; e-sign remains the later seam.

**WO-J — Admin automation rules ✅ SHIPPED 2026-08-25 (researched 2026-08-23,
`docs/design/workflow-builder-research.md`).**
Shipped exactly as designed: three tenant tables (`automation_rules`,
`automation_rule_versions`, `automation_runs`) with FORCE RLS + parity
probes in the same commit; safe JSON-Logic-subset evaluator (closed
operator set, validate-at-save, lookup-only `{{var}}` templating — never
template code); five sweep-based matchers (stale offer, overdue invoice,
accepted work, all-visits-done, dormant customer) derived from queries
like the Next-actions generators; three actions on existing rails
(owner email, customer email, CRM note); fire policies once-per-record/
cooldown/every-time over the run ledger; MAX_FIRES_PER_SWEEP=25 with
visible `throttled` rows; draft→publish immutable versions with revert-as-
new-version; dry-run with zero side effects; daily `automation.sweep` in
DAILY_KINDS; `/automation` builder page (condition rows composing the
subset, ordered action cards, run log) gated admin like the backend's
SETTINGS_MANAGE. Original scope note kept below for the record.
Platform-admin trigger-condition-action rules on the existing job-queue/
audit/mailer rails: closed trigger enum over domain events, JSON Logic (or
zen-engine) conditions, fixed action catalog, fire-once-per-record default,
loop prevention + execution caps, draft→publish with immutable versions,
runs log, dry-run. Form builder UI (no canvas). Embedding a ready platform
was researched and rejected — n8n on license (SUL, paid Embed), Windmill/
Kestra/Temporal/Trigger.dev on 4 GB-VPS footprint, Activepieces on paid
multi-tenant features, Node-RED on tenancy. No embedded scripting, ever.
Estimated: 2 sessions. Sits last because E/H/I each enrich the trigger/
action catalog; pull earlier if multiple WOs start hand-rolling bespoke
"when X do Y" settings.

## Committed queue — second arc (planned 2026-08-26)

The first arc (WO-A…WO-J + WO-G phase 2) is fully shipped and certified;
`main` carries it as of `04d0057`. This arc is built from the 2026-08-26
backlog sweep: every item below was verified OPEN against the code (not
just unchecked in TODO.md), needs NO owner decision, and is sequenced
legal-exposure-first.

**WO-K — AR legal-compliance trio. ✅ SHIPPED 2026-08-26.**
Shipped exactly as scoped: `corrected_invoice_number` snapshot at credit-note
creation (backfilled in-migration), the PDF's labelled CORRECTS column, CII
BT-25 `InvoiceReferencedDocument`; `services/late_interest.py` advisory
computation on the issued detail + the reference-rate setting on the dunning
screen (org column, `/settings/late-interest`); MT940 in
`bank_statement.parse` with a filesec kind + content check, and the format
message now names everything supported. Original scope kept below.
(1) Art. 219: a credit note must print the corrected invoice's number —
today `invoice_pdf.py` renders no reference; add the link field where the
credit-note flow already knows its source invoice, render it on the PDF
AND the EN 16931 CII XML, refuse issuing a credit note with no reference.
(2) Statutory late-payment interest (Dir. 2011/7/EU): when no contractual
`penalty_rate` is set, compute the statutory default — ECB reference rate
+ 8 pp, plus the Art. 6 €40 flat recovery cost — as an ADVISORY figure on
overdue issued invoices (AR aging + invoice detail + dunning context);
contractual rate always overrides; the existing penalty-invoicing
machinery can consume it, never auto-issues. (3) MT940 bank-statement
import beside CSV/camt.053, and fix the unsupported-format message that
omits XML (which IS supported). Estimated: 1–2 sessions.

**WO-L — Transport claim hygiene (the 2026-08-08 §11/§12/§13 smalls). ✅ SHIPPED 2026-08-26.**
Shipped exactly as decided: `ignored` joins the overcharge chain (detected/
packaged → ignored, reason REQUIRED and audited; ignored → detected
reinstate; a sent demand keeps the harvested three outcomes; the WO-82 pin
updated to the sanctioned edge set); UNMATCHED claim lines carry their
distinct suppliers (set at build time, wire + SPA hint, never filable);
`transport/decision.py` is the first writer past `submitted` —
approved/rejected/partial, with partial stamping `rejected_at` on the named
frozen lines and recomputing vat/fee on the surviving base at the FROZEN
rate through `fee.compute_fee` (the documented seam, minimum floor
included). One migration (CHECK widened + two line columns). Original
scope kept below.
(1) §12: an explicit, audited `ignored` outcome on a detected overcharge
claim-back — reversible, with a reason, so an operator's decision to drop
one is a recorded event instead of an eternally-open row. (2) §11: the
supplier candidate list on an `UNMATCHED` claim line (small, fully
specified). (3) §13: partial rejection of a VAT claim — `status.py` names
the decision-received/rejected transitions as unbuilt; implement
amount-level partial outcomes with the fee interplay `fee.py`'s
documented seam already reserves. Estimated: 1–2 sessions.

**WO-M — Recycle-bin extension + the invoice→VAT-claim link. ✅ SHIPPED 2026-08-26.**
Shipped: `deleted_at/by` on expense reports, inbox transactions, recurring
schedules and issued attachments (SOFT_DELETE_MODELS + one migration);
deletes stamp + audit instead of destroy; the generic Trash listing/restore
(`/invoices/trash/other`, INVOICE_RESTORE-gated) and the daily BIN_PURGE now
empties both bins with what-was-destroyed audit meta. The claim link is
real: `queries.claims_backed_by_invoice` (frozen line + submitted/approved/
paid/rejected claim) makes deletion a HARD 409 `invoice_backs_filed_claim`
before any consent, and the bulk path skips with the same words; a
withdrawn claim releases the invoice. Original scope kept below.
Owner-approved 2026-08-15: extend the soft-delete bin (binned_at pattern,
30-day purge already in DAILY_KINDS) to expenses, expense reports,
receipts and standalone documents. Then the real invoice→VAT-claim link,
and REFUSE deleting an invoice that backs a claim — today nothing knows.
Estimated: 1–2 sessions.

**WO-N — Accessibility & form-label pass. ✅ SHIPPED 2026-08-26.**
`Login.tsx` labels are not programmatically associated (zero `htmlFor`);
sweep the auth screens first, then the high-traffic forms; visible focus
states; Playwright assertions so the association cannot silently regress.
Estimated: 1 session.

**WO-O — First-load performance. ✅ SHIPPED 2026-08-26.**
The SPA's first-load payload roughly doubled under Vite 8 (recorded
2026-08-08, still open). Measure, re-split (manualChunks / route-level),
and land a bundle-size tripwire in CI so the next regression fails a
check instead of a user. Estimated: 1 session.

**WO-P — Guided onboarding checklist (R19). ✅ SHIPPED 2026-08-26.**
A derived, dismissible setup card (issuer profile → modules → team →
first customer/invoice) computed from state that already exists — no new
tables. Closes the last "empty workspace, now what?" gap the demo seed
papers over. Estimated: 1 session.
Shipped as planned: services/onboarding.py derives the five steps, the one
persisted bit is `organizations.onboarding_dismissed_at` (e8f0a2b4c6d8),
dismissal is SETTINGS_MANAGE-gated + audited, the dashboard card links each
undone step to its screen. WO-N shipped as scripts/check-labels.mjs (CI) +
htmlFor/id across 19 pages + getByLabel e2e; WO-O shipped as the rolldown
codeSplitting fix (first load 773→415 kB) + scripts/check-bundle.mjs (CI).

**WO-Q — Supplier reliability rating (§12 criteria; DESIGN-FIRST). ✅ SHIPPED 2026-08-27** (both deliverables).
The owner's criteria are recorded (overcharges, exchange-rate treatment,
lines charged that were never agreed) but TODO.md itself says it needs a
design pass: each criterion's contribution, the window, and a
presentation that reads as EVIDENCE rather than a verdict on a
counterparty. Deliverable 1 is the design doc; code only after.
Estimated: 2 sessions including design.

**WO-R — Load/perf test harness (R15). ✅ SHIPPED 2026-08-27.**
A repeatable load harness (k6 or locust script over the seeded demo
workspace, worker-tier only), a recorded baseline, and the p95 budgets
the index-strategy rule keeps referring to. Estimated: 1 session.

*What actually shipped, and the two places it departed from the order above:*

1. **Neither k6 nor locust.** k6 is a Go binary absent from this toolchain that
   cannot drive an ASGI app in-process, so every run would need a second live
   environment to keep true; locust drags `gevent`, `flask` and `werkzeug` into
   a backend that has added no dependency without cause all arc. The harness is
   `httpx` + `asyncio` — both already in `requirements.txt` and already driving
   every API test — so it measures exactly the stack the tests measure. Recorded
   here rather than made quietly.
2. **Not "over the seeded demo workspace".** The demo seed is a fixed size, and
   a fixed size cannot answer a question about scale. The harness seeds its own
   workspace at a size given on the command line, which is what made the
   400 → 20,000 curve possible.

And the finding, which is the part worth carrying forward: **§3.5's specific
fear does not reproduce.** `expected_rebate`'s whole-history median walk grew
17× across 50× of data — sub-linear. The read that grows fastest is the
analytics `explore` group-by (24.9× across the same range), superlinear but not
quadratic, and it is the one to index or roll up first if a workspace grows
another order of magnitude. The gate is a **growth ratio**, not a millisecond
budget, so a slower CI runner does not move the verdict; it was proven to bite
with a seeded `O(n²)` before being trusted. Baseline and the machine it came
from: `docs/perf/BASELINE-2026-08-27.md`. **Concurrency is NOT covered** — every
figure is one sequential caller — and that third of R15 stays open.

Not in this queue (stale, verified done): CI runners (alive since
2026-08-25), `main` unbuildable/behind (merged current 2026-08-26),
runbook regeneration (maintained), demo data (shipped). Decision-gated
items stay fenced below.

## Owner-side track (parallel, not code)

1. Finish the in-flight VPS deploy (`./scripts/vps-deploy.sh`).
2. ~~Repo public~~ ✅ DONE 2026-08-25 → Actions free → **CI run #465 green
   at `46d3167`** (first verdict since 2026-08-12).
3. CI alive ✅ → set 3 deploy secrets + DEPLOY_ENABLED → merge-to-main
   deploys. (Also recommended now public: secret scanning + push
   protection in repo Settings → Security.)
4. After that: GHCR prebuilt images (adds a CI job; README count bump).
5. Still the highest-value validation item: one real redacted supplier
   statement through the system.

## Decision-gated (outside the committed path)

SMS provider + who pays per message (B3/SMS) · e-sign provider (acceptance
countersign, phase 3) · external price-data module incl. Scrapling stealth
stance (cost analytics phase 3) · two-way calendar sync (Google/Graph/
CalDAV) · recovered-VAT P&L line, budget-vs-actual, profitability export
(phase-3 backlog) · recycle-bin extensions + invoice→VAT-claim link.

## Sequencing rationale, in one paragraph

A pilot customer opens the app daily for the calendar and Next actions —
those two (WO-A→C) convert InvoiceIQ from a bookkeeping tool into an
operating tool, and the research says exactly those loops (fast follow-up,
visible chasing) carry independently-proven value. WO-D then closes the
owner's full arc (offer → … → acceptance → final invoice → frozen P&L),
which is the demo that sells the product. E/F/G add reach (customer
notices, photos, analytics) without blocking anything upstream. Everything
needing an owner decision is fenced off so the queue never stalls.

---

# ARC 3 — planned 2026-08-27, from a VERIFIED backlog sweep

> **How this queue was built, and why it is trustworthy.** Every planning
> doc in the repo (`TODO.md` in three slices, `BACKLOG.md`, this plan,
> `DECISIONS-NEEDED.md`, `RELEASE-READINESS.md`, `M0-exit-gate.md`, the
> `docs/audit/` + `docs/security/` sets, `docs/transport/rules.md`,
> `docs/product/`, `docs/design/`) was swept for open work: 160 candidate
> items. Each was then **adversarially verified against the code** — docs
> rot in BOTH directions, and this repo shipped twenty work orders in a
> fortnight, so "open" is a claim to be checked, not a fact. 126 items
> reached a verdict before the sweep's budget ran out: **61 open/partial,
> 50 owner-gated, 15 stale stamps** (doc entries claiming open work that
> is already shipped). The queue below is drawn only from items with
> cited code evidence; the un-verified remainder (~34 low-signal
> duplicates) is not planned around.
>
> Ahead of this queue and already committed: **WO-Q deliverable 2** (the
> reliability service/route/UI, built to `docs/design/supplier-reliability-rating.md`)
> and **WO-R** (the R15 load/perf harness). Both re-verified still-open by
> this sweep.

## The queue

**WO-S — Transport statement intake: the front door. ✅ SHIPPED 2026-08-27.**
Built as ordered, with two corrections to the order itself. First, the count:
**seven** parsers were unreachable, not five — Moeve (WO-68) and BP (WO-69)
shipped after the sentence below was written. Second, "parser selection" is not
a route concern and deliberately did not become one: `fuel_card_parser.select`
detects the network from the file's own marker line and raises when none
matches, so a `network` field would only have handed an operator a way to have
E100 bytes parsed as Eurowag. The route exposes `GET /networks` as INFORMATION
instead, read from the live registry so it cannot advertise a parser that does
not exist. The entity is resolved before the file is read, so a cross-tenant id
is an opaque 404 rather than a parse performed on a stranger's behalf. One
statement-level audit event, keyed on the bytes' SHA-256, closes the gap the
per-row events leave: a replayed statement writes no row and would otherwise
leave no trace that anyone uploaded it. 16 route tests, 4 e2e specs.

*The original order, for the record:* `statement_ingest.py`
is service-only: no route imports `ingest_statement` (grep over
`app/api`), no transport route accepts `UploadFile`, and no SPA page
matches fuel/statement. Five shipped parsers (Eurowag, E100, Q8, DKV, TFC
— WO-62…65) and the whole nine-rule capture gate are therefore
**unreachable from the product**; a statement can only enter by a Python
call. Build the multipart route (parser selection, `filesec` kind + content
check like every other upload path), the SPA upload page with the capture
review result rendered, and the refusal vocabulary. Effort: medium.
Certification: the existing ingest tests plus route-level tests for parser
mis-selection and a refused statement; an e2e that uploads a synthetic
statement and reads its warnings; seeded violation on the content check.

**WO-T — Claim lifecycle: the payment leg. ✅ SHIPPED 2026-08-27.**
Built as ordered, with one correction. The certification line asked for *"the
WO-82 edge-set pin extended to the new sanctioned edge"* — but that pin is
about a **different table**: `test_wo82_overcharge_lifecycle.py` pins the
overcharge claim-back chain (`vat_overcharge_claims`), while the refund-claim
lifecycle (`vat_refund_claims`) had **no equivalent pin at all**. Extending the
wrong one would have looked like coverage and been none, so the missing pin was
written instead: `test_wo_t_claim_edge_set.py` scans the transport service
package for every `.status = "<literal>"` assignment and asserts the (module,
destination) pairs equal a declared table of five sanctioned edges. It matches
on the attribute rather than the variable, so a writer that renames its local
is still caught, and it carries its own seeded-violation self-test.

Two design calls worth keeping: `submitted_date` is stamped AT the transition
and the signature offers no way to supply one, because a back-dated filing is
not a fact this surface gets to assert; and `paid_amount` is REQUIRED and never
derived from the approved base — a member state does not always pay what it
approved, and defaulting the field would quietly assert that it did. The audit
event carries the variance and the days-to-refund the interval finally makes
real. `recovery.median_days_to_refund`, `null` in every workspace since WO-81,
now computes; its DEVIATIONS note is corrected in place rather than left
standing. 16 service + route tests, 3 pin tests, 4 e2e specs.

*The original order, for the record:* Nothing anywhere writes
`status='paid'`, `paid_date` or `submitted_date` (grepped across services
and routes: `lock.submit_claim` flips status only; WO-L's `decision.py`
stamps `decision_date`/`approved_date`). So `recovery.py:149`'s
median-days-to-refund ships **null forever** and the booked-cash north star
never closes its own loop. Stamp `submitted_date` at the submit
transition, add the `approved → paid` transition with `paid_date` and its
audited actor, and let the recovery median compute. Effort: medium.
Certification: transition tests incl. the refusals (paying an unapproved
claim, double-paying), a median that goes from null to a hand-computed
figure, the WO-82 edge-set pin extended to the new sanctioned edge.

**WO-U — Reachability: three shipped surfaces nobody can reach. ✅ SHIPPED
2026-08-27.** Two of the three were as diagnosed; the third was not, and the
difference is recorded rather than papered over.

**(a) was worse than "no screen".** The fee-rate routes had no UI, and
`lock.submit_claim` refuses `fee_rate_not_configured` until a rung resolves — so
an org that had bought this product could not file a single claim through it,
and the only way to open the gate was a Python shell. There is now a "Fee rates"
tab on `/vat-admin` carrying the chain in resolution order (most specific
first), both entry styles, and the copy that says what an empty list means. Also
closed here: **the `vat_fee_rates` tenancy exemption had expired.** Its own
stated condition was *"gains a probe in the same commit that gives the rate a
route"* — WO-95 gave it three routes and the probe never followed, so the
exemption text was asserting "no route reads or writes THESE rows" while three
did. It is now a real probe over the real HTTP routes, and the exemption is gone
rather than reworded.

**(b) was misdiagnosed.** The order says `Excise.tsx` "renders a raw `entity_id`
because its picker was deferred". There is no picker to add: **the page takes no
entity input at all.** What it actually did was print the internal uuid beside
`entity_name` in a report a person reads — and since `entity_name` is
non-nullable on the wire, the uuid was never even a fallback. The uuid is gone.
An existing spec asserted its presence; that line was describing the wart rather
than defending it, and now asserts the name and the uuid's absence.

**(c) was exactly as described.** `/issuer` and `/reimbursements` both existed
and were reachable only by in-page links. Both are in the menu now. One caveat
worth stating: `/reimbursements` requires `EXPENSE_APPROVE`, a gate this nav
module cannot express (its `perm` field takes VAT_* only), so it is gated
`admin: true` and the page's own controls enforce the real permission — better a
destination an admin can reach and might be refused inside than a paid-out batch
nobody can find.

*The original order, for the record:* (a) The
fee-rate admin routes shipped (`admin.py:406-465`, `test_wo95_fee_rates.py`)
with **zero frontend hits** — the 15%/€50 decision has no screen. (b)
`Excise.tsx` still renders a raw `entity_id` because its picker was
deferred on a permission split that `components/EntityPicker.tsx` (WO-80)
already solves. (c) `shell/nav.ts` has no `/issuer` or `/reimbursements`
entry — both pages exist and are reachable only by in-page links. One
frontend order, no backend change. Effort: small-medium. Certification:
e2e per surface (fee-rate save round-trip, picker degradation without
`ISSUED_READ`, nav entries present and routed), plus the tenancy probe
WO-95 promised for the fee-rate routes.

**WO-V — The data promises the storage layer does not keep. ✅ SHIPPED
2026-08-27.** Both halves closed, and each one turned out to have a
prerequisite the order did not know about.

**(a) The FX triple guard reaches `invoices`.** WO-88/89 built it and applied
it to two transport tables; WO-89's own notes recorded that `invoices` was left
carrying only the value-domain check and did not fix it. That is not an
incidental table: the transport vertical's claim lines resolve THROUGH it, so
after WO-89 a fuel transaction could not lie about its euro while the invoice it
pointed at still could. `ck_invoices_fx_provenance` closes it, with WO-89's
fail-closed migration pre-flight. No writer-side gate was added, deliberately:
`fx.eur_total` is the only code that sets those columns and cannot produce a
contradiction, so a second gate would be dead code — WO-88's own reasoning.

*The prerequisite:* the predicate was a hand-written literal on one table and a
verbatim copy on the other, which is exactly how a third acquires a subtly
different rule. It is now built once (`app/models/fx.py::fx_provenance_check`),
with the clause ORDER preserved byte-for-byte so `alembic check` sees no drift.

*The correction:* the order said `expense_items` needs "a stated redesign rather
than a copied constraint", and that is right but understated. **`expense_items`
cannot carry the guard at all** (no EUR column — its converted figure is
`amount`, in the REPORT's currency, NOT NULL), and **`expense_reports` is the
bigger hole the order did not name**: it has `total_eur` and *no `fx_source`
column whatsoever*, so nothing records how that euro was arrived at. Closing
that is a schema change plus a backfill decision about already-approved money —
owner-facing, so recorded in DECISIONS-NEEDED rather than decided here. Both
exemptions are RECOMPUTED from the live models by a test, so the day someone
adds the missing column the suite fails and asks for the constraint.

**(b) The retention purge stops hard-deleting past the bin — for expenses.**
The docstring said expenses kept the direct hard-delete "UNTIL the recycle bin
learns those entities", and the bin had learned `expense_report` in WO-M itself.
The sentence outlived its own condition and the category kept hard-deleting for
an arc — the same failure mode as WO-U's expired tenancy exemption, twice in two
work orders.

*The prerequisite, and it was load-bearing:* **the generic bin's purge destroyed
ROWS and never BYTES.** Routing a category with receipts through it would have
silently orphaned every file — a regression wearing the shape of an improvement.
`bin.Kind` now carries a `bytes_of` hook and `purge_expired` uses it, at PURGE
and never at soft-delete (a restored report with no receipts has not been
restored).

*What stays open, honestly:* **inbound email attachments still hard-delete**,
and that one is genuine rather than stale — `InboundInvoice` has no `deleted_at`
column, so the bin cannot hold it. Giving it the columns is a migration plus a
`KINDS` entry plus `SOFT_DELETE_MODELS` registration, tracked as its own work.
A test pins the current truth AND asserts the model still lacks the column, so
whoever adds it is sent here. The rule that fell out: never route a category
into a bin that cannot hold it, because that is not a recycle bin — it is a
differently-spelled hard delete.

*The original order, for the record:* Two gaps,
one theme. (a) WO-89's FX triple guard (a euro may not deny that a rate
was used, nor that one was needed) lives only on `fuel_transactions` and
`vat_off_invoice_rebates`; `models/invoice.py:64` and `expense.py:116`
still carry only the WO-8 value-domain check — the platform finding WO-89
recorded and did not fix. Note `expense_items` has no EUR column, so its
invariant needs a stated redesign rather than a copied constraint. (b) The
recycle bin (WO-M) does not cover inbound email attachments, and the
retention purge still **hard-deletes past the bin** — a promise the
product now makes on every other delete. Effort: medium. Certification:
mirrored CHECK constraints with a fail-closed migration pre-flight, a
seeded dishonest euro refused at the writer, a retention purge proven to
route through the bin, both seeded violations restored by inverse edit.

**WO-W — Automation reaches outward, and delivery stops duplicating. ✅
SHIPPED 2026-08-27.** Both halves as ordered.

**The action composes; it does not learn to make an HTTP request.** `emit_webhook`
joins the catalog and calls `webhooks.emit`, which already signed (HMAC-SHA256),
already refused a private address (SSRF guard) and already delivered through the
durable queue with retry and dead-lettering. One design call worth keeping: a
rule publishes ONE event type, `automation.fired`, rather than a name of its own
choosing — `webhooks.EVENT_TYPES` is a catalog receivers subscribe against, and
letting a workspace invent names would let it publish events nobody could have
subscribed to and no document describes. Which rule fired is in the PAYLOAD.

**The idempotency key is what makes the composition safe.** A sweep re-evaluates
records constantly — a cooldown expiring, an `every_time` policy, a worker
retrying — and the run ledger governs whether a rule FIRES, not whether a
delivery is duplicated. Keying on `(rule, record)` means a re-fire that should
notify once notifies once.

**The dedup is the unique index, never a pre-SELECT.** Check-then-insert is
exactly the shape two concurrent callers both pass, and this is a retry path.
Each endpoint inserts in its own SAVEPOINT, so a collision rolls back that row
and leaves the others — an endpoint registered BETWEEN two emits still gets its
first delivery — and a collision cannot poison the caller's transaction, which
matters because `emit` runs inside business operations that have already done
their real work.

**The key is OPT-IN**, and the partial index (`WHERE idempotency_key IS NOT
NULL`) says so in the schema rather than inheriting it from a dialect's NULL
handling. A caller with no natural key must not invent one: an invented key that
collided would SUPPRESS a delivery that should have happened — worse than the
duplicate it was meant to prevent. So the 19 existing callers were deliberately
NOT backfilled with manufactured keys; they keep the old behaviour exactly, and
each can adopt a key when it has a real one.

*The original order, for the record:*
**WO-W — Automation reaches outward, and delivery stops duplicating.**
`models/automation.py:54` lists three action kinds (two emails, a CRM
note) while a full webhook subsystem already exists — HMAC-signed,
SSRF-guarded, queue-delivered (`services/webhooks.py`, `routes/webhooks.py`).
Wire `webhooks.emit` into the action catalog and the rule builder, so a
rule can reach an external system. In the same subsystem:
`webhooks.emit()` creates a delivery per endpoint with **no idempotency
key and no dedup** (`webhooks.py:133-168`), so a retried caller
double-delivers. Add the key + unique index + backfill the callers.
Effort: small-medium. Certification: an automation run that fires a
webhook end-to-end through the queue, a duplicate emit proven to
deliver once, the dry-run proven to send nothing.

**WO-X — AP capture throughput: batch upload + honest progress.** Every
capture endpoint takes a single `UploadFile` and `Upload.tsx` reads
`files?.[0]`; `ui/FileUpload.tsx` already has an unused `multiple` prop.
AP arrives in batches, so this is the daily friction. Pair it with X2: the
202+poll scaffold exists (`routes/invoices.py:1154`) but the poll returns
only queued/running/parsed/failed — no stage, page count or percent, so a
40-page scan looks hung. Effort: medium-large. Certification: N files → N
runs with per-file outcomes incl. partial failure, quota enforcement per
file not per request, an e2e drag-drop of three files, and a progress
contract test that a long job reports advancing stages.

**WO-Y — The gates that only run on my machine.** `test:vr` (visual
regression) has committed chromium-linux baselines but `ci.yml:157` still
calls it a LOCAL gate — CI already runs the version-matched Playwright
container, so the gate is one job step away from being real. Same order:
`routes/reimbursements.py:163` `pay_batch` appears in **zero test files**
while its payment-run twin has a real concurrency test to copy — an
unverified lock on a payout path. Effort: small-medium. Certification: VR
running in CI and proven to bite on a seeded pixel change; a truly
concurrent `pay_batch` test proven to fail without the lock.

**WO-Z — The statement review queue.** After WO-S makes ingestion
reachable, `statement_ingest.py:111`'s admission that "the warnings list
IS the review surface" becomes the next honest gap: warnings are
ephemeral, nothing persists a finding, and WO-70 named the queue
not-attempted. One tenant table with FORCE RLS in its creating migration,
a worklist, and resolution verbs. Effort: medium. Certification: parity
probe in the same commit, a finding surviving a restart, a resolution
audited.

### Deferred with a stated reason (not queued)

- **R51's materialised-metric drift check** — correctly deferred: it has
  no subject. All 17 transport tables are sources of record; WO-87's
  "rollup" is in-memory. The trigger is the first rollup table.
- **`advertised_prices`** — the premise is dead: WO-Q's design
  (2026-08-27) derives reliability from existing rows and explicitly
  drops the table. `savings.py:180`'s blocker note is now stale.
- **M4 settlement modelling** (cash-at-pump netting, Polish MPP split
  payment) and **G4.7's margin report** (needs `my_prices`/`wholesale_prices`)
  — both milestone-scale; they want their own arc, not a slot in this one.
- **G4.7 anomaly rules (R54)**, **G4.8 refund-estimate funnel**, the
  **fuel €/L analytics slice**, **customer document store + F3 country
  readiness**, **checklist rules (nace/trade-register/PoA)**,
  **receipt-control RUN trigger**, **VIES live lookup**, **statement-byte
  vaulting**, **q_ledger export hub**, **rebate merge preview**, **N1
  capture fields**, **N4 thumbnails**, **L2 multi-rate VAT on received
  invoices**, **create-another-org**, **IdP role mapping for the four new
  roles**, **dropping `users.org_id`/`role` after soak**, **Issue.tsx
  action grouping**, **ex-client archive export**, **`action_deadline`
  aggregation** — all verified genuinely open, all real work, none
  outranking the eight above. They are the arc-4 candidate pool.

## Fenced — owner decisions, with the question to answer

Fifty gated items reduce to these. Each blocks software work that is
otherwise ready:

1. **Billing go-live.** Stripe/EveryPay are code-complete (Checkout,
   Portal, signed webhook, Meter events). Activate live billing, or keep
   manual pilot invoicing? Downstream: archive paid-extension wiring,
   auto-charge at quota cap, the metering allowance reconciliation
   (`plans.py:38-45` over-grants: one doc allowance applied to two
   counters), dogfood VAT rate/scheme, and R5.
2. **Seller-of-record VAT** for platform subscriptions — WO-48 ships a 0%
   placeholder. Which entity invoices, under which regime?
3. **Transport commercialisation (§10).** The module is unpurchasable: no
   plan tier, no add-on price, no fee numbers. Plus C12's fee-invoicing
   board — `payout_to`, receivable vs deduct-and-remit, F-numbering.
4. **§13 partial rejection: does the freeze extend to overcharge
   claim-backs?** And its sibling: a drifted claim-back has no way
   forward — refresh, re-snapshot, or refuse?
5. **R55 peer benchmark** — cross-entity cohort policy (how many
   contributors before a comparison may be shown, and what may be shown).
6. **External price data (ADR-0027 phase 3)** incl. the Scrapling stealth
   stance; FX markup trend needs a market-wide series from the same call.
7. **Excise**: who owns the per-country rate table, is eligibility
   (≥7.5 t / carrier registration) to be modelled, is there a lapsed-regime
   flag, and should a claim lifecycle be harvested at all?
8. **The shadow run** — one real client, one real quarter, reconciled
   against their own figures. Still the highest-value validation item in
   the repo and it needs a client, not code.
9. Smaller, each one question: e-sign provider · pay-in-portal rail · SMS
   channel and who pays per message · two-way calendar sync provider ·
   OCR/document-AI vendor (L1) · DATEV vs SAF-T market pick · data
   residency regions · production KEK custody · public API GA scope ·
   live-IdP SSO testing · per-country statutory retention floors ·
   seller-entity detection for Q8/DKV/TFC/Moeve/BP · re-onboarding a
   terminal `inactive` customer · automated Port One rebate ingestion ·
   richer contract-audit term types (fee %, ratios, tiers) ·
   note-override DELETE · restating FX-wrong expense figures a human
   already approved.

## Stale stamps corrected in this pass

Verification found fifteen doc entries claiming open work that is
shipped. Corrected in the same commit as this plan: §11 supplier
attribution (three entries — WO-L), partial rejection of a VAT claim (two
entries — WO-L), the H1.2 plan ladder (resolved 2026-08-15), X3 SSO
secret → keyvault (shipped), the `bank_lines` currency money defect
(fixed 2026-08-13), claim-back abandonment (answered as `ignored`, WO-L),
pre-expiry notice + paid retention (shipped), project lifecycle phases
4–5 (shipped), registry unifications C1.5/C1.6/C1.7 (WO-23/15/24), R19
onboarding (WO-P), R14 backup/restore (decision + drill done), the Vite-8
payload regression (WO-O), and "no `api/routes/transport/*` exists" —
which is now ten routers.
