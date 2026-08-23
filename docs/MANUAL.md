# InvoiceIQ — User Manual

> The hands-on guide to the product, screen by screen and workflow by workflow.
> Architecture and specification live in [`docs/architecture/`](./architecture/);
> this document is for the person **using** the workspace. Examples use neutral
> placeholder businesses — every feature works the same for any industry.

---

## 1. Getting started

### 1.1 Signing in and workspaces

- **Register** creates a new, fully isolated workspace (organization) with you as
  its **owner**. Registering the same company name twice creates a second,
  separate workspace — data is never merged.
- **Invitations** are the only way into an existing workspace: a teammate with
  the right role sends an invite from **Workspace → Team**; the link sets your
  name and password and drops you into that workspace with the role the inviter
  chose.
- If you belong to several workspaces, the **organization switcher** (top bar)
  moves between them. Each switch is a hard context change — lists, reports and
  permissions all follow the selected workspace.
- Single sign-on (OIDC) and SCIM user provisioning are available per workspace
  for enterprise setups (**Settings**).

### 1.2 Roles — who can do what

Four stored role tiers resolve into an 8-role, deny-by-default permission
matrix that the server enforces on every request (the UI merely hides what you
cannot do). Team invites can also assign the finer business roles (for example
**finance manager** — full bookkeeping without workspace administration):

| Role | Typical person | Can |
|---|---|---|
| **Owner** | founder / managing director | everything below, plus access control, audit log, billing, workspace settings |
| **Admin** | office manager | manage catalogs (tax codes, currencies, cost objects), documents, dunning, policies, team |
| **User** | employee / bookkeeper | day-to-day work their business role permits (expenses; for a finance manager: invoices, payments, issuing, projects) |
| **Read-only** | reviewer / accountant with view access | look, never touch |

Two boundaries worth knowing:

- **Document wording is configuration**: saving or editing a contract/offer
  template needs settings-manage rights, even though *generating* a document
  from a template only needs invoice-write. A bookkeeper books; an owner or
  admin decides what the company's contracts say.
- **Segregation of duties** on money movement: the person who creates a payment
  run cannot approve it, and neither can mark it paid.

---

## 2. Payables — supplier invoices in

### 2.1 Capture

Three ways in, all landing in the same pipeline:

- **Upload** (`Payables → Upload`): PDF (text layer or scanned — OCR runs on the
  worker), UBL/CII e-invoice XML, Factur-X hybrid, CSV/JSON. Large or scanned
  files parse asynchronously; the capture appears in **Captures** while it runs.
- **Email intake** (`Payables → Email intake`, module): each workspace gets an
  inbound address; attachments are parsed and queued for review.
- **API ingest** for automation.

Every capture records **provenance**: which method read the file, which fields
were extracted vs. defaulted, and (for OCR/AI paths) per-field confidence.
Nothing is booked without a human confirming it in the **Review** queue.

### 2.2 Review and validation

The review screen shows the parsed draft field by field. One validation engine
runs two kinds of rules: **blocking** (tax totals that don't add up, missing
mandatory fields) and **advisory** (possible duplicates — exact number matches
and same-vendor/similar-amount candidates — flagged, never silently dropped).
Confirming saves the invoice with EUR conversion at the ECB rate for the issue
date, with the FX source recorded.

### 2.3 Suppliers under dual control

`Payables → Suppliers` is the vendor master. Changing an existing supplier's
**IBAN or tax id** never writes directly — it creates a **pending change
request** that a *different* admin must apply. Payment runs refuse a vendor with
a pending change. This is the classic invoice-fraud control, on by default.

### 2.4 Approval and payment

Invoices move through an approval workflow (priority-ordered policies, e.g.
"above 1 000 € needs a second pair of eyes"). Approved invoices are grouped
into **payment runs** (`Payables → Payment runs`): create → approve (different
person) → mark paid (third person), then export the SEPA `pain.001` bank file —
export-once guarded, every export audited with its message id.

### 2.5 Deleting, trash, and the archive

- **Trash** (`Invoices → Trash`): deleting an invoice is a soft delete with a
  consent step; trashed invoices can be restored.
- **Archive** (`Invoices → Archive`): archived invoices are kept for their full
  retention window (default 3 years) even if the live workspace changes plan.
  Before anything expires you get a **pre-expiry notice** with the option to
  export everything or extend retention; nothing is silently destroyed, and
  every destruction that does happen is audited with what was destroyed.

---

## 3. Receivables — invoicing your customers

- **Issuer profile** (`/issuer`): your legal entity (or several) — name,
  registration, VAT number, address, logo, invoice numbering prefix. An invoice
  cannot be issued until the profile is complete (EU Art. 226 completeness).
