"""Billing provider seam (ADR-0013): two payment paradigms behind one interface.

Providers come in two `kind`s:

* **subscription** — Stripe. A hosted Checkout starts a provider-managed
  subscription; a signed webhook is the AUTHORITY for plan/status. No local
  payment record is needed.
* **redirect** — EveryPay (Baltic card gateway). A hosted payment page charges a
  one-off (the first period), we verify the result server-side on return, store
  a card token, and drive recurring charges ourselves (merchant-initiated, MIT).
  There is no provider-side subscription object and no customer portal.

The default is `NullProvider` (kind `none`) whenever no provider is configured:
nothing charges, the in-app plan switch stays for dev/demo.

Networked SDKs/clients are imported LAZILY inside each provider so the module is
import-safe with no billing configured (tests, no-billing deploys).
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.core.config import settings


class BillingError(RuntimeError):
    """A billing operation could not be completed (misconfig / provider fault)."""


@dataclass(frozen=True)
class CheckoutSession:
    """The result of starting a hosted payment: a URL to send the buyer to, plus
    a provider reference (set for redirect providers so the return can be
    correlated; None for subscription providers that self-report via webhook)."""

    url: str
    reference: str | None = None


@dataclass(frozen=True)
class PaymentStatus:
    """A redirect provider's server-verified payment result.

    `state` is our vocabulary: settled | failed | pending. `token` is the stored
    card token captured on the initial payment (for later MIT), when present.
    """

    state: str
    token: str | None = None


@dataclass(frozen=True)
class SubscriptionEvent:
    """The provider-agnostic shape a verified Stripe webhook is reduced to."""

    event_id: str
    event_type: str
    customer_id: str | None
    subscription_id: str | None
    plan_key: str | None
    status: str | None


class BillingProvider(Protocol):
    kind: str          # subscription | redirect | none
    name: str          # stripe | everypay | none
    enabled: bool

    async def ensure_customer(self, *, org_id: str, name: str, email: str | None) -> str: ...

    async def start_checkout(
        self, *, org_id: str, plan_key: str, amount_eur: float,
        order_reference: str, customer_id: str | None,
    ) -> CheckoutSession: ...

    async def create_portal_url(self, *, customer_id: str) -> str: ...

    def parse_webhook(self, payload: bytes, signature: str | None) -> SubscriptionEvent: ...

    async def verify_payment(self, *, reference: str, order_reference: str) -> PaymentStatus: ...

    async def charge_mit(self, *, token: str, amount_eur: float, order_reference: str) -> PaymentStatus: ...

    async def report_usage(self, *, customer_id: str, meter_event: str, quantity: int, identifier: str) -> None: ...


class NullProvider:
    """No billing provider configured — money operations are unavailable."""

    kind = "none"
    name = "none"
    enabled = False

    async def ensure_customer(self, *, org_id, name, email):
        raise BillingError("Billing is not configured.")

    async def start_checkout(self, *, org_id, plan_key, amount_eur, order_reference, customer_id):
        raise BillingError("Billing is not configured.")

    async def create_portal_url(self, *, customer_id):
        raise BillingError("Billing is not configured.")

    def parse_webhook(self, payload, signature):
        raise BillingError("Billing is not configured.")

    async def verify_payment(self, *, reference, order_reference):
        raise BillingError("Billing is not configured.")

    async def charge_mit(self, *, token, amount_eur, order_reference):
        raise BillingError("Billing is not configured.")

    async def report_usage(self, *, customer_id, meter_event, quantity, identifier):
        raise BillingError("Billing is not configured.")


# --- Stripe (subscription) -------------------------------------------------

_STRIPE_STATUS = {
    "active": "active", "trialing": "active", "past_due": "suspended",
    "unpaid": "suspended", "incomplete": "suspended",
    "incomplete_expired": "canceled", "canceled": "canceled", "paused": "suspended",
}


class StripeProvider:
    kind = "subscription"
    name = "stripe"
    enabled = True

    def __init__(self) -> None:
        if not settings.stripe_secret_key:
            raise BillingError("StripeProvider requires stripe_secret_key.")
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise BillingError(
                "The 'stripe' package is required when billing is enabled (pip install stripe)."
            ) from exc
        self._stripe = stripe
        stripe.api_key = settings.stripe_secret_key

    async def ensure_customer(self, *, org_id, name, email):
        try:
            customer = self._stripe.Customer.create(name=name, email=email, metadata={"org_id": org_id})
            return customer.id
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Stripe customer creation failed: {exc}") from exc

    async def start_checkout(self, *, org_id, plan_key, amount_eur, order_reference, customer_id):
        price_id = settings.stripe_price_for(plan_key)
        if not price_id:
            raise BillingError(f"No Stripe price configured for plan '{plan_key}'.")
        try:
            session = self._stripe.checkout.Session.create(
                mode="subscription", customer=customer_id,
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=settings.billing_success_url,
                cancel_url=settings.billing_cancel_url,
                client_reference_id=org_id,
                subscription_data={"metadata": {"org_id": org_id, "plan_key": plan_key}},
            )
            return CheckoutSession(url=session.url, reference=session.id)
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Stripe checkout session failed: {exc}") from exc

    async def create_portal_url(self, *, customer_id):
        try:
            session = self._stripe.billing_portal.Session.create(
                customer=customer_id, return_url=settings.billing_portal_return_url
            )
            return session.url
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Stripe portal session failed: {exc}") from exc

    def parse_webhook(self, payload, signature):
        if not settings.stripe_webhook_secret:
            raise BillingError("stripe_webhook_secret is not set; refusing unverifiable webhook.")
        try:
            event = self._stripe.Webhook.construct_event(
                payload, signature or "", settings.stripe_webhook_secret
            )
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Webhook signature verification failed: {exc}") from exc
        return reduce_stripe_event(event)

    async def verify_payment(self, *, reference, order_reference):
        raise BillingError("Stripe reports results via webhook, not payment verification.")

    async def charge_mit(self, *, token, amount_eur, order_reference):
        raise BillingError("Stripe recurring is provider-managed (no MIT charge here).")

    async def report_usage(self, *, customer_id, meter_event, quantity, identifier):
        """Send a Stripe Billing Meter event for metered/overage pricing. The
        `identifier` dedupes retries within Stripe's window (belt-and-braces with
        our own reported-delta bookkeeping)."""
        try:
            self._stripe.billing.MeterEvent.create(
                event_name=meter_event,
                identifier=identifier,
                payload={"stripe_customer_id": customer_id, "value": str(quantity)},
            )
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"Stripe meter event failed: {exc}") from exc


def reduce_stripe_event(event: dict) -> SubscriptionEvent:
    """Map a verified Stripe event dict to our provider-agnostic shape (pure)."""
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
        event_id=event.get("id", ""), event_type=etype, customer_id=customer_id,
        subscription_id=subscription_id, plan_key=plan_key, status=status,
    )


def _plan_from_subscription_items(sub_obj: dict) -> str | None:
    items = ((sub_obj.get("items") or {}).get("data")) or []
    for item in items:
        price_id = (item.get("price") or {}).get("id")
        for plan_key in ("starter", "pro"):
            if price_id and price_id == settings.stripe_price_for(plan_key):
                return plan_key
    return None


# --- EveryPay (redirect + MIT) --------------------------------------------

# EveryPay payment_state → our vocabulary. `settled` is the only success.
_EVERYPAY_STATE = {
    "settled": "settled", "authorized": "settled",
    "failed": "failed", "voided": "failed", "abandoned": "failed",
    "chargebacked": "failed", "refunded": "failed",
    "initial": "pending", "waiting_for_sca": "pending",
    "sending_to_processor": "pending", "waiting_for_3ds_response": "pending",
}


class EveryPayProvider:
    """EveryPay APIv4 (https://every-pay.com). HTTP Basic (api_username/secret);
    JSON over `httpx`. Hosted payment page → redirect back → server-side verify;
    recurring via a token + merchant-initiated (MIT) charges we schedule."""

    kind = "redirect"
    name = "everypay"
    enabled = True

    def __init__(self) -> None:
        if not settings.everypay_configured:
            raise BillingError("EveryPayProvider requires everypay_api_username/secret/account_name.")
        self._base = settings.everypay_api_base_url.rstrip("/")
        self._username = settings.everypay_api_username
        self._secret = settings.everypay_api_secret
        self._account = settings.everypay_account_name

    # EveryPay has no customer object; the org id is a stable local reference.
    async def ensure_customer(self, *, org_id, name, email):
        return org_id

    def _auth_header(self) -> dict:
        raw = f"{self._username}:{self._secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def _base_body(self, *, amount_eur: float, order_reference: str) -> dict:
        return {
            "api_username": self._username,
            "account_name": self._account,
            "amount": f"{amount_eur:.2f}",
            "order_reference": order_reference,
            "nonce": _nonce(order_reference),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
        }

    async def _post(self, path: str, body: dict) -> dict:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(f"{self._base}{path}", json=body, headers=self._auth_header())
        except Exception as exc:  # noqa: BLE001 - network
            raise BillingError(f"EveryPay request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise BillingError(f"EveryPay {path} returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def start_checkout(self, *, org_id, plan_key, amount_eur, order_reference, customer_id):
        body = self._base_body(amount_eur=amount_eur, order_reference=order_reference)
        body.update({
            "customer_url": f"{settings.api_public_base_url.rstrip('/')}"
                            f"{settings.api_v1_prefix}/billing/everypay/return",
            "callback_url": f"{settings.api_public_base_url.rstrip('/')}"
                            f"{settings.api_v1_prefix}/billing/everypay/callback",
            # Tokenise this first (customer-initiated) payment for later MIT charges.
            "request_token": True,
            "token_agreement": "recurring",
        })
        data = await self._post("/payments/oneoff", body)
        link = data.get("payment_link")
        reference = data.get("payment_reference")
        if not link or not reference:
            raise BillingError(f"EveryPay oneoff missing payment_link/reference: {json.dumps(data)[:200]}")
        return CheckoutSession(url=link, reference=reference)

    async def create_portal_url(self, *, customer_id):
        raise BillingError("EveryPay has no customer portal.")

    def parse_webhook(self, payload, signature):
        raise BillingError("EveryPay confirmation is via payment verification, not a signed webhook.")

    async def verify_payment(self, *, reference, order_reference):
        import httpx

        params = {"api_username": self._username, "payment_reference": reference}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    f"{self._base}/payments/{reference}", params=params, headers=self._auth_header()
                )
        except Exception as exc:  # noqa: BLE001
            raise BillingError(f"EveryPay status request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise BillingError(f"EveryPay status HTTP {resp.status_code}: {resp.text[:200]}")
        return _everypay_status(resp.json())

    async def charge_mit(self, *, token, amount_eur, order_reference):
        body = self._base_body(amount_eur=amount_eur, order_reference=order_reference)
        body.update({"token": token, "token_agreement": "recurring"})
        data = await self._post("/payments/mit", body)
        return _everypay_status(data)

    async def report_usage(self, *, customer_id, meter_event, quantity, identifier):
        raise BillingError("EveryPay has no metered-usage API (flat card charges only).")


def _everypay_status(data: dict) -> PaymentStatus:
    """Map an EveryPay payment object to our PaymentStatus (pure)."""
    state = _EVERYPAY_STATE.get(data.get("payment_state"), "pending")
    # The stored token is returned on the tokenised payment; field name varies by
    # account config — accept the common shapes.
    token = data.get("token") or (data.get("cc_details") or {}).get("token")
    return PaymentStatus(state=state, token=token)


def _nonce(seed: str) -> str:
    """A unique-per-request nonce. EveryPay requires uniqueness, not secrecy;
    derive it from the order reference + wall clock so retries differ. (Scripts
    can't use Math.random; a hash of order_ref+timestamp is unique enough.)"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return hashlib.sha256(f"{seed}:{stamp}".encode()).hexdigest()[:32]


# --- selection -------------------------------------------------------------

_provider: BillingProvider | None = None


def get_billing_provider() -> BillingProvider:
    """Singleton provider chosen from settings (`active_billing_provider`)."""
    global _provider
    if _provider is None:
        active = settings.active_billing_provider
        if active == "stripe":
            _provider = StripeProvider()
        elif active == "everypay":
            _provider = EveryPayProvider()
        else:
            _provider = NullProvider()
    return _provider


def set_billing_provider(provider: BillingProvider | None) -> None:
    """Test seam: inject a fake provider (or None to fall back to settings)."""
    global _provider
    _provider = provider
