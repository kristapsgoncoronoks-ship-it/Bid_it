# InvoiceIQ — Business Analysis & Rebuild Specification

**Repository:** `/home/user/Bid_it` (branch `main`) · backend `/home/user/Bid_it/backend` · frontend `/home/user/Bid_it/frontend`
**Analysed:** 2026-07-24 · **Author:** Senior Business Analyst (reverse-engineering pass)
**Purpose:** a business-level specification of what the system *does* and what a rebuild *must* do. Feeds a system architect, then a prompt engineer.

> **Method note.** Every claim below is grounded in a file/symbol in the repo. Where the repo's own
> documentation (`README.md`, `ARCHITECTURE.md`, `docs/architecture/data-model.md`) contradicts the
> code, **the code wins** and the discrepancy is flagged. The README and ARCHITECTURE.md are
> materially **stale** — they describe a ~12-test invoice-analytics MVP; the code is a ~32k-LOC
> backend with 761 tests spanning AP, AR, expenses, banking, compliance and enterprise identity.
> The `docs/product/*` set (PRD, personas, workflows, pricing, risks) is current and unusually good.

---

## 0. Executive orientation — what this actually is

InvoiceIQ is a **multi-tenant European SME finance operations platform**. It is not "an invoice
scanner". Reading the code rather than the README, the system spans five money surfaces:

| Surface | What it owns | Crown-jewel status |
|---|---|---|
| **AP (received invoices)** | capture → extract → review → approve → pay → age | mature |
| **AR (issued invoices)** | customer master → issuer entities → draft/issue/credit/collect | **the crown jewel** |
| **Employee expenses** | claim → policy check → approval chain → reimbursement | mature |
| **Banking & cash** | statement import → reconciliation → payment runs → SEPA → cash position | mature-to-partial |
| **Platform** | tenancy, authz, audit, retention, GDPR, SSO/SCIM, billing, jobs, webhooks | mature |

The **product thesis** (from `docs/product/product-requirements.md` §1) is: *"turn a company's messy,
multi-supplier invoice and expense flow into a clean, VAT-aware, audit-ready financial record —
captured automatically, categorised, checked for errors, and exportable to accounting systems and
structured e-invoice formats."* The PRD is explicit that the built surface is "three or four
products" and picks **AP capture + VAT correctness + spend analytics** as the sellable wedge, with
AR issuing as a strong *attach*. A rebuild team must understand that the **code is broader than the
intended commercial wedge** — that is a deliberate, documented position, not accident.

The defensible asset is the **proprietary, multi-channel, line-item invoice dataset** plus
demonstrable correctness on EU VAT, FX and tenant isolation.

---

## 1. Business purpose & actors

### 1.1 The business problem

European SMEs and the accountancy practices serving them receive invoices in six formats across
email, portals and post, re-key them into accounting software, and discover duplicate payments, VAT
errors and missing invoices months later at year-end. Simultaneously they must issue their own
invoices in a form that satisfies EU VAT Directive 2006/112/EC Art. 226 and (from 2028–2030, under
ViDA) mandatory structured e-invoicing. Existing tools split the problem: capture tools (Dext,
AutoEntry, Klippa, Rossum) don't handle EU VAT/multi-entity; accounting packages (Xero, QuickBooks,
Sage) don't capture well; enterprise AP (Basware, Tipalti) is too heavy.

**InvoiceIQ's lane** (`docs/product/product-requirements.md` §4): *multi-channel capture + EU
VAT/e-invoice correctness for SMEs and their accountants — lighter than enterprise AP, more
compliance-serious than a receipt scanner.*

### 1.2 Customer segments (ranked by fit, from the PRD §2)

1. **Accountancy & bookkeeping practices (2–50 staff)** — the *beachhead*. One practice = many
   tenants; buys tools that save billable hours; strongest referral loop.
2. **Multi-entity SMEs (10–250 employees)** — transport/logistics, property, construction,
   professional services. Multiple cards/suppliers/legal entities. Strong fit for costing dimensions
   (vehicle/property/project) and VAT-across-entities.
3. **Finance-team-of-one SMEs (5–50 employees)** — one bookkeeper drowning in PDFs.

De-prioritised: micro/sole traders (low ACV) and large enterprise (long cycle), though the
*enterprise groundwork* (SSO, retention, DSAR, audit export, residency) is largely built.

### 1.3 Personas → system roles (`docs/product/personas.md`)

| Persona | Role in system | Primary need |
|---|---|---|
| Priya — practice principal | `owner` of firm workspace | multi-client console, seat economics, DPA |
| Marco — client manager/bookkeeper | `admin` per client | fast capture, duplicate flags, instant client switching |
| Sofia — SME owner/MD | `owner` | spend by entity/vehicle/project, audit-readiness |
| Tom — finance lead | `admin` | intake channels, validation, exports, roles+audit |
| Elena — AP clerk | `user` / employee | guided capture, clear "what's missing" |
| David — external auditor | read-only `user` / Auditor | traceability to source doc + tamper-evident log |
| Nadia — platform operator | `is_platform_admin` flag (never a company role) | cross-tenant support with least privilege |
| Ravi — expense claimant | `user` (Employee) | submit receipt, track to reimbursement (post-MVP persona) |

### 1.4 Tenancy model

- **Tenant = `Organization`** (`app/models/organization.py`). Everything hangs off exactly one org.
  The org carries: `name`, `plan` (trial/starter/pro/enterprise), `status`
  (active|suspended|canceled), `region` (data residency, default `eu`), validation toggles
  (`ai_validation_enabled`, `human_validation_enabled`), and billing linkage
  (`stripe_customer_id`, `stripe_subscription_id`, `everypay_token`, `everypay_next_charge`).
- **Users belong to an org** via `users.org_id`, but there is a **`memberships` table**
  (`app/models/membership.py`, migration `deb447b02296_memberships_table.py`) enabling a user to
  hold memberships in several orgs, with an **org-switch** flow (`audit.A.SWITCH_ORG`,
  `tests/test_org_switch.py`, `get_current_user_unscoped` in `app/api/deps.py`). The *active* org is
  `user.org_id`, repointed on switch. A **live active membership in the active org is enforced on
  every request** — a suspended/removed membership is a hard 401 even if the account is active
  (`app/api/deps.py::get_current_user`, `tests/test_membership_enforcement.py`).
- **Registration creates a workspace** and its `owner`; every registration is an isolated tenant even
  for the same company name (PRD §11 acceptance criterion).
- **Joining is invitation-only** (`app/models/invitation.py`, `routes/team.py`, token invites with
  expiry — migration `c822c6b08d12_invitation_expiry.py`).
- **Seats** are metered as active memberships against the plan's seat cap
  (`app/services/plans.py::active_seats` / `seats_available`).
- **Platform operator** is a *separate boolean* `users.is_platform_admin`, never a company role, with
  its own cross-tenant routes (`routes/platform.py`) that deliberately run **unscoped**.

### 1.5 Permission model (two orthogonal layers)

**Layer A — stored role ladder** (`app/core/roles.py`, `app/models/user.py::UserRole`):
`user_free (0) < user (1) < admin (2) < owner (3)`. All are scoped to the user's own company; none
grants cross-company power. `is_platform_admin` outranks all.

**Layer B — the business permission matrix** (`app/core/authz.py`) — the real authorization
vocabulary, **deny-by-default**:

- **20 permissions**: `invoice.read/write/delete/approve`, `expense.read/write/approve`,
  `issued.read/write/send`, `payment.read/write`, `report.read`, `export.run`, `audit.read`,
  `member.read/manage`, `role.assign`, `settings.manage`, `billing.manage`.
- **8 business roles**: Organization Owner, Administrator, Finance Manager, Accountant, Approver,
  Employee, Auditor, Read-only. `ROLE_PERMISSIONS` grants *exactly* what is listed — anything
  unlisted is denied.
- **Backward-compatible resolution**: `business_role(user)` maps the stored 4-tier value
  (`owner→OWNER`, `admin→ADMINISTRATOR`, `user→EMPLOYEE`, `user_free→READ_ONLY`) and passes through
  already-expanded 8-role values. Unrecognised → least privilege (READ_ONLY).
- **Bridges**: `is_platform_admin` ⇒ all permissions; `is_expense_approver` ⇒ additively grants
  `expense.approve`.
- **Single choke point**: `authz.require(user, Permission.X)` → 403. Routers ask for permissions,
  never inspect roles. The matrix is published at `GET /api/v1/auth/authz-matrix` and documented in
  `docs/security/authorization-policy-matrix.md`, kept in lock-step by
  `tests/test_authz.py::test_every_role_is_in_the_matrix`.
- **Notable segregation of duties**: `billing.manage` is **Owner only** (Administrator excluded);
  `issued.send` is withheld from Accountant; Approver can approve but not write; Auditor can read
  everything + audit + export but write nothing.

**Layer C — entitlement gates** stacked on top of authz:
- **Module gating** — `app/services/modules.py::require_enabled(db, org, key)` → 403. Modules:
  `analytics`, `intake`, `fx`, `validation` (all **core**, always on) + `issuing`, `expenses`,
  `email_intake`, `budget` (activatable, default off).
- **Plan gating** — `app/services/plans.py::allows_module`. `trial` (3 seats, €0, all add-ons),
  `starter` (2 seats, €29, expenses+budget), `pro` (10 seats, €99, all), `enterprise` (200 seats,
  custom, all).
- **Usage quotas** — per-role monthly limits matrix (`role_policies`, `usage_counters`,
  `app/services/access.py`), enforced at invoice creation; 0 = unlimited; admins/owners unlimited.

---

## 2. Core business capabilities

*(Grouped as business capabilities. "Commercial value" = why a customer pays. "Key rules" = the
constraints a rebuild must reproduce.)*

### Group A — Accounts Payable (supplier invoices)

**A1. Multi-channel capture.** Upload PDF/image/CSV/JSON/XML, email-in to a per-workspace inbound
address, or API ingest. Deterministic-first chain: structured e-invoice XML (UBL 2.1 / UN-CEFACT
CII, EN-16931) → Factur-X/ZUGFeRD XML embedded in a hybrid PDF → PDF text layer (pdfplumber) →
Tesseract OCR for scans. Every path yields the **same confirmable draft** a human accepts.
*Value:* eliminates re-keying; the #1 job-to-be-done (J1).
*Rules:* nothing is persisted until a human confirms; AI/OCR never wins over a structured field;
XML hardened with `defusedxml` (XXE/billion-laughs); every upload passes one security choke point
(`app/services/filesec.py`: size cap, magic-byte type validation, EICAR + optional ClamAV,
fail-closed when configured).

**A2. Extraction with honest provenance.** Upload returns **202 + a run id**; parse/OCR happens on
the **worker tier**, and the client polls (`queued → parsed → failed`). `extraction_runs` +
`extraction_fields` record per-field value, status (`extracted|defaulted|missing`), confidence, and
`original` vs `normalized` vs `reviewed` values, behind a pluggable `ExtractionProvider` registry
(five providers: PDF, e-invoice XML, JSON, CSV, image).
**The confidence model is deliberately honest**: e-invoice XML / CSV / JSON carry `None` (= *exact*,
a typed field, not "unknown"); PDF text layer `0.85`; OCR `0.55`; anything below the `0.75` threshold
is flagged `low_confidence`.
*Value:* reviewers see *what* was uncertain, not a black box; the audit story for capture.
*Limits:* provenance covers only **five header fields** (`invoice_number`, `vendor_name`,
`issue_date`, `due_date`, `currency`) — **line items carry no per-field confidence**; and there is
**no learning loop** — `reviewed_value` is stored for audit only, nothing feeds back.

**A3. Review, validation & duplicate control.** A rule engine (`app/services/validation.py`) with
**14 deterministic checks**: `missing_number`, `no_lines`, `subtotal_mismatch`, `total_mismatch`,
`tax_mismatch`, `line_math`, `non_positive_total`, `future_date`, `old_date`, `due_before_issue`,
`unknown_currency`, `duplicate`, `duplicate_cross_supplier`, `fx_deviation` (>3% from ECB).
Tolerances: `0.01` money, `0.02` tax, `max(0.01, 1%)` per line. Two independent org-level toggles,
**both off by default**: *AI validation* (advisory — resolves `passed`/`flagged`, never blocks; the
`ai_enrich()` LLM seam is a literal no-op) and *human validation* (routes to `pending` until a person
approves/rejects). With neither on, status is `none`.
Duplicate detection is **advisory in both engines**: exact `invoice_number` match, split into `exact`
(same vendor — near-certain double upload) vs `cross_supplier` (different vendor). No fuzzy matching,
no amount/date similarity scoring, and **no hash-based rejection of a re-uploaded identical file**.
*Rebuild flag:* there are **two overlapping validators with different rules and tolerances** —
`validation.py` (advisory, org-toggled, findings persisted as JSON) and `invoice_review._reconcile`
(always-on, blocking at the submit gate, zero tolerance). Unify them.

