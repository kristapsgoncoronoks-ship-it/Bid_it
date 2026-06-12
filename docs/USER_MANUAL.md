# USER MANUAL — Fleet Fuel & VAT Refund System

How to use the software day-to-day. For installation see [INSTALL.md](INSTALL.md);
for security policy see [SECURITY.md](../SECURITY.md); for architecture see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 0. Starting the app

**Which case are you?**

### A. On your own computer (one person)

1. **Start it** — double-click the launcher in the program folder:
   `start.bat` (Windows) · `start.command` (macOS) · `start.sh` (Linux).
   The first run installs what it needs (about a minute) and opens your browser
   automatically. *(Only requirement: Python 3.10+ — if it's missing, the launcher
   tells you where to get it. Full steps in [INSTALL.md](INSTALL.md).)*
2. **First time only** — a **setup page** appears: choose an admin username and
   password, leave the HTTPS box ticked, click **Create account & finish**.
3. **Open it any time** at **`http://localhost:8050`** (or `https://…` once a
   certificate is in place). Sign in with the account you created.
4. **Stop it** — close the small console window the launcher opened, or press
   **`Ctrl+C`** in it. Your data stays on disk; start again whenever you like.

> The console window must stay open while you use the app — it *is* the running
> program. Closing it shuts the app down cleanly (nothing is lost).

### B. On a shared server (a team)

On a server the app runs continuously as a background **service**, so nobody has to
keep a window open. IT sets this up once (full walkthrough — Ubuntu and Windows, TLS,
reverse proxy, automatic backups — in [INSTALL.md](INSTALL.md)). Day-to-day you just
open the company address in a browser, e.g. **`https://fuel.yourcompany.local`**, and
sign in.

To start / stop / restart the service (Linux, run by IT):

```bash
sudo systemctl start fleetfuel       # start
sudo systemctl stop fleetfuel        # stop
sudo systemctl restart fleetfuel     # restart (e.g. after a config change)
sudo systemctl status fleetfuel      # is it running?
journalctl -u fleetfuel -n 50        # recent log lines
```

The service auto-starts on boot and restarts itself if it ever crashes; backups run on
a nightly schedule. Adding colleagues is done in the web **Admin** panel (create them
as **processor**), not on the server.

> **Growing past one server.** The same software runs unchanged from a single laptop to
> a multi-server fleet (separate web nodes + a worker fleet + a PostgreSQL database +
> off-machine document storage), set up entirely through configuration. That's an IT
> concern, not a day-to-day one — the full ladder and settings are in
> **[SCALING.md](SCALING.md)**.

---

## 1. Signing in & roles

Open `https://<server>:8050` (or your company URL) and sign in. Your role is shown
in the top bar next to your name. There are two roles:

| Role | Can do |
|---|---|
| **processor** | The day-to-day worker: import batches, register statements, attach documents, pricing analytics, exports. **Cannot** see the VAT-refund module (claims, readiness, recovery, customers/CRM), do server setup, user administration, or overall software changes. Individual capabilities are configurable by an admin. |
| **admin** | Everything, **plus** the **VAT-refund module** (claims 1A→5, readiness, recovery & fees, the customer CRM) and the **Admin** panel: create users, assign roles, reset passwords, disable accounts, **adjust which capabilities processors have**, **switch whole parts of the app on/off (Modules)**, review the login & error logs, run/verify backups, and check document integrity. |

A processor's capabilities (data import, invoice control, pricing, documents,
exports) are switches an admin sets in the Admin panel — so you can give one
colleague import‑only access and another compliance‑only, for example. The
**VAT‑refund module is always admin‑only**, whatever capabilities a processor holds.

Every change you save is recorded in the audit log **under your username** — visible
on the History page. Sessions are protected (login lockout after repeated failures,
per‑IP throttle, 8‑hour idle timeout). Sign out with the link in the top bar.

## 2. The pages, left to right

