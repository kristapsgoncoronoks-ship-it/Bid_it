"""Stripe billing (Phase 3.9 / ADR-0013): the event→entitlement mapping, webhook
idempotency, and route gating — all without touching the real Stripe SDK.

The provider seam is injected with a Fake so no network / secret keys are needed;
the pure `reduce_stripe_event` mapping is tested directly.
"""

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import billing as billing_svc
from app.services import modules as modules_svc
from app.services.billing_provider import (
    BillingError,
    CheckoutSession,
    NullProvider,
    SubscriptionEvent,
    get_billing_provider,
    reduce_stripe_event,
    set_billing_provider,
)


class FakeProvider:
    """Stand-in subscription (Stripe-like) provider — no SDK, no network."""

    kind = "subscription"
    name = "stripe"
    enabled = True

    def __init__(self, event: SubscriptionEvent | None = None):
        self._event = event
        self.customer_id = "cus_fake123"

    async def ensure_customer(self, *, org_id, name, email):
        return self.customer_id

    async def start_checkout(self, *, org_id, plan_key, amount_eur, order_reference, customer_id):
        return CheckoutSession(url=f"https://checkout.test/{plan_key}", reference="cs_test")

    async def create_portal_url(self, *, customer_id):
        return "https://portal.test/session"

    def parse_webhook(self, payload, signature):
        if self._event is None:
            raise BillingError("signature verification failed")
        return self._event


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_billing_provider(None)  # back to settings-derived (NullProvider) for other tests


# --- provider selection ----------------------------------------------------


def test_default_provider_is_null_when_unconfigured():
    set_billing_provider(None)
    p = get_billing_provider()
    assert isinstance(p, NullProvider)
    assert p.enabled is False


@pytest.mark.asyncio
async def test_null_provider_operations_raise():
    p = NullProvider()
    with pytest.raises(BillingError):
        await p.ensure_customer(org_id="o", name="n", email=None)
    with pytest.raises(BillingError):
        await p.start_checkout(
            org_id="o", plan_key="pro", amount_eur=99.0, order_reference="r", customer_id="c"
        )


# --- pure event mapping ----------------------------------------------------


def test_reduce_checkout_completed():
    ev = reduce_stripe_event(
        {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_1",
                    "subscription": "sub_1",
                    "metadata": {"plan_key": "pro"},
                }
            },
        }
    )
    assert ev.customer_id == "cus_1" and ev.subscription_id == "sub_1"
    assert ev.plan_key == "pro" and ev.status == "active"


def test_reduce_subscription_past_due_is_suspended():
    ev = reduce_stripe_event(
        {
            "id": "evt_2",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_1",
                    "customer": "cus_1",
                    "status": "past_due",
                    "metadata": {"plan_key": "pro"},
                }
            },
        }
    )
    assert ev.status == "suspended" and ev.plan_key == "pro"


def test_reduce_subscription_deleted_is_canceled():
    ev = reduce_stripe_event(
        {
            "id": "evt_3",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_1", "customer": "cus_1", "status": "canceled"}},
        }
    )
    assert ev.status == "canceled"


def test_reduce_unknown_event_is_noop():
    ev = reduce_stripe_event({"id": "evt_x", "type": "invoice.paid", "data": {"object": {}}})
    assert ev.plan_key is None and ev.status is None


# --- entitlement application + idempotency ---------------------------------


async def _org(db_session) -> Organization:
    org = await db_session.scalar(select(Organization))
    org.stripe_customer_id = "cus_fake123"
    await db_session.commit()
    return org


@pytest.mark.asyncio
async def test_apply_sets_plan_and_subscription(auth_client, db_session):
    org = await _org(db_session)
    ev = SubscriptionEvent(
        "evt_a", "checkout.session.completed", "cus_fake123", "sub_9", "pro", "active"
    )

    changed = await billing_svc.apply_subscription_event(db_session, ev)
    assert changed is True

    await db_session.refresh(org)
    assert org.plan == "pro"
    assert org.status == "active"
    assert org.stripe_subscription_id == "sub_9"


