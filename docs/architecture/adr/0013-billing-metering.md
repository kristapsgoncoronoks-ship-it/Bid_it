# ADR-0013 — Subscription billing (Stripe) + usage metering

**Status:** Accepted — **Stripe** chosen and wired (Checkout + Customer Portal + signed webhook). *Supersedes the earlier "merchant-of-record (Paddle)" proposal.*

## Context
We sell EU-wide SaaS subscriptions with a usage component (documents processed). Charging customers means: subscription lifecycle (create/upgrade/downgrade/cancel/dunning), a payment UX, and **VAT on *our own* invoices**. Entitlements (plans, seat limits, `usage_counters`, per-role quotas, module gating) already exist in-app and stay authoritative.

## Selected approach
**Stripe Billing (direct)** behind a swappable provider seam (`services/billing_provider.py`):
- **Stripe Checkout** (hosted) for a paid subscription — no card data touches us (PCI SAQ-A).
- **Stripe Customer Portal** for self-serve payment-method / cancel / invoice history.
- A **signed webhook** (`POST /billing/webhook`) is the **authority** for a tenant's `plan`/`status`: the browser redirect is never trusted. Events are applied **idempotently** (`processed_stripe_events`, unique event id — Stripe delivers at-least-once).
- The **NullProvider is the default** (no `stripe_secret_key`): nothing charges, the in-app plan switch stays for dev/demo. Turning billing on is purely config (`stripe_secret_key`, `stripe_webhook_secret`, per-plan `stripe_price_*`).

Entitlement enforcement stays fully in-app; Stripe owns *money + subscription state* only. `stripe` is a lazy, optional import — absent on no-billing/test deployments.

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
Shipped: provider seam (Null default + Stripe), org `stripe_customer_id`/`stripe_subscription_id`, `processed_stripe_events` idempotency ledger + migration, `/billing/checkout` + `/billing/portal` (admin) + signed `/billing/webhook`, entitlement application (plan/status + add-on reconciliation), frontend Checkout/Portal wiring, tests. **Deferred:** usage-overage metered pricing to Stripe (meters exist; not yet reported), Stripe Tax enablement + the VAT remit/file runbook, and dunning-email customisation (Stripe handles baseline dunning).
