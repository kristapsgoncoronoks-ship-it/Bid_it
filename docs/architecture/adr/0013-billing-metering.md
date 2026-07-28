# ADR-0013 — Subscription billing (Stripe + EveryPay) + usage metering

**Status:** Accepted — **Stripe** (global) and **EveryPay** (Baltic cards) wired behind one provider seam, selected by config. *Supersedes the earlier "merchant-of-record (Paddle)" proposal.*

## Context
We sell EU-wide SaaS subscriptions with a usage component (documents processed). Charging customers means: subscription lifecycle (create/upgrade/downgrade/cancel/dunning), a payment UX, and **VAT on *our own* invoices**. Entitlements (plans, seat limits, `usage_counters`, per-role quotas, module gating) already exist in-app and stay authoritative.

## Selected approach
**Stripe Billing (direct)** behind a swappable provider seam (`services/billing_provider.py`):
- **Stripe Checkout** (hosted) for a paid subscription — no card data touches us (PCI SAQ-A).
- **Stripe Customer Portal** for self-serve payment-method / cancel / invoice history.
- A **signed webhook** (`POST /billing/webhook`) is the **authority** for a tenant's `plan`/`status`: the browser redirect is never trusted. Events are applied **idempotently** (`processed_stripe_events`, unique event id — Stripe delivers at-least-once).
- The **NullProvider is the default** (no `stripe_secret_key`): nothing charges, the in-app plan switch stays for dev/demo. Turning billing on is purely config (`stripe_secret_key`, `stripe_webhook_secret`, per-plan `stripe_price_*`).

Entitlement enforcement stays fully in-app; Stripe owns *money + subscription state* only. `stripe` is a lazy, optional import — absent on no-billing/test deployments.