**Dashboard** — opens with a **month-close status strip** (data loaded? invoices
received? statements reconciled? anomalies? backed up?) and a **“What needs action”
worklist** — claims ready to submit, claims blocked on documents/activation, claims
aging past 120 days unpaid, and fees ready to invoice — each line links straight to
where you act. Below, the month at a glance: diesel litres, fleet effective €/L, net
spend, reclaimable VAT. Then the diesel benchmark (suppliers ranked cheapest‑first by
effective price) and the month‑over‑month trend. The period selector switches months.

Tables across the app sort on a column click, type‑to‑filter once they pass a few
rows, and keep their header visible while you scroll. Press **?** for keyboard shortcuts.

**Compare** — answer "who was cheapest for X in Y during Z": pick period, supplier,
country, product, and an optional date range; the table recomputes. "ALL" widens any
filter. *Effective* price includes rebates (e.g. Q8 after Port One); *doc* price is
what's printed on the invoice.

**Head-to-head** — the negotiation page. Days where 2+ suppliers bought diesel in the
same country: each supplier's price, the cheapest highlighted green, the spread, and
the **overpay €** versus the cheapest available that day. The total at the top is
your evidence number for supplier talks.

**Entities & VAT** — per legal entity per country: net, reclaimable VAT, gross. One
row here = one potential refund stream.

**Stations** — diesel stations ≥300 L ranked by effective €/L with routing flags:
green **PREFER** (≤1.40), red **AVOID** (≥1.62). Share with dispatchers.

**Savings** — the avoidable‑overpay view as a chart: how much was spent above the
cheapest available option, the headline figure dispatch and procurement act on.

**FX vs ECB** — the exchange rates used versus the official ECB reference, so any
currency conversion in the claims is transparent and auditable.

**Transactions** — the line‑level detail, filterable by client, supplier, country,
location/station and date. **Anomalies are highlighted in place** (amber) — the flag
sits on the exact transaction it belongs to, learned from each country/period's own
price spread (no fixed threshold). **Discount/adjustment lines** (e.g. MOEVE promo
lines) are highlighted (blue) and related to the supplier/country/period they apply to.
The **Rebate/Discount** column shows the rebate actually applied (e.g. Q8's Port One),
a discount line's value, or — when the separate rebate invoice isn't present — an
**expected** rebate estimated from historic data, so known discounting is never lost.

**Import batch** — upload a supplier's PDF, a ZIP, or a structured **XML e‑invoice**
(UBL/CII, EN 16931 — these parse deterministically at high confidence, no AI). Every
upload box is also a **drag‑and‑drop zone**: drag a file from your file manager straight
onto it, or click to browse. The box turns green and shows the file name once it's
attached. Two ways to process: **Extract draft now** (process immediately and review),
or **Queue for later** which parks the file in the **Waiting room** for background
processing (see §3b).

**Every upload is confirmed OK or rejected.** The moment a file arrives it is written
to the durable data lake and immediately **read back and re-hashed** to prove it landed
intact:
- **✓ Upload OK** (green) — the file is safely archived (it can no longer be lost, only
  deleted by a user) and is **sent for processing**. You then review the draft, or it
  goes to the waiting room.
- **✗ Upload failed — batch rejected** (red) — the file could not be stored safely. The
  **bad data is discarded** (nothing is kept and nothing is processed) and you are asked
  to **re-upload the entire batch**. The problem is recorded in the admin error log.

The same OK/failed confirmation applies when attaching an original PDF/scan to an
invoice in the **Documents** vault. Every upload — successful or failed — is recorded
on the **Imports** report.
On confirm, it registers the statement + vaults the source (see §3a). When an AI
backend processes an invoice, its structured output is also archived in a **data lake**
(separate from the PDF) so it can be reused without re-calling the API. Your VAT‑refund
**claim records live in their own isolated database** — protected from the monthly data
rebuild — and are included in every backup.

**Waiting room** — the durable intake queue for uploaded batches (see §3b).

**Invoice control** — two controls on one page (see §4).

**Contracts** — the **contract‑compliance auditor**: checks every invoiced line against
the supplier's contracted discount terms and flags **short discounts** (rebate applied
below contract) and **over‑ceiling** prices, with the **recoverable EUR** per breach to
claw back. Add rules per supplier/country/station (SQL‑LIKE) with an expected discount
(€/L) and/or a max NET price (€/L). Pure data — finds money you're already owed.