**A4. AP approval workflow.** A 14-state workflow (`app/models/invoice.py::WorkflowState`) driven by
`app/services/invoice_workflow.py::TRANSITIONS`. Legal edges:

```
uploaded            → processing, review_required, draft
processing          → review_required, draft
review_required     → draft
draft               → submitted
submitted           → partially_approved, approved, rejected, draft   (draft = return for correction)
partially_approved  → approved, rejected, draft
approved            → scheduled_for_payment, disputed, draft          (draft = controlled correction)
rejected            → draft, archived
scheduled_for_payment → partially_paid, paid, disputed
partially_paid      → paid, disputed
paid                → disputed, archived
disputed            → draft, approved, scheduled_for_payment
cancelled           → archived      · archived → ∅
```
Plus `_CANCELLABLE`: any state except `cancelled/archived/paid/rejected` may go to `cancelled`.
`EDITABLE = {uploaded, processing, review_required, draft}`; `LOCKED = {approved,
scheduled_for_payment, partially_paid, paid, archived}` (`locked_at`/`locked_by` stamped when the
chain completes); `TERMINAL = {archived}`.
**Controls:** optimistic concurrency (`assert_version` → 409 on stale, `version` bumped on every
mutation), illegal transition → 422, and **segregation of duties** — the user in
`Invoice.submitted_by` cannot approve/reject/return it (`_guard_decider`, platform-admin exempt), and
a named approval step is decidable only by its assignee.
**Approval policy engine** (`app/services/approval_policy.py`): active policies ordered
`priority ASC, created_at ASC`, **first fully-matching wins**; criteria are `min_amount` (against
`total_eur` else `total`), `department_id`, `cost_center_id`, `legal_entity_id`, `vendor_id` — all
nullable = wildcard. The chain is one step per `approver_ids` entry plus an optional
`finance_final` tail step; **no policy ⇒ exactly one open step** (preserving the single-approver
default). Only the lowest-seq pending step is actionable (queue-jumping → 403); reject/return marks
remaining steps `skipped` (never deleted); resubmit rebuilds the chain from scratch.
**The one hard AP gate** is `POST /invoices/{id}/submit` → `invoice_review._reconcile()`: per line
`tax_rate ∈ [0,100]` and `amount == q2(qty × unit_price)`; header subtotal/tax/total must equal the
recomputed values **exactly** (no tolerance) or the submit is refused 422. No line items → 422.
*Value:* the control environment an auditor asks for.
*Gap to flag:* **there is no three-way match** — no purchase-order or goods-receipt entity exists on
the AP side; a "PO" is only an uploaded attachment. Duplicate detection never blocks.

**A5. AP settlement & aging.** `supplier_payments` ledger is the source of truth;
`invoices.amount_paid` is a derived cache; paid/outstanding/overdue status is **computed, never
stored** (`app/services/ap_status.py` — note `overdue` beats `partial` in the precedence order).
Payment is only permitted from `{scheduled_for_payment, partially_paid}` (422 otherwise) and
`new_total > total` is refused 400 — **overpayment of a supplier is impossible**.
Aging (`ap_aging.py`): open-payable set = `{approved, scheduled_for_payment, partially_paid}`;
`DUE_SOON_DAYS = 7`; buckets `1-30 / 31-60 / 61-90 / 90+` for overdue, else `due_soon` / `later`.
A daily job (`AP_DUE_ALERTS` via `scheduler.enqueue_daily`, idempotent) emails a due/overdue digest
to the issuer profile's address.

**A6. Vendor master.** `vendors`: `name` (unique per org), `tax_id`, `country`, `category`, `iban`,
`bic`. Dedup is **exact stripped-name match only** — "Acme Ltd" and "Acme Ltd." become two vendors.
*This is the single largest control gap found in the AP domain:* `POST /vendors` and
`PATCH /vendors/{id}` carry **no `authz.require` and no `audit.record`**, there is no version guard,
no IBAN mod-97 checksum, and no dual approval — yet `sepa.payment_run_sepa` pays whatever IBAN sits
on the vendor row. **A rebuild must gate, audit and verify supplier bank-detail changes.**

**A7. Costing dimensions.** Every invoice can be tagged cost centre / department / project / vehicle
/ property (`app/core/dimensions.py`, max 80 chars each). Three of these have **master tables**
(`departments`, `cost_centers`, `projects` — `app/models/costing.py`) linked by **composite FK
`(org_id, x_id) → master(org_id, id)`**, which makes a cross-tenant link structurally impossible at
the DB level. Masters are **archived, never hard-deleted**, and carry `version` optimistic
concurrency. A dual-read transition is in progress: `costing.resolve_link_id()` matches **code first,
then name**, case-insensitively, and **invents nothing** (unmatched → NULL); an idempotent backfill
job (`costing.backfill_links`) sweeps existing rows. Vehicle and property remain free-text.
*Value:* "where does the money go, per vehicle/property/project" — the multi-entity SME's core ask.
*Limit:* invoice-level tagging only — **no split/percentage allocation across cost objects**.

### Group B — Accounts Receivable / outbound invoicing

See **§3** — documented in depth as the crown jewel.

### Group C — Employee expenses (SAP Concur-style)

**C1. Claim capture.** Three item kinds — `standard`, `mileage` (`distance × rate`), `per_diem`
(`days × rate`); seven categories (travel, meals, accommodation, transport, supplies, software,
other). Derived amounts only fire when `amount == 0`. A **bank-statement import** (CSV or PDF-OCR,
15 MB cap, **debits only**) fills an "available expenses" inbox (`expense_transactions`); items can be
matched to a bank line within a `0.01` tolerance, stamping `bank_reference` and `verified=true`.
Receipt OCR (`receipt_ocr.py`) suggests merchant/date/amount/VAT/currency but **writes nothing**.

**C2. Expense-report lifecycle** (`app/services/expense_state.py`):
`draft · submitted · partially_approved · approved · rejected · returned ·
marked_for_reimbursement · reimbursed`. `EDITABLE = {draft, returned}` (owner only);
`TERMINAL = {rejected, reimbursed}`. Actions: `submit` (draft/returned→submitted), `withdraw`
(submitted→draft), `approve`/`reject`/`return_for_correction` (from submitted),
`mark_for_reimbursement` (approved→), `mark_reimbursed` (approved/marked→).

**C3. Ownership & segregation of duties.** A non-oversight user sees **404** (not 403) on another
employee's report — enumeration-safe. Oversight = `is_expense_approver` **or** admin-or-above.
`employee_id == current.id` → 403 *"You cannot approve your own expense report"*. The workspace must
always retain **≥1 approver** (400 on removing the last one).

**C4. Two-gate submission.** `POST /expenses/{id}/submit` refuses on: not owner (403), illegal state
(409), **no items** (422), and — the hard control — **every item must carry a business purpose
(`comment`) AND a receipt**, where a receipt is satisfied by an uploaded file OR a non-blank
`missing_receipt_declaration` OR the item being `mileage`/`per_diem` (computed allowances are
receipt-exempt). Then the **policy gate**: only findings whose code appears in the policy's
`blocking_rules` (empty by default) can block.

**C5. Policy engine — 11 rules** (`app/services/expense_policy.py`, one policy row per org, unset =
not enforced): `over_item_max`, `over_category_cap`, `missing_receipt` (above a threshold),
`out_of_policy_category`, `unsupported_currency`, `mileage_rate` (± tolerance vs a sanctioned rate),
`missing_business_purpose` (above a threshold), `late_submission` (days since spend date),
`weekend_spend`, `duplicate_receipt` (same sha256), `duplicate_amount_date_merchant`.
**Design rule, stated in the code: suspicious expenses are FLAGGED for review, never auto-rejected.**
*Limits:* duplicate detection is **intra-report only** (never cross-report or cross-employee); there
is **no per-diem policy rule** (per-diem is a computation shape, not a sanctioned rate table).

**C6. Approval chain.** A near-clone of the AP engine but with **only one criterion —
`min_amount`** (no department/cost-centre/entity routing). Same first-match-by-priority selection,
optional finance-final tail step, and "no policy ⇒ one open step". Delegation = **manual
reassignment of a pending step only** — no out-of-office delegate, no SLA/escalation timer, no
auto-approve.

**C7. Reimbursement.** `ReimbursementBatch`: `open → paid | cancelled` (both terminal). Only
`approved`/`marked_for_reimbursement` reports with no existing batch may be added (422 otherwise).
`mark_paid` stamps a reference and flips each report to `reimbursed`. Outputs: CSV and **SEPA
pain.001**, using the issuer profile as debtor and `User.iban`/`bic` per employee (employees without
an IBAN are silently skipped and counted in an `X-Skipped` header). **No bank connectivity** — the
XML is a download.
*Commercial position:* the PRD deliberately **freezes** this module as post-MVP — it competes with
Pleo/Payhawk, which needs cards + banking. It is built and maintained, not sold.

### Group D — Banking, cash & collections

The domain has **two settlement rails and one bank mirror**: AP money-out (approval →
`scheduled_for_payment` → payment run → ledger → SEPA/CSV file), AR money-in (receipt → allocation →
ledger → dunning), and bank reconciliation which sits *beside* both as **advisory annotation** — it
never posts cash.

**D1. Payment runs.** A run is a **payout batch**: N scheduled supplier invoices under one method +
reference, paid as one bank transfer and therefore reconcilable as one bank debit. States:
`open → paid | cancelled` (both terminal). Candidate pool = `workflow_state ==
scheduled_for_payment AND payment_run_id IS NULL`, ordered by due date. `mark_paid` settles **every
invoice in full** — partial payment through a run is impossible. Cancel unlinks invoices back to the
pool; **a paid run cannot be reversed** (corrections go invoice-by-invoice as negative ledger rows).
*Double-payment guards (five):* pool exclusion, create-time "already in a run" check, `open`-only
state gate, optimistic `version` (409), and `SELECT ... FOR UPDATE` row lock.
*Gaps a rebuild must close:* selection is **100% manual cherry-pick** — no due-date window, no
early-payment-discount capture, no cash-availability constraint; **no maker-checker on the run
itself** (permissions separate approve from pay, but one user holding both can do both); **no guard
against exporting the bank file twice** (the SEPA/CSV GETs have no `PAYMENT_WRITE`, no
already-exported flag, and work on an unpaid run).

**D2. SEPA.** **pain.001.001.03** (Customer Credit Transfer Initiation) is genuinely implemented —
correct ISO 20022 namespace, `GrpHdr` + `PmtInf` with duplicated `NbOfTxs`/`CtrlSum`,
`PmtTpInf/SvcLvl/Cd = SEPA`, `ChrgBr = SLEV`, one `CdtTrfTxInf` per invoice with truncated
`EndToEndId`(35)/`Cdtr/Nm`(70)/`RmtInf/Ustrd`(140). **One renderer, two rails** — the same
`sepa.build_pain001` serves AP payment runs and expense reimbursements.
**camt.053** is implemented **parse-only** (inbound). **pain.008 (direct debit), mandates (UMR,
FRST/RCUR, CORE/B2B), pain.002 status reports, camt.052/054 are entirely absent.**
*Gaps:* **no IBAN mod-97 checksum and no BIC format validation anywhere** — a structurally invalid
IBAN goes into the bank file unchallenged; `MsgId` is deterministic (`RUN-{id[:8]}`) so re-export
produces a duplicate message id; no XSD validation; no per-creditor aggregation (two invoices for one
vendor = two transfers); execution date is backdated, never forward-dated; **vendors with no IBAN are
silently skipped and the skipped count is discarded by the route** — the treasurer is never warned.
There is **no bank connectivity** (no EBICS/host-to-host/API): the file is a download a human uploads.

**D3. Bank statement import.** One entry point, four branches: **CSV** (BOM-tolerant, case-insensitive
synonym sniffing across 5 column families, 8 date formats, signed-amount or debit/credit columns),
**camt.053 XML** (defusedxml, namespace-agnostic, `BookgDt`→`ValDt` fallback), and **PDF** via text
layer or OCR. The distinctive PDF trick is **running-balance disambiguation**: the last money token
is declared a balance column when `|Δbalance| ≈ amount` for **≥60% of rows**, and direction is then
derived from the balance delta; otherwise everything defaults to debit **with an explicit warning**.
Import is duplicate-guarded by **SHA-256 of the raw bytes** (409 on re-upload) — byte identity, not
transaction-level dedup.

