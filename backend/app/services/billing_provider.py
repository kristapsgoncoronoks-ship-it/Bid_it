"""Billing provider seam (ADR-0013): Stripe, behind a swappable interface.

The default is `NullProvider` — active whenever no Stripe secret key is set. In
that mode NOTHING charges anyone; the in-app plan switch (`PUT /billing/plan`)
stays available for dev/demo, exactly as before this module existed. When a
Stripe key IS configured, `get_billing_provider()` returns the `StripeProvider`
and paid plan changes must flow through Stripe Checkout; the signed webhook is
the AUTHORITY for a tenant's plan/status.

`stripe` is imported LAZILY inside `StripeProvider` so the package is only a
hard dependency where billing is actually turned on — tests and no-billing
deployments never import it.

A `BillingError` is raised for provider/config faults so the route can turn it
into a clean 4xx/5xx rather than leaking a Stripe SDK exception.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings


class BillingError(RuntimeError):
    """A billing operation could not be completed (misconfig / provider fault)."""


@dataclass(frozen=True)
class SubscriptionEvent:
    """The provider-agnostic shape a verified webhook is reduced to.

    `plan_key` is None when the event doesn't map to a known Price (ignored).
    `status` is our lifecycle vocabulary: active | suspended | canceled.
    """

    event_id: str
    event_type: str
    customer_id: str | None
    subscription_id: str | None
    plan_key: str | None
    status: str | None


class BillingProvider(Protocol):
    enabled: bool

    async def ensure_customer(self, *, org_id: str, name: str, email: str | None) -> str: ...

    async def create_checkout_url(
        self, *, customer_id: str, plan_key: str, org_id: str
    ) -> str: ...

    async def create_portal_url(self, *, customer_id: str) -> str: ...

    def parse_webhook(self, payload: bytes, signature: str | None) -> SubscriptionEvent: ...


class NullProvider:
    """No billing provider configured — money operations are unavailable."""

    enabled = False

    async def ensure_customer(self, *, org_id: str, name: str, email: str | None) -> str:
        raise BillingError("Billing is not configured.")

    async def create_checkout_url(self, *, customer_id: str, plan_key: str, org_id: str) -> str:
        raise BillingError("Billing is not configured.")

    async def create_portal_url(self, *, customer_id: str) -> str:
        raise BillingError("Billing is not configured.")

    def parse_webhook(self, payload: bytes, signature: str | None) -> SubscriptionEvent:
        raise BillingError("Billing is not configured.")


# Stripe subscription status → our lifecycle vocabulary.
_STRIPE_STATUS = {
    "active": "active",
    "trialing": "active",
    "past_due": "suspended",
    "unpaid": "suspended",
    "incomplete": "suspended",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
    "paused": "suspended",
}


class StripeProvider:
    """Stripe Checkout + Customer Portal + signed webhooks.

    We are the seller of record with direct Stripe (see ADR-0013 tradeoff): VAT
    on our own subscriptions is our responsibility. Stripe Tax can compute it
    (enable per-account); this seam stays agnostic to that toggle.
    """

    enabled = True

    def __init__(self) -> None:
        if not settings.stripe_secret_key:
            raise BillingError("StripeProvider requires stripe_secret_key.")
        try:
            import stripe  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise BillingError(
                "The 'stripe' package is required when billing is enabled (pip install stripe)."
            ) from exc
        self._stripe = stripe
        stripe.api_key = settings.stripe_secret_key

    async def ensure_customer(self, *, org_id: str, name: str, email: str | None) -> str:
        try:
            customer = self._stripe.Customer.create(
                name=name, email=email, metadata={"org_id": org_id}
            )
            return customer.id
        except Exception as exc:  # noqa: BLE001 - SDK raises many types
            raise BillingError(f"Stripe customer creation failed: {exc}") from exc

    async def create_checkout_url(self, *, customer_id: str, plan_key: str, org_id: str) -> str:
        price_id = settings.stripe_price_for(plan_key)
        if not price_id:
            raise BillingError(f"No Stripe price configured for plan '{plan_key}'.")
        try:
            session = self._stripe.checkout.Session.create(
                mode="subscription",
                customer=customer_id,
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=settings.billing_success_url,
                cancel_url=settings.billing_cancel_url,
                client_reference_id=org_id,
                # Ties the resulting subscription back to the tenant defensively.
                subscription_data={"metadata": {"org_id": org_id, "plan_key": plan_key}},
            )
            return session.url
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Stripe checkout session failed: {exc}") from exc

    async def create_portal_url(self, *, customer_id: str) -> str:
        try:
            session = self._stripe.billing_portal.Session.create(
                customer=customer_id, return_url=settings.billing_portal_return_url
            )
            return session.url
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Stripe portal session failed: {exc}") from exc

    def parse_webhook(self, payload: bytes, signature: str | None) -> SubscriptionEvent:
        if not settings.stripe_webhook_secret:
            raise BillingError("stripe_webhook_secret is not set; refusing unverifiable webhook.")
        try:
            event = self._stripe.Webhook.construct_event(
                payload, signature or "", settings.stripe_webhook_secret
            )
        except Exception as exc:  # noqa: BLE001 - signature / parse failure
            raise BillingError(f"Webhook signature verification failed: {exc}") from exc
        return reduce_stripe_event(event)


def reduce_stripe_event(event: dict) -> SubscriptionEvent:
    """Map a verified Stripe event dict to our provider-agnostic shape.

    Pure (no network / SDK), so the mapping is unit-testable on its own. Handles
    the subscription lifecycle events we subscribe to; anything else reduces to a
    no-op event (plan_key + status None) that the applier ignores.
    """
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    customer_id = obj.get("customer")
    subscription_id = None
    plan_key = None
    status = None

    if etype.startswith("customer.subscription."):
        subscription_id = obj.get("id")
        status = "canceled" if etype.endswith(".deleted") else _STRIPE_STATUS.get(obj.get("status"))
        meta = obj.get("metadata") or {}
        plan_key = meta.get("plan_key") or _plan_from_subscription_items(obj)
    elif etype == "checkout.session.completed":
        subscription_id = obj.get("subscription")
        status = "active"
        plan_key = (obj.get("metadata") or {}).get("plan_key")

    return SubscriptionEvent(
        event_id=event.get("id", ""),
        event_type=etype,
        customer_id=customer_id,
        subscription_id=subscription_id,
        plan_key=plan_key,
        status=status,
    )


def _plan_from_subscription_items(sub_obj: dict) -> str | None:
    """Reverse a subscription's Price id back to our plan key (fallback path)."""
    items = ((sub_obj.get("items") or {}).get("data")) or []
    for item in items:
        price_id = (item.get("price") or {}).get("id")
        for plan_key in ("starter", "pro"):
            if price_id and price_id == settings.stripe_price_for(plan_key):
                return plan_key
    return None


_provider: BillingProvider | None = None


def get_billing_provider() -> BillingProvider:
    """Singleton provider chosen from settings. Null unless a Stripe key is set."""
    global _provider
    if _provider is None:
        _provider = StripeProvider() if settings.billing_enabled else NullProvider()
    return _provider


def set_billing_provider(provider: BillingProvider | None) -> None:
    """Test seam: inject a fake provider (or None to fall back to settings)."""
    global _provider
    _provider = provider