**VAT refunds** — the claim matrix and lifecycle (see §5).

**Claims** — the "can we file?" view: per claimable quarter, READY vs BLOCKED with the
exact blocking reasons (activation, missing docs, unresolved refs, threshold), plus the
list of open (submitted, awaiting refund) claims with aging. Export to Excel.

**Recovery** — tracks submitted → approved → paid refund amounts with aging (unpaid
over 120 days flagged red), **and the service‑fee settlement**: the fee charged per
claim, whether we invoice the customer or deduct and remit the net, and a one‑click
**fees statement** (Excel). Issue the fee invoice once a refund is paid (see §5a).

**Pricing intel** — the competitiveness engine. Toggle daily / weekly / monthly;
each supplier's effective NET price per city is compared against three baselines:
your MY Prices (upload as CSV: country,city,date,net_price), the supplier pack (vs
other suppliers same city), and a wholesale index (upload to unlock true margin).
Sorted by EUR impact so the money is at the top; unmatched volume shown openly.
Export the daily/weekly/monthly grid to Excel to build pricing models. All prices
NET final, VAT excluded, rebates applied — stated on the page so it's unarguable.
The **Self‑sourced benchmark** card turns your own data into competitor intelligence:
for each country/period where you used 2+ suppliers, it shows the **best price you
actually achieved** and the **avoidable overpay** vs that best (route volume to the
cheaper supplier you already use) — no external data. **Adopt best‑of as MY benchmark**
loads those prices so the margin/gap columns measure everyone against the best you got.
The **Client portal price scraping** card automates the MY‑Prices benchmark: an admin
adds a supplier portal (a no‑code JSON/CSV config, or a custom adapter) and stores the
entity's login (encrypted at rest); **Scrape now** pulls that account's NET prices
straight into MY Prices (source `portal:<SUPPLIER>`). Use only portals you're
authorized to access; on a locked‑down box run the scraper from a connected machine.

**Anomalies** — a relative scan flagging stations priced above their country average,
month-over-month price jumps, vehicle volume spikes and off-period dates.

**Documents** — the invoice vault (see §6).

**Suppliers / Customers** — the master-data cards. Suppliers: legal identity, VAT
registrations, bank accounts, products, invoice registry. **Customers (CRM, admin
only)** is the mini‑CRM that drives the VAT‑refund lifecycle:
- the **adjustable submission checklist rules** (default: contract, customer data,
  bank account, NACE business activity, trade register, power of attorney) — the
  system verifies these, nobody ticks them by hand, and the claim statuses 1A→1E
  follow them automatically;