- **Issue** (`Receivables → Issue`): line items, per-line VAT (four schemes,
  server-computed), customer from the customer master, optional **project** link
  (see §5 — this is how revenue reaches project profitability). Issued invoices
  are **immutable**: corrections are credit notes, never edits.
- **Receipts & reconciliation**: record money received and allocate one receipt
  across several invoices; import bank statements (CSV/camt.053) and reconcile.
- **Recurring** schedules, **dunning** reminder ladders, **partner document
  gates** (don't invoice a counterparty whose contract documents are missing),
  and **invoice reports** round out the loop.

---

## 4. Expenses

Employees submit expense reports (standard, mileage, per-diem items) with
receipt photos; an 11-rule policy engine flags violations; approval chains and
reimbursement batches (CSV + SEPA) close the loop. Expense items carry the same
cost dimensions as invoices — including **project**, which is how employee
spend reaches project profitability.

---

## 5. Projects — the full lifecycle, offer to profit

This is the heart of the system for any business that works in projects — a
construction job, a consulting engagement, a season of maintenance contracts.
The product tracks the whole arc:

```mermaid
flowchart LR
  O[Open project] --> OFF[Offer / estimate]
  OFF -->|accepted| CT[Contract]
  CT --> PLAN[Invoicing plan]
  PLAN --> WORK[Work: invoices issued<br/>+ costs collected]
  WORK --> ACC[Acceptance & handover]
  ACC --> FIN[Final invoicing]
  FIN --> CLOSE[Close → P&L frozen]
```

Every stage is live.

### 5.1 Open a project

`Workspace → Cost objects → Projects`. A project has a code, a name, dates and
a status (`active → closed → archived`). Click through to the **project page**
(`/projects/{id}`) — the one screen where the whole lifecycle lives.

### 5.2 Offers and estimates

The **Offers** card: draft an offer with line items and a total, send it,
and record the outcome — `accepted`, `rejected`, or revise it (a revision is a
new **version**; the old one is kept as `superseded`, so the negotiation
history survives). A sent offer can be pulled back to draft. Offer numbering
follows your workspace's own scheme — the platform only enforces uniqueness.

The latest **accepted** offer's total becomes the project's **estimated
revenue**, so estimated-vs-actual is readable from day one.

### 5.3 Contract and documents

The **Contract & documents** card stores the signed contract and any other
project papers — uploaded, or **generated from a template** (§6): pick one of
your saved template versions (or a platform standard), and the system fills in
the parties, project, offer total and dates, renders a PDF, and files it with
the project.

### 5.4 The invoicing plan

Break the contracted sum into planned instalments ("advance", "on delivery",
"final"). The plan card tracks **contracted vs. actually issued vs. remaining**
— the figures come from the same source the P&L uses, so the plan never
disagrees with the profit view.

### 5.5 Collecting costs

Three kinds of cost flow into a project, all in EUR at recorded rates:

1. **Supplier invoices** — on any received invoice, the **Project allocation**
   editor assigns the whole invoice, specific lines, or percentage splits
   across projects. Splits are cent-exact: shares are rounded per-share and
   any residue lands deterministically on the largest share.
2. **Expenses** — expense items tagged with the project.
3. **Manual cost entries** — wages, or anything without a document: label,
   category, amount, date, straight on the project page.

### 5.6 Reading the P&L

The project page's P&L card shows revenue (issued invoices linked to the
project, net of credit notes), the three cost streams, profit and margin. The
figures state their own basis — **live figures** while the project runs.

### 5.7 Closing — the freeze

Closing a project **freezes the P&L**: a snapshot is stored in the same
transaction as the status change. Documents that arrive after close (a late
supplier invoice, a straggling expense) do **not** silently move the frozen
numbers — they appear separately as **"arrived after close"** adjustments, so
the closed figure stays the closed figure and the difference is explained.
Reopening a project discards the snapshot (audited) and returns to live
figures.

---

### 5.8 Acceptance and the final invoice

The project page's **Acceptance & final invoice** card closes the loop. When
the work is done (Next actions suggests it once all scheduled assignments
are), generate the acceptance document from its template, get it signed, and
**Record acceptance** — optionally linking the signed file and a note. The
sign-off is stamped, audited, and revocable.

Then **Prepare final invoice**: it starts from the contracted remainder
(what the invoicing plan says is still uninvoiced) and you add **labelled
adjustment lines** — extra work, damages, deductions, either direction — so
the difference explains itself instead of hiding. The composed lines open in
the normal issuing form for review and issue. Two rules the system holds:
a total at or below zero is refused (money flowing back to the customer is a
**credit note**, never a negative invoice), and — if you enable it in
Settings → Projects & offers — the final invoice waits until acceptance is
recorded. The offer numbering prefix lives in the same settings block.

