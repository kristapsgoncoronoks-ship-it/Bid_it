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

**WO-N — Accessibility & form-label pass.**
`Login.tsx` labels are not programmatically associated (zero `htmlFor`);
sweep the auth screens first, then the high-traffic forms; visible focus
states; Playwright assertions so the association cannot silently regress.
Estimated: 1 session.

**WO-O — First-load performance.**
The SPA's first-load payload roughly doubled under Vite 8 (recorded
2026-08-08, still open). Measure, re-split (manualChunks / route-level),
and land a bundle-size tripwire in CI so the next regression fails a
check instead of a user. Estimated: 1 session.

**WO-P — Guided onboarding checklist (R19).**
A derived, dismissible setup card (issuer profile → modules → team →
first customer/invoice) computed from state that already exists — no new
tables. Closes the last "empty workspace, now what?" gap the demo seed
papers over. Estimated: 1 session.

**WO-Q — Supplier reliability rating (§12 criteria; DESIGN-FIRST).**
The owner's criteria are recorded (overcharges, exchange-rate treatment,
lines charged that were never agreed) but TODO.md itself says it needs a
design pass: each criterion's contribution, the window, and a
presentation that reads as EVIDENCE rather than a verdict on a
counterparty. Deliverable 1 is the design doc; code only after.
Estimated: 2 sessions including design.

**WO-R — Load/perf test harness (R15).**
A repeatable load harness (k6 or locust script over the seeded demo
workspace, worker-tier only), a recorded baseline, and the p95 budgets
the index-strategy rule keeps referring to. Estimated: 1 session.

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