- onboarding & **per‑country activation** (request → receive the country's documents);
- **document templates**: upload your own contract / power‑of‑attorney template with
  `{{placeholders}}` (e.g. `{{company_name}}`, `{{reg_number}}`, `{{bank_iban}}`,
  `{{refund_country}}`) as .txt/.html/.md/.docx — the system **generates the document
  per customer** (optionally as PDF), warns on screen about any unfilled fields, and
  can file the draft in the customer's documents;
- documents can carry a **valid‑until date** — an expired power of attorney stops
  satisfying the checklist and is flagged on the dashboard worklist before it expires;
- the **fee terms** (% of refunded VAT, per‑declaration minimum, per‑country
  overrides, and where the refund is paid). Red **INPUT** = data still to be collected.

**Data manager** — direct table editing for master data (processor with data‑import
capability). Pick database →
table; every row is editable inline (Save / Delete), the bottom row adds new
records. Deleted rows are recoverable: their full values stay in History.

**Imports** — the data‑import report: every upload, extraction and statement
registration with its outcome (**received / success / partial / failed**), the user,
file, client, supplier and record count. Filter by channel, status, client, supplier
and date. Append‑only audit of what came in and whether it landed.

**Files** — the permanent file archive (data lake). **Every uploaded file is archived
here on arrival** (SHA‑256, same storage as the PDF vault) and kept forever; it is only
removed by an **explicit Delete** here, or after **Verify integrity** flags it
corrupt/missing — never lost automatically. Whether a file's *data* is processed/used is
a separate concern: the file stays regardless.

**Doc mining** — re‑reads the vaulted documents (PDF text + structured XML), extracts
EU VAT numbers, and **proposes fills for the yellow INPUT gaps** in supplier/customer
master data where the country code matches. Review each proposal and press **Apply**
(audit‑logged) — nothing is written automatically.

**History** — the audit trail of every change in every database: filter by database,
table, **from date / till date**, and record key. Each row shows when, what, the
action, **who (By column)**, and a field-level diff or full snapshot.

**Admin** (admins only) — user management (create users, set roles, reset passwords,
disable accounts) and **adjust processor capabilities**; **Modules — switch whole
parts of the app on/off** (analytics, intake, compliance, VAT refunds, FX): a part
that's off disappears from the menu and its pages answer "turned off" until re‑enabled;
the login log and the **error log**; security status (TLS, password storage); the
backup schedule with one‑click **Run backup**, **Verify last backup** and **Check
document integrity**.

## 3. Monthly routine (processor)

Day 1–3 of the new month, when supplier invoices arrive:

1. **Load the data** (server CLI or ask your admin): drop the supplier files, edit
   `month_config.py`, run `consolidate.py` (every supplier must PASS validation
   against its own invoice totals — the system refuses to build on a mismatch),
   then `build_master.py` and `history.py`.
2. **Invoice control** page → enter the period → Run control. Work the colors:
   - red **MISSING** = activity happened but no invoice registered → chase the supplier
   - red **RECEIVED – DOC MISSING** = invoice registered, PDF not attached → get it,
     attach on the Documents page
   - **NO ACTIVITY** = nothing expected there, fine.
   Check the **orphan transactions** list — fueling not covered by any invoice.
3. **Register each supplier's statement/coversheet** (same page, bottom form):
   supplier code, statement reference, date, then paste one line per issued invoice:
   `invoice_no; date; country; currency; net; vat`. Attach the statement PDF itself
   on the Documents page. The reconciliation table then triages every issued invoice:

   | Verdict | Meaning / your action |
   |---|---|
   | PROCESS – COMPLETE | registered + original on file → flows to the VAT claim. Done. |
   | PROCESS – DOC MISSING | obtain and attach the original PDF |
   | PROCESS – IDENTIFY | read the exact invoice number off the statement, update the registry |
   | PROCESS – NOT REGISTERED | issued per statement but unknown to us → investigate |
   | DISCARD | zero VAT (rebates, settlements) → archive only |
   | DISCARD – DOMESTIC | invoice country = entity's home country → belongs in the regular domestic VAT return, **not** the refund process. Automatic. |

3a. **Or import the whole batch at once** — Import batch page: drop the supplier ZIP
   (coversheet + invoices). The system reads it and shows a **review screen** — parsed
   invoice lines beside the source, every field editable, with a draft gross total to
   check against the coversheet. Fix anything, then **Confirm**: it registers the
   statement, syncs VAT-bearing invoices, and files the PDFs in the vault in one step.
   For recognised suppliers this is offline and instant; for new layouts it uses the
   configured AI extractor (still a draft you confirm). Nothing is saved until you confirm.

3b. **The Waiting room (deferred processing).** Heavy extraction (especially AI) is
   decoupled from upload so a burst of files can't overload the server. Press **Queue
   for later** on Import batch and the file is stored durably on arrival; a background
   worker extracts it one at a time. The Waiting room page shows each job's state —
   *queued → processing → ready* (then review & commit like §3a) — with counts and a
   "Process queued now" button.
   - **No data loss:** the uploaded bytes are written to disk before the job is recorded,
     kept until you've reviewed and committed, and a crash mid‑process is retried.
   - **AI out of tokens/quota:** the job is parked as **waiting** and retried
     automatically every 4 hours, without counting as a failure. After several retries it
     becomes **held** — it stays safely in the waiting room until you top up the API
     credit and press **Send now** (or **Send / restart all** to re‑run the whole
     backlog).
   - **Backlog gate:** while documents are still unprocessed, adding new ones to the
     waiting room is paused so a stuck pile can't grow — an admin can grant a temporary
     override.

4. **Dashboard / Compare / Head-to-head** — review prices, send Stations flags to
   dispatch, note negotiation evidence.
5. **Backup** runs nightly automatically; after a heavy editing day you can run one
   manually (`python3 backup.py`) or from the Admin panel (admins also get a one‑click
   "Verify last backup" and "Check document integrity").

## 4. Reading the receipt control

Expectation = the supplier's **cadence** (from the supplier card: every 14 days,
every 30 days, or monthly-per-country) × **activity** (did transactions actually
happen in that slot/country). So "no invoice from Austria" is only a problem if
there was Austrian fueling — otherwise the row says NO ACTIVITY and nothing is
chased. If a supplier confirms in writing that no invoice exists for a flagged slot,
a processor can set `waived=1` on that row in the Data manager (claims database →
invoice_receipt_control); the waiver survives re-runs.

