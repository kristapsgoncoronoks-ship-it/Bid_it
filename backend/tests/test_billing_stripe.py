"""Stripe billing (Phase 3.9 / ADR-0013): the event→entitlement mapping, webhook
idempotency, and route gating — all without touching the real Stripe SDK.

The provider seam is injected with a Fake so no network / secret keys are needed;
the pure `reduce_stripe_event` mapping is tested directly.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.billing_payment import BillingPayment
from app.models.job import Job
from app.models.organization import Organization
from app.services import billing as billing_svc
from app.services import job_handlers, jobs
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
        # One session id per call, as Stripe would — the route persists it as
        # the in-flight Checkout marker (BE-022) and the column is unique.
        return CheckoutSession(
            url=f"https://checkout.test/{plan_key}", reference=f"cs_test_{uuid.uuid4().hex[:8]}"
        )

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
async def test_webhook_durably_queues_verified_event_then_worker_applies_it(
    auth_client, db_session
):
    """BILL-REL-001: 200 means "committed as a job", not "applied inline"."""
    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_wh", "checkout.session.completed", "cus_fake123", "sub_wh", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))

    r = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert r.status_code == 200
    assert r.json() == {"received": True, "queued": True, "created": True}

    org = await db_session.scalar(select(Organization))
    await db_session.refresh(org)
    assert org.plan != "pro"  # nothing applied on the request path
    queued = await db_session.scalar(select(Job))
    assert queued is not None
    assert queued.kind == job_handlers.STRIPE_SUBSCRIPTION_EVENT
    assert queued.idempotency_key == "evt_wh"
    assert queued.org_id == org.id

    ran = await jobs.run_once(
        db_session, "billing-test", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,)
    )
    assert ran is not None and ran.status == "succeeded"
    await db_session.refresh(org)
    assert org.plan == "pro"


@pytest.mark.asyncio
async def test_webhook_redelivery_dedupes_the_durable_job(auth_client, db_session):
    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_same", "checkout.session.completed", "cus_fake123", "sub_wh", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    first = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    second = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert first.json()["created"] is True
    assert second.status_code == 200
    assert second.json() == {"received": True, "queued": True, "created": False}
    rows = list(await db_session.scalars(select(Job)))
    assert len([j for j in rows if j.idempotency_key == "evt_same"]) == 1


@pytest.mark.asyncio
async def test_webhook_unknown_customer_is_a_harmless_200_without_a_job(auth_client, db_session):
    ev = SubscriptionEvent(
        "evt_unknown", "checkout.session.completed", "cus_nobody", "sub_x", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    r = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert r.status_code == 200
    assert r.json() == {"received": True, "queued": False, "created": False}
    assert await db_session.scalar(select(Job)) is None


@pytest.mark.asyncio
async def test_webhook_enqueue_failure_asks_stripe_to_retry(auth_client, db_session, monkeypatch):
    """No durable ownership → non-2xx, so the provider redelivers (never a 200
    that says "received" about an event we did not keep)."""
    from app.api.routes import billing as billing_route

    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_db_down", "checkout.session.completed", "cus_fake123", "sub_wh", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))

    async def fail_enqueue(*args, **kwargs):
        raise RuntimeError("simulated queue/database failure")

    monkeypatch.setattr(billing_route.jobs, "enqueue_with_outcome", fail_enqueue)
    r = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert r.status_code == 503
    assert await db_session.scalar(select(Job)) is None


@pytest.mark.asyncio
async def test_webhook_worker_failure_is_retryable(auth_client, db_session, monkeypatch):
    await _org(db_session)
    ev = SubscriptionEvent(
        "evt_retry", "checkout.session.completed", "cus_fake123", "sub_wh", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    r = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert r.status_code == 200

    original = billing_svc.apply_subscription_event

    async def transient_failure(db, event):
        raise RuntimeError("simulated transient database/business failure")

    monkeypatch.setattr(billing_svc, "apply_subscription_event", transient_failure)
    failed = await jobs.run_once(
        db_session, "billing-test", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,)
    )
    assert failed is not None
    assert failed.status == "queued" and failed.attempts == 1  # backoff, not lost

    monkeypatch.setattr(billing_svc, "apply_subscription_event", original)
    await jobs.retry(db_session, failed)
    succeeded = await jobs.run_once(
        db_session, "billing-test", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,)
    )
    assert succeeded is not None and succeeded.status == "succeeded"
    org = await db_session.scalar(select(Organization))
    await db_session.refresh(org)
    assert org.plan == "pro"


@pytest.mark.asyncio
async def test_queued_event_whose_customer_was_rebound_dead_letters_and_touches_nobody(
    auth_client, db_session
):
    """Between enqueue and run the Stripe customer id moved to another tenant
    (or was cleared): the job must not mutate the queued tenant on the strength
    of a stale binding, and no retry can cure it → dead-letter on attempt 1."""
    org = await _org(db_session)
    ev = SubscriptionEvent(
        "evt_rebound", "checkout.session.completed", "cus_fake123", "sub_wh", "pro", "active"
    )
    set_billing_provider(FakeProvider(ev))
    r = await auth_client.post(
        "/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
    )
    assert r.status_code == 200 and r.json()["queued"] is True

    org.stripe_customer_id = None  # the binding is gone
    await db_session.commit()
    job = await jobs.run_once(
        db_session, "billing-test", kinds=(job_handlers.STRIPE_SUBSCRIPTION_EVENT,)
    )
    assert job is not None
    assert job.status == "dead" and job.attempts == 1
    assert "no longer resolves" in (job.last_error or "")
    await db_session.refresh(org)
    assert org.plan != "pro"


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
    # WO-AD: a correctly configured deployment has a price id for the plan it
    # sells. Before this line existed the test passed only because FakeProvider
    # swallowed the missing price — real Stripe would have raised, and the
    # route would have answered 502.
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200
    assert r.json()["url"] == "https://checkout.test/pro"


@pytest.mark.asyncio
async def test_checkout_refuses_a_priced_plan_with_no_provider_price(auth_client, monkeypatch):
    """WO-AD: the go-live gap this order found. Business was on the ladder
    (§2a) but had no Stripe price-id slot, so the SPA offered a checkout that
    could only 502. A missing price is a CONFIGURATION gap, not a gateway
    fault: the route now says so as a 400 the screen can render, and never
    reaches the provider."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_business", None)
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "business"})
    assert r.status_code == 400, r.text
    assert "not yet available" in r.json()["detail"]


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
async def test_enterprise_self_upgrade_blocked_billing_disabled(auth_client, db_session):
    """R5(b): a None-priced ("contact us") plan must never be self-service, even
    with no billing provider wired at all — the default/most common deployment
    state. Before the fix this returned 200 (Python: `None` is falsy, so the old
    `billing_enabled and target.price_eur` guard never fired for Enterprise)."""
    org = await db_session.scalar(select(Organization))
    before = org.plan

    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "enterprise"})
    assert r.status_code == 409, r.text

    await db_session.refresh(org)
    assert org.plan == before


