# USER MANUAL — Fleet Fuel & VAT Refund System

How to use the software day-to-day. For installation see [INSTALL.md](INSTALL.md);
for security policy see [SECURITY.md](../SECURITY.md); for architecture see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Signing in & roles

Open `https://<server>:8050` (or your company URL) and sign in. Your role is shown
in the top bar next to your name. There are two roles:

| Role | Can do |
|---|---|
| **processor** | The day-to-day worker: import batches, register statements, attach documents, run VAT claims, manage customers/fees, use every page and export. **Cannot** do server setup, user administration, or overall software changes. Individual capabilities are configurable by an admin. |
| **admin** | Everything a processor can, **plus** the **Admin** panel: create users, assign roles, reset passwords, disable accounts, **adjust which capabilities processors have**, review the login & error logs, run/verify backups, and check document integrity. |

A processor's capabilities (data import, invoice control, VAT claims, customers,
pricing, documents, exports) are switches an admin sets in the Admin panel — so you
can give one colleague claims‑only access and another import‑only, for example.

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

**Transactions** — the raw canonical fuel lines for a period, filterable, for spot
checks and drill‑down.

**Import batch** — upload a supplier's PDF or a ZIP of PDFs. Two ways to process it:
**Extract draft now** (process immediately and review), or **Queue for later** which
parks the file in the **Waiting room** for background processing (see §3b). On confirm,
it registers the statement + vaults the PDFs (see §3a).

**Waiting room** — the durable intake queue for uploaded batches (see §3b).

**Invoice control** — two controls on one page (see §4).

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

**Anomalies** — a relative scan flagging stations priced above their country average,
month-over-month price jumps, vehicle volume spikes and off-period dates.

**Documents** — the invoice vault (see §6).

**Suppliers / Customers** — the master-data cards. Suppliers: legal identity, VAT
registrations, bank accounts, products, invoice registry. **Customers** also drives
the VAT‑refund lifecycle: onboarding (trade registry, bank account, signed contract),
**per‑country activation** (request → receive the country's documents, e.g. power of
attorney), and the **fee terms** (% of refunded VAT and the per‑declaration minimum,
with per‑country overrides, and where the refund is paid). Red **INPUT** = data still
to be collected; a customer/country must be activated before a claim can be submitted.

**Data manager** — direct table editing for master data (processor with data‑import
capability). Pick database →
table; every row is editable inline (Save / Delete), the bottom row adds new
records. Deleted rows are recoverable: their full values stay in History.

**History** — the audit trail of every change in every database: filter by database,
table, **from date / till date**, and record key. Each row shows when, what, the
action, **who (By column)**, and a field-level diff or full snapshot.

**Admin** (admins only) — user management (create users, set roles, reset passwords,
disable accounts) and **adjust processor capabilities**; the login log and the **error
log**; security status (TLS, password storage); the backup schedule with one‑click
**Run backup**, **Verify last backup** and **Check document integrity**.

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

## 5. VAT refunds, quarter by quarter (processor)

The page shows every stream — **entity × refund country × period (Q1–Q4 + YEAR)** —
with the VAT amount in EUR and local currency, the threshold verdict, document
coverage, home portal, deadline and status.

**Threshold verdicts:** READY (≥ €400 quarterly), DEFER TO ANNUAL (under €400 but the
year total ≥ €50), BELOW ANNUAL MIN (accumulate). Quarters show "months missing"
until all three months are loaded — file only after the quarter is complete.

**The quarterly run:**
1. After quarter end, click **Generate claim workbook** (or run `vat_refund.py`).
   The Excel contains one Overview plus one **claim-pack sheet per stream**: the
   APPLICANT block (your entity's registration data and payout IBAN from the
   customer master), then invoice-level lines — issuer, issuer VAT ID, invoice ref,
   goods code, taxable base and VAT **in the refund country's currency**.
2. Fix every **yellow INPUT cell** before filing — missing invoice refs, VAT IDs,
   applicant data. The system will physically refuse to let you submit otherwise.
3. Confirm document coverage is green ("3/3 docs") — every invoice needs its original.
4. File in the entity's home portal (e-MTA / EDS / Mano VMI), then set the stream's
   status to **submitted** in the dropdown.

**Statuses & the locks:** draft → ready → **submitted** → approved → paid.
Setting *submitted* runs three checks and then **locks every invoice in the claim** —
one invoice can be claimed exactly once, ever, including across quarterly vs annual.
You'll see a red BLOCKED banner if: an invoice is already claimed elsewhere, any
ref is still INPUT, or any document is missing. *rejected* / *withdrawn* release the
locks (the invoices become claimable again); going from submitted straight back to
draft is deliberately impossible.

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
- The fee is **charged when the refund is paid** (status → *paid*), computed on the
  amount actually refunded.
- **Settlement follows where the refund lands** (set per customer): paid to the
  *customer* → we **issue a fee invoice**; paid to *us* → we **deduct the fee and remit
  the net** to the customer.
- On the Recovery page, once a claim is paid, click **Issue invoice** (the per‑claim fee
  report becomes the invoice), and download the monthly **fees statement** for the
  aggregate per customer.

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
- **…handle a tax office rejection?** Set the stream to *rejected* (locks release),
  fix the issue, refile in a later period.
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