## 5. VAT refunds, quarter by quarter (admin only)

The whole VAT-refund module — **VAT refunds, Claims readiness, Recovery & fees, and
the Customers (CRM) page** — is visible to **admins only**.

The page shows every stream — **entity × refund country × period (Q1–Q4 + YEAR)** —
with the VAT amount in EUR and local currency, the threshold verdict, document
coverage, home portal, deadline and the **workflow status (1A→5)**.

**Threshold verdicts:** READY (≥ €400 quarterly), DEFER TO ANNUAL (under €400 but the
year total ≥ €50), BELOW ANNUAL MIN (accumulate). Quarters show "months missing"
until all three months are loaded — file only after the quarter is complete.

### The status workflow (1A → 5)

Every claim carries a status code. The **pre-submission stages are system-controlled**
— nobody can tick them by hand; the claim climbs automatically as the checklist
completes:

| Code | Meaning | Who sets it |
|---|---|---|
| **1A** | Missing documents / checklist incomplete | system |
| **1B** | All documents received — period not ended | system |
| **1C** | Can be submitted (a caveat remains, e.g. defers to annual) | system |
| **1E** | Ready to submit | system |
| **2** | Submitted | you |
| **2A** | Successfully submitted | you |
| **2B** | Document request received *(record the response deadline)* | you |
| **3** | Decision received | you |
| **3A** | Money received *(fee becomes chargeable)* | you |
| **3B** | Rejection *(locks kept — appeal or invoice the fee)* | you |
| **3D** | Under appeal *(locks kept)* | you |
| **3C** | Confiscation by government *(locks kept)* | you |
| **4** | Ready to invoice fee (refund went to the customer) | you |
| **4A** | Ready to invoice credit (refund came to us) | you |
| **5** | Closed | you |

The system checklist behind 1A→1E is **adjustable** (Customers page): by default
*contract, customer data, bank account, NACE business activity, trade register,
power of attorney*, plus the claim-level checks — all invoices received & processed,
all invoice documents attached, and **the claim period has ended** (a hard gate: a
Q2 claim physically cannot be submitted before 30 June).

When you advance a status you can attach a **note** (rejection reason, what was
requested) and — for 2B/3D — a **deadline**; both show on the claim and feed the
dashboard worklist.