@pytest.mark.asyncio
async def test_enterprise_self_upgrade_blocked_billing_enabled(
    auth_client, db_session, monkeypatch
):
    """R5(b), the worse half: even in a fully-wired, live-Stripe deployment, the
    Enterprise self-upgrade must still be refused — billing being "on" must not
    accidentally make a custom-priced plan free."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    org = await db_session.scalar(select(Organization))
    before = org.plan

    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "enterprise"})
    assert r.status_code == 409, r.text

    await db_session.refresh(org)
    assert org.plan == before


@pytest.mark.asyncio
async def test_checkout_requires_billing_manage(auth_client, db_session, monkeypatch):
    # BILLING_MANAGE is owner-only (matrix-aligned) — even an administrator is
    # refused, and so is a plain user.
    from app.core.config import settings
    from app.models.user import User, UserRole

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    set_billing_provider(FakeProvider())
    user = await db_session.scalar(select(User))

    user.role = UserRole.admin
    await db_session.commit()
    assert (
        await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    ).status_code == 403

    user.role = UserRole.user
    await db_session.commit()
    assert (
        await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    ).status_code == 403


# --- BE-022: no second subscription through Checkout ------------------------


@pytest.mark.asyncio
async def test_checkout_refuses_when_the_workspace_already_holds_a_subscription(
    auth_client, db_session, monkeypatch
):
    """BE-022 (found by the R4 review): a customer who already holds
    `stripe_subscription_id` starting another Checkout would get a SECOND
    subscription at the provider — two monthly charges for one plan. Until R4
    the SPA's disabled button was the only guard, and it expired at 90 s. The
    route now refuses with a 409 that points at the Portal, before the
    provider is reached."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    org = await _org(db_session)
    org.stripe_subscription_id = "sub_live"
    await db_session.commit()

    class _NeverReached(FakeProvider):
        async def start_checkout(self, **kwargs):
            raise AssertionError("checkout must not reach the provider")

    set_billing_provider(_NeverReached())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 409, r.text
    assert "Manage billing" in r.json()["detail"]


