# InvoiceIQ — Personas & Value Propositions

> Companion to the [PRD](./product-requirements.md). Personas are grouped by role in the buying/using motion: **buyer**, **administrator**, **end-user**, plus the **partner/channel** persona (accountants) that is our beachhead. Each includes goals, frustrations, success criteria, and the specific value we deliver.

The persona set maps onto the product's four company roles (`user`/`admin`/`owner` + separate platform operator) — see [PRD §5 F-G3]. Note the deliberate rename: the company's top role is **owner** (scoped to their company), never a system-wide admin.

---

## Persona map at a glance

| Persona | Segment | Buys? | Admins? | Uses daily? | App role |
|---|---|---|---|---|---|
| Priya — Practice Principal | Accountancy firm | ✅ economic buyer | — | — | owner (of firm workspace) |
| Marco — Client Manager / Bookkeeper | Accountancy firm | influencer | ✅ per-client | ✅ heavy | admin |
| Sofia — SME Owner / MD | Multi-entity SME | ✅ economic buyer | — | occasional | owner |
| Tom — Finance Lead / Bookkeeper | SME | champion | ✅ | ✅ heavy | admin |
| Elena — AP Clerk / Office Manager | SME | — | — | ✅ heavy | user / processor |
| David — External Auditor | SME's auditor | — | — | read-only, periodic | user (read-only) |
| Nadia — Platform Operator | InvoiceIQ (us) | — | ✅ cross-tenant | ✅ | platform operator |

> The **employee expense claimant** is intentionally *not* a v1 primary persona — the expenses module is post-MVP (see PRD §8). Included as a secondary persona below for completeness.

---

## 1. Priya — Accountancy Practice Principal  *(Economic buyer · Beachhead)*

- **Context:** Runs a 12-person practice serving 120 SME clients. Owns the tooling budget and the P&L. Time is her firm's product.
- **Jobs:** Grow the practice without adding headcount; cut non-billable data-entry hours; reduce compliance risk across clients; stay ahead of e-invoicing mandates.
- **Frustrations:** Staff spend hours re-keying client invoices; every client sends data differently; errors surface at year-end; existing capture tools don't handle EU VAT/multi-entity well; onboarding a new client is painful.
- **Buying trigger:** "If this saves each bookkeeper 5 hours a week, it pays for itself and I can take on more clients."
- **Success criteria:** ↓ hours-per-client, ↑ clients-per-bookkeeper, clean audit trail, painless client onboarding.
- **Value proposition:** *"Process more clients with the same team. One place to capture, check, and export every client's invoices — VAT-correct, multi-entity, audit-ready — with the errors caught before they reach the books."*
- **What she needs from us:** multi-client console, per-client isolation with a warranty, seat-based pricing that scales with her team, a DPA she can rely on, and export into the accounting systems she already runs.

---

## 2. Marco — Client Manager / Bookkeeper  *(Power user · Influencer)*

- **Context:** Handles 15–20 client companies. Lives in the tool 6 hours a day. Priya listens to him on tooling.
- **Jobs:** Capture each client's invoices fast; catch duplicates/VAT errors before filing; reconcile the period; export to the client's ledger; answer "where did this money go?" instantly.
- **Frustrations:** Context-switching between clients; chasing missing invoices; manual duplicate hunting; re-keying from PDFs; end-of-quarter crunch.
- **Success criteria:** minutes-per-invoice down, zero duplicate payments slipped through, one-click period export, fast client switching.
- **Value proposition:** *"Switch clients in a click, drop in any file format, and let the checks flag the problems. Reconcile a month in an afternoon, not a week."*
- **What he needs:** fast capture + review, strong duplicate/validation flags, dimensions to slice spend, clean exports, and rock-solid tenant separation so he never mixes up two clients.

---

## 3. Sofia — SME Owner / Managing Director  *(Economic buyer · Direct segment)*

- **Context:** Runs a 60-person transport company with 3 legal entities and 40 vehicles. Not an accountant. Wants control and visibility, not spreadsheets.
- **Jobs:** Know spend per entity/vehicle/project in real time; stop overpaying and missing invoices; be ready for VAT and e-invoicing obligations; give her bookkeeper and auditor a clean record.
- **Frustrations:** Fuel and supplier invoices scattered across cards and inboxes; no per-vehicle cost view; surprises at VAT time; reliance on one person's spreadsheet.
- **Buying trigger:** a missed/duplicate invoice, an audit scare, or a new e-invoicing mandate.
- **Success criteria:** live spend-by-dimension, fewer errors, "I could hand this to any auditor tomorrow."
- **Value proposition:** *"See exactly where the money goes — per entity, per vehicle, per project — and turn a mess of invoices into an audit-ready record automatically."*
- **What she needs:** dashboards + dimensions (vehicle/entity/project), multi-entity VAT, an audit trail, and a price that's obviously worth it for a non-finance buyer.

