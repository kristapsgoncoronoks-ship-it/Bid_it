# Commercial Readiness Review — Bid_it (InvoiceIQ)
**Reviewer:** Commercial Director, 4-agent SaaS review board
**Repo/branch:** `/home/user/Bid_it`, `claude/bidit-invoice-data-analytics`
**Method:** Read the onboarding, AP, and AR frontend pages and their backing services; ran the app for real (backend `uvicorn` + `app.seed` + frontend `vite dev`) and drove it with Playwright, capturing live screenshots of Dashboard, Captures, Invoices, Issue (AR), Payment Runs, Cash Position, Review queue, Upload, and Billing; cross-checked what the screens show against the actual demo SQLite DB with direct SQL queries; read `plans.py`/`modules.py`/`billing.py` end to end.

---

## 1. Did I actually run it?

**Yes.** From a clean checkout:
```
cd backend && rm -f invoiceiq_demo.db
DATABASE_URL="sqlite+aiosqlite:///./invoiceiq_demo.db" SECRET_KEY=... \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
DATABASE_URL="sqlite+aiosqlite:///./invoiceiq_demo.db" SECRET_KEY=... \
  .venv/bin/python -m app.seed
# → "Seeded 'Demo Logistics Ltd' with 83 invoices across 7 vendors.
#    Issued 22 outbound invoices (paid/overdue/open mix). ..."
cd frontend && npm run dev -- --host 127.0.0.1 --port 5173
```
Logged in as `demo@invoiceiq.app` / `demo1234` (the exact credential printed on the product's own login screen) via a Playwright script, and screenshotted Dashboard, Captures, Invoices, Issue, Payment Runs, Cash Position, Review, Upload, and Billing at full resolution. This is real evidence, not a read of component code.

**Headline result of doing this: the flagship numbers on the app contradict each other**, which is finding #1 below and the single most important thing this pass found.

---

## 2. Onboarding

Registration is a single-page mode toggle on `/login` (`frontend/src/pages/Login.tsx`) — org name, your name, email, password, "Create workspace." No email verification gate blocks first use (`require_email_verification` is a config knob, off by default in dev). A fresh org lands on `plan="trial"` (`backend/app/models/organization.py:29`), 3 seats, all four add-on modules *available* on the plan but still individually off by default (`modules.py`, `Module(..., default=False)`) — a first-time admin has to visit **Settings** to turn on Invoice issuing/Expenses/Email intake/Budgeting. `Settings.tsx` presents this as a clean toggle list with descriptions, not a hidden switch.

There is no guided setup wizard/checklist (no "add your first vendor," "connect your bank," "invite your team" flow). This is mitigated by good empty states everywhere I checked (Captures: "Upload an invoice and it will appear here… Upload an invoice" button; Payment Runs: "No scheduled invoices awaiting payment"; Review: an explicit amber banner explaining *why* the queue is empty — "Validation is off. Turn on AI and/or human validation in Settings"). For the stated target persona (accountancy-firm bookkeepers, per `docs/product/personas.md`), this is workable self-serve onboarding, not a blocker — but it is thinner than what a buyer would expect at a €29–99/mo SaaS price point.

**A non-technical admin can get to a working, empty org without a developer.** Getting to a *populated, self-consistent* org is where it breaks — see §3.

## 3. The central finding: the demo data lies to itself

I clicked from **Invoices** → **Cash Position** → **Payment Runs** → **Dashboard**, the exact path a salesperson would walk a prospect through, and the numbers do not reconcile:

| Screen | What it shows |
|---|---|
| `/invoices` | 83 invoices, mostly `paid`/some `pending`, totalling **>€1,063,592.82** (screenshot: rows like INV-2026-0008 `paid €25,567.48`, INV-2026-0004 `pending €22,239.50`) |
| `/dashboard` | "Overdue payables **€0.00**" · "Due within 7 days **€0.00**" |
| `/cash-position` | "You owe (payable) **€0.00**" · "Nothing due soon or overdue 🎉" |
| `/payment-runs` | "No scheduled invoices awaiting payment." · "No payment runs yet." |

I verified the root cause directly against the demo DB rather than guessing:
```sql
SELECT workflow_state, count(*), sum(total) FROM invoices GROUP BY workflow_state;
→ ('draft', 83, 1063592.82)
```
Every one of the 83 seeded AP invoices sits at `WorkflowState.draft` — none has ever been submitted, approved, scheduled, or paid through the real AP lifecycle. `backend/app/services/cash_position.py` (and `payment_run`, and the Dashboard's `payables` section) correctly and deliberately only count invoices in `{approved, scheduled_for_payment, partially_paid}` (`_PAYABLE_STATES`, `cash_position.py:26-30`) — that is sound design, not a bug in the reading logic. The bug is in `backend/app/seed.py`: it sets the **legacy** `Invoice.status` enum (`paid`/`pending`/`overdue`, used only by the Invoices list and analytics) directly (`app/seed.py:169-181,228`) but **never calls the workflow endpoints** (`/submit`, `/approve`, `/pay`) that would advance `workflow_state` — the field the "cash position"/"payables" surfaces actually read. The code's own comment even flags the intended contract: *"the two are synced at the paid milestone"* (`app/models/invoice.py:38-41`) — the seed script breaks that contract.

**Why this matters commercially:** the product's own login page advertises this exact demo (`demo@invoiceiq.app / demo1234`, printed under the sign-in form). Any prospect — or any AE running a live demo — who clicks from the populated Invoices list to the "Cash Position" page (explicitly built as "the value-first surface" pattern this codebase uses elsewhere) sees a contradiction: a million euros of "paid" invoices, and a page insisting nothing is owed and nothing is overdue, party emoji included. That is exactly the kind of "feature that works only if you know which button not to click" the review charter asks to catch — here it's "don't click past Invoices" — and it is squarely a **hidden manual step**: someone has to hand-walk 83 invoices through submit→approve before a demo would hold together, and nothing in the seed script, README, or onboarding docs tells a salesperson that.

To be precise about scope: I confirmed this is a **demo/seed defect, not a production defect**. The real submit/approve/pay path (`ReviewInvoice.tsx` → `POST /invoices/{id}/submit|approve|reject|transition`) is reachable from every invoice via a persistent "Open review & approval workspace →" link on `InvoiceDetail.tsx:71-77`, and is covered by passing tests (`test_invoice_workflow_unit`, `test_invoice_review_e2e`, both in the 1091-passed baseline). A real customer entering invoices through the real flow would not hit this. But **the packaged, advertised demo is broken today**, and that is exactly what the remit asked me to test.

**Debate outcome: CONFIRMED at P0.** Independently reproduced end-to-end (fresh seed, direct SQL query, live `cash_position.summary()` call), root-caused in `seed.py`, confirmed the fix is 100% confined to the demo-data generator with no production-code risk. See `docs/audit/agent-debate.md`.

## 4. AP workflow (as a user experiences it)

Read `CaptureQueue.tsx`, `CaptureReview.tsx`, `Review.tsx`, `ReviewInvoice.tsx`, `InvoiceDetail.tsx`, `PaymentRuns.tsx`; live-screenshotted Captures, Invoices, Payment Runs, Review.

This side of the product is well built. Specifics:
- **Loading/empty/error states are consistently handled** via a shared `QueryState`/`Skeleton`/`EmptyState` component set, not ad hoc per page (`CaptureQueue.tsx:40-59`).
- **Confirm-before-create is explicit and correctly gated**: `CaptureReview.tsx` disables "Confirm & create invoice" until vendor/number/issue-date are set, and shows a `ConfirmDialog` when confirming a capture the user hasn't fully cleared ("Create anyway").
- **Payment Runs has genuinely good safety UX** — not just a claim, verified in code: re-exporting an already-exported SEPA file requires an explicit `ConfirmDialog` ("Re-export with a new message id… the bank will treat it as a separate payment instruction"), and exporting a run with suppliers missing an IBAN requires a second, named acknowledgement dialog ("Some suppliers will not be paid… I understand these will not be paid") — `PaymentRuns.tsx:293-331`. Maker-checker is surfaced in the UI copy itself ("Neither the run's creator nor its approver can pay it").
- **Minor gap:** the "Cancel" button on an open or approved payment run fires immediately with no confirmation at all (`PaymentRuns.tsx:211-213,232-234`) — inconsistent with the care taken on the export flows two lines away. Low severity (cancelling doesn't move money), but worth fixing for consistency.
- Extraction is deterministic-first (structured XML/UBL/CII, OCR fallback) and needs no AI key to function — the Upload screen (`/upload`) is honest about this ("E-invoice XML (UBL/Factur-X) is read exactly; scanned PDFs use OCR").

## 5. AR workflow (Issue invoices)

Live-screenshotted `/issue`. Functionally rich — issuing, recurring schedules, per-invoice email/remind/PDF/XML, credit notes, dispute/write-off, all present and (per the passing `test_issued_*` ×9 suite) working. But the UI is the densest and most intimidating screen in the product: each of the 24 demo rows carries **~12 small text-only action links** (`Mark paid` / `Record…` / `Credit` / `Void` / `Dispute` / `Write off` / `Duplicate` / `Email` / `Remind` / `View` / `PDF` / `XML`) wrapped across two lines, with no icons, no grouping into safe-vs-destructive, and — unlike Payment Runs — **no confirmation dialog on `Void` or `Write off`**. `Issue.tsx` is 1,034 lines, the single largest page component in the app, mixing invoice creation, recurring-schedule management, and this full action table into one screen. For the target persona (a bookkeeper switching between many client entities all day, per `docs/product/personas.md`'s "Marco" persona), this is a plausible source of real-world misclicks and a genuine day-one intimidation factor, even though nothing here is functionally broken.

## 6. Reporting/export trustworthiness

Spot-checked the calculation path rather than trusting the README's claims:
- `analytics.py`'s per-report currency resolution (`_pick_currency`, `analytics.py:49-70`) is correctly currency-scoped, defaulting to "EUR" only when an org has **zero** invoices — the ARCH_plan.md claim that `analytics.summary()` hardcodes EUR is **stale/false**, confirmed by reading the current code, not just citing the discovery agent's flag.
- `report_writers.py` (`to_xlsx`/`to_pdf`) consumes the **same** `explore.run()` result dict the JSON/CSV export paths use — verified in code (`_header_and_rows`, shared by both writers) — so the three export formats structurally cannot disagree with each other or re-derive a figure.
- Formula-injection safety is real, not aspirational: every string cell (dimension **and** measure columns, including a negative number's leading `-`) is prefixed with `'` if it starts with `=+-@\t\r` (`report_writers.py:29-44`), mirrored independently in `erp_export._safe`/`audit_export._safe`. (Note: this safety is *not* universal — the Lead Product Developer and Lead System Architect independently found `payment_run.py`/`reimbursement.py`/`explore.py` CSV exports lack it; see the merged P1 finding in `docs/audit/remediation-roadmap.md`.)
- `test_report_writers.py` + `test_explore.py` (15 tests) pass cleanly when re-run targeted (`.venv/bin/python -m pytest tests/test_report_writers.py tests/test_explore.py -q` → `15 passed in 9.23s`).

Reporting numbers can be trusted to reconcile *within the app's own definitions* — the risk is not in export math, it's in the definitional gap described in §3/§7 (what counts as "payable" at all).

## 7. Billing / plan clarity

`Billing.tsx` + `plans.py`/`billing.py` are honest and legible: four tiers (Trial/Starter/Pro/Enterprise) with seats, price, and module inclusion spelled out per card; a visible banner states "Prices are indicative — nothing is charged until billing is connected" when no provider is wired. Downgrade guards are real: `PUT /billing/plan` refuses to drop below current seat usage ("Starter allows 2 seats but 5 are in use. Remove members first," `billing.py:75-80`) and silently disables add-on modules the new plan drops (`_reconcile_modules`).

Two commercial-clarity gaps, both verified in code:
- **Silent module loss on downgrade, no UI warning.** Switching Trial→Starter instantly turns off Invoice issuing with a single click and zero confirmation (`Billing.tsx` has no `ConfirmDialog` on `choosePlan`, unlike the care taken in `PaymentRuns.tsx`). A customer mid-trial who has issued real invoices could lose that capability without being told what they're about to lose.
- **Self-serve billing cannot collect real payment today.** With the shipped default (`billing_provider=none` → `NullProvider`), `PUT /billing/plan` changes `org.plan` directly with no payment step at all (`billing.py:64-93` — the checkout-required branch only triggers when `settings.billing_enabled` is true). A prospect can click "Switch to Enterprise" and get Enterprise entitlements for free; nothing charges anyone until an operator wires a live Stripe/EveryPay key. This is explicitly labeled in the UI as expected ("nothing is charged until billing is connected") so it isn't *dishonest*, but it does mean **the product cannot yet execute a self-serve paid transaction** — any real revenue today requires an operator to manually flip a plan/provider setting out of band.
  - **Debate outcome: CONFIRMED at P1, and found worse than described** — `plans.py`'s `enterprise` tier has `price_eur=None` ("contact us"), and the billing-enabled guard (`settings.billing_enabled and target.price_eur`) is falsy whenever `price_eur` is `None` — meaning **any org owner can self-upgrade to Enterprise for free via `PUT /billing/plan` even when a live Stripe/EveryPay key IS wired**, a bypass that would survive the finding's own literal proposed remediation. See `docs/audit/agent-debate.md`.

## 8. Could this be demoed to a prospect today?

**Not safely, as shipped.** The specific, reproducible reason is §3: the advertised demo login walks a viewer straight into a page-to-page contradiction on the two numbers a finance buyer cares about most (what we owe, what we're owed). Every other flow I drove live — Dashboard, AR Issue, Cash Position's receivables/aging math, Review queue, Upload — rendered correctly, with real numbers, real charts, and honest empty-state/labeling copy ("not a bank balance," "not a forecast"). Fixing the seed script to drive AP invoices through the real submit/approve/pay workflow (the same endpoints `ReviewInvoice.tsx` already calls, already tested) is very likely a same-day fix — this is a demo-data QA gap, not a re-architecture.

## 9. Over-built vs. under-built (the charter's explicit ask)

The team has shipped enterprise identity plumbing — full SAML 2.0, OIDC with PKCE, SCIM 2.0 user provisioning, per-tenant/BYOK envelope-encrypted secret custody, and Postgres `FORCE ROW LEVEL SECURITY` — all tested, all real (`test_sso_oidc`, `test_saml`, `test_scim`, `test_rls` all in the green baseline). These are exactly what an enterprise buyer's security questionnaire asks for, and they are defensible given the accountancy-firm beachhead persona genuinely needs "rock-solid tenant separation." But they were built **before the product can execute a single self-serve paid transaction** (§7) and before its own demo data holds together end to end (§3). That is a real prioritization inversion worth naming: sophisticated buyer-side trust infrastructure exists, but the basic commercial loop (show a coherent number → let someone pay for it) is not yet closed.

---

## Overall readiness classification: **Limited pilot**

**Justification:**
- **Not "Not suitable for customer use"** — the underlying engine is real and well-tested (1091 passed/4 skipped reproduced independently per the baseline agent; my own targeted re-runs and live-driven screenshots corroborate it). AP/AR workflows, maker-checker payment-run controls, export formula-injection safety, and currency-scoped reporting all check out under direct inspection, not just documentation.
- **Not "Internal demo only"** — a real customer entering their own invoices through the real (tested) workflow gets correct, self-consistent numbers; this isn't a toy.
- **Not yet "Controlled paid pilot" or "General commercial release"** — two concrete blockers: (a) the packaged demo used to *sell* the product contradicts itself on its two headline numbers (§3, debate-confirmed P0), and any pilot conversation risks the same embarrassment unless someone manually fixes the demo data first; (b) self-serve billing collects zero real payment today, and even the Enterprise tier can be self-upgraded for free regardless of billing configuration (§7, debate-confirmed P1) — so even a "paid" pilot has to be arranged and provisioned entirely outside the product today.
- Settling on **Limited pilot**: suitable for a hand-held pilot with a design-partner customer where InvoiceIQ's own team enters/curates the data (or supervises the customer doing so) and payment is arranged out-of-band by contract — but not yet ready to be handed to a self-serve prospect or an unattended sales demo.

**Fastest path to "Controlled paid pilot":** (1) fix `app/seed.py` to drive its AP invoices through the real workflow-submit/approve/pay endpoints so Cash Position/Payment Runs/Dashboard reconcile with the Invoices list; (2) add a confirmation step to the Billing downgrade flow, and close the `price_eur=None` Enterprise self-upgrade bypass; (3) decide and document whether a real Stripe/EveryPay key will be wired before the first paying pilot or whether that pilot will be invoiced manually.