@pytest.mark.asyncio
async def test_checkout_refuses_for_a_suspended_subscriber_too(
    auth_client, db_session, monkeypatch
):
    """A declined card sets the org suspended while the subscription still
    exists (past_due). The fix is the payment method in the Portal — a new
    Checkout would leave the past-due subscription in place beside a new one."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    org = await _org(db_session)
    org.stripe_subscription_id = "sub_past_due"
    org.status = "suspended"
    await db_session.commit()
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_checkout_stays_open_without_a_subscription_and_for_redirect_providers(
    auth_client, db_session, monkeypatch
):
    """The guard is about a subscription OBJECT. A workspace that never
    subscribed checks out as before; a redirect provider (EveryPay) has no
    subscription object — its recurring charge IS the stored token — so a
    workspace holding an EveryPay token still starts a hosted payment."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    org = await _org(db_session)
    assert org.stripe_subscription_id is None
    set_billing_provider(FakeProvider())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200, r.text

    class _Redirect(FakeProvider):
        kind = "redirect"
        name = "everypay"

        async def start_checkout(self, **kwargs):
            return CheckoutSession(url="https://pay.test/x", reference=None)

    org.everypay_token = None
    org.stripe_subscription_id = "sub_stale_from_a_previous_provider"
    await db_session.commit()
    set_billing_provider(_Redirect())
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200, r.text
    # …and the page's flag follows the ACTIVE provider's object, never the
    # stale id of a previous one (R6 review A4).
    assert (await auth_client.get("/api/v1/billing")).json()["has_subscription"] is False
    org.everypay_token = "tok_recurring"
    await db_session.commit()
    assert (await auth_client.get("/api/v1/billing")).json()["has_subscription"] is True
    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_subscriber_cannot_set_the_free_plan_in_app(auth_client, db_session, monkeypatch):
    """BE-023 (R6 review S1/P-1): `PUT /billing/plan {free}` for a Stripe
    subscriber dropped the entitlements at once while Stripe kept charging,
    and the next renewal webhook put the paid plan back. The only honest
    cancel is the Portal's; the in-app free switch answers 409 pointing there.
    A workspace WITHOUT a subscription still switches freely (the in-app path
    for the no-billing and trial cases)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    org = await _org(db_session)
    org.plan = "pro"
    org.stripe_subscription_id = "sub_live"
    await db_session.commit()
    set_billing_provider(FakeProvider())
    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "free"})
    assert r.status_code == 409, r.text
    assert "Manage billing" in r.json()["detail"]
    await db_session.refresh(org)
    assert org.plan == "pro"

    org.stripe_subscription_id = None
    await db_session.commit()
    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "free"})
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["key"] == "free"


@pytest.mark.asyncio
async def test_has_subscription_is_the_active_providers_object(
    auth_client, db_session, monkeypatch
):
    """R6 review A4: a Stripe deployment whose org still carries an EveryPay
    token from a provider switch was told it has a subscription, routed to a
    Portal it has no account at, and never offered Checkout."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    org = await _org(db_session)
    org.everypay_token = "tok_from_before_the_switch"
    await db_session.commit()
    set_billing_provider(FakeProvider())
    assert (await auth_client.get("/api/v1/billing")).json()["has_subscription"] is False
    org.stripe_subscription_id = "sub_live"
    await db_session.commit()
    assert (await auth_client.get("/api/v1/billing")).json()["has_subscription"] is True


@pytest.mark.asyncio
async def test_cancel_clears_the_subscription_id_so_a_reactivated_workspace_can_subscribe(
    auth_client, db_session
):
    """R6 review S2: the id is what the guards read. Kept after a cancel it
    would refuse a reactivated workspace's next Checkout forever; support
    still finds the customer by `stripe_customer_id`."""
    org = await _org(db_session)
    org.plan = "pro"
    org.stripe_subscription_id = "sub_9"
    await db_session.commit()
    ev = SubscriptionEvent(
        "evt_c2", "customer.subscription.deleted", "cus_fake123", "sub_9", None, "canceled"
    )
    assert await billing_svc.apply_subscription_event(db_session, ev) is True
    await db_session.refresh(org)
    assert org.stripe_subscription_id is None
    assert org.stripe_customer_id == "cus_fake123"
    assert org.plan == billing_svc.plans.DEFAULT_PLAN


# --- BE-022: the window before the webhook lands (R6 review A1) --------------