## Second provider: EveryPay (Baltic cards)
The product serves Baltic transport entities, where **[EveryPay](https://every-pay.com)** (EE/LV/LT, APIv4, banks Swedbank/SEB/LHV) is the local card acquirer of choice. It is wired as a **second provider behind the same seam**, selected by `billing_provider` (`auto|stripe|everypay|none`). EveryPay is a **different paradigm** — a hosted card *gateway*, not a subscription platform — so the seam distinguishes two provider `kind`s:

- **subscription** (Stripe): hosted Checkout → provider-managed subscription → **signed webhook** is the authority.
- **redirect** (EveryPay): hosted payment page charges the first period (a one-off), the buyer is redirected back, and **we verify the result server-side** (`GET /payments/:ref`) — never trusting the redirect. A `billing_payments` row correlates the provider reference ↔ (tenant, plan) so the return **and** the server callback both resolve and apply it **idempotently** (either can win). The initial payment is **tokenised** (`request_token`), and **recurring is merchant-initiated (MIT)**: *we* schedule the monthly charge on the durable job queue (`everypay.charge_mit`, enqueued by the daily scheduler for tenants whose `everypay_next_charge` is due), advancing the next-charge date on success and **suspending** on a decline (a following day re-enqueues — simple retry/dunning). There is **no provider-side subscription object and no customer portal** — both are consequences of the gateway model, surfaced honestly in the UI (no "Manage billing" button for EveryPay).

Why add it rather than Stripe-only: Stripe's Baltic card coverage and local acquiring are weaker than a domestic acquirer, and some Baltic customers expect a local PSP. The seam made the cost of a second provider an *adapter*, not a rewrite. Trade-off: EveryPay puts the recurring-billing state machine (scheduling, dunning, token-expiry) on **us** — accepted here as a bounded, queue-backed job; advanced dunning is a follow-up.

## VAT responsibility (the deliberate tradeoff)
Direct Stripe means **we are the seller of record**: EU VAT registration and remittance/OSS filing are **our** obligation. **Stripe Tax** can *calculate* and collect the correct VAT per customer location and produce compliant invoices, but it does **not** remit or file returns for us — that stays a finance-back-office task. This is the cost of choosing Stripe over a true merchant-of-record (Paddle/Lemon Squeezy), which would have absorbed that compliance entirely. We accept it for Stripe's control, ubiquity, developer ergonomics, and lower fees, and revisit at the volume where MoR fees < the cost of running VAT compliance ourselves.

## Alternatives considered
- **Merchant-of-record (Paddle/Lemon Squeezy)** — absorbs EU VAT filing entirely; but higher fees, less control of the payment UX, and a smaller ecosystem. The right call *if* VAT back-office cost dominates early — revisit trigger below.
- **Build billing ourselves** — never; undifferentiated, high-risk regulatory surface.
- **No metering (flat seats only)** — leaves usage-based value on the table; doesn't match the pricing hypothesis.

## Why appropriate
Stripe is the lowest-friction, best-documented subscription platform; hosted Checkout/Portal keep us out of PCI scope and card storage; the webhook-as-authority + idempotency pattern matches ADR-0011; and the provider seam means a later MoR switch is a new adapter, not a rewrite.

## Risks
- **VAT compliance burden on us** → enable Stripe Tax for calculation; document the remit/file runbook; the MoR revisit trigger below is the escape hatch.
- **Webhook loss / drift** (missed event → stale entitlement) → idempotent applier + reconcile from Stripe as source of truth; a periodic sync job is the follow-up if drift appears.
- **Signature/secret misconfig** → webhook fails **closed** (400) when the secret is unset or the signature is bad; never applies an unverified event.
- **Meter↔provider drift** (usage overage) → meters remain the entitlement authority; reconcile reported usage.

## Revisit when
MoR fees drop below the cost of running VAT compliance in-house *or* cross-border VAT filing becomes a real operational drag (→ move to Paddle/Lemon Squeezy behind the same seam); or enterprise contracts need custom invoicing/procurement (PO, wire, net-30) that Stripe Checkout doesn't fit.

## Status of implementation
Shipped: provider seam (Null default + **Stripe** + **EveryPay**), selectable via `billing_provider`; org `stripe_customer_id`/`stripe_subscription_id` + `everypay_token`/`everypay_next_charge`; `processed_stripe_events` idempotency ledger (generic, both providers) + `billing_payments` (redirect-flow correlation) + migrations; `/billing/checkout` (provider-agnostic) + `/billing/portal` (Stripe) + signed `/billing/webhook` (Stripe) + `/billing/everypay/return` + `/billing/everypay/callback`; entitlement application (plan/status + add-on reconciliation); EveryPay MIT recurring on the job queue (`everypay.charge_mit` + daily scheduler); frontend provider-aware Checkout/Portal wiring; and **metered-usage overage reporting to Stripe** — `usage_counters.reported` watermark, `billing_usage.report_org_usage` reports only `count - reported` as a **Stripe Billing Meter event** (deterministic `identifier` → idempotent), wired as the `billing.report_usage` queue job (daily, only when Stripe is the active provider); tests (Stripe + EveryPay + usage). **Deferred:** Stripe Tax enablement + VAT remit/file runbook, EveryPay advanced dunning/retry policy + token-expiry handling.

**H1.6 dogfood fallback (WO-48):** an ADDITIONAL, config-gated path —
`app/services/platform_billing.py` — invoices InvoiceIQ's OWN paying tenants through the platform's
own AR module (`issuer.py`'s numbering, `issued_service.build_invoice`'s one true construction path,
the SAME PDF/XML/send/dunning surface every tenant already uses) instead of a payment provider, per the
M2 exit criteria's explicit fallback clause: *"we can invoice our own customers through our own AR
module (dogfooding) if Stripe credentials slip. Revenue is not blocked on a provider."* Gated by
`settings.dogfood_billing_enabled` (`platform_org_id` configured AND `not billing_enabled`) so a
tenant is never double-invoiced once Stripe/EveryPay goes live. Generates ONE invoice per (subscriber
org, calendar month) for every OTHER active org whose plan carries a price — idempotent via
`issued_invoices.uq_issued_invoices_subscription_period`, enqueued once/day by the scheduler (carried
by the platform org itself, like `everypay.charge_mit`). Delivery and dunning are NOT new code: the
generated invoice is an ordinary row in the platform org's own `issued_invoices`, so the pre-existing
`POST /issued/{id}/send` and the daily `dunning.run` job already cover it (the platform org is just
another row `scheduler.enqueue_daily`'s per-org loop visits). Applies a **0% placeholder VAT rate**
(`settings.platform_subscription_vat_rate`) — it asserts no VAT treatment and charges nothing extra —
pending the seller-of-record VAT decision below (`docs/DECISIONS-NEEDED.md` §2/§2b); this is
generation + delivery only, no payment collection (collection is the same manual `PATCH
/issued/{id}/payment` route every tenant already uses for a bank transfer — full Stripe/EveryPay
collection remains H1.4, owner-blocked).

**R5(b) fix (WO-31):** `PUT /billing/plan` now refuses (409) a self-service switch to ANY plan with
`price_eur is None` (currently only `enterprise`, "contact us" custom pricing) **unconditionally**,
independent of `settings.billing_enabled`. Previously the guard was
`billing_enabled and target.price_eur`, and `None` is falsy in Python, so the guard never fired for
Enterprise regardless of whether billing was wired — any self-registered org owner could
self-upgrade to the 200-seat Enterprise plan for free, even in a live-Stripe deployment. `Billing.tsx`
now renders a non-actionable "Contact sales" control instead of an active switch button for any
`price_eur === null` plan. **This does NOT resolve R5(a)** — whether/when a live Stripe/EveryPay key
is wired before GA, or pilots are invoiced manually, remains an open GTM/business decision tracked in
`docs/DECISIONS-NEEDED.md` item 2.
