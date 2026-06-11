# USER MANUAL — Fleet Fuel & VAT Refund System

How to use the software day-to-day. For installation see INSTALL.md; for security
policy see SECURITY.md; for architecture see README.md.

---

## 1. Signing in & roles

Open `https://<server>:8050` (or your company URL) and sign in. Your role is shown
in the top bar next to your name:

| Role | Can do |
|---|---|
| **viewer** | See every page, use all filters, download all Excel exports. Cannot change anything (save buttons return "Insufficient permissions"). |
| **editor** | Everything a viewer can, plus: change VAT claim statuses, upload documents, register statements, edit master data in the Data manager. |
| **admin** | Everything, plus the **Admin** panel: create users, set roles, reset passwords, disable accounts, review the login log. |

Every change you save is recorded in the audit log **under your username** — visible
to everyone on the History page. Sign out with the link in the top bar.

## 2. The pages, left to right

**Dashboard** — opens with a **month-close status strip** (data loaded? invoices
received? statements reconciled? anomalies? backed up?) — the whole monthly checklist
at a glance. Below, the month at a glance: diesel litres, fleet effective €/L, net spend,
reclaimable VAT, gross invoiced. Below: the diesel benchmark (suppliers ranked
cheapest-first by effective price) and the month-over-month trend. The period
selector at the top switches months.

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

**Import batch** — upload a supplier's PDF or a ZIP of PDFs; the system extracts a
draft of the issued-invoice lines, shows it for you to check and edit, and on confirm
registers the statement + vaults the PDFs (see §3a).

**Invoice control** — two controls on one page (see §4).

**VAT refunds** — the claim matrix and lifecycle (see §5).

**Recovery** — tracks submitted → approved → paid refund amounts with aging; claims
unpaid over 120 days flagged red to chase the tax authority.

**Payments** — invoices due for the period; generate a **SEPA bank file** (pain.001)
for the EUR payments to upload to your bank. Non-EUR shown separately for FX.

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

**Suppliers / Customers** — the master-data cards: legal identity, VAT registrations,
bank accounts, products, invoice registry (suppliers); registration data, payout
account, portals (customers). Red **INPUT** = data still to be collected.

**Data manager** — direct table editing for master data (editor+). Pick database →
table; every row is editable inline (Save / Delete), the bottom row adds new
records. Deleted rows are recoverable: their full values stay in History.

**History** — the audit trail of every change in every database: filter by database,
table, **from date / till date**, and record key. Each row shows when, what, the
action, **who (By column)**, and a field-level diff or full snapshot.

**Admin** (admins only) — user management, login log, security status (TLS,
password storage).

## 3. Monthly routine (editor)

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

4. **Dashboard / Compare / Head-to-head** — review prices, send Stations flags to
   dispatch, note negotiation evidence.
5. **Backup** runs nightly automatically; after a heavy editing day you can run one
   manually (`python3 backup.py`).

## 4. Reading the receipt control

Expectation = the supplier's **cadence** (from the supplier card: every 14 days,
every 30 days, or monthly-per-country) × **activity** (did transactions actually
happen in that slot/country). So "no invoice from Austria" is only a problem if
there was Austrian fueling — otherwise the row says NO ACTIVITY and nothing is
chased. If a supplier confirms in writing that no invoice exists for a flagged slot,
an editor can set `waived=1` on that row in the Data manager (claims database →
invoice_receipt_control); the waiver survives re-runs.

## 5. VAT refunds, quarter by quarter (editor)

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

**Domestic VAT** never appears in these claims — it's discarded at statement triage
(§3) and belongs in the entity's regular home VAT return.

## 6. The document vault

Every invoice must carry its physical document — original PDF or scan. On the
Documents page each known invoice shows its attachments (click to download; a
[SharePoint] link appears if the vault runs on SharePoint) or a red **MISSING** flag
with an upload form. Files are SHA-256 fingerprinted: re-uploading the same file is
skipped, and attaching a file that already sits on a *different* invoice triggers a
wrong-attachment warning. Submission of any VAT claim is blocked while a document
is missing.

## 7. Master data: keeping it right (editor)

- **A supplier changes IBAN / you learn a missing VAT number:** Data manager →
  suppliers → the relevant table → edit → Save. The change is logged with your name
  and flows instantly into claim packs and controls.
- **A new entity (customer):** add the row in customers + a payout bank account +
  its supplier account numbers. Red INPUTs on the Customers page show what's still
  missing; claim packs cannot be submitted until the applicant data is complete.
- **A new supplier:** master data via Data manager (or `supplier_db.py` seed), set
  the **invoice_cadence** (drives receipt control), register its first statement —
  the invoices auto-sync. Transaction-level loading (prices into benchmarks)
  additionally needs a ~30-minute row-map training in `supplier_specs.py` — see
  README "onboarding".

## 8. Excel deliverables — what each file is

| File | Contents |
|---|---|
| **Fleet_Fuel_Master_<month>.xlsx** | Runbook, supplier specs, all transactions, diesel benchmark, interactive supplier comparison (edit the **blue cells**), head-to-head, entity & VAT view, payment calendar, station scorecard |
| **Fleet_Fuel_History_Report.xlsx** | Month-over-month trends from the database: prices, litres, entity VAT, station drift, plus a DB guide with ready-made SQL |
| **VAT_Refund_Claims_<year>.xlsx** | Claim overview + one filing-ready pack per stream; **yellow cells = INPUT to complete**, orange = excluded/locked elsewhere |

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
- **…add a colleague read-only?** Admin → Create user → role *viewer*.
- **…handle a tax office rejection?** Set the stream to *rejected* (locks release),
  fix the issue, refile in a later period.
- **…prove an invoice was only claimed once?** VAT workbook pack: the Duplicate
  control column; or query `vat_claimed_invoices` — each ref appears exactly once.

## 10. What the system will refuse to do (by design)

- Build reports from data that doesn't reconcile to the invoices (validation FAIL stops the pipeline)
- Submit a claim with INPUT invoice refs, missing documents, or an invoice already claimed elsewhere
- Revert a submitted claim to draft without an explicit reject/withdraw
- Let a viewer change anything, or any user change/disable their own admin account
- Include domestic-VAT invoices in refund claims
- Record any change anonymously, or store a password in readable form

If you hit a red BLOCKED banner, it is one of these guardrails — the message says
which, and the fix is always listed in §5 or §6.