async def _checkout(auth_client, plan="pro"):
    return await auth_client.post("/api/v1/billing/checkout", json={"plan": plan})


@pytest.mark.asyncio
async def test_a_second_checkout_is_refused_while_the_first_is_in_flight(
    auth_client, db_session, monkeypatch
):
    """Between `POST /billing/checkout` and the worker applying
    `checkout.session.completed` the org holds no subscription id, so the
    steady-state guard cannot fire — a second tab, a second billing manager
    or the page's wait running out could open a SECOND subscription. Every
    started Checkout is persisted as a `BillingPayment` in state `initial`
    and another is refused while an unexpired one exists."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    monkeypatch.setattr(settings, "stripe_price_business", "price_business_test")
    org = await _org(db_session)
    set_billing_provider(FakeProvider())
    first = await _checkout(auth_client)
    assert first.status_code == 200, first.text
    rows = list(
        await db_session.scalars(select(BillingPayment).where(BillingPayment.org_id == org.id))
    )
    assert len(rows) == 1 and rows[0].state == "initial" and rows[0].provider == "stripe"
    assert rows[0].reference.startswith("cs_test_")

    second = await _checkout(auth_client, "business")
    assert second.status_code == 409, second.text
    assert "still open" in second.json()["detail"]


@pytest.mark.asyncio
async def test_the_providers_expired_event_reopens_checkout_and_completed_settles_the_marker(
    auth_client, db_session, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    org = await _org(db_session)
    set_billing_provider(FakeProvider())
    assert (await _checkout(auth_client)).status_code == 200
    marker = await db_session.scalar(select(BillingPayment).where(BillingPayment.org_id == org.id))
    assert marker.state == "initial"

    # The owner closed the tab; Stripe says the session expired → the marker
    # is abandoned and a new Checkout is accepted.
    expired = reduce_stripe_event(
        {
            "id": "evt_exp",
            "type": "checkout.session.expired",
            "data": {"object": {"id": marker.reference, "customer": "cus_fake123"}},
        }
    )
    assert expired.checkout_session_id == marker.reference
    assert await billing_svc.subscription_event_org_id(db_session, expired) == org.id
    await billing_svc.apply_subscription_event(db_session, expired)
    await db_session.refresh(marker)
    assert marker.state == "abandoned"
    assert (await _checkout(auth_client)).status_code == 200

    # The second one is paid: `completed` settles ITS marker and sets the
    # subscription id, and from here the steady-state guard refuses.
    second = await db_session.scalar(
        select(BillingPayment).where(
            BillingPayment.org_id == org.id, BillingPayment.state == "initial"
        )
    )
    completed = reduce_stripe_event(
        {
            "id": "evt_done",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": second.reference,
                    "customer": "cus_fake123",
                    "subscription": "sub_new",
                    "metadata": {"plan_key": "pro"},
                }
            },
        }
    )
    await billing_svc.apply_subscription_event(db_session, completed)
    await db_session.refresh(second)
    await db_session.refresh(org)
    assert second.state == "settled" and org.stripe_subscription_id == "sub_new"
    r = await _checkout(auth_client)
    assert r.status_code == 409 and "already has a subscription" in r.json()["detail"]


@pytest.mark.asyncio
async def test_an_abandoned_checkout_ages_out_without_any_webhook(
    auth_client, db_session, monkeypatch
):
    """No `expired` event (the webhook is not wired, or was lost): the marker
    ages out with the Stripe session's own expiry, so the lock never outlives
    the session it protects."""
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings
    from app.services.billing_provider import CHECKOUT_TTL_SECONDS

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_test")
    org = await _org(db_session)
    set_billing_provider(FakeProvider())
    assert (await _checkout(auth_client)).status_code == 200
    assert (await _checkout(auth_client)).status_code == 409
    marker = await db_session.scalar(select(BillingPayment).where(BillingPayment.org_id == org.id))
    marker.created_at = datetime.now(UTC) - timedelta(seconds=CHECKOUT_TTL_SECONDS + 1)
    await db_session.commit()
    assert (await _checkout(auth_client)).status_code == 200


def test_reduce_checkout_session_expired_carries_the_session_and_nothing_to_apply():
    ev = reduce_stripe_event(
        {
            "id": "evt_x",
            "type": "checkout.session.expired",
            "data": {"object": {"id": "cs_1", "customer": "cus_1"}},
        }
    )
    assert ev.checkout_session_id == "cs_1"
    assert ev.plan_key is None and ev.status is None and ev.subscription_id is None
