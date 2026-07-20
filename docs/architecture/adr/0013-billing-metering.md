# ADR-0013 — Merchant-of-record billing + usage metering

**Status:** Proposed (plan/metering model built; provider wiring pending)

## Context
We sell EU-wide SaaS subscriptions with a usage component (documents processed). Charging EU customers means handling VAT/MOSS on *our own* invoices — a real compliance burden — plus subscription lifecycle and metered overage.

## Selected approach
Keep the **existing plan + module-gating + usage-metering** model (plans, seat limits, `usage_counters`, per-role quotas) as the source of truth for *entitlements*. Wire a **merchant-of-record** provider (e.g. Paddle) for *money*: it handles EU VAT on our subscriptions, checkout, dunning, and tax invoices. Usage overage reported to the provider from our meters. Entitlement enforcement stays in-app; the provider is the billing/tax system of record.

## Alternatives considered
- **Stripe Billing (direct)** — maximal control + usage-based billing, but *we* become responsible for EU VAT registration/MOSS filing across member states early — significant overhead pre-scale.
- **Build billing ourselves** — never; undifferentiated, high-risk, regulatory surface.
- **No metering (flat seats only)** — leaves usage-based value on the table and doesn't match the pricing hypothesis.

## Why appropriate
A merchant-of-record removes the biggest early compliance burden (cross-border VAT on our sales) so we can monetise without a tax back-office. Our meters already exist and stay authoritative for entitlements; the provider is swappable behind a billing seam.

## Risks
- MoR fees + less control over the payment UX → acceptable trade for compliance offload early; revisit at scale.
- Meter↔provider drift → reconcile usage reports; meters remain the entitlement authority.

## Revisit when
Volume/margin justifies taking VAT compliance in-house (→ Stripe Billing direct), or enterprise contracts need custom invoicing/procurement flows.
