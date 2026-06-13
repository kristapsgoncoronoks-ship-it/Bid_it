# Product Roadmap — how the Fleet Fuel & VAT Refund System should evolve

**Audience:** the five Baltic transport entities that operate this system, and whoever
maintains it. **Purpose:** turn a solid back-office tool into a system that actively
*makes and saves money, removes manual work, and de-risks compliance*.

This roadmap is organised by **business outcome**, then sequenced into three horizons.
Every item ties back to a measurable result, not a feature for its own sake.

---

## What the business actually needs (the "why")

The software exists to serve five outcomes. Everything below ladders up to one of them:

| Goal | The money question it answers | Today | Where we want to be |
|------|-------------------------------|-------|---------------------|
| **Recover more VAT, faster** | "How much foreign VAT are we owed, and is every claim airtight before the deadline?" | Claims tracked 1A→5 with a checklist + deadlines | Nothing claimable is ever missed or rejected; cash comes back sooner |
| **Cut fuel spend** | "Are we paying a competitive net price, and where are we being overcharged?" | Price intelligence + anomaly flags per line | Overcharges caught automatically and turned into supplier credits / renegotiation |
| **Remove manual work** | "How many hours does a close take, and how much is re-keying?" | Intake → validate → master → history pipeline | One-click monthly close; humans only handle exceptions |
| **De-risk compliance** | "If a tax authority audits us tomorrow, can we prove every number?" | Full audit log, document vault, integrity checks | Audit-ready in minutes, evidence reproducible on demand |
| **Decide better** | "Which supplier, which country, which route is cheapest — and what's it trending to?" | Reports + benchmarks | Forward-looking recommendations, not just rear-view reports |

KPIs to track the whole way: **€ VAT recovered / € claimable**, **days-to-refund**,
**€ overcharges identified**, **hours per monthly close**, **% claims rejected**,
**deadline misses (target: 0)**.

---

## Horizon 1 — Trust & time-savings (next 1–2 quarters)
*Theme: make the existing pipeline reliable, proactive, and hands-off. Highest ROI is
not new features — it is removing the manual babysitting and the "did it work?" anxiety.*

1. **Proactive notifications (email/Teams).** *Backlog item.* The worklist already knows
   filing deadlines, expiring documents (POA), 2B/3D open actions, and money-received
   prompts — push them. **Outcome:** zero missed VAT deadlines, no expired POA silently
   breaking a claim. *Depends on: an SMTP/Graph sender (the SharePoint Graph creds may
   already cover it).*
2. **One-click monthly close.** Chain `consolidate → build_master → history →
   invoice_control → backup` behind a single guarded button with a live progress log and
   an OK/Bad summary (the upload-receipt pattern already exists). **Outcome:** a close
   that takes a half-day of CLI steps becomes minutes; fewer human errors.
3. **Finish the integrity/error self-control loop.** The admin error log + backup +
   document SHA-verification shipped; add a **scheduled health digest** (nightly: backup
   ran? hashes match? any errors logged? any claim past deadline?) emailed to admins.
   **Outcome:** problems surface before a human notices, not after.
4. **Scale foundation (in progress).** Land the Postgres-cutover work tracked in
   `SCALING.md` so the system survives growth: route all modules through `db.connect()`,
   port the dialect-isms, move the queue + leases to a shared DB. **Outcome:** more
   entities / more invoices without the single-box ceiling; multi-user without contention.
5. **PDF generation for .docx templates.** *Backlog item.* Text templates already export
   PDF; finish docx. **Outcome:** contracts/POAs generated and filed without a manual
   export hop.
6. **Test-coverage + money-precision sweep.** *Backlog items.* Cover `invoice_control`,
   `ingest`, `build_master`, `history`; finish the `money.f2` sweep. **Outcome:** changes
   ship safely; no rounding drift on amounts that feed VAT claims.

---

## Horizon 2 — Money & decisions (3–4 quarters out)
*Theme: turn the data we already collect into recommendations and recovered cash.
We have the history; start acting on it.*

1. **Overcharge → recovery workflow.** Today anomalies are *flagged*; make them
   *actionable*: group flagged overcharges per supplier/period into a "claim back" packet
   (evidence + computed delta in net EUR), track it to a credit note. **Outcome:** the
   price-intelligence module pays for itself in supplier credits.