## 5b. The schedule — planning the work

`Overview → Schedule`. Put people on projects for a day or a time window.
Planners (bookkeeping roles and up) see the whole workspace week, filter by
person or project, and assign; everyone else automatically sees **only their
own work** on the same screen — confirm an assignment when you accept it,
mark it done when the work is. Double-bookings are **warned about, never
blocked** — the warning names the colliding times and saves anyway, because
real schedules overlap.

Being assigned, rescheduled or cancelled emails you automatically, and a
**reminder** arrives before each assignment starts (24 hours by default,
adjustable per assignment). **Your calendar on your phone**: the Schedule
page's setup card gives you a private subscription link — add it once in
Google, Apple or Outlook calendar and your assignments appear and stay
updated. Anyone with the link can see your schedule, so treat it like a
password; **Regenerate** cuts off the old link instantly. Customer arrival
notices are the next slice of this module.

## 6. Document templates

`Workspace → Templates`. The trust model, in one sentence: **the platform
provides master documents; you adjust them into your own versions, and nothing
the platform does later ever changes your saved copies.**

- **Standard documents** (left): maintained by the platform operator — a demo
  contract, acceptance document and offer cover letter to start with (each says
  in its own text that it is an example, not legal advice; professionally
  drafted standard texts replace them centrally when available).
- **Adjust** copies a master's text into the editor. Edit it, name it ("Our
  contract — strict payment terms"), save. Keep as many versions as you like;
  edit or delete them freely. Your copies are frozen — a later change to the
  master never reaches them.
- **Placeholders** like `{{project.code}}`, `{{customer.name}}`,
  `{{offer.total}}`, `{{company.legal_name}}` are filled when you generate a
  document from a project. Anything the system doesn't know **stays visibly
  unreplaced** in the output — a gap you can see before anyone signs, instead
  of a silently blank clause.
- Generating (project page → Contract card → "Generate from template…")
  renders your chosen version against the project and files the PDF with the
  project's documents.

Saving or editing template text requires settings-manage rights; generating
documents needs only the normal bookkeeping role.

---

## 6b. Next actions

The dashboard's **Next actions** card is the day's work, computed from your
records: offers sitting unanswered past three days, invoices past due with
money outstanding, uploads waiting for review, and your own recurring
deadlines ("prepare the VAT report" — set the day, cadence and lead time
once). Every item clears ITSELF when the work happens — accept the offer,
receive the payment, confirm the upload — and **Dismiss** silences one item
forever. Nothing piles up, by design.

## 7. Insights

- **Dashboard** — "what needs me today": approvals waiting, captures to
  review, aging, cash position; every section respects your role.
- **Explore** — self-service pivot over spend by any dimension.
- **Benchmark, FX, Cash position, Budget** — supplier comparison, ECB-vs-paid
  FX markup, receivables/payables position, category budgets.
- Exports are CSV/Excel/PDF, formula-injection-safe, with the basis (net, EUR)
  stated on the file.

---

## 8. Workspace administration

- **Tax codes / Currencies / Cost objects** — per-workspace catalogs (admin).
  Master data is archived, never hard-deleted, so history always resolves.
- **Team / Access** — invite members, change roles; owners see the access
  surface and the **audit log** (append-only, hash-chained, exportable — every
  mutating action in the workspace, attributable and verifiable).
- **Documents** — the content-addressed registry of every stored original.
- **Billing** — plan, usage against limits, upgrade.
- **Settings** — workspace configuration, validation toggles, SSO.

---

## 9. For the platform operator

A platform administrator (not a workspace role) additionally has:

- `/platform` — tenant operations.
- **Template masters** — `GET/PUT /platform/templates/{key}`: add or replace
  the standard documents every workspace sees as starting points. Demo texts
  seed themselves; replacing a master's body changes what *future* adjustments
  start from and never touches any workspace's saved versions.

---

## 10. Principles the product holds everywhere

1. **Your data is yours alone** — tenant isolation is enforced three ways on
   every query (application filters, an ORM guard, and database row-level
   security), and tested per table.
2. **Money is exact** — decimal arithmetic, server-recomputed totals, one FX
   convention with provenance, no cross-currency sums without a recorded rate.
3. **Issued documents are immutable** — corrections are new linked documents.
4. **Everything is audited** — in the same transaction as the change.
5. **Nothing is silently destroyed** — soft delete, archives with notices,
   and audit records of what was removed.
6. **Zero external calls by default** — with default settings the system runs
   with no third-party AI or network dependency; AI capture is opt-in and
   advisory.
