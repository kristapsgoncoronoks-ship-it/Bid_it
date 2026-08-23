# CRM module ("clients") — deep-research findings, 2026-08-23

> **Question (owner):** internal CRM system including a module for clients;
> example github.com/twentyhq/twenty.
>
> **Method:** search-only deep research (this sandbox blocks page fetches):
> 6 angles, every claim requiring two independent sources — vendor
> self-claims labelled, folklore statistics called out. ~50 findings; the
> load-bearing ones below.

## Part 1 — Twenty: adopt, embed, or neither?

**Verdict: neither embed nor fork. Use it as a design reference only.**

1. **Licensing is a trap for code reuse (corroborated).** The repo is
   AGPL-3.0 *plus* ~240 server modules under a separate commercial
   "enterprise" license in the same tree (billing, advanced permissions,
   SSO), plus MIT packages. Combining Twenty server code into our
   GPL-3.0-or-later product makes the combined work AGPL-governed and
   requires carefully excising the commercially-licensed files. Legal
   overhead with no offsetting benefit.
2. **Alien architecture (corroborated).** NestJS/TypeORM/Redis/BullMQ Nx
   monorepo, 4 mandatory containers, 2 GB RAM hard minimum (4–8 GB
   recommended) — roughly a second platform the size of ours on the same
   4 GB VPS. Its metadata-driven runtime schema regenerates GraphQL per
   workspace — powerful, and nothing like our stack.
3. **Tenancy mismatch (partly corroborated, consistent).** Twenty is
   schema-per-workspace, not RLS. Adopting it beside our three-layer
   row-level model means per-tenant schemas and per-tenant runtime
   migrations — the exact isolation-reimplementation cost that killed the
   Spark idea (§2b of the analytics design), in CRM form.
4. **Operational fragility documented (corroborated).** Self-host upgrade
   failures are the recurring complaint (crash-looping migrations
   v1.21→v1.23, blank UI after version jumps; cross-version upgrades only
   supported since v1.22).
5. **Side-by-side via API is legally clean** (mere aggregation — corroborated)
   **but operationally wrong here:** shared-IdP SSO (Keycloak-class infra we
   don't run), and the documented core pain — the same customer existing in
   two systems with bidirectional sync conflicts. Practitioner consensus for
   a product that ALREADY owns the customer master, offers and dunning:
   build the thin CRM layer in-app.
6. **What Twenty IS good for:** its design choices are widely praised and
   match modern consensus — no separate Lead entity, pipeline state as a
   status on the record, notes/tasks as first-class attachable records,
   kanban canon. We copy the ideas, not the code.

## Part 2 — What the module should be (evidence-backed)

**The segment norm is almost exactly what InvoiceIQ already has.** Table
stakes for a "clients" module in an invoicing product (corroborated across
Moneybird, sevDesk, Zoho, Jobber, Holded): contact master with billing data
+ auto-linked document history + free-text notes + a DERIVED activity
timeline (documents sent/viewed/paid, status changes — not a hand-curated
feed). Usage surveys: contact management ~94%, interaction tracking ~88%,
scheduling/reminders ~85% — while pipeline/forecasting features trail badly.
SMEs lean toward CRM inside the tool they already run.

**Gaps between the norm and our tree — the actual v1:**

1. **Customer notes + derived timeline** on the customer page: one
   reverse-chronological stream merging notes (new, small table) with what
   we already know (offers sent/accepted, invoices issued/paid, dunning
   sent, projects, generated documents). Timeline = mostly a VIEW over
   existing audited events.
2. **Lifecycle status on the customer record, NO lead entity
   (corroborated anti-pattern):** a hard lead→customer conversion step is a
   documented source of duplicates and lost context (Salesforce model);
   modern simple CRMs put a stage attribute on the record. For us:
   `customers.lifecycle` ∈ prospect|active|dormant|lost — a column, not a
   table.
3. **Kanban over the EXISTING offer pipeline:** columns = offer status
   (+ org-configurable in-between stages later), won/lost via the existing
   accept/reject transitions (terminal, off the board, reportable), and
   **staleness flags** (Pipedrive's praised "rotting": red after N days
   without movement) feeding the Next-actions module. Stage transitions
   persisted as timestamped history rows for time-in-stage — cheap now,
   impossible to reconstruct later.
4. **Quote-viewed signal** (segment table stakes): when the client portal
   (below) opens an offer, stamp viewed_at and surface it on the timeline.
5. **Follow-ups:** already designed — the Next-actions research IS the CRM
   follow-up layer (Pipedrive's "every open deal has exactly one next
   activity" and OnePageCRM's action stream independently validate it).

**Explicitly OUT (evidence: unused or abandoned in this segment):** lead
scoring, forecasting/weighted pipeline, email inbox sync/BCC logging
(embedded in NO surveyed invoicing suite), a separate lead entity, custom
objects.

## Part 3 — "Module for clients": the client portal

The strongest reading of the owner's phrase, and widespread in the segment
(Jobber Client Hub, Zoho portal, Invoice Ninja, Moneybird):

- **Auth: magic links, not accounts (corroborated as the dominant model)** —
  tokenized, expiring, revocable links; clients who visit twice a year
  never manage a password. Layered checks for sensitive actions.
- **Minimal valuable scope (synthesized from what everyone ships first):**
  (1) view/approve/decline an OFFER — with the e-sign seam and "request
  changes"; (2) view invoices with status and pay (payment-rail decision
  separate); (3) see shared documents (contract, acceptance — reusing
  project_documents sharing flags); (4) that's all.
- Evidence notes, honestly: "portals get invoices paid ~4× faster" is a
  convergent VENDOR claim (Jobber, QuickBooks) with no independent study;
  portal → quote-acceptance evidence is weak/indirect (e-sign completion
  speed is the nearest real datum); "fewer where's-my-invoice calls" is
  vendor-reported. The portal is justified by segment table-stakes and the
  quote-approval flow, not by inflated stats.

## Folklore alerts (do not cite, including in our own marketing)

- "CRM failure rate is 30–70%" — incompatible definitions across ancient
  studies; not a usable number.
- "80% of sales need 5+ follow-ups / 44% give up after one" — debunked;
  traces to a 1942 salesmen survey. The Next-actions copy must not use it.

## Proposed work orders (queue after current plan, decision-free)

- **WO-H — CRM light:** customer notes + derived timeline + lifecycle
  status column + offers kanban with staleness + stage-history rows.
  All in-app, no new external anything. ~1–2 sessions.
- **WO-I — Client portal:** magic-link tokens (revocable, per-customer),
  offer view/approve/decline (+viewed_at), invoice list/status, shared
  documents. Payment-in-portal rides the existing rails; e-sign stays the
  later seam. ~2 sessions.
