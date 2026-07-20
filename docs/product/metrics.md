# InvoiceIQ — Product Metrics & Event-Tracking Plan

> Companion to the [PRD](./product-requirements.md). Defines the north-star, the activation event, funnel & retention metrics, the event schema to instrument, and guardrails. Written so engineering can implement tracking without further product input.

---

## 1. North-star metric

**Weekly Active Reconciling Tenants (WART)** — the number of workspaces that, in a given week, **captured-and-confirmed ≥ N invoices AND viewed a dashboard or exported** (i.e. actually used the core loop, not just logged in).

- **Why:** it captures *value delivered* (invoices turned into a trusted record and used), not vanity logins. It is a leading indicator of retention and expansion, and it aligns every team (capture quality → analysis → export).
- **Guardrail against gaming:** requires *both* capture and use; a tenant uploading junk without confirming/using it doesn't count.
- **Threshold N:** start at **N=10 invoices/week**; calibrate from cohort data.

Supporting north-star context: **total invoices processed/month** (the moat-building dataset) and **% of processed invoices auto-captured deterministically** (product leverage / cost).

---

## 2. Activation event

**Activation = a workspace reaches "First Reconciled Value" (FRV):** within its first 14 days, it **confirms ≥ 10 invoices** *and* performs **one value action** (view a dashboard, run a by-dimension breakdown, or export).

- **Rationale:** a single upload isn't activation; the "aha" is *seeing your own spend clean and usable*. FRV predicts conversion and retention better than signup or first-upload.
- **Instrumentation:** derived from `invoice.confirmed` count + any of `dashboard.viewed` / `export.performed` within the tenant's first 14 days.
- **Onboarding is optimised toward FRV**: the setup flow should drive to "capture your first 10 invoices, then see them."

Secondary activation for the **issuing** attach: **first issued invoice delivered** (`issued.created` → PDF generated).

---

## 3. Funnel & lifecycle metrics (AARRR)

| Stage | Metric | Definition | Target (hyp.) |
|---|---|---|---|
| **Acquisition** | Signups / week; signup→workspace-created | Registrations creating a workspace | — |
| **Activation** | Activation rate | % of new workspaces hitting FRV in 14d | ≥ 40% |
| | Time-to-activation | Median days signup→FRV | ≤ 3 days |
| | Time-to-first-value | Median minutes signup→first `invoice.confirmed` | ≤ 20 min |
| **Retention** | WART / WAU | See north-star | grow WoW |
| | 4-week / 12-week logo retention | % tenants still active | ≥ 85% / ≥ 75% |
| | Feature retention | % activated tenants using capture again in week 2 | ≥ 70% |
| **Referral** | Invites sent/accepted; practice→client expansion | Seats + linked workspaces added | — |
| **Revenue** | Trial→paid conversion | % trials converting | ≥ 20% |
| | NRR (net revenue retention) | Expansion − churn − contraction | ≥ 110% |
| | ARPA, LTV\:CAC | Standard SaaS | LTV\:CAC ≥ 3 |
| | Docs processed / paying tenant | Expansion signal (usage metric) | grow |

---

## 4. Product quality / trust metrics (fintech-specific)

These are as important as growth metrics — trust is the product.

| Metric | Why it matters | Target |
|---|---|---|
| **Deterministic capture rate** | % invoices parsed without AI/manual (leverage + cost) | ≥ 70% and rising |
| **Correction rate** | % confirmed drafts a human had to edit | trend ↓ |
| **Duplicate catch rate** | duplicates flagged / duplicates that existed | ≥ 95% |
| **VAT/FX correctness** | sampled audit of computed VAT vs. source | ~100% |
| **Export re-work rate** | % exports needing manual fix downstream | ↓ |
| **Job success rate / DLQ depth** | queue health (recurring, reminders, webhooks) | ≥ 99% success; DLQ ~0 |
| **Webhook delivery success** | signed deliveries 2xx within N attempts | ≥ 99% |
| **Cross-tenant leak incidents** | isolation integrity | **0 (hard)** |
| **Audit-chain verification pass** | tamper-evidence intact per tenant | 100% |

---

## 5. Retention drivers (what keeps tenants)

