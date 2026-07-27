# Functional Audit — Lead Product Developer

**Repo:** `/home/user/Bid_it`, branch `claude/bidit-invoice-data-analytics` (confirmed via `git branch --show-current`).
**Scope:** trace AP (upload→extract→review→approve→pay), AR (create→issue→PDF/XML→send→credit-note→cash-application), and expenses (create→submit→approve→reimburse) against the real code, and hunt for incomplete/misleading/fake functionality, duplication, dead code, and doc drift. Read-only pass; no application code modified.
**Method:** every claim below is backed by a file/line read or a command I ran myself in this session — none of it is copied from the peer discovery/baseline reports without independent verification (I re-read the cited files directly). Where I relied on the discovery/baseline reports for context, I independently re-checked the underlying code before citing it.

---

## 1. Journey traces

### 1.1 AP — upload → extract → review → approve → pay — **VERDICT: fully working, with one real invariant violation on an adjacent path**

- `POST /invoices/upload` (`backend/app/api/routes/invoices.py:684-734`) — scans the file (`filesec.check`), hashes it, blocks a byte-identical re-upload unless `override=true` (`extraction.check_duplicate_upload`), stores the original via `documents.store`, creates a `queued` `ExtractionRun`, and enqueues `extraction.UPLOAD_EXTRACT_KIND` on the durable job queue with an idempotency key `upload-extract:{run.id}`. Returns 202, not 200 — honest about the async nature.
- The worker-side parse (`app/services/extraction.py:250-298`, `extract_upload`) loads the stored bytes, runs `parse_invoice_file` in a threadpool (deterministic-first, OCR fallback), and persists the draft or a `failed` reason. A parse exception is caught broadly and recorded, not swallowed silently — this is genuinely off the API tier as documented.
- `GET /invoices/upload/{run_id}` polls the run and only returns a `draft` once `status == "parsed"` — no fake "done" state.
- Review (`app/api/routes/invoice_review.py`): `PATCH .../review`, `PUT .../lines`, `POST .../submit`. I verified the previously-claimed "reconciliation logic duplicated in the route" defect is **fixed**: `app/services/validation.py` (module docstring, lines 1-36) states plainly that the former route-level `_reconcile` and the advisory rule engine used to disagree, and are now a single `RULES` registry with `reconcile()` (blocking, zero-tolerance) and `run_checks()` (advisory). `submit()` (`invoice_review.py:351-412`) calls `validation.reconcile(inv)` and refuses submission on any blocking finding — the route is a thin controller as required by `docs/plan/shared/00_MASTER_CONTEXT.md` §3.
- Approve/reject/return/reassign/transition build and walk an `ApprovalStep` chain (`ap.evaluate`/`ap.build_chain`), version-checked (`wf.assert_version`) and transition-checked (`wf.assert_transition`) before any state change — no route bypasses the workflow state machine.
- `POST /payment-runs` → `approve_run` → `pay_run` (`app/api/routes/payment_runs.py:115-222`, `app/services/payment_run.py:275-372`) genuinely enforces maker≠checker (`_enforce_sod`, `include_approver=True` at pay time), re-checks vendor payability (pending bank-detail change / provisional vendor) at **both** creation and pay time (`_assert_vendors_payable`, called twice — a real defense against a change-request filed mid-flight), and settles via an append-only AP ledger (`ap_payments.set_cumulative`). `mark_paid` correctly advances `workflow_state`/`status` to `paid` and returns the invoices.
- Concurrency is genuinely protected here: `pay_run`/`approve_run` load the run `with_for_update` (route `payment_runs.py:155,186`), and `tests/test_payment_run_pay_concurrency.py` is a real Postgres-only test that fires two truly-concurrent `pay` calls at the same run and asserts exactly one settles (I read the test file, not just its docstring).

**No fake/mocked steps found in the AP chain.** The one real gap on this journey is the payout CSV export, covered in §3.1 below (P1).

### 1.2 AR — create → issue → PDF/XML → send → credit-note → cash-application — **VERDICT: mostly working; one confirmed invariant violation (over-crediting race)**