**What a claim contains.** A claim is built **from your registered invoices** — every
line ties to **one specific invoice**, and there is **one row per product code** (row 1 =
product code 1, row 2 = product code 2, …), never a combined "ALL" line. A transaction
that can't be tied to a registered invoice shows as **UNMATCHED**, and a claim with any
unmatched or document‑less line **cannot be filed** — resolve it by registering the
invoice and attaching its document (search the vault or upload — see §6). Claim figures
stay **editable** (Data manager → claims), but a row is protected against accidental
change: it opens read‑only and only a deliberate **Edit** + a save **confirm** writes it.

**The quarterly run:**
1. After quarter end, watch the stream reach **1E Ready to submit** (open its
   *checklist* link to see exactly what's missing while it's 1A).
2. Click **Generate claim workbook** (or run `vat_refund.py`) and fix every
   **yellow INPUT cell** — the system refuses to submit otherwise.
3. Confirm document coverage is green ("3/3 docs") — every invoice needs its original.
4. File in the entity's home portal (e-MTA / EDS / Mano VMI), then set the stream to
   **2 Submitted**.

**Locks.** Setting *2 Submitted* **locks every invoice in the claim** — one invoice is
claimed exactly once, ever, including across quarterly vs annual. Rejection (3B),
appeal (3D) and confiscation (3C) **keep the locks** — you contest the decision or
invoice the fee; only an explicit admin **Withdraw (release locks)** frees the
invoices for a corrected re-claim.

**Quarterly vs annual, handled dynamically.** Low‑VAT quarters (under €400) defer into
the **annual** claim, while strong quarters can still be filed quarterly. The annual
claim is the *mop‑up* for whatever wasn't already claimed quarterly: e.g. Q1+Q2 small →
annual, Q3 large → filed as Q3, Q4 → filed as Q4, then the annual claim picks up Q1+Q2
(and any invoices the customer sent late). One invoice is still claimed exactly once.

**Domestic VAT** never appears in these claims — it's discarded at statement triage
(§3) and belongs in the entity's regular home VAT return.

## 5a. Service fees & settlement (Recovery page)

The agency fee is **% of the refunded VAT, floored at a per‑declaration minimum** —
whichever is higher (e.g. 8% of €1,000 = €80, but a €130 minimum → €130 is charged).
Rates are set per customer with optional per‑country overrides on the Customers page.

- The fee **rate is frozen the moment a claim is submitted** — later rate changes only
  affect un‑submitted claims.
- The fee is **charged when the money is received** (status → *3A*), computed on the
  amount actually refunded.
- **Settlement follows where the refund lands** (set per customer): paid to the
  *customer* → advance to **4 Ready to invoice fee** and **issue the fee invoice**;
  paid to *us* → advance to **4A Ready to invoice credit** (we deduct the fee and remit
  the net).
- The Recovery page's **Workflow (2→5)** column shows each claim's status code with
  its decision date / deadline / note and a one‑click **suggested next step** —
  after 3A it offers 4 or 4A (by payout route), then **5 Closed**. Download the
  monthly **fees statement** for the aggregate per customer.

## 6. The document vault

Every invoice must carry its physical document — original PDF or scan. On the
Documents page each known invoice shows its attachments (click to download) or a red
**MISSING** flag with an upload form. Submission of any VAT claim is blocked while a
document is missing. Files are SHA‑256 fingerprinted: re‑uploading the same file is
skipped, and attaching a file that already sits on a *different* invoice triggers a
wrong‑attachment warning.

**How it's organised.** Originals are filed under a logical, human‑navigable tree so
they're easy to locate by hand:

```
<Customer> <RegNo> / <Year> / <Country> / <Claim period Qn|Annual> / <file>
```

The same structure is used whichever backend stores the bytes — **local folder**
(default), **SharePoint**, or **FTP/FTPS** (set by the admin via `DOC_BACKEND`; see
INSTALL.md). When low‑VAT quarters merge into the annual claim, the documents of the
invoices in that claim are **automatically re‑filed** from their `Qn` folders into the
year's `Annual` folder — so the vault always mirrors the real claim composition.
`verify_documents` (Admin → Check document integrity) re‑hashes every stored file to
detect corruption or a missing original.