**D4. Reconciliation.** Four match targets (`receipt`, `reimbursement`, `payment_run`,
`issued_payment`) via a polymorphic `matched_kind`/`matched_id` soft reference. Direction routing by
the signed line amount; **hard amount tolerance of €0.02** (fixed, not configurable, not
percentage-based); strict **1:1** (a target already matched is excluded); score =
`(100 − days_off) + 25 if the target reference is a substring of the line description`; top 8
suggestions. **There is no auto-match** — the module docstring states matching is advisory and
"nothing here mutates a receipt, a batch, or the payment ledger". Confirming a match sets only the
line's status. `unmatch` reverses it; `ignore` handles bank fees/interest.
*Business consequence a rebuild must decide on:* **reconciling a bank line does not settle an
invoice.** Partial and many-to-many matching are unsupported; there is no write-off/cash-discount
tolerance. (Partial settlement *is* supported one level down, at the receipt-allocation layer.)

**D5. Cash position & cash flow.** `cash_position.summary()` returns receivables (reusing the
canonical AR report incl. aging + a DSO proxy), payables (open supplier invoices with outstanding,
overdue, scheduled and in-run counts), reconciliation counts + unmatched amount, and
`net_position = receivables.outstanding − payables.outstanding`. **This is a working-capital gap, not
a bank balance** — there is no bank-account entity and no opening/closing balance.
`cash_flow.monthly()` is **historical only** — it walks *backwards* from today over the AR ledger
(in) and AP ledger + paid reimbursement batches (out). **Despite the name there is no forecast.**