2. **Refund forecasting & cash-flow view.** From open claims + historical approval times
   per country, project "€X expected back by month Y." **Outcome:** treasury can plan
   around VAT cash; financing decisions improve.
3. **Supplier & country scorecards.** Net-price competitiveness, rebate realisation,
   invoice-quality (how often documents are missing/late), VAT-recovery success rate —
   per supplier and per country, trended. **Outcome:** data-backed leverage at contract
   renewal; steer volume to the genuinely cheapest channel.
4. **Route/station cost intelligence.** Using the `station` (city) dimension and net
   effective price, surface "cheaper station within N km / on this corridor."
   **Outcome:** drivers/dispatch nudged toward lower-cost fills — direct spend reduction.
5. **Automated claim assembly.** When a period closes and the checklist is green, auto-
   advance eligible claims to "ready to submit" and pre-build the submission packet.
   **Outcome:** humans approve, not assemble — faster filing, fewer rejections.
6. **Smarter extraction.** Expand the PDF parser registry and add learning/fallback for
   new supplier layouts so new fuel cards onboard without code. **Outcome:** onboarding a
   new supplier is a data task, not an engineering task.

---

## Horizon 3 — Platform & strategic moat (12+ months)
*Theme: from internal tool to platform — integrations, automation at the edges, and an
AI layer that compounds the data advantage.*

1. **E-filing integrations with tax-authority portals.** Where Member States expose
   electronic 2008/9/EC submission, integrate so claims file directly (the
   `portal_scraper` groundwork hints at the direction). **Outcome:** submission becomes a
   click; status syncs back automatically.
2. **ERP / accounting integration.** Two-way with the finance system (postings, credit
   notes, refund receipts). **Outcome:** no double-entry; the books and this system agree
   by construction.
3. **Multi-tenant / multi-group.** Clean isolation so additional entities or an external
   "VAT-recovery-as-a-service" offering can run on one deployment. **Outcome:** the tool
   becomes a sellable service, not just a cost centre.
4. **AI copilot for compliance & analysis.** A natural-language layer over the audited
   data: "Why was the Belgium Q2 claim rejected?", "Show suppliers whose net price drifted
   above market this quarter," "Draft the appeal for claim 3D-…". Grounded strictly in the
   audit log and documents (no hallucinated numbers). **Outcome:** expert-level answers
   without an analyst; faster appeals and reviews.
5. **Real-time fuel-price feeds & alerts.** Move price intelligence from periodic to live
   where feeds allow, with threshold alerts. **Outcome:** react to market moves in days,
   not at month-end.
6. **Driver/operations mobile surface.** A thin read-only mobile view (nearest cheap
   station, card status). **Outcome:** the cost intelligence reaches the point of decision
   — the pump.

---

## Sequencing principles (how to choose what's next)

1. **Money and risk beat polish.** Anything that recovers VAT, catches overcharges, or
   prevents a deadline miss/rejection outranks UX nicety.
2. **Automate the recurring pain first.** The monthly close and notifications repay
   themselves every period.
3. **Don't scale problems — fix correctness first.** Finish the money-precision and
   test-coverage sweeps before multiplying volume on Postgres.
4. **Keep the guardrails.** Every new surface honours the existing conventions: NET-EUR
   final prices, `money.py` quantisation, escaped output, audited changes, admin-only VAT
   module, and the error/backup/integrity self-control loop. Evolution must not erode the
   compliance backbone that makes the numbers trustworthy.
5. **Measure the outcome, not the output.** Each shipped item is judged against the KPIs
   above — € recovered, days-to-refund, hours-per-close, rejections, deadline misses.

---

## Near-term backlog (the concrete next steps, from `CLAUDE.md`)
- Email/Teams notifications for worklist deadlines & expiring documents (H1 #1).
- One-click monthly close (H1 #2).
- Postgres horizontal-scale port — see `SCALING.md` (H1 #4, in progress).
- PDF generation for `.docx` templates (H1 #5).
- Money-precision sweep (`build_master`, analytics overpay/total) + test coverage for
  `invoice_control`/`ingest`/`build_master`/`history` (H1 #6).
- Finish the remaining `except: pass` → log migration. (The `rejected` keep-locks gate
  is done — locks now release only via `withdraw_claim`.)

This roadmap is a direction, not a contract — revisit it each quarter against the KPIs
and what the entities are actually feeling as pain.