---

## 4. Tom — Finance Lead / Bookkeeper (in-house)  *(Champion · Administrator)*

- **Context:** The finance-team-of-one (or two) at an SME. Owns the numbers. Configures the workspace.
- **Jobs:** Get every invoice in without re-keying; enforce checks; manage users/roles; export to the accounting package; keep VAT clean; close the period.
- **Frustrations:** Drowning in email attachments; manual data entry; no time for analysis; being the single point of failure.
- **Success criteria:** capture automated, checks trusted, close faster, less firefighting.
- **Value proposition:** *"Stop re-keying and start reviewing. Everything lands in one queue, gets checked, and exports clean — so closing the month is a review, not a rebuild."*
- **What he needs:** intake channels (upload/email/API), validation, roles + audit, exports, usage he can see, and admin controls scoped to *his* company only (never system-wide).

---

## 5. Elena — AP Clerk / Office Manager  *(End-user · Processor)*

- **Context:** Handles day-to-day invoice entry and filing. May not be finance-trained. Uses the tool constantly but shallowly.
- **Jobs:** Get an invoice into the system correctly and quickly; know what's missing; not make mistakes that get caught later.
- **Frustrations:** Confusing forms; unclear what's required; fear of doing it wrong; repetitive typing.
- **Success criteria:** fast, guided capture; clear "what's missing" prompts; confidence the entry is right.
- **Value proposition:** *"Drop the file, check the highlighted draft, done. The system tells you what's missing before it's a problem."*
- **What she needs:** dead-simple capture + review, clear empty/loading/error states, guardrails, and permissions that let her do her job and nothing riskier.

---

## 6. David — External Auditor  *(End-user · Read-only, periodic)*

- **Context:** Comes in quarterly/annually. Needs to trust the record and trace it.
- **Jobs:** Verify completeness and integrity; trace any figure to its source document; confirm nothing was altered.
- **Frustrations:** Incomplete records, no source documents, no change history, "trust me" spreadsheets.
- **Success criteria:** every figure traceable to an inert source doc; a verifiable, immutable change log; read-only access that can't alter anything.
- **Value proposition:** *"Every number traces to the original document, and the audit log proves nothing was changed. Grant read-only access and let them self-serve."*
- **What he needs:** read-only role, document vault, hash-chained audit trail he can verify, and exports.

---

## 7. Nadia — Platform Operator  *(Internal · Cross-tenant admin)*

- **Context:** Our own ops/support. Manages the platform across tenants; **never** a company role.
- **Jobs:** Support customers, manage the global limits matrix, monitor jobs/webhooks/queues, investigate incidents — without ever leaking one tenant's data to another.
- **Frustrations:** Blind spots into failing jobs; no safe way to help a customer without over-broad access.
- **Success criteria:** effective support with least privilege; full observability; every operator action audited.
- **Value proposition (internal):** *"See platform health and help any tenant, with cross-tenant power that is separate from — and never confused with — a customer's own admin."*
- **What she needs:** the separate `is_platform_admin` capability, operator-only cross-tenant reads, job/queue/webhook dashboards, and audit of operator actions.

---

## Secondary persona (post-MVP)

### 8. Ravi — Employee Expense Claimant
- **Context:** Non-finance employee submitting occasional expenses. Only relevant when the **expenses module** ships (post-MVP).
- **Jobs:** Submit a receipt + business purpose fast; get reimbursed; know the status.
- **Value proposition:** *"Snap the receipt, add the purpose, submit — and track it to reimbursement."*
- **Note:** included for completeness; **not** a v1 primary persona. See PRD §8.

---

## Value-proposition summary (one line each)

| Persona | One-line value |
|---|---|
| Priya (Principal) | Serve more clients with the same team — capture, check, export, done. |
| Marco (Bookkeeper) | Any format in, problems flagged, a month reconciled in an afternoon. |
| Sofia (SME Owner) | See where every euro goes — per entity, vehicle, project — audit-ready automatically. |
| Tom (Finance Lead) | Stop re-keying; closing the month becomes a review, not a rebuild. |
| Elena (AP Clerk) | Drop the file, confirm the draft, done — with guardrails. |
| David (Auditor) | Every figure traces to its source; the audit log proves integrity. |
| Nadia (Operator) | Help any tenant with least-privilege, fully-audited cross-tenant access. |