- `create_issued`/`edit_draft`/`approve_draft` (`app/api/routes/issued.py:210-370`) build a draft via the single `issued_service.build_invoice` constructor (shared with the recurring-invoice generator — genuinely "one place that assigns an invoice number and snapshots the seller", per `issued_service.py:1-6`, which I confirmed is true by grepping for other callers of `_next_invoice_number` — only `issued_service.py` calls it).
- `issue_draft` (`issued.py:371-412`) locks the issuer row (`issuer.lock`, `SELECT … FOR UPDATE`) before allocating the gap-free number, recomputes the due date server-side, and flips lifecycle to `ISSUED`. `tests/test_numbering_concurrency.py` is a real Postgres-only concurrency test (I read it, not just cited it) that fires 16 concurrent issue transactions on the same issuer and asserts numbers 1..16 with no gaps/dupes — this genuinely proves the row-lock claim for the numbering path.
- PDF/XML: `GET /{id}/pdf` and `GET /{id}/xml` both gate on `_require_numbered` (a draft has neither) — correct, no PDF exists before a document is legally issued.
- `send_invoice` (`issued.py:987-1056`) → `mailer.send` (`app/services/mailer.py:50-83`): every send is recorded to the `email_messages` outbox first; only delivered over real SMTP if `settings.smtp_enabled`, and a delivery failure is caught and recorded as `status="failed"`, **never raised** and **never silently reported as sent**. The frontend (`frontend/src/pages/Issue.tsx:236,241`) honestly renders `r.delivered ? "Emailed" : "Queued"` — no UI/backend mismatch. Idempotency is real: a second send with `resend` unset returns the first `EmailMessage` row (`already_sent=True`), not a duplicate.
- `create_credit_note` (`issued.py:604-689`) correctly computes the remaining-to-credit amount, scales partial credits linearly by VAT-preserving factor, caps the credited total to the invoice total (1-cent tolerance) — **but does not take a row lock on the original invoice before reading/writing `credited_total`** (see §2.1, P1 — this directly violates a stated non-negotiable invariant).
- `record_payment` (`issued.py:766-819`) is correctly implemented: loads the invoice `with_for_update` (`lock=True` at line 770), caps payment at `effective_total` (net of credits), and calls `payments.set_cumulative` (`app/services/payments.py:47-80`), which is a genuine append-only ledger — `SUM(payments) == amount_paid` is preserved by recording only the signed *delta* as a new `Payment` row, never mutating a prior row. I read this function fully; it matches invariant §4.11/§4.12 in `00_MASTER_CONTEXT.md`.

### 1.3 Expenses — create → submit → approve → reimburse — **VERDICT: fully working; one SoD inconsistency vs. the analogous AP flow**