@pytest.mark.asyncio
async def test_apply_is_idempotent_on_event_id(auth_client, db_session):
    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_dup", "checkout.session.completed", "cus_fake123", "sub_9", "pro", "active"
    )

    assert await billing_svc.apply_subscription_event(db_session, ev) is True
    # Same event id redelivered → skipped, no second effect.
    assert await billing_svc.apply_subscription_event(db_session, ev) is False


@pytest.mark.asyncio
async def test_apply_unknown_customer_is_noop(auth_client, db_session):
    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_u", "customer.subscription.updated", "cus_STRANGER", "sub_1", "pro", "active"
    )
    assert await billing_svc.apply_subscription_event(db_session, ev) is False


@pytest.mark.asyncio
async def test_downgrade_disables_addon_modules(auth_client, db_session):
    org = await _org(db_session)
    # Put the tenant on pro with the paid 'issuing' add-on enabled.
    org.plan = "pro"
    await db_session.commit()
    await modules_svc.set_enabled(db_session, org.id, "issuing", True)
    await db_session.commit()
    assert "issuing" in await modules_svc.enabled_keys(db_session, org.id)

    # Stripe reports a switch to 'starter' (no issuing) → module reconciled off.
    ev = SubscriptionEvent(
        "evt_dg", "customer.subscription.updated", "cus_fake123", "sub_9", "starter", "active"
    )
    assert await billing_svc.apply_subscription_event(db_session, ev) is True

    await db_session.refresh(org)
    assert org.plan == "starter"
    assert "issuing" not in await modules_svc.enabled_keys(db_session, org.id)


@pytest.mark.asyncio
async def test_cancel_returns_to_default_plan(auth_client, db_session):
    org = await _org(db_session)
    org.plan = "pro"
    await db_session.commit()

    ev = SubscriptionEvent(
        "evt_c", "customer.subscription.deleted", "cus_fake123", "sub_9", None, "canceled"
    )
    assert await billing_svc.apply_subscription_event(db_session, ev) is True

    await db_session.refresh(org)
    assert org.plan == billing_svc.plans.DEFAULT_PLAN
    assert org.status == "canceled"


# --- webhook route ---------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_applies_verified_event(auth_client, db_session):
    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_wh", "checkout.session.completed", "cus_fake123", "sub_wh", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))

    r = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert r.status_code == 200
    assert r.json() == {"received": True, "applied": True}

    org = await db_session.scalar(select(Organization))
    await db_session.refresh(org)
    assert org.plan == "pro"


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature(auth_client):
    set_billing_provider(FakeProvider(None))  # parse_webhook raises → 400
    r = await auth_client.post("/api/v1/billing/webhook", content=b"{}")
    assert r.status_code == 400


# --- checkout / portal gating ----------------------------------------------


@pytest.mark.asyncio
async def test_checkout_503_when_billing_unconfigured(auth_client):
    set_billing_provider(None)  # NullProvider; settings.billing_enabled is False
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_checkout_returns_url_when_enabled(auth_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")  # billing_enabled True
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.test/pro"


@pytest.mark.asyncio
async def test_checkout_rejects_free_plan(auth_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "trial"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_portal_requires_existing_customer(auth_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/portal")
    assert r.status_code == 400  # no stripe_customer_id yet


@pytest.mark.asyncio
async def test_paid_plan_switch_blocked_when_billing_enabled(auth_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    set_billing_provider(FakeProvider())
    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "pro"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_checkout_requires_admin(auth_client, db_session, monkeypatch):
    from app.core.config import settings
    from app.models.user import User, UserRole

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    set_billing_provider(FakeProvider())
    user = await db_session.scalar(select(User))
    user.role = UserRole.user
    await db_session.commit()
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 403