1. **The dataset compounds.** The longer a tenant captures, the more their history, dimensions, and trends live only here — switching cost rises with value.
2. **Period rhythm.** Monthly close creates a recurring, habitual return (reconcile → export). Instrument and nurture the monthly loop.
3. **Trust events.** A caught duplicate / prevented VAT error / passed audit are "saved my bacon" moments — surface and celebrate them.
4. **Multi-entity + dimensions lock-in.** Once spend is tagged by vehicle/entity/project, the reporting is bespoke to them.
5. **Integrations.** Webhooks/exports wired into their stack make removal costly.
6. **Accountant ↔ client graph.** Practices that put clients on the platform anchor both sides.

**Leading churn signals to alert on:** activation stall (no FRV by day 10), capture drop-off (week-2 usage < week-1), rising correction rate, export abandonment, seats going inactive.

---

## 6. Event-tracking plan

Principles: **tenant-scoped** (every event carries `org_id`, never PII in properties), **derived from existing audit/domain actions where possible**, **no financial figures or personal data in analytics payloads** (counts/booleans/enums only), EU-hosted analytics.

### Core events

| Event | Trigger | Key properties (no PII) |
|---|---|---|
| `workspace.created` | Registration → org created | plan, source |
| `user.invited` / `user.joined` | Invitation lifecycle | role |
| `invoice.uploaded` | File parsed to draft | source (upload/email/api/xml), parse_method (xml/text/ocr/ai), duration_bucket |
| `invoice.confirmed` | Draft saved as record | currency, has_dimensions (bool), vat_scheme |
| `validation.flagged` | A check fires | check_type (duplicate/missing/tax/anomaly), resolved (bool) |
| `dimension.tagged` | Dimension set on invoice/expense | dimension_key |
| `dashboard.viewed` | Analytics page loaded | view (summary/by_dimension/vat/aging) |
| `export.performed` | CSV/Excel/SAF-T/ERP export | format, scope |
| `issued.created` | Outbound invoice issued | doc_type (invoice/credit_note), entity_id_hash |
| `issued.payment_recorded` | AR payment | status (paid/partial), overdue (bool) |
| `webhook.registered` / `webhook.delivered` | Integration | event_type, response_class (2xx/4xx/5xx) |
| `job.completed` | Background job outcome | kind, status |
| `limit.reached` | Usage cap hit | metric (docs/uploads), plan |
| `plan.upgraded` / `plan.downgraded` | Billing change | from_plan, to_plan |
| `retention.hold_applied` | Legal hold | — |

### Derived metrics (computed, not tracked)
- **FRV / activation** = `invoice.confirmed` ≥10 AND (`dashboard.viewed` OR `export.performed`) within 14d.
- **WART** = weekly tenants with ≥N `invoice.confirmed` AND a value action.
- **Deterministic capture rate** = `invoice.confirmed` where parse_method ∈ {xml,text} / all confirmed.
- **Correction rate** = confirmed-with-edits / confirmed (needs a `was_edited` flag on confirm).

### Instrumentation notes
- Reuse the **hash-chained audit log** and **domain events** already emitted (`webhooks.emit`) as the source of truth; the analytics layer subscribes rather than double-instrumenting.
- Add a lightweight `was_edited` flag on invoice confirm to power correction rate.
- Analytics pipeline must be **EU-hosted** and carry **no PII / no amounts** in event properties (buckets/enums/booleans only). See [risks](./risks.md).

---

## 7. Dashboards to build (internal)

1. **Growth:** signups, activation rate, time-to-activation, WART, conversion, NRR.
2. **Product health:** deterministic capture rate, correction rate, duplicate catch rate, export re-work.
3. **Reliability:** job success/DLQ, webhook success, p95 latencies, uptime.
4. **Trust/compliance:** cross-tenant incidents (must be 0), audit-verification pass rate, retention/legal-hold status.
5. **Revenue:** MRR, ARPA, plan mix, overage revenue, churn/expansion.

---

## 8. Metrics summary

- **North-star:** Weekly Active Reconciling Tenants (capture **and** use).
- **Activation:** First Reconciled Value — ≥10 confirmed invoices + a value action within 14 days.
- **Retention driver #1:** the compounding, tenant-owned dataset + the monthly close rhythm.
- **Non-negotiable guardrails:** zero cross-tenant leaks, ~100% VAT/FX correctness, ≥99% job/webhook success.
- **Instrument from existing audit/domain events; EU-hosted analytics; no PII/amounts in payloads.**