## 7. Master data: keeping it right (processor)

- **A supplier changes IBAN / you learn a missing VAT number:** Data manager →
  suppliers → the relevant table → edit → Save. The change is logged with your name
  and flows instantly into claim packs and controls.
- **A new entity (customer):** add the row in customers + a payout bank account +
  its supplier account numbers. Red INPUTs on the Customers page show what's still
  missing; claim packs cannot be submitted until the applicant data is complete.
- **A new supplier:** master data via Data manager (or `supplier_master.py` seed), set
  the **invoice_cadence** (drives receipt control), register its first statement —
  the invoices auto-sync. Transaction-level loading (prices into benchmarks)
  additionally needs a one-time row-map setup in `supplier_specs.py` (see
  [FILE_INDEX.md](FILE_INDEX.md) for where each module lives).

## 8. Excel deliverables — what each file is

| File | Contents |
|---|---|
| **Fleet_Fuel_Master_<month>.xlsx** | Runbook, supplier specs, all transactions, diesel benchmark, interactive supplier comparison (edit the **blue cells**), head-to-head, entity & VAT view, payment calendar, station scorecard |
| **Fleet_Fuel_History_Report.xlsx** | Month-over-month trends from the database: prices, litres, entity VAT, station drift, plus a DB guide with ready-made SQL |
| **VAT_Refund_Claims_<year>.xlsx** | Claim overview + one filing-ready pack per stream; **yellow cells = INPUT to complete**, orange = excluded/locked elsewhere |
| **VAT_Claim_Readiness_<year>.xlsx** | The Claims page export: "Ready to submit" (with blocking reasons) + "Open claims" (aging) sheets |
| **VAT_Fees_Statement_<year>.xlsx** | The Recovery page export: charged fees and net remittances aggregated per customer, plus a per-claim detail sheet |
| **Pricing grid (daily/weekly/monthly)** | The Pricing intel export for building pricing models — effective NET price per supplier/city vs your baselines |

Convention everywhere: **blue cells** are interactive inputs you may change;
**yellow cells** are missing data you must supply; nothing else should be edited by
hand — regenerate instead.

## 9. "How do I…" quick answers

- **…see what changed last week and who did it?** History → pick database → from/till
  dates → the By column names the user.
- **…undo a deletion?** History → find the DELETE row → its snapshot holds all
  values → re-add them via Data manager.
- **…check if we can already file Belgium for Vestroidas?** VAT refunds page: the
  stream must be READY, no "months missing", docs green — then file and set submitted.
- **…know which supplier to use in Belgium tomorrow?** Head-to-head (structural
  answer) + Stations (which exact stations to prefer/avoid).
- **…add a colleague with limited access?** Admin → Create user → role *processor*,
  then untick the capabilities they shouldn't have (e.g. leave only VAT claims).
- **…handle a tax office rejection?** Advance the claim to **3B Rejection** — the
  invoice locks are **kept** so you can appeal (**3D**) or invoice the fee. Only use
  **Withdraw (release locks)** if you must re-claim from scratch, then fix and refile
  (see §5).
- **…prove an invoice was only claimed once?** VAT workbook pack: the Duplicate
  control column; or query `vat_claimed_invoices` — each ref appears exactly once.

## 10. What the system will refuse to do (by design)

- Build reports from data that doesn't reconcile to the invoices (validation FAIL stops the pipeline)
- Submit a claim with INPUT invoice refs, missing documents, or an invoice already claimed elsewhere
- Revert a submitted claim to draft without an explicit reject/withdraw
- Let a processor do server/user administration, or any user disable their own admin account
- Include domestic-VAT invoices in refund claims
- Record any change anonymously, or store a password in readable form

If you hit a red BLOCKED banner, it is one of these guardrails — the message says
which, and the fix is always listed in §5 or §6.