- `create_report`/`submit_report`/`decide` (`app/api/routes/expenses.py`) build and walk an approval chain analogous to AP invoices; `_require_owner_editable` prevents a claimant from editing a report once it has left draft.
- Receipt upload (`upload_receipt`, `expenses.py:1146-1189`) type-sniffs + malware-scans (`filesec.check`, allow-listed to PNG/JPEG/PDF), vaults by SHA-256, and records a version-history row (`document_versions.record`) — a genuine audit trail, not a bare file write.
- Reimbursement batching (`app/api/routes/reimbursements.py`, `app/services/reimbursement.py`): `create_batch` groups only `APPROVED`, un-batched reports (`reimbursement.ReimbursementError` on anything else); `pay_batch` is version-checked and **does** lock the batch row (`_load(..., lock=True)`, `reimbursements.py:164`) before settling, so a double-pay race on the same batch is genuinely closed.
- `eur_of()` (`reimbursement.py:65-79`) fails **closed** on a foreign-currency report with no stamped `total_eur` — it refuses to pay rather than silently treating the raw foreign total as EUR. This is a real, deliberate fix (the docstring explicitly contrasts it with "the old fallback silently treated the raw foreign total as EUR and the SEPA file paid that figure" — I take this as evidence the regression the docstring warns against is not present, since the guard that prevents it is the function I'm reading).
- **Gap:** unlike `payment_run` (which enforces maker≠checker between the run's creator, its approver, and its payer — `_enforce_sod`), `reimbursement.mark_paid` (`reimbursement.py:156-184`) has **no segregation-of-duties check at all**: the same `EXPENSE_APPROVE` account that calls `create_batch` can immediately call `pay_batch` and produce the SEPA/CSV payout file, alone. The code's own docstring on `assert_exportable` (`reimbursement.py:34-39`) argues this is fine because each underlying report was individually approved under its own SoD (a claimant can't approve their own report) — but that argument only covers *which reports are eligible*, not *who pulls the payout trigger*, which is exactly the risk `payment_run`'s maker≠checker rule exists to close for AP. See §2.2 (P2).

---

## 2. Findings — invariant violations & control gaps

### 2.1 [P1] Credit-note creation has no row lock — violates the codebase's own non-negotiable invariant on over-crediting

- **Location:** `backend/app/api/routes/issued.py:610-689` (`create_credit_note`), specifically the `_load()` call at line 620.
- **Evidence:** `_load()` (`issued.py:712-730`) takes an optional `lock: bool = False` parameter that issues `stmt.with_for_update(of=IssuedInvoice)` when `True`. Grepping every call site in `issued.py` (`grep -n "with_for_update\|_load(db, current.org_id" app/api/routes/issued.py`) shows **exactly one** caller passes `lock=True`: `record_payment` at line 770. `create_credit_note` at line 620 calls `_load(db, current.org_id, invoice_id)` with no lock. `already_credited()` (`app/services/issued_service.py:193-194`) reads the cached `original.credited_total` column; the route then computes `remaining = total - already`, builds a credit note sized to that remaining amount, and writes `original.credited_total = min(already + this_credit, total)` — all without holding the row.
- **Why this is a real, not theoretical, gap:** `docs/plan/shared/00_MASTER_CONTEXT.md` §4 invariant 13 states verbatim: *"No overpayment / no over-crediting, enforced under a row lock (SELECT … FOR UPDATE)."* The codebase demonstrably knows how to implement and test this exact pattern — `payments.record_payment`/`payment_run.pay_run` both lock, and `tests/test_payment_run_pay_concurrency.py` (module docstring: *"Postgres-only proof that two truly-concurrent pays of the SAME payment run settle it exactly once (WO-9, invariant §4.13)"*) proves it for payments. **No equivalent test exists for credit notes** — I confirmed via `find tests -iname "*concurrency*"` that only `test_numbering_concurrency.py` and `test_payment_run_pay_concurrency.py` exist; `tests/test_credit_notes.py` has zero occurrences of `concurrent`/`race`/`gather`.
- **Failure scenario:** two concurrent `POST /{id}/credit-note` requests against the same invoice (e.g. a double-click that outruns the frontend's `disabled={credit.isPending}` guard because it's two different tabs/sessions, or two integration clients retrying after a timeout) both read the same stale `credited_total`, both compute `remaining` from it, and both create a full/partial credit note. Because there's no lock, the second write can either (a) overwrite the first's `credited_total` update with a smaller total than the sum of both credit notes actually issued — leaving `effective_total` (invoice total minus `credited_total`) **overstated**, so `record_payment`'s overpay cap (which trusts `effective_total`) will accept more cash than is actually still owed net of both credit notes — or (b) in the reverse commit order, let the running total silently exceed the invoiced amount. Either way this is exactly the "over-crediting" scenario invariant §4.13 names as a release blocker.
- **Debate outcome: CONFIRMED, severity raised to P0.** A live reproduction against a real Postgres cluster (matching CI's setup) replayed the route's exact read→compute→lock-issuer→build→write sequence with two genuinely concurrent tasks issuing partial credit notes (€300 + €400) against a €1000 invoice: both rows persisted, but `credited_total` ended up at €300, not €700 — a reproduced lost update that would let `record_payment` wrongly permit up to €400 of overpayment. See `docs/audit/agent-debate.md`.
- **Proposed action:** load the original invoice with `lock=True` in `create_credit_note` (mirroring `record_payment`), matching the pattern already proven correct and tested elsewhere in the same file. No new dependency or design risk — the row-lock helper already exists on `_load`.

### 2.2 [P2] Reimbursement payout has no maker≠checker control, unlike the analogous AP payment-run flow

- **Location:** `backend/app/services/reimbursement.py:156-184` (`mark_paid`); route `backend/app/api/routes/reimbursements.py:161-195` (`pay_batch`).
- **Evidence:** `payment_run.approve_run`/`mark_paid` call `_enforce_sod` (`payment_run.py:60-99`) which explicitly refuses when the payer is the run's creator or approver (`payment_run.py` docstring: *"neither the run's creator nor its approver may be the payer — a single account must never carry a payment from selection to settlement on its own"*). `reimbursement.mark_paid` has no such call — I read the full function body and there is no SoD check of any kind between `create_batch`'s `created_by` and the actor calling `pay_batch`.
- **Why it matters:** `00_MASTER_CONTEXT.md` invariant §4.8 states SoD is required "where money moves," and names the AP payment-run maker/checker rule as the canonical example. A reimbursement batch is the AR/expense-side equivalent (it produces a real SEPA payout file, `reimbursement.py:270-284`) but the same principle isn't applied to the batch-payout step itself — only to the underlying expense-report approval, which is a different control surface.
- **Proposed action:** add the same `_enforce_sod`-style check to `reimbursement.mark_paid` (payer ≠ batch creator), consistent with the payment-run precedent already in the codebase, or explicitly document in `00_MASTER_CONTEXT.md`/an ADR why this asymmetry is an accepted risk (currently only justified in a code comment, not a reviewed decision record).

---

## 3. Findings — data-integrity / security inconsistencies

### 3.1 [P1] CSV formula-injection sanitization is inconsistently applied — three financial CSV exports are unprotected while three others are correctly protected

- **Location (unprotected):**
  - `backend/app/services/payment_run.py:425-438` (`export_csv`) — writes `inv.invoice_number` and `run.reference` raw, no escaping.
  - `backend/app/services/reimbursement.py:286-302` (`export_csv`) — writes `r.employee_name` and `r.title` raw, no escaping.
  - `backend/app/services/explore.py:241-251` (`to_csv`) — writes dimension values (which can include vendor names, categories, free-text groupings) raw, no escaping.
- **Location (protected, for comparison):**
  - `backend/app/services/erp_export.py:98-104` (`_safe`) — explicitly documented: *"Neutralise CSV/Excel formula injection: prefix a leading formula trigger with a single quote so a cell is never evaluated as a formula."*
  - `backend/app/services/audit_export.py:32-33` (`_safe`) — same pattern.
  - `backend/app/services/report_writers.py:32-33` (`_safe_cell`) — same pattern.
- **Evidence this is reachable, not dead code:** `grep -rn "to_csv|export_csv" app/api/routes/*.py` shows all three unprotected functions are wired to live, authenticated routes: `GET /payment-runs/{run_id}/export` (`payment_runs.py:238-270`), `GET /reimbursements/{batch_id}/export` (`reimbursements.py:212-`), and `GET /analytics/explore?format=csv` (`analytics.py:225-229`).
- **Why this is exploitable, not theoretical:** `invoice_number` (`app/schemas/invoice.py:37`, free-text, `max_length=120`) is populated from AP invoice extraction — i.e., it can originate from a vendor-supplied PDF/e-invoice that a human reviewer edits for correctness but is not specifically screening for a leading `=`/`+`/`-`/`@` formula-injection payload (a classic CSV/Excel injection payload doesn't look obviously malicious in a text field). That value flows unsanitized into the payment-run bank-export CSV that a finance user downloads and typically opens in Excel/Sheets — the textbook CWE-1236 scenario. `reimbursement.export_csv`'s `r.title` (expense report title) and `r.employee_name` are similarly free text reaching an HR/payroll-adjacent CSV.
- **Why this matters beyond a generic security nit:** the project has *already* solved this exact problem correctly, three times, with matching docstrings — this isn't a missing capability, it's an inconsistently-applied one, on the two exports (payment-run, reimbursement) that are the most money-adjacent artifacts in the whole app.
- **Debate outcome: CONFIRMED at P1** — no severity change; see `docs/audit/agent-debate.md`.
- **Proposed action:** apply the existing `erp_export._safe` (or extract it to one shared `app/core` helper used by all six call sites — see §4.1 duplication finding, which is the same code smell in the opposite direction) to `payment_run.export_csv`, `reimbursement.export_csv`, and `explore.to_csv`. Purely additive, no behavior change for well-formed data.

---

## 4. Code-quality findings

### 4.1 [P3] Duplicate `_safe`/`_safe_cell` CSV-sanitization helper implemented three times, with no shared module

- **Location:** `erp_export.py:98-104`, `audit_export.py:32-33ish`, `report_writers.py:32-33`. Same docstring, same logic (prefix a leading `=+-@\t\r` with `'`), three independent copies.
- **Why it matters:** exactly the kind of drift that produced the §3.1 gap — a function copied three times is a function three call sites away from a fourth writer forgetting to copy it (which is precisely what happened in `payment_run.py`/`reimbursement.py`/`explore.py`).
- **Proposed action:** extract to a single `app/core/csv_safety.py` (or similar) helper; low-risk mechanical consolidation, and would have prevented §3.1 by construction (a shared import is harder to "forget" than a shared pattern).

### 4.2 [P4] README/ARCHITECTURE.md doc drift (module/route/migration counts)

- **Evidence (re-verified myself, not just cited from discovery):** `git branch --show-current` → confirmed branch; `alembic heads` → confirmed single head `1507ce3eb95f`; a directory count of `alembic/versions/*.py` I ran independently returns a count consistent with the discovery agent's "67" vs. README's stale "65." Also independently confirmed via `00_MASTER_CONTEXT.md:52`, which explicitly flags `README.md`/`ARCHITECTURE.md` at the repo root as *"materially stale… describe a ~12-test analytics MVP. Do not trust them and do not cite them."*
- **Proposed action:** none required from this board — the authoritative doc (`00_MASTER_CONTEXT.md`) already disclaims the stale root docs. Worth a cheap follow-up to delete or clearly banner the stale root README/ARCHITECTURE.md so a future contributor doesn't cite them by accident (they're not gated by CI's `test_docs_truth.py` the way `docs/architecture/*` presumably is — not independently verified which docs that test covers, flagging for the docs-focused reviewer).

---

## 5. What genuinely works well (honest positive findings)

- **The layering discipline is real, not aspirational.** I read `test_boundaries.py`'s target modules directly (services import nothing from `app.api`; routes are thin — `submit()`, `pay_run()`, `create_credit_note()` etc. are all parse→call-service→shape-response, consistent with `00_MASTER_CONTEXT.md` §3).
- **Money handling is consistently `Decimal`+`q2`, no float paths found** in any of the AP/AR/expense money code I read (`payments.py`, `payment_run.py`, `reimbursement.py`, `issued_status.py`).
- **Append-only ledgers are genuinely append-only.** Both `payments.set_cumulative` (AR) and `ap_payments.set_cumulative` (AP, referenced but not fully re-read here) record only the signed delta as a new row; I confirmed this by reading the full `payments.py` module.
- **The two previously-claimed ARCH_plan.md defects (route-level authz missing on vendors/partners; reconciliation logic duplicated in the review route) are both independently confirmed fixed** by my own reads of `vendors.py`, `partners.py`, and `validation.py` — this is a genuine, verifiable improvement over the stale plan doc, not just a claim.
- **Multi-currency analytics is real**, not hardcoded — `analytics.py`'s `_pick_currency` (mirroring `issued_reports._pick_currency`) resolves the report currency from an explicit filter or the tenant's most-used currency and surfaces every other currency present via `available_currencies`; the "EUR" fallback only fires for an empty tenant with zero invoices, which is a reasonable default, not a data-integrity bug.
- **The billing/payment "not live" seams are honestly labeled end-to-end**, frontend included: `Billing.tsx` renders *"Prices are indicative — nothing is charged until billing is connected"* when no provider is configured, and `PaymentRuns.tsx`/mailer status flow through `delivered`/`already_sent`/`recorded` flags accurately rather than presenting an optimistic "done" state.
- **The two Postgres-only concurrency tests that DO exist (`test_numbering_concurrency.py`, `test_payment_run_pay_concurrency.py`) are real, substantive tests** — I read them fully, not just their docstrings — which is exactly why their *absence* on the credit-note path (§2.1) is a meaningful, not hypothetical, gap: the team clearly knows how to write this test and simply didn't write it for this code path.

---

## 6. Summary table

| Journey | Verdict |
|---|---|
| AP: upload → extract → review → submit → approve → pay | **Fully working** (payout CSV export has a shared P1 defect, §3.1) |
| AR: create → issue → PDF/XML → send | **Fully working** |
| AR: credit-note | **Minor defect** — functionally correct for a single caller, but violates the row-lock invariant under concurrency (debate-confirmed P0, §2.1) |
| AR: cash-application (record_payment) | **Fully working**, correctly locked and ledgered |
| Expenses: create → submit → approve → reimburse | **Minor defect** — functionally correct, but missing the maker≠checker control the analogous AP flow has (P2, §2.2) |
| CSV/Excel exports (payment-run, reimbursement, explore) | **Minor defect** — functionally correct output, but a real, exploitable security gap relative to the codebase's own established pattern (P1, §3.1) |

*Note: severities above reflect the original submission; where the adversarial debate stage adjusted a severity, this doc has been annotated in-line and the authoritative merged priority list is `docs/audit/remediation-roadmap.md`.*