**D6. Dunning / collections.** A configurable per-tenant ladder (`dunning_policies`, unique per
`(org, level)`), with a built-in default of **level 1 @ 3 days (tone `reminder`), level 2 @ 14 days
(`firm`), level 3 @ 30 days (`final` — "referred for collection within 7 days")**. Three-way policy
semantics worth preserving verbatim: **no rows = the built-in default; rows with some active = only
those; rows all inactive = dunning disabled.** Triggered by a daily idempotent job (one per org per
day), a bulk "run reminders" action, or a single-invoice send (which also advances the level so the
cron won't immediately repeat it).
`resolve_level` picks the **highest** level whose threshold is met — an invoice first seen at day 40
jumps straight to level 3. **Idempotency: each level fires at most once ever per invoice**
(`issued_invoices.dunning_level` high-water mark).
*Stoppers* are all lifecycle side effects (dispute, write-off, void/cancel, paid, fully credited),
plus no buyer email and ladder exhaustion. **Absent: promise-to-pay, payment plans, dunning holds,
dunning fees (incl. the EU Dir. 2011/7 €40 recovery fee), non-email channels, customer-level (vs
invoice-level) dunning.** A partial payment does **not** pause the ladder.

**D7. FX.** EUR is the hard-wired base; rates are *units per 1 EUR* (ECB convention) in a
**cross-tenant** `ecb_rates` table. Sourced from the ECB 90-day feed via an **admin-triggered**
`POST /fx/refresh` (12s timeout, never raises, degrades gracefully) — **there is no scheduled refresh
job**, so rates go stale unless someone clicks. Rate-on-date rule: latest rate **on or before** the
date; if the date precedes all cached rates, use the earliest and set `approximate=True`. 25 European
currencies are registered, 13 ECB-published and 12 flagged permanently **`indicative`**. Invoice
valuation precedence: EUR → the **supplier-stated rate if present** → ECB → `unknown` (which yields
`total_eur = None` rather than a wrong number); the choice is persisted as `fx_source`.
**There is no FX gain/loss accounting** — no revaluation, no realised/unrealised split;
`total_eur` is stamped once at issue date. What exists instead is a genuinely differentiated
analytic: `fx.ecb_comparison` computes, per non-EUR supplier invoice, `markup_eur = eur_at_stated −
eur_at_ecb` — *"how much is your supplier's FX margin costing you."*

### Group E — Documents & records

Content-addressed document storage (SHA-256) over pluggable backends
(`app/core/storage.py`: S3/local/memory), a document registry + append-only **version chain**
(`document_versions`), **integrity verification** that re-hashes stored bytes against the recorded
digest (`app/services/integrity.py`), **retention policies + legal hold**
(`app/services/retention.py`), and **GDPR erasure** that respects statutory retention
(`app/services/privacy.py`).
*Value:* satisfies Dir. 2006/112/EC Art. 233 (authenticity, integrity, legibility for the retention
period) and GDPR simultaneously — the thing that closes an accountancy-practice security review.

### Group F — Analytics & reporting

**F1. Fixed KPI dashboards** — summary (spend, tax, unpaid, counts, avg, vendor count), spend over
time (monthly), top vendors, by line-item category, **by cost-allocation dimension** (untagged rolls
up to `(unassigned)` so it always sums to total), by status. Adjacent: cash position, cash flow,
AP aging worklist.

**F2. Explore — self-service pivot.** Fact grain is the **line item** (`line_items ⋈ invoices ⋈
vendors`): grouping invoice totals by a line attribute would double-count; grouping line amounts never
does, and invoice counts use `COUNT DISTINCT`. **9 dimensions** (vendor, country, category, status,
validation, currency, month, quarter, year) × **6 measures** (net, tax, gross, quantity, lines,
invoices). Guardrails: whitelist-only registries (unknown → 422, and **no SQL-injection surface**
because expressions are callables, never interpolated strings), **max 2 dimensions**, rows clamped to
1000, everything aggregated **in the database** over composite indexes. CSV export.

**F3. Supplier benchmark.** *Independent* — per-supplier scorecard (spend, invoices, tax, avg,
effective tax rate, paid ratio, category count, first/last invoice, spend share). *Combined* —
cross-supplier price comparison per category on **effective unit price = line spend ÷ line quantity**,
yielding the cheapest supplier, a per-supplier `deviation_pct`, `overspend_vs_cheapest`, and a rolled-up
**`total_savings_opportunity`**. Self-declared advisory: unit comparability across different products
in one category is loose.

**F4. Budget.** Positioned as **household/personal monthly budgeting**, not corporate budget control:
one recurring monthly limit per `(org, category)` in EUR, actuals taken **VAT-inclusive (gross)** and
ECB-converted, with budget-vs-actual per category, an overall total, and a 6-month trend. Advisory —
exceeding a budget sets an `over` flag; nothing is blocked and no alert fires.

**F5. AR reports** — summary/turnover, receivables (status view + aging buckets + a DSO proxy), by
partner, and output-VAT (grouped by `(scheme, doc_type, rate)`, applying VAT only under the
`standard` scheme and **negating credit notes** so they reduce the output-VAT base).

*Cross-cutting rules:* reports **never sum across currencies**
(`tests/test_money_invariants.py::test_issued_report_never_sums_across_currencies`); all CSV output is
formula-injection-safe.
*Rebuild flags:* the Explore dimension list **does not include the five cost-allocation dimensions**
(those only reach the separate fixed `/analytics/by-dimension` report) — unify them; currency is
hard-coded `"EUR"` in `analytics.summary()`, the benchmark summary and budget, so multi-currency
reporting is unfinished **outside** the AR reports (which correctly force one currency per report);
and the six KPI endpoints carry **no `authz.require`** while the three dashboards beside them require
`REPORT_READ`.

### Group G — Platform

**G1. Identity.** bcrypt passwords (72-byte truncation for passlib compatibility), HS256 JWT with a
24h TTL carrying `sub`, `org` and a **`jti` bound to a `sessions` row that is re-validated on every
request** — so a token *is* revocable (logout, sign-out-everywhere, password reset, deactivation all
revoke). Brute-force lockout (`failed_login_count` / `locked_until`, default 10 attempts / 15 min,
checked **before** password verification, → 429 with `Retry-After`) plus a coarse IP-keyed rate
limit on `/auth/*`. Email verification and password reset use single-use hashed tokens; both flows
are **enumeration-safe** (always 200/`{"sent":true}`). Email verification gating login is **opt-in and
off by default**.

**G2. SSO & directory.** Per-tenant `sso_connections` keyed by a public slug.
**OIDC is production-grade**: authorization-code + **PKCE S256**, a signed stateless `state` JWT
(600s TTL carrying conn/nonce/verifier), full ID-token validation (RS256 signature vs JWKS, issuer,
audience, expiry, **nonce replay**, mandatory `email`). JIT provisioning refuses an email belonging to
another workspace, creates the user with an **unusable password hash**, honours `allowed_domain`, and
maps IdP groups → roles picking the **highest-ranked** mapping — **owner is never granted and never
demoted**. **SCIM 2.0 Users** works (create/list with `filter userName eq`, get, PUT, PATCH in the
Okta/Entra dialects, DELETE = **soft deactivate**), authenticated by a per-connection bearer token
whose sha256 only is stored, and executing inside the tenant guard + RLS. **SAML is a deliberate,
documented stub — the ACS endpoint returns 501**; assertion validation is intentionally unimplemented
because a hand-rolled XML-DSig validator is an auth bypass.

**G3. Audit trail.** `audit_events` with a per-tenant monotonic `seq` and a **SHA-256 hash chain**
(`prev_hash|seq|org|actor|action|target|at_ms|meta`), `UniqueConstraint(org_id, seq)` so a concurrent
append **conflicts loudly rather than forking the chain**, and a Postgres transaction advisory lock
making read-then-insert atomic. ~90 canonical dotted action names. `record()` is **best-effort — it
never raises** — but commits **atomically with the operation it describes**. `verify_chain()` walks
the chain and reports the first break. Export (CSV/JSON) includes `seq`/`prev_hash`/`hash` so an
auditor can **re-verify offline**, with `X-Audit-Chain-Verified` / `X-Audit-Event-Count` headers.
*Honest limit:* append-only **by convention** — tamper-evident, not tamper-proof (no DB trigger).

**G4. Retention & legal hold.** `retention_policies(org, category, retain_days)` — **absence of a row
means keep forever** (opt-in, safe by default). Categories: `invoices`, `expenses`, `email_intake`.
**`issued_invoices` is deliberately excluded** (gap-free numbering makes ledger deletion a separate
gated decision) and audit events are never purged. Purge deletes children-then-parent and removes the
object-storage bytes best-effort, and is audited. `legal_holds` are placed with a reason and
**released, never deleted** — **any active hold blocks every purge and every erasure**.

**G5. GDPR erasure (DSAR).** Keyed by the subject's email; each location classified
`erase | retain | blocked`: users are **pseudonymised** (`erased-<hash16>@erased.invalid`, row kept so
audit attribution and FKs survive, memberships suspended), expense employee names redacted, inbound
email rows **and their stored bytes** deleted; `issued_invoices` **RETAINED** citing GDPR Art. 17(3)(b)
statutory accounting retention; the audit trail **RETAINED** because redaction would break the hash
chain. A preview endpoint classifies without mutating. Audited with a **hashed subject reference** —
proving handling without re-storing the identity.
*Gap:* **no data-portability / DSAR export (Art. 20)** exists — only erasure.

**G6. Documents & integrity.** Content-addressed, tenant-prefixed, two-level-sharded object keys
(`<prefix>/<org>/<ab>/<cd>/<sha256>`) over three backends (memory/local/S3) behind one Protocol, with
`services/documents.py` as the **single choke point**. A `documents` registry row is written for every
stored object; `document_versions` is an append-only supersession chain for the two single-file slots
(issuer logo, expense receipt). Three integrity sweeps that **never raise — a failure is a finding**:
re-hash every stored reference; verify the AR ledger invariants (`amount_paid` == Σ ledger, no
over-allocated receipt); verify exactly one current version per slot.

**G7. File security.** One gate before anything parses or OCRs: size cap; magic-byte sniffing with a
universal reject list (PE/ELF/Mach-O, **zip/PK — so no xlsx/docx/jar macro carriers**, RAR, gzip, 7z,
shebang, OLE) and active-content markers (`<html`, `<script`, `<?php`, `<%`, `<jsp:`); per-kind
allowlists; **EICAR always**, plus **optional ClamAV that fails CLOSED** when configured but
unreachable. XXE handled by `defusedxml`. Everything is served inert (`attachment` + `nosniff`).

**G8. Billing.** Two providers behind one `BillingProvider` Protocol plus a **`NullProvider`
(default)** whose every money operation raises "Billing is not configured": **Stripe** (hosted
Checkout + Billing Portal, the **signed webhook is the authority** for plan/status, Billing Meter for
metered usage) and **EveryPay** (Baltic card gateway — hosted page, **server-side verify, never
trusting the browser redirect**, and merchant-initiated recurring charges we schedule ourselves).
Idempotency via a `processed_stripe_events` ledger keyed by event id (deliberately *not* org-scoped).
A cancellation **drops to the free plan, never deletes data**; downgrades disable now-unentitled
modules but **never evict seats**. `PUT /billing/plan` returns **409 if billing is live and the target
is paid** — entitlements must never outrun payment. Usage reporting is incremental and idempotent
(`count − reported` watermark).
*Status: code-complete, operationally not live* — no credentials, and the EU-VAT/seller-of-record
question is unresolved (`docs/DECISIONS-NEEDED.md` §2).

**G9. Webhooks.** 17 event types across invoice/issued/expense/reimbursement lifecycles.
HMAC-SHA256 over the exact body → `X-InvoiceIQ-Signature: sha256=<hex>`; the secret is shown **once**
at creation. Delivery is enqueued as a durable job, so **retry/backoff/dead-letter come free from the
queue**. **SSRF defence** blocks non-http(s), localhost, IP literals and hostnames resolving to
private/loopback/link-local/reserved ranges (explicitly `169.254.169.254`), checked at create/update
**and again at delivery time** (DNS-rebinding defence), where a block is terminal, not retried.
`emit()` never raises into the business action.
*Missing:* secret rotation, replay/redeliver, per-endpoint circuit breaker, signature timestamp.

**G10. Jobs & scheduling.** **The database rows are the queue** — no Redis/Celery. Statuses
`queued/running/succeeded/failed/dead`; idempotent `enqueue` (a matching live job is returned rather
than duplicated); an optimistically-guarded `UPDATE ... WHERE status='queued'` claim so two workers
cannot both win; exponential backoff `30 × 2^(n-1)` capped at 3600s; **dead-letter after
`max_attempts` (default 5)**; a job whose kind has **no registered handler is dead-lettered
immediately**; stale leases (>300s) reclaimed. The handler runs inside `set_current_org(job.org_id)`
so queries and audit attribution are tenant-correct. **The scheduler is stateless** — no cron table;
`enqueue_daily` keys each periodic job by date so enqueuing a hundred times yields exactly one job per
org per day. Workers support **lanes** (`--kinds` / `--exclude`) so heavy OCR runs on its own pool.
A **queue SLO probe** at `GET /health/queue` returns **503 when degraded** (oldest-pending age >900s
or any dead job) and exports Prometheus gauges — aggregate numbers only, no tenant data.

**G11. Platform operator console.** List tenants (metadata only — never invoice data), set
status/plan, and edit the **global usage-limits matrix** (the only runtime-editable policy, and
explicitly refused to a company owner because it is a cross-company privilege). No impersonation, no
cross-tenant search, no tenant deletion.

**G12. Config safety & observability.** `Settings._validate_production` **crashes at boot** in
production on a dev `secret_key`, a SQLite `database_url`, `kek_provider=env` without a key, or
`cors_origins` containing `*`. OpenAPI/docs are disabled in production. Security headers (HSTS on
HTTPS, nosniff, frame-deny, API CSP `default-src 'none'`), a framework-agnostic `AppError`, JSON logs
with `X-Request-ID` propagation, Prometheus metrics, and `/health`, `/health/ready`, `/health/queue`.
Secrets at rest use **AES-256-GCM envelope encryption** (`core/keyvault.py`, AAD-bound, `kv1.`
envelope, raises on tamper); the default KEK derives from the app secret, BYOK via env, cloud KMS is a
documented seam, not built.

---

## 3. The invoice / AR engine in depth (crown jewel)

*(All file references are under `/home/user/Bid_it/backend/`.)*

### 3.1 The actors in the model

| Entity | File | Role |
|---|---|---|
| `IssuerProfile` | `app/models/issuer.py` | **A legal entity we invoice AS** (the seller) |
| `Customer` (+`CustomerContact`) | `app/models/customer.py` | Sales customer master (the buyer) |
| `Partner` (+`PartnerDocument`) | `app/models/partner.py` | Counterparty with a **pre-invoicing document gate** |
| `IssuedInvoice` (+`Line`, `Attachment`) | `app/models/issued_invoice.py` | The document — invoice **or** credit note |
| `RecurringInvoice` | `app/models/recurring_invoice.py` | A schedule + frozen template |
| `Payment` | `app/models/payment.py` | The AR cash-application ledger |

### 3.2 Issuer registry — multiple legal entities, per-entity numbering

`app/services/issuer.py`.

- An org may register **multiple** issuer entities. Exactly one is `is_default` (used when an invoice
  names none). `create_issuer` makes the org's *first* entity the default; `set_default` flips it.
- **Each entity owns its own gap-free series**: `invoice_prefix` + `next_number`, and a *separate*
  `credit_note_prefix` + `next_credit_number` (accounting best practice — credit notes carry their
  own series). Number format: `{prefix}{issue_year}-{counter:04d}` (`issued_service._next_invoice_number`).
- **Art. 226 completeness gate**: `REQUIRED_FIELDS = (legal_name, vat_number, address_line1, city,
  postal_code, country)`. `missing_fields()`/`is_complete()`; the routes refuse to create/issue with
  a 409 listing the missing fields (`routes/issued.py::_guard`, `_resolve_issuer`).
- **Seller snapshot**: `seller_snapshot(profile)` freezes legal name, trade name, VAT no., registration
  no., full address, contacts, IBAN/BIC, payment instructions and footer notes onto the invoice as
  `seller_json`. A later profile edit **never rewrites a finalized invoice**.
- Per-entity logo (`logo_sha256` → object storage), default currency, `payment_terms_days` (default
  14), and `default_penalty_rate` (% p.a.).
- Deletion is refused while an issuer is the default or referenced (`tests/test_issued_multi_issuer.py::test_cannot_delete_default_or_referenced_issuer`).

**Concurrency-safe numbering** — `issuer.lock(db, org_id, issuer_id)` does
`SELECT ... FOR UPDATE` on the issuer row. Two overlapping issue transactions on the *same* entity
serialize on that row and cannot read the same `next_number`; different entities lock different rows
and therefore number **in parallel**. The DB backstop is
`UniqueConstraint(org_id, number)` on `issued_invoices` — even if a lock were bypassed, a duplicate
document number is impossible. Proven by `tests/test_numbering_concurrency.py::test_concurrent_numbering_is_gap_free_and_unique_per_issuer`
(a real-Postgres CI job) and `tests/test_issued_guarantees.py::test_duplicate_number_is_rejected_by_the_db`.

### 3.3 Customer master

`app/models/customer.py`. Reusable billing + shipping addresses, `vat_number`,
`registration_number`, contacts, `payment_terms_days`, `default_currency`, `is_active`.
**Rule:** the buyer block is *prefilled from* the customer but **snapshotted onto** the invoice
(`routes/issued.py::_resolve_links`), because the customer record may change later. A buyer name (or
a `customer_id` that supplies one) is mandatory — 400 otherwise.

### 3.4 Partner pre-invoicing gate

`app/services/partners.py`. A partner may require a signed **contract** and/or **acceptance act**
before any invoice may be issued to it (`requires_contract`, `requires_acceptance`;
`missing_signed()`). Enforced **at issue time, not on a draft**
(`routes/issued.py::_enforce_partner_gate` → 409 *"Cannot issue to X: awaiting signed Contract"*).
Penalty (late-interest) invoicing is opt-in per partner and only permitted once a contract is signed.

### 3.5 The document lifecycle

Two orthogonal axes — a **stored workflow lifecycle** and a **derived AR status**.

**Stored lifecycle** (`app/services/issued_lifecycle.py`, column `issued_invoices.lifecycle`):
`draft · approved · issued · disputed · written_off · cancelled`. Legal transitions:

| Action | From | To |
|---|---|---|
| `approve` | draft | approved |
| `issue` | draft, approved | issued |
| `dispute` | issued | disputed |
| `undispute` | disputed | issued |
| `write_off` | issued, disputed | written_off |
| `cancel_draft` | draft, approved | cancelled |

`EDITABLE = {draft}` — **only a draft may be edited or deleted; everything else is immutable.**
An illegal transition is a **409** naming the allowed source states. Actor authorization is enforced
at the route (`authz.require(..., ISSUED_WRITE)`); the table encodes only legality.

**Derived AR status** (`app/services/issued_status.py`, *never stored*):
`draft · approved · sent · viewed · open · partial · overdue · paid · credited · credit_note ·
disputed · written_off · void`.
- `ar_status_of()` — lifecycle overrides first, then the payment-derived status. `outstanding_of()`
  returns 0 for a credit note and for `written_off | cancelled | draft | approved` — **only a live
  receivable accrues an outstanding balance.**
- `effective_total() = total − credited_total`; `outstanding = max(0, effective_total − amount_paid)`.
- `status_of()` is the *display* status: refines a still-open receivable by delivery state
  (viewed > sent > open). Reports deliberately bucket on `ar_status_of` so a merely-delivered invoice
  still counts as an **open receivable**.
- `penalty_of()` — accrued late-payment interest, **advisory only, never added to the stored total**:
  `outstanding × rate%/year × days_overdue/365` (ACT/365), zero unless the invoice carries a
  `penalty_rate` and is overdue with a balance. Rate precedence at build time: explicit > partner
  default > issuer default (`issued_service.build_invoice`).

**Born-final vs draft.** `POST /issued` creates a **numbered, issued** invoice by default (the
historical behaviour); `draft: true` creates an editable draft with **no number** and **no partner
gate**. `POST /{id}/issue` finalizes: re-checks the partner gate, locks the draft's *own* issuer
entity, re-stamps `issue_date` (default today), recomputes `due_date` preserving the draft's payment
term gap, allocates the number from that entity's series, sets `lifecycle=issued`, `issued_at`, and
emits the `issued.created` webhook.

### 3.6 Immutability and the correction mechanism

- Editing anything other than a draft → **409** *"Only a draft invoice can be edited… Issue a credit
  note to correct an issued invoice."*
- A **credit note** is the same table with `doc_type='credit_note'` and `corrected_invoice_id`. It:
  gets its **own** number series from the *original's* issuer entity; reuses the original's **frozen
  seller snapshot**; has **no due date** ("a credit note is not a receivable — nothing falls due");
  stores amounts **positive**; and bumps the original's `credited_total`.
- **Omit `lines`** ⇒ credit the whole *remaining* amount. After a partial credit this scales the
  original lines by `remaining/total` so the credit note grosses exactly to `remaining` while
  preserving the rate mix (VAT is linear in the net).
- **Over-crediting is refused**: caller-supplied lines exceeding the un-credited amount (with a
  1-cent rounding tolerance) → 400; `credited_total` is clamped with `min(new_credited, total)` so it
  can never drift past the invoiced total.
- Cannot credit a credit note (400). Cannot credit a non-receivable (`_require_receivable`).
- Tested end-to-end in `tests/test_credit_notes.py` (full credit cancels; partial reduces outstanding;
  over-credit refused; payment capped at credited total; turnover reduced in reports; PDF marked
  CREDIT NOTE).

### 3.7 Cancellation vs write-off vs void

Three distinct business events, deliberately not conflated:
- **cancel** — a *never-issued* draft/approved document (`POST /{id}/cancel`). Row kept for audit;
  reads VOID.
- **void** — an *issued* invoice cancelled before settlement (`POST /{id}/void`). Refused if already
  voided, if lifecycle ≠ issued, if it is a credit note, if **any payment is recorded** ("refund and
  remove them first"), or if **any credit note exists against it**.
- **write off** — bad debt on an issued/disputed invoice. Outstanding reads 0, but **the turnover
  stays on record** (it was real revenue; only collectability changed).

### 3.8 Delivery, viewing and idempotent email

- `POST /{id}/send` requires `Permission.ISSUED_SEND` (Owner/Admin/Finance Manager only). Recipient =
  override or `buyer_email`; 400 if neither.
- **Idempotency**: once `sent_at` is set, a repeat send is a **no-op** returning the first
  `EmailMessage` with `already_sent=true` — no second email, no second outbox row. `resend=true`
  overrides. Proven by `tests/test_issued_guarantees.py::test_send_is_idempotent`.
- `sent_at` records only the **first** delivery.
- `POST /{id}/mark-viewed` is the seam an open-tracking pixel or public-link open calls. **First-wins
  and idempotent**; requires the invoice to have been sent (409 otherwise).
- All outbound mail is recorded in `email_messages` (an outbox) whether SMTP is configured or not,
  and listed at `GET /issued/emails`.

### 3.9 Server-side tax and totals

`app/services/vat.py` is a pure function — **the client never supplies totals**.
- `compute(lines, scheme)` normalises each line: `net = q2(qty × unit_price × (100 − discount%)/100)`
  with the discount clamped to [0,100]; groups lines into **rate buckets**; quantizes each bucket's
  base then computes `vat = q2(base × rate/100)`; `tax_total = q2(Σ bucket vat)`;
  `total = q2(subtotal + tax_total)`.
- **VAT schemes**: `standard`, `reverse_charge`, `intra_eu`, `exempt`. The three zero-VAT schemes
  force **every line's effective rate to 0 regardless of what was entered**, and attach the legally
  required note (`SCHEME_NOTES`): Art. 196 (reverse charge), Art. 138 (intra-Community supply),
  Art. 132–137 (exempt). EN-16931 **BT-120** (`tax_exemption_reason`) is defaulted from the scheme
  note when not supplied.
- **Tax code catalogue**: a line may carry a `tax_code`; `tax_codes.resolve()` validates it is known
  and active (400 otherwise), the catalogue **drives** `vat_rate`, and the canonical code is
  **snapshotted onto the line** — a later catalogue edit must not change an issued invoice.
- Per-rate breakdown is what a compliant invoice must show and is rendered on the PDF and in the XML.
- Money everywhere is `Decimal` quantized ROUND_HALF_UP via `app/core/money.py::q2`; storage is
  `Numeric(14,2)`. `tests/test_money_invariants.py::test_money_never_uses_float` guards this.

### 3.10 PDF and e-invoice XML — "the PDF matches the stored values"

- `app/services/facturx.py::build_cii` emits **EN-16931 UN/CEFACT CII XML** from the invoice + frozen
  seller + the recomputed VAT result. `GET /issued/{id}/xml` returns it standalone.
- `app/services/invoice_pdf.py::build_pdf` draws the human-readable invoice (reportlab) and **embeds
  that same XML** via pypdf — a hybrid **Factur-X** document that the product's *own* reader
  (`einvoice.extract_embedded_xml`) round-trips. Honest limitation documented in the module
  docstring: *not* strict PDF/A-3 (colour profile / XMP conformance is a hardening step).
- **Both the PDF and the XML are rebuilt from the stored line rows** through the same `vat.compute`,
  so the rendered document cannot disagree with the database. Pinned by
  `tests/test_issued_multi_issuer.py::test_pdf_content_matches_stored_invoice_values`.
- **A draft has no PDF and no XML** — `_require_numbered` → 409 *"A draft has no PDF or e-invoice XML
  until it is issued."* The period ZIP export explicitly filters `number IS NOT NULL` ("a draft has no
  legal PDF — never export one"), capped at 500 documents.
- The logo comes from the invoice's **own** issuer entity (fallback: default).
- PDF generation degrades to **503** with a clear message if reportlab/pypdf are unavailable
  (`PdfUnavailable`), rather than corrupting output.

### 3.11 Recurring invoices + cross-worker dedup

`app/services/recurring.py`.
- A schedule stores a **frozen `IssuedInvoiceCreate` template** (buyer, lines, scheme, note, penalty,
  currency — *without* per-occurrence dates) plus cadence: `weekly|monthly|quarterly|yearly` ×
  `interval`, `start_date`, `next_run_date`, optional inclusive `end_date`, `active`.
- `advance()` adds whole months with **day clamping to the target month's last day** (31 Jan +1 month
  → 28/29 Feb) — `tests/test_recurring.py::test_advance_month_end_clamps`.
- `generate_due()` locks the issuer once for the whole sweep, then for each due schedule materialises
  occurrences while `next_run_date <= today`, bounded by a **`_CATCHUP_CAP = 24`** safety cap so a
  schedule paused for years cannot flood.
- **Idempotency is enforced twice**: a pre-check for an existing `(recurring_id, recurring_period)`
  invoice, plus the hard DB backstop
  `UniqueConstraint(org_id, recurring_id, recurring_period)` — so two overlapping workers cannot
  double-generate an occurrence (`tests/test_issued_guarantees.py::test_recurring_occurrence_dedup_constraint`).
- A schedule that has run past its `end_date` **auto-closes** (`active=False`).

### 3.12 Cash application (AR)

`app/services/payments.py` — an **append-only ledger** whose invariant is
`SUM(payments.amount) == invoices.amount_paid`.
- `set_cumulative` (the UI sends the new cumulative total) records the **delta** as a signed entry and
  refreshes the derived cache and `paid_date` (settlement date when fully paid; the supplied date for
  a partial; `None` when nothing is paid).
- `add_receipt_allocation` applies a bank receipt to an invoice; `reverse_allocation` appends an
  **offsetting negative entry** rather than mutating or deleting the original — the ledger is never
  rewritten.
- **Overpay cap**: `PATCH /{id}/payment` refuses `amount_paid > effective_total` (400, naming the
  amount owed after credit notes).
- **Row lock on settlement**: `_load(..., lock=True)` takes `SELECT ... FOR UPDATE of IssuedInvoice`
  so concurrent settlement writes serialize and the cap cannot be bypassed.
- A credit note refuses payment (400: "a credit note is not a receivable").
- Ledger visible at `GET /issued/{id}/payments`.

### 3.13 Attachments and tenant-scoped file access

`IssuedInvoiceAttachment` — supporting documents (signed PO, delivery note, contract). **Never part
of the legal invoice PDF.** Bytes go to content-addressed object storage; the row keeps
`sha256`/`size`/`mime`/`uploaded_by`. 25 MB cap; `filesec.reject_active_content()` blocks
executables/archives/scripts **before** storing. Download is scoped by `(id, invoice_id, org_id)` and
served **inert**: `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff`. The table
carries its own `org_id` **and** a composite FK `(org_id, invoice_id) → issued_invoices(org_id, id)`
— a cross-tenant attachment is structurally impossible.

### 3.14 Everything is audited and evented

Every AR mutation records an audit event (`issued.create/edit/approve/issue/duplicate/cancel/dispute/
undispute/write_off/void/credit_note/payment/sent/reminder/viewed/attachment`, plus
`recurring.create/update/generate`, `partner.document_sign`) and the money-moving ones emit signed
webhooks (`issued.created`, `issued.credit_note`, `issued.payment`).

### 3.15 AR reporting

`app/services/issued_reports.py` → `/issued/reports/{summary|receivables|partners|vat}`, each JSON or
`?format=csv`. Receivables has a `status` and an `aging` view. **All CSV output is
formula-injection-safe** (`_csv_safe` prefixes a leading `= + - @ TAB CR` with `'`). Reports are
**single-currency** (a `currency` filter, never a cross-currency sum) and drafts are excluded
(`tests/test_issued_draft_lifecycle.py::test_reports_exclude_drafts`).

### 3.16 Route-level enforcement summary (where the rules live)

| Rule | Enforced in |
|---|---|
| Module must be active | `modules.require_enabled(db, org, "issuing")` on every route |
| Issuer must be Art. 226-complete | `routes/issued.py::_guard`, `_resolve_issuer` |
| Read/write/send permission | `authz.require(...)` — router dependency `_require_issued_read` + inline write/send |
| Partner documents signed | `_enforce_partner_gate` (issue time only) |
| Only a draft is editable | `issued_lifecycle.is_editable` |
| Legal state transitions | `issued_lifecycle.target_for(action, current)` |
| Live receivable only (pay/credit/send/remind) | `_require_receivable` |
| Numbered document only (PDF/XML/export) | `_require_numbered` |
| Not voided | `_reject_if_voided` |
| No overpay | `record_payment` vs `effective_total` |
| No over-credit | `create_credit_note` vs `already_credited` |
| Gap-free unique numbering | `issuer.lock()` + `UniqueConstraint(org_id, number)` |
| Recurring dedup | `UniqueConstraint(org_id, recurring_id, recurring_period)` |
| Send idempotency | `sent_at` + last `EmailMessage` lookup |
| Tenant scope | route filter + ORM guard + Postgres RLS |

---
## 4. Data model (business view)

### 4.1 The tenancy spine

Every business row carries **`org_id`** with `ForeignKey("organizations.id", ondelete="CASCADE")`.
There are **63 tables** across 46 model modules (`app/models/`). Primary keys are portable UUIDs
(`app/models/base.py::GUID` — native Postgres `uuid`, `CHAR(36)` on SQLite), always handled as `str`
at the Python boundary so behaviour is identical in tests and production. Every table carries
`created_at`/`updated_at` (`TimestampMixin`).

**The cross-tenant-reference guard.** Where one tenant row references another, the model uses a
**composite foreign key `(org_id, child_id) → parent(org_id, id)`** backed by a
`UniqueConstraint(org_id, id)` on the parent. This makes a cross-tenant link *structurally
impossible at the database level*, not merely unlikely. Used by: `payments → issued_invoices`,
`payments → receipts`, `issued_invoice_attachments → issued_invoices`, `invoices → cost_centers /
departments / projects`, `cost_centers → departments`, `issued_invoices → issuer_profiles`,
`customers`. **A rebuild must keep this pattern** — it is the single most valuable structural idea in
the schema.

Child tables with no `org_id` (`line_items`, `issued_invoice_lines`) are reachable only through an
already-scoped parent. `expense_items` was deliberately given a denormalised `org_id` so it can be
scoped directly rather than trusting a join (migration `de3b47386d45`).

### 4.2 Core entity groups

**Tenancy & identity**
`organizations` (tenant root: name, plan, status, region, validation toggles, Stripe/EveryPay
linkage) → `users` (email unique globally, hashed password, 4-tier role, `is_active`,
`email_verified`, `is_platform_admin`, `is_expense_approver`, employee `iban`/`bic`,
`failed_login_count`/`locked_until`) · `memberships` (multi-org: user × org × status × role
snapshot) · `invitations` (token, expiry) · `sessions` (token revocation by `jti`) ·
`sso_connections` (per-tenant OIDC/SAML/SCIM config, client secret sealed) · `role_policies`
(configurable per-role usage limits) · `org_modules` (entitlements) · `usage_counters`
(`count`/`reported` watermark).

**AP record**
`vendors` (supplier master, unique `(org_id, name)`) → `invoices` → `line_items`.
`invoices` carries: identity (`invoice_number`, `issue_date`, `due_date`, `currency`), money
(`subtotal`, `tax_amount`, `total`, all `Numeric(14,2)`), **FX** (`fx_rate` = units per EUR,
`total_eur`, `fx_source` ∈ eur|stated|ecb|unknown), **aging status** (`status`), **workflow**
(`workflow_state`, `version` for optimistic concurrency, `locked_at`/`locked_by`, `submitted_by`/
`submitted_at`, `legal_entity_id`, `account_code`), **settlement** (`amount_paid` derived cache,
`paid_date`, `payment_run_id`), **dimensions** (free-text `cost_center`/`department`/`project`/
`vehicle`/`property_ref` + normalised `*_id` composite FKs), and **validation**
(`validation_status` ∈ none|passed|flagged|pending|approved|rejected, `validation_findings` JSON
array of `{severity, code, message, field}`, `validated_by`, `validated_at`).
Related: `supplier_payments` (AP ledger), `payment_runs`, `approval_policies`/`approval_steps`,
`invoice_comments`/`invoice_attachments`, `extraction_runs`/`extraction_fields`,
`inbound_invoices`/`email_intakes` (email capture), `receipts` (cash receipts), `bank_statements`/
`bank_lines`.

**AR record**
`issuer_profiles` (our legal entities, per-entity numbering + logo) · `customers`/`customer_contacts`
· `partners`/`partner_documents` → `issued_invoices` (invoice **and** credit note in one table,
discriminated by `doc_type`; self-FK `corrected_invoice_id`) → `issued_invoice_lines`,
`issued_invoice_attachments` · `recurring_invoices` · `payments` (AR ledger) · `dunning_policies` ·
`email_messages` (outbox).

**Master & reference data**
`departments` / `cost_centers` / `projects` (code + name + status active|archived + `version`
optimistic concurrency; **archived, never hard-deleted**) · `tax_codes` (catalogue, snapshotted onto
issued lines) · `currencies` · `ecb_rates` · `budget_targets`.

**Expenses**
`expense_policies` · `expense_reports` → `expense_items` (+ `expense_comments`) ·
`expense_transactions` (the bank-statement "available expenses" inbox) ·
`expense_approval_policies`/`expense_approval_steps` · `reimbursement_batches`.

**Platform & compliance**
`audit_events` (append-only, hash-chained) · `jobs` (durable queue) · `webhook_endpoints`/
`webhook_deliveries` · `documents`/`document_versions` · `retention_policies`/`legal_holds` ·
`billing_payments`/`processed_stripe_events` (idempotency ledger) · `auth_tokens` (email
verification / password reset).

### 4.3 Key lifecycles at a glance

| Entity | States |
|---|---|
| **AP invoice** (`workflow_state`) | uploaded → processing → review_required → draft → submitted → partially_approved → approved → (scheduled_for_payment → partially_paid → paid) · rejected · disputed · cancelled · archived |
| **AP invoice** (aging `status`) | draft · pending · paid · overdue |
| **Issued invoice** (`lifecycle`, stored) | draft · approved · issued · disputed · written_off · cancelled |
| **Issued invoice** (AR status, derived) | draft · approved · sent · viewed · open · partial · overdue · paid · credited · credit_note · disputed · written_off · void |
| **Master data** (`status`) | active · archived |
| **Organization** (`status`) | active · suspended · canceled |
| **Membership** (`status`) | active · (suspended/removed → hard 401) |

### 4.4 Derived-never-stored rule

A recurring, deliberate design invariant: **payment/aging status is computed from the amounts, never
stored**, so it can never drift (`app/services/issued_status.py`, `app/services/ap_status.py`).
Cached running totals (`amount_paid`) exist for read performance but are kept exactly equal to the
sum of an append-only ledger (`payments`, `supplier_payments`), and downward corrections are
*offsetting negative entries*, never mutations. **A rebuild must preserve this.**

---
## 5. Integrations & external surfaces

### 5.1 Inbound

| Surface | Contract | Auth | Notes |
|---|---|---|---|
| **File upload** | `POST /invoices/upload` (PDF, XML, CSV, JSON, PNG, JPEG; 15 MB) | user JWT | 202 + run id; parse on the worker; poll for the draft |
| **Email intake** | `POST /email/inbound` — a **provider-agnostic normalised payload** (`to`, `from`, `subject`, `token`, `secret`, base64 attachments) | **none by default** | Tenant resolved from a 16-hex address token (`<token>@in.invoiceiq.app`), rotatable. Optional shared secret. **No provider adapter is shipped** (SendGrid/Mailgun/Postmark would each need one) |
| **Bank statement** | `POST /reconciliation/statements` (CSV, camt.053 XML, PDF; 15 MB) | `PAYMENT_WRITE` | SHA-256 duplicate guard |
| **Bank statement (expenses)** | `POST /expenses/import/bank-statement` | expenses module | debits only → available-expenses inbox |
| **SCIM 2.0** | `/scim/v2/Users` (POST/GET/PUT/PATCH/DELETE) | per-connection bearer token | Users only; **no Groups, no discovery endpoints** |
| **OIDC callback** | `GET /auth/sso/callback` | signed state + PKCE | token returned to the SPA in the URL **fragment** |
| **SAML ACS** | `POST /auth/sso/saml/acs` | — | **501 Not Implemented — SAML login does not work** |
| **Billing webhooks** | `POST /billing/webhook`, `/billing/everypay/callback` | **signature / server-side verify, no bearer** | returns 200 on a verified-but-ignored event so the provider stops retrying |
| **ECB rates** | ECB `eurofxref-daily.xml` / `-hist-90d.xml` | — | pull-only, admin-triggered, never on the request path |

### 5.2 Outbound

| Surface | Format / contract |
|---|---|
| **E-invoice XML** | **EN 16931 UN/CEFACT CII**, guideline `urn:cen.eu:en16931:2017` (Factur-X Comfort). Type codes 380 invoice / 381 credit note. Emits seller/buyer with `schemeID="VA"` VAT registration, postal addresses, **BT-13** PO reference, delivery event, payment means TypeCode **58** (SEPA credit transfer) with IBAN, per-rate VAT breakdown incl. **BT-120** exemption reason, **BT-146** post-discount net price, and the full monetary summation. `GET /issued/{id}/xml` |
| **Hybrid PDF** | reportlab-drawn invoice with `factur-x.xml` embedded via pypdf — round-trips through the product's own reader. **Not strict PDF/A-3** (colour profile / XMP conformance is stated as a hardening step) |
| **UBL 2.1** | **inbound read only — there is no UBL writer** |
| **ERP / accounting** | CSV file exports only: **generic** (the only one carrying all cost dimensions), **Xero Bills** (NET per bill; account defaults `400`, TaxType `NONE`; cost centre → TrackingOption1), **QuickBooks Bills** (GROSS; account defaults "Accounts Payable"). **DATEV is deliberately absent** — it needs the German SKR chart + EXTF spec and would otherwise be a guess. **No live ERP connector, no OAuth, no push** |
| **SEPA** | **pain.001.001.03** for AP payment runs and for expense reimbursement batches |
| **Bank CSV** | payment-run and reimbursement-batch CSVs |
| **Reports** | Explore pivot CSV, AR report CSVs, audit CSV/JSON, accounting ledger CSV |
| **Webhooks** | 17 event types, HMAC-SHA256 signed, queue-delivered with retry/DLQ, SSRF-guarded |
| **Email** | Outbox-first (`email_messages` written before delivery); SMTP relay only if configured; failure never raises. Templates: invoice delivery, 3-tone dunning ladder, AP due digest |

### 5.3 What is deliberately NOT integrated

No Peppol Access Point, no AS4, no national e-invoicing portal (KSeF / SdI / FatturaPA / Chorus);
no bank connectivity (no EBICS, host-to-host, PSD2 AISP/PISP); no two-way ERP sync; no card issuing;
no lending/factoring; no tax filing. The PRD §7 states these boundaries explicitly — *"we never move
money, which keeps us outside PSD2/e-money licensing"*.

---

## 6. Compliance, legal & risk constraints

### 6.1 Tenant isolation — the P0 constraint

**A cross-tenant leak is a GDPR Art. 33/34 breach and is existential for this product** (PRD NFR-1,
risks register R1). Three independent layers:

1. **Per-route filters** — every query carries `.where(Model.org_id == current.org_id)`.
2. **ORM guard** — a `do_orm_execute` hook injects `with_loader_criteria(model, model.org_id == org)`
   for **every model in a 59-entry `TENANT_MODELS` tuple**, on **SELECTs only** (writes are guarded by
   loading the row scoped first). Bounded per request by a pure-ASGI `TenantScopeMiddleware` that
   resets the ContextVar at both ends, so a value can never leak between requests.
3. **Postgres RLS** — `ENABLE` + **`FORCE` ROW LEVEL SECURITY** with a `tenant_isolation` policy
   (`USING` **and** `WITH CHECK`) keyed off the per-transaction GUC `app.current_org`, kept in sync by
   an `after_begin` hook plus an explicit `apply_db_tenant()` for the request's first transaction.

**Governance that a rebuild must copy:** `tests/test_rls.py::test_rls_migration_covers_every_tenant_table`
asserts the union of `TENANT_TABLES` across all migrations **equals `{m.__tablename__ for m in
TENANT_MODELS}` exactly** — a new tenant table cannot ship without an RLS policy. Plus the
**composite-FK pattern** that makes cross-tenant references structurally impossible.

**Deliberate unscoped paths** (org context = `None`, which the RLS policy treats as pass-all):
bootstrap/unauthenticated routes, cross-org endpoints (`get_current_user_unscoped`, which *must*
filter by `user_id` themselves), platform-operator routes, the worker claim + scheduler sweeps, and
billing webhooks (which resolve the org then re-scope before mutating).

**Behavioural rule proven in tests:** a cross-tenant fetch by id returns an **opaque 404, never 403**
— object-id guessing yields no information.

**Known caveats:** the *live* RLS test only runs when `RLS_TEST_DATABASE_URL` is set (the CI Postgres
job supplies it); a Postgres **superuser bypasses RLS**, so the app must not run as one.

### 6.2 Financial-correctness invariants

| # | Invariant | Enforced by |
|---|---|---|
| FI-1 | All currency amounts are `Decimal`, quantized **ROUND_HALF_UP** to 2dp; **never float** | `core/money.py::q2`, `Numeric(14,2)`, `test_money_invariants.py::test_money_never_uses_float` |
| FI-2 | Server recomputes every total; client-supplied tax/totals are never trusted | `vat.compute` (AR), `persist_invoice` / `_reconcile` (AP) |
| FI-3 | `SUM(payments.amount) == issued_invoices.amount_paid` and `SUM(supplier_payments.amount) == invoices.amount_paid` | append-only signed ledgers; corrections are **offsetting negative rows**, never mutations |
| FI-4 | Payment/aging status is **derived, never stored** | `issued_status.py`, `ap_status.py` |
| FI-5 | No overpayment — AR capped at `total − credited`, AP capped at `total`, allocation capped by both the receipt's unallocated balance and the invoice's outstanding | route-level checks + `SELECT ... FOR UPDATE` |
| FI-6 | No over-crediting — total credited ≤ invoiced total (1-cent tolerance), `credited_total` clamped | `create_credit_note` |
| FI-7 | Invoice numbers are **gap-free, sequential and unique per issuer entity** | `issuer.lock()` FOR UPDATE + `UniqueConstraint(org_id, number)`, proven under real Postgres concurrency |
| FI-8 | An issued document is **immutable**; correction is only by credit note | `issued_lifecycle.EDITABLE = {draft}` |
| FI-9 | The rendered PDF/XML always matches the stored values | both rebuilt from the stored lines through the same `vat.compute` |
| FI-10 | Reports never sum across currencies | `test_issued_report_never_sums_across_currencies` |
| FI-11 | FX provenance is always one of `{eur, stated, ecb, unknown}`; `unknown` yields NULL, never a wrong number | `fx.eur_total` + test |
| FI-12 | Recurring generation is idempotent across workers | `UniqueConstraint(org_id, recurring_id, recurring_period)` |
| FI-13 | Invoice email is idempotent — one send per invoice unless `resend` | `sent_at` + last-message lookup |
| FI-14 | All CSV/Excel exports are formula-injection-safe | `_safe`/`_csv_safe` in four writers |

**Known invariant violations to fix in a rebuild:** `ap_aging.summarize` sums outstanding **across
currencies** without conversion; `reimbursement.eur_of` / `payment_run.eur_of` fall back to the raw
foreign `total` and then label the sum EUR (and the SEPA file emits it as `Ccy="EUR"`); the expense
module's item-level `fx_rate` **multiplies** while the ECB convention **divides** — two unlabelled
conventions coexist and `fx_source` on an expense item is unvalidated free text.

### 6.3 Legal / regulatory obligations the design encodes

- **EU VAT Directive 2006/112/EC Art. 226** — the mandatory invoice content set, enforced as the
  issuer-profile completeness gate + the buyer block + the per-rate VAT breakdown.
- **Art. 196 / 138 / 132–137** — the legal notes required under reverse-charge / intra-EU / exempt
  schemes, auto-attached and defaulted into EN-16931 BT-120.
- **Art. 233** — authenticity of origin, integrity of content and legibility for the whole retention
  period. Addressed by SHA-256 content-addressed storage + re-hash integrity sweeps + the hash-chained
  audit trail + inert document serving. **The PRD flags this as needing legal review, not as settled.**
- **EN 16931 / ViDA (2028–2030)** — structured e-invoicing in and out is the on-ramp; national
  formats are treated as a post-MVP expansion matrix.
- **GDPR** — the company is a **processor** for tenant-uploaded business data and a **controller** for
  account/usage data. Required: a DPA per customer, an Art. 30 record, a 72-hour Art. 33/34 breach
  process, DSAR handling, and sub-processor transparency. Art. 17(3)(b) is cited in code as the reason
  issued invoices survive an erasure request.
- **Statutory retention** — varies by member state; the PRD's working assumption is a **10-year
  default, tenant-configurable, with legal hold**, and erasure that **surfaces rather than silently
  resolves** the retention conflict.
- **PSD2 / e-money** — avoided by construction: the product never moves money. Any future banking or
  lending feature must go through a licensed partner.
- **Data residency** — EU by default; a per-tenant `region` with a fail-closed **421** backstop exists
  but is off by default and single-region in practice.

### 6.4 Authorization & segregation of duties

Deny-by-default permission matrix (§1.5), enforced as an **imperative `authz.require(...)` call inside
each handler** — which means coverage is a per-route discipline with **no framework-level guarantee**.
Confirmed gaps: the whole `partners` router has **no permission checks** (any member of an
issuing-enabled org can create partners and sign the contract/acceptance documents that gate
invoicing); `vendors` create/update has neither a permission check nor an audit record despite
controlling the IBAN that gets paid; and several read endpoints (`GET /team/members`, `GET /webhooks`,
`GET /jobs`, `GET /access/*`, `GET /modules`, `GET /settings/validation`, the six KPI analytics
endpoints) are open to any authenticated member.

Existing SoD controls: AP submitter cannot approve their own invoice; expense claimant cannot approve
their own report; a named approval step is decidable only by its assignee; queue-jumping is refused;
billing is owner-only; `PAYMENT_*` is a distinct duty from `INVOICE_APPROVE`.
**Missing SoD:** no maker≠checker enforcement on a **payment run** (a user holding both permissions can
approve and pay), and no dual control on a supplier bank-detail change.

### 6.5 Operational risk gaps worth naming

1. **Org suspension is only checked at login** — an already-issued token keeps working on a suspended
   tenant for up to its 24-hour TTL.
2. **No tenant deletion / offboarding path** exists at all.
3. **No GDPR data-portability export** (Art. 20).
4. **The inbound email endpoint's shared secret is optional** — unset means anyone who guesses a
   64-bit token can inject documents into a tenant's review inbox.
5. **Rate limiting is per-process**, so with N replicas the real ceiling is N × limit (documented as a
   deliberate first version; a shared store is the scale path).
6. **The default KEK derives from the app secret** — rotating `SECRET_KEY` invalidates every sealed
   value. Cloud KMS is a seam, not a build.

---

## 7. What a rebuild must do — prioritized requirements

*MUST = the product is not this product without it. SHOULD = required to sell to the stated segments.
COULD = differentiator, not a blocker. Each is written to be testable.*

### 7.1 MUST — tenancy, security, correctness

- **M1.** Every business row carries `org_id`. **Three independent isolation layers**: per-query
  filters, an automatic ORM-level scope guard over a registry of tenant models, and database-enforced
  row-level security keyed to a per-transaction session variable. *Test: an unfiltered query in
  tenant B's context returns zero of tenant A's rows; a raw SQL query does too.*
- **M2.** **A CI test must assert that the set of RLS-protected tables equals the set of tenant-scoped
  models exactly.** A new tenant table cannot merge without a policy. *Test: adding a model without a
  policy fails the build.*
- **M3.** Cross-tenant references use a **composite FK `(org_id, child_id) → parent(org_id, id)`**.
  *Test: inserting a child pointing at another tenant's parent is rejected by the database.*
- **M4.** A cross-tenant or non-existent object returns an **opaque 404, never 403**.
- **M5.** **Deny-by-default authorization**: a permission vocabulary, a role→permission matrix, and a
  single `require(user, permission)` choke point. Routes ask for permissions, never inspect roles.
  *Test: every role appears in the matrix; an unlisted permission is denied.* **Improve on the
  original: enforce it as a route dependency/decorator so coverage is structural, and add a CI test
  that every mutating route declares a permission.*
- **M6.** All money is `Decimal`, quantized **ROUND_HALF_UP** to 2dp, stored `Numeric(14,2)`. No float
  arithmetic on currency anywhere. *Test: a source scan proves no float path touches money.*
- **M7.** The server recomputes **every** total, tax and line amount. A client-supplied total is
  ignored. *Test: posting a wrong total yields the recomputed one.*
- **M8.** Payment/aging status is **derived from amounts and dates, never stored**.
- **M9.** Settlement is an **append-only signed ledger**; the cached running total equals the ledger
  sum; a correction or reversal is an **offsetting negative entry**, never a mutation or delete.
  *Test: reverse an allocation, assert the original row is unchanged and the invariant holds.*
- **M10.** **Overpayment and over-crediting are impossible**, enforced under a row lock.
- **M11.** Every data change is attributed to an actor in an **append-only, hash-chained audit trail**
  with a per-tenant monotonic sequence and a uniqueness constraint that makes a concurrent append
  conflict rather than fork. The chain is verifiable and exportable with `prev_hash`/`hash` so an
  auditor can re-verify offline. Audit recording is best-effort (never breaks the operation) but
  commits atomically with it.
- **M12.** **One file-security choke point** before any parse/OCR: size cap, magic-byte type
  validation against a per-context allowlist, active-content rejection, malware scan that **fails
  closed** when a scanner is configured. XML parsed with an XXE-hardened parser. All documents served
  inert (`attachment` + `nosniff`).
- **M13.** **Production config validation crashes at boot** on a development secret, a non-production
  database URL, a missing KEK, or a wildcard CORS origin. API docs disabled in production.
- **M14.** Stored third-party secrets are **envelope-encrypted (AES-256-GCM, AAD-bound)** and never
  logged; tampering raises rather than silently returning empty.

### 7.2 MUST — the AR / invoicing engine

- **M15.** An **issuer registry of multiple legal entities**, each with its **own gap-free numbering
  series** (and a **separate credit-note series**), its own logo, payment terms, default currency and
  penalty rate. Exactly one default. *Test: two entities number independently and in parallel.*
- **M16.** An invoice may only be created/issued when the chosen issuer satisfies the **Art. 226
  minimum** (legal name, VAT number, address line, city, postal code, country); otherwise a 409 naming
  the missing fields.
- **M17.** **Concurrency-safe numbering**: allocate under `SELECT ... FOR UPDATE` on the issuer row,
  with a `UNIQUE(org_id, number)` database backstop. *Test: N concurrent issues on one entity produce
  N gap-free unique numbers — run against real Postgres, not SQLite.*
- **M18.** **The seller is snapshotted onto the invoice at issue time.** Editing the issuer profile
  afterwards must never change a finalized document.
- **M19.** Buyer details are prefilled from the customer master but **snapshotted onto the invoice**.
- **M20.** Lifecycle `draft → approved → issued → (disputed ⇄ issued) → written_off`, plus
  `cancelled` from draft/approved. **Only a draft is editable or deletable.** An illegal transition
  returns 409 naming the legal source states.
- **M21.** **A draft carries no number** and has no PDF, no e-invoice XML and no place in any export or
  report. *Test: reports exclude drafts; the period ZIP filters unnumbered documents.*
- **M22.** **An issued invoice is immutable; the only correction is a credit note** with its own
  series, linked to the corrected invoice, reusing that invoice's frozen seller snapshot, carrying no
  due date, reducing the invoice's outstanding balance and the reported turnover.
- **M23.** **Over-crediting is refused** (1-cent tolerance) and the running credited total is clamped
  to the invoiced total. Omitting lines credits exactly the remaining amount, scaling the original
  lines so the rate mix is preserved. A credit note cannot be credited.
- **M24.** **Cancel, void and write-off are three distinct events.** Void is refused on an invoice with
  any payment recorded or any credit note against it. A written-off invoice owes nothing but **keeps
  its turnover on record**.
- **M25.** **Server-side tax**: per-line discount, per-rate bucketing with rounding applied at the
  bucket level, and the four VAT schemes (`standard`, `reverse_charge`, `intra_eu`, `exempt`) where
  the three zero-rate schemes force every line to 0% **regardless of the rate entered** and attach the
  required legal note / exemption reason.
- **M26.** **The PDF and the e-invoice XML are rebuilt from the stored line rows through the same tax
  function**, so the rendered document can never disagree with the database. *Test: assert PDF content
  matches stored values.*
- **M27.** Outbound e-invoice is **EN 16931** (CII today; UBL should be added), embedded in the PDF as
  a hybrid Factur-X document that the product's own reader round-trips.
- **M28.** **Sending is idempotent** — a repeat send is a no-op returning the first delivery, unless
  explicitly a resend. `sent_at` records only the first delivery. View tracking is first-wins and
  idempotent and requires a prior send.
- **M29.** **Recurring schedules are idempotent across workers** via a `UNIQUE(org, schedule, run
  date)` constraint plus a pre-check, with a **catch-up cap** so a long-paused schedule cannot flood,
  month-end day clamping, and auto-close past the end date.
- **M30.** File access is tenant-scoped by `(document id, parent id, org id)` and served inert.

### 7.3 MUST — AP and the record

- **M31.** Multi-channel capture (upload, email, API) with a **deterministic-first chain**: structured
  e-invoice XML → embedded hybrid-PDF XML → PDF text layer → OCR. Every path produces the **same
  reviewable draft**; **nothing persists until a human confirms**.
- **M32.** **Capture runs off the request tier** (202 + poll or callback), so a burst of scanned PDFs
  never ties up the API.
- **M33.** **Honest confidence**: a structured/typed source is *exact* (no confidence score), not
  "0.99". Per-field provenance keeps `original`, `normalized` and `reviewed` values and a
  low-confidence flag. *Improve on the original: extend provenance to line items.*
- **M34.** **One validation engine**, not two, with an explicit **block vs advise** policy per rule.
  Minimum rule set: missing mandatory fields, subtotal/tax/total consistency, per-line math, duplicate
  (same supplier + number) and cross-supplier duplicate, date sanity, unknown currency, FX deviation
  from the reference rate.
- **M35.** An AP workflow with **optimistic concurrency** (stale write → 409), a **record lock on
  approval** (reopening is an audited controlled correction), and **segregation of duties** (the
  submitter cannot approve).
- **M36.** **Supplier bank-detail changes must be permission-gated, audited, and validated** (IBAN
  mod-97 + BIC format), ideally with a change-freeze or re-verification window before the account is
  paid. *This is the single largest control gap in the original.*
- **M37.** Supplier payment cannot exceed the invoice total; a payment run cannot include an invoice
  already in a run; a run is version-guarded and row-locked.

### 7.4 MUST — platform operations

- **M38.** A **durable database-backed job queue** with idempotent enqueue, an atomic single-winner
  claim, exponential backoff, a **dead-letter state**, stale-lease reclaim, and immediate
  dead-lettering of an unknown job kind. Handlers execute inside the job's tenant scope.
- **M39.** A **stateless scheduler** — periodic work is enqueued with a date-keyed idempotency key, so
  scheduling is re-entrant and needs no cron table.
- **M40.** A **queue SLO probe** returning 503 when the dead-letter depth or oldest-pending age
  breaches thresholds, exposing aggregate numbers only.
- **M41.** **Configurable retention with legal hold.** Absence of a policy means keep forever. An
  active hold blocks every purge **and** every erasure. Ledger documents and audit events are never
  purged. Purges are audited and remove the stored bytes.
- **M42.** **GDPR erasure that respects statutory retention** — pseudonymise the person, retain the
  legally-required financial records and the audit chain, and **surface the conflict** rather than
  silently resolving it. Record the request against a hashed subject reference.
- **M43.** Session-bound tokens that are **revocable on every request**, brute-force lockout, and
  enumeration-safe verification/reset flows.
- **M44.** Entitlements in three layers: **module gating**, **plan gating**, and **usage quotas** that
  return a payment-required status at the cap with the usage visible in-product. **Never lose or
  delete a document because of a limit.**
- **M45.** Signed outbound webhooks delivered through the job queue (retry + DLQ inherited), with
  **SSRF protection re-checked at delivery time**.

### 7.5 SHOULD

- **S1.** **Bank reconciliation must be able to post cash**, not merely annotate — or the product must
  state loudly that matching is bookkeeping only. Add **partial and many-to-many matching**, a
  **configurable tolerance** (absolute and percentage), and a **write-off / cash-discount tolerance**.
- **S2.** **A real cash forecast** — the current "cash flow" view is historical only. Project from due
  dates, payment runs and recurring schedules.
- **S3.** **Payment-run selection intelligence** — a due-date window, early-payment discount capture,
  a cash-availability constraint, and per-creditor aggregation in the payment file.
- **S4.** **Maker ≠ checker on the payment run**, and an **export-once guard** on bank files with a
  unique message id per generation (the original re-emits an identical `MsgId`).
- **S5.** **IBAN mod-97 and BIC format validation** before any payment file is produced, and surface
  (never silently discard) the count of payees skipped for a missing IBAN.
- **S6.** **A scheduled FX refresh job**, and remove the demo "wobble" from the seed path. Keep the
  `approximate` / `indicative` provenance flags — they are excellent.
- **S7.** **One currency registry**, not two (the tenant catalogue and the FX currency list can
  disagree today).
- **S8.** **Multi-currency reporting throughout** — several analytics surfaces hard-code EUR while the
  AR reports correctly force a single currency per report.
- **S9.** **Unify cost-allocation dimensions into the Explore engine** and finish the free-text →
  master-data migration (including master tables for vehicle and property). Consider **split/percentage
  allocation** across cost objects.
- **S10.** **Ship the capture-review UI** — the backend queue, per-field review and lineage endpoints
  exist with **zero frontend consumption**. Either build it or delete the endpoints.
- **S11.** **A learning loop** — reviewed corrections are stored but never fed back. At minimum add
  per-vendor field-mapping memory.
- **S12.** **Email-intake hardening** — make the shared secret mandatory and ship at least one real
  provider adapter.
- **S13.** **Enterprise identity go-live** — finish SAML with a vetted XML-DSig library (never
  hand-rolled), add SCIM Groups and discovery endpoints, and prove OIDC against a real IdP.
- **S14.** **Re-check tenant status on every request**, not only at login.
- **S15.** **A tenant offboarding path** (export → delete) and a **GDPR data-portability export**.
- **S16.** **ERP exports with configurable account/tax mappings** (the Xero/QuickBooks defaults are
  hard-coded constants), and DATEV/SAF-T only against a real chart of accounts and country profile.
- **S17.** **A multi-client console for accountancy practices** — the stated beachhead. Org switching
  exists; the console does not.
- **S18.** **Approval SLAs, escalation and out-of-office delegation** in both approval engines
  (today only manual reassignment exists).
- **S19.** **Cross-report and cross-employee expense duplicate detection** (today it is intra-report
  only) and a real **per-diem rate table**.
- **S20.** **Dunning: promise-to-pay, payment plans, and a dunning hold**; consider statutory
  late-payment recovery fees (EU Dir. 2011/7).
- **S21.** **Webhook secret rotation, replay/redelivery, and a signature timestamp.**
- **S22.** **Distributed rate limiting** once a metric shows the per-replica ceiling is insufficient.

### 7.6 COULD

- **C1.** A real document-AI / OCR provider behind the existing extraction seam, with per-field
  confidence surfaced (the interface and honest-confidence plumbing already exist).
- **C2.** Peppol / AS4 transmission and national e-invoice formats (KSeF, FatturaPA, Chorus) as ViDA
  deadlines land.
- **C3.** Supplier-portal credentialed capture adapters (the credential vault is real; the adapters
  are not).
- **C4.** SEPA direct debit (pain.008) + mandate management — required for any "collect automatically"
  collections story.
- **C5.** Materialised analytics rollups for very large tenants (the Explore engine is designed to
  read one with no API change).
- **C6.** Three-way match (purchase order + goods receipt) if the product moves upmarket.
- **C7.** Adopt the already-built grouped navigation IA (Overview / Payables / Receivables / Insights /
  Workspace) that exists as a fixture-driven design showcase but is not wired to the live app.

---

## 8. Maturity assessment — what is real

### 8.1 Production-grade (real logic, enforced, tested)

Tenant isolation (three layers + the CI parity test + composite FKs) · the AR invoicing engine end to
end (numbering, immutability, credit notes, lifecycle, recurring, cash application, reports) ·
server-side VAT + EN-16931 CII generation + hybrid Factur-X PDF · money/Decimal discipline and FX
provenance · deterministic e-invoice reading (UBL 2.1 + CII + embedded Factur-X, XXE-hardened) · the
file-security gate · off-tier extraction with lineage · the AP 14-state workflow with optimistic
concurrency, record lock and SoD · the AP approval-policy engine · both settlement ledgers · payment
runs · the expense state machine, policy engine, approval chain and reimbursement batches · SEPA
pain.001 generation · CSV/camt.053/PDF statement parsing (the running-balance heuristic is a genuine
differentiator) · the dunning ladder with fire-once idempotency · the Explore pivot engine · the
hash-chained audit trail + offline-verifiable export · retention + legal hold · GDPR erasure ·
content-addressed storage + integrity sweeps · the durable job queue + stateless scheduler + SLO probe
· signed SSRF-guarded webhooks · session-revocable auth with lockout · OIDC + SCIM Users · billing
code for both providers · production config validation.

### 8.2 Real but scope-limited

PDF/OCR heuristics (well engineered but heuristic — vendor is "the first line that looks like a name",
VAT rate is *inferred*; every result is warning-labelled) · ERP export (file templates with hard-coded
account/tax defaults; DATEV deliberately not attempted) · email intake (complete pipeline,
provider-agnostic, **no adapter shipped**, secret optional) · the combined supplier benchmark
(mathematically sound, self-declared advisory) · budget (category-only, EUR-only, no alerts) · the
tax-code catalogue (reference data the UI reads; issued lines still store a raw rate, the FK is future
work) · cash position (a working-capital view, not a bank balance) · multi-org membership (works, but
`users.org_id` is still the authoritative projection — a dual-write migration in progress).

### 8.3 Advisory by design — never blocks

`ai_enrich()` (a literal no-op LLM seam) · AI validation findings · AP duplicate candidates · receipt
OCR suggestions · expense policy findings unless explicitly listed as blocking · bank-statement
matching (`verified` flag) · **bank reconciliation itself** (annotation only — it never posts cash) ·
accrued late-payment interest (computed on read, never added to a total, never invoiced) · the
benchmark savings opportunity · budget overspend.

### 8.4 Stubs, seams and the honestly unfinished

| Item | Reality |
|---|---|
| **"AI" extraction** | **No AI exists.** Five deterministic providers wrapping local libraries; the only ML is Tesseract. The `ExtractionProvider` registry is a well-designed, empty seam. |
| **`NullProvider` (billing)** | The default. Every money operation raises "Billing is not configured." Nothing charges anyone. |
| **SAML** | Request side + SP metadata work; **the ACS endpoint returns 501** — SAML login does not function, deliberately, with the reasoning documented. |
| **SCIM** | Users only — no Groups, no `/ServiceProviderConfig`, `/Schemas` or `/ResourceTypes`, no ETags. |
| **Data residency** | Model + fail-closed 421 backstop exist and are tested; single-region in practice, off by default, no tenant-relocation path. |
| **Cloud KMS** | A documented seam. The default KEK derives from the app secret. |
| **DATEV / SAF-T** | Deliberately absent pending a real chart of accounts and country profiles. |
| **UBL outbound** | Read-only — no writer. |
| **E-invoice transmission** | No Peppol AP, no AS4, no national portal. Delivery is an email attachment or a download. |
| **PDF/A-3 conformance** | Explicitly not achieved — a functional embedded-XML hybrid only. |
| **Bank connectivity** | None. SEPA files are downloads a human uploads to a bank portal. |
| **pain.008 / mandates / pain.002 / camt.052/054** | Entirely absent. |
| **Cash forecast** | Absent — the "cash flow" module only looks backwards. |
| **FX gain/loss** | Absent — no revaluation, `total_eur` stamped once. |
| **Capture-review UI** | Backend complete; **zero frontend consumption** (verified by grep). |
| **Extraction learning loop** | Does not exist. |
| **Dead AP workflow states** | `uploaded`, `processing`, `review_required` are declared and reachable in the transition table but **never assigned anywhere**. |
| **The 8-role matrix** | Aspirational at the data layer — only four role values are ever stored; Finance Manager, Accountant, Approver and Auditor are unreachable except through the forward-compatible resolver. |
| **Three-way match** | No purchase-order or goods-receipt entity exists. |
| **Grouped navigation IA** | Built as a fixture-driven showcase under `/design`; the live app still ships a flat ~25-item nav. |
| **`reclaimable_tax` (expenses)** | Captured on every item and **never read by any computation**; the "reclaimable VAT" figure sums all VAT including drafts and rejected reports. |

### 8.5 Documentation health

`README.md` and `ARCHITECTURE.md` are **materially stale** (they describe a 12-test analytics MVP);
`docs/architecture/data-model.md` marks as "target/not built" several things that now exist
(`payments`, `customers`, `tax_codes`, approval policies). By contrast `docs/product/*` (PRD,
personas, workflows, pricing, risks, metrics), `docs/architecture/domain-modules.md`,
`docs/security/authorization-policy-matrix.md`, the 22 ADRs, `docs/BACKLOG.md` and
`docs/DECISIONS-NEEDED.md` are current, honest and unusually high quality. **A rebuild should treat
the product docs and ADRs as the specification and the top-level READMEs as legacy.**

---

## 9. Open questions a rebuild team must get answered

### 9.1 Strategy & scope (blocking — these change what gets built first)

1. **What is the actual product?** The code is four products (AP, AR, expenses, banking/cash). The PRD
   says sell AP capture + VAT correctness, with AR issuing as an attach. **But AR is the most mature,
   most defensible thing in the repo.** Confirm the wedge before writing a line.
2. **Beachhead: accountancy practices or direct-to-SME?** This decides whether the first surface is a
   multi-client console (which does not exist) or single-workspace polish.
3. **Which 2–3 countries first?** Determines VAT rules, e-invoice formats, retention periods, chart of
   accounts for ERP export, and language.
4. **Is the expenses module in or frozen?** It is substantial, maintained, and explicitly cut from the
   sellable wedge. Keeping it warm has a real maintenance cost.
5. **Is the transport/fleet angle (vehicle dimension, fuel, VAT refund) a primary vertical?** There is
   a sibling VAT-refund product; the relationship is undefined.

### 9.2 Financial & functional semantics (ambiguous in the code)

6. **Should reconciling a bank line settle an invoice?** Today it is pure annotation. If it should
   post cash, the whole matching design changes (tolerances, partials, many-to-many, unapplied cash).
7. **What is "cash position" supposed to mean?** There is no bank-account entity or balance; today's
   figure is receivables minus payables. Is a real bank balance in scope?
8. **Which FX convention is canonical?** The invoice/ECB side divides; the expense side multiplies. And
   should there be FX revaluation / gain-loss accounting at all?
9. **What happens to a foreign-currency payment file?** Today a non-EUR amount with no EUR conversion
   is emitted labelled `Ccy="EUR"`. Multi-currency payments need an explicit answer.
10. **Should `reclaimable_tax` on an expense item drive anything?** It is captured and ignored.
11. **Is per-invoice single-value dimension tagging enough, or is split allocation required?**
12. **Should validation ever block?** Two engines disagree today. Define the block/advise policy per
    rule.
13. **Should tax codes be enforced on issued lines** (an FK) or remain a rate-picker convenience?
14. **Should a partial payment pause the dunning ladder?** Today it does not.

### 9.3 Compliance & legal (block launch)

15. **Confirm the default retention period** (the PRD proposes 10 years, tenant-configurable) and the
    **erasure-vs-retention policy** with counsel.
16. **Confirm the data-residency commitment and the sub-processor list**, especially any AI/OCR vendor
    and its region.
17. **Is a signed DPA + Art. 30 record required before the first paying customer?** (Assume yes for
    accountants and regulated SMEs.)
18. **What is the stance on AI processing of customer documents** — EU-hosted only and default-off, or
    opt-in external models under SCCs?
19. **Has the Art. 233 authenticity/integrity claim been reviewed by counsel?** The technical substrate
    exists; the legal claim has not been validated.
20. **Do we pursue SOC 2 Type II / ISO 27001?** The technical substrate is built; certification is a
    business decision with process cost.

### 9.4 Commercial

21. **Primary billing metric** — documents processed per month, seats, or a hybrid? (Both are already
    metered; the current *quota* keys off the individual user's role, not the org's plan, which is
    almost certainly wrong for a hybrid model.)
22. **Which billing provider** — Stripe (control, usage-based) or a merchant-of-record like Paddle
    (handles EU VAT on our own subscriptions)? Related: **who is the seller of record**, and who owns
    VAT registration and filing?
23. **What is the plan ladder?** The code has trial/starter/pro/enterprise at €0/€29/€99/custom; the
    pricing hypothesis proposes Free/Starter €39/Team €99/Business €249/Enterprise plus a per-seat
    **Practice** partner plan. They do not match.
24. **Overage policy** — block at the cap or auto-charge? (Guardrail already stated: never lose a
    document because of a limit.)

### 9.5 Technical decisions carried over

25. **Scoped API keys** — routes currently assume the caller is a real user row; a machine principal
    model is undesigned.
26. **Refresh-token rotation and access-token TTL** before any public API GA (24h bearer tokens today).
27. **Production KEK provider** — stay on env/BYOK or wire a cloud KMS?
28. **Which IdP to certify against** for the SSO go-live, and approval to add a vetted SAML library.
29. **Will multi-region actually be stood up**, or should region pinning be removed rather than
    advertised?
30. **How far does the multi-org membership migration go** — is `users.org_id` retired, and does a user
    get to create a second org?

---

## Appendix A — user-facing surface (frontend)

39 pages / ~15.5k LOC of React. Public: login, SSO callback, accept-invite, verify-email,
forgot/reset password. Authenticated: Dashboard, Cash position, Explore, Benchmark, FX, Invoices,
Invoice detail, Invoice review, Review queue, Upload, Email intake*, Budget*, Issue*, Issued reports*,
Customers*, Receipts*, Reconciliation*, Partners*, Dunning settings*(admin), Issuer, Payment runs,
Vendors, Expenses*, Expense detail*, Expense policy*(admin), Reimbursements, Team, Access matrix
(owner), Audit (owner), Sessions, Billing, Platform (operator), Settings. `*` = module-gated.

**Two structural observations for a rebuild.** (1) **The frontend lags the backend substantially** —
there is no UI for documents, retention, privacy/DSAR, webhooks, jobs, integrity, tax codes,
currencies, costing master data, recurring schedules, or the capture-review queue, all of which have
complete APIs. (2) Frontend gating is **cosmetic** (nav filtering only; routes remain reachable by
URL) — the real enforcement is always server-side, which is correct, but the UI must not be treated as
a control.

## Appendix B — scale of the artifact

63 tables · 46 model modules · 39 route modules · 71 service modules · ~32k LOC backend + ~15.5k LOC
frontend · 61 Alembic migrations · 114 test modules / ~17.7k test LOC / 761 passing tests · 7 CI jobs
including a real-Postgres job for RLS and concurrent numbering · 22 ADRs.
