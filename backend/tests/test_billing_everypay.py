"""EveryPay billing (ADR-0013): a redirect-verify card gateway alongside Stripe.

No network / SDK — a Fake redirect provider stands in, and the pure status
mapping is tested directly. Covers: provider selection, the start-checkout →
persist-payment path, server-side confirm (idempotent), the return/callback
routes, and recurring MIT charges.
"""

import pytest
from sqlalchemy import select

from app.models.billing_payment import BillingPayment
from app.models.organization import Organization
from app.services import billing as billing_svc
from app.services import scheduler
from app.services.billing_provider import (
    CheckoutSession,
    NullProvider,
    PaymentStatus,
    _everypay_status,
    get_billing_provider,
    set_billing_provider,
)


class FakeEveryPay:
    """Stand-in redirect (EveryPay-like) provider."""

    kind = "redirect"
    name = "everypay"
    enabled = True

    def __init__(self, *, verify=PaymentStatus("settled", "tok_123"), mit=PaymentStatus("settled")):
        self._verify = verify
        self._mit = mit
        self.started: list[str] = []

    async def ensure_customer(self, *, org_id, name, email):
        return org_id

    async def start_checkout(self, *, org_id, plan_key, amount_eur, order_reference, customer_id):
        self.started.append(order_reference)
        return CheckoutSession(
            url=f"https://igw-demo.every-pay.com/lp/{plan_key}", reference=f"ep_{order_reference}"
        )

    async def create_portal_url(self, *, customer_id):
        raise AssertionError("no portal")

    async def verify_payment(self, *, reference, order_reference):
        return self._verify

    async def charge_mit(self, *, token, amount_eur, order_reference):
        return self._mit


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_billing_provider(None)


def _everypay_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "billing_provider", "everypay")
    monkeypatch.setattr(settings, "everypay_api_username", "u")
    monkeypatch.setattr(settings, "everypay_api_secret", "s")
    monkeypatch.setattr(settings, "everypay_account_name", "EUR3D1")
    return settings


# --- selection + pure mapping ----------------------------------------------


def test_provider_selects_everypay(monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(None)
    from app.services.billing_provider import EveryPayProvider

    assert isinstance(get_billing_provider(), EveryPayProvider)


def test_selection_prefers_stripe_when_both_set(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "billing_provider", "auto")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(settings, "everypay_api_username", "u")
    monkeypatch.setattr(settings, "everypay_api_secret", "s")
    monkeypatch.setattr(settings, "everypay_account_name", "EUR3D1")
    assert settings.active_billing_provider == "stripe"


def test_none_when_unconfigured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "billing_provider", "auto")
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "everypay_api_username", None)
    assert settings.active_billing_provider == "none"
    set_billing_provider(None)
    assert isinstance(get_billing_provider(), NullProvider)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("settled", "settled"),
        ("authorized", "settled"),
        ("failed", "failed"),
        ("abandoned", "failed"),
        ("voided", "failed"),
        ("initial", "pending"),
        ("waiting_for_sca", "pending"),
        ("something_new", "pending"),
    ],
)
def test_status_mapping(raw, expected):
    assert _everypay_status({"payment_state": raw}).state == expected


def test_status_reads_token():
    assert _everypay_status({"payment_state": "settled", "token": "tok_9"}).token == "tok_9"
    assert (
        _everypay_status({"payment_state": "settled", "cc_details": {"token": "tok_cc"}}).token
        == "tok_cc"
    )


# --- checkout persists a payment -------------------------------------------


@pytest.mark.asyncio
async def test_checkout_persists_billing_payment(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay())

    r = await auth_client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://igw-demo.every-pay.com/lp/pro")

    pay = await db_session.scalar(select(BillingPayment))
    assert pay is not None
    assert pay.provider == "everypay" and pay.plan_key == "pro" and pay.state == "initial"
    assert pay.reference.startswith("ep_")


@pytest.mark.asyncio
async def test_portal_unavailable_for_everypay(auth_client, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay())
    r = await auth_client.post("/api/v1/billing/portal")
    assert r.status_code == 400


# --- confirm (verify server-side) ------------------------------------------


async def _seed_payment(db_session, *, reference="ep_ref1", plan="pro", state="initial"):
    org = await db_session.scalar(select(Organization))
    db_session.add(
        BillingPayment(
            org_id=org.id,
            provider="everypay",
            reference=reference,
            order_reference="ord1",
            plan_key=plan,
            amount_eur=99.0,
            state=state,
        )
    )
    await db_session.commit()
    return org


@pytest.mark.asyncio
async def test_confirm_settles_and_applies_plan(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(verify=PaymentStatus("settled", "tok_abc")))
    org = await _seed_payment(db_session)

    assert await billing_svc.confirm_redirect_payment(db_session, "ep_ref1") is True

    await db_session.refresh(org)
    assert org.plan == "pro"
    assert org.status == "active"
    assert org.everypay_token == "tok_abc"
    assert org.everypay_next_charge is not None
    pay = await db_session.scalar(
        select(BillingPayment).where(BillingPayment.reference == "ep_ref1")
    )
    assert pay.state == "settled"


@pytest.mark.asyncio
async def test_confirm_is_idempotent(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(verify=PaymentStatus("settled", "tok_abc")))
    await _seed_payment(db_session)

    assert await billing_svc.confirm_redirect_payment(db_session, "ep_ref1") is True
    # Second call (callback after return) short-circuits on state == settled.
    assert await billing_svc.confirm_redirect_payment(db_session, "ep_ref1") is True


@pytest.mark.asyncio
async def test_confirm_failed_payment(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(verify=PaymentStatus("failed")))
    org = await _seed_payment(db_session)

    assert await billing_svc.confirm_redirect_payment(db_session, "ep_ref1") is False
    await db_session.refresh(org)
    assert org.plan == "trial"  # unchanged
    pay = await db_session.scalar(
        select(BillingPayment).where(BillingPayment.reference == "ep_ref1")
    )
    assert pay.state == "failed"


@pytest.mark.asyncio
async def test_confirm_unknown_reference(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay())
    assert await billing_svc.confirm_redirect_payment(db_session, "nope") is False


# --- return + callback routes ----------------------------------------------


@pytest.mark.asyncio
async def test_return_route_verifies_and_redirects(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(verify=PaymentStatus("settled", "tok_r")))
    org = await _seed_payment(db_session, reference="ep_ret")

    r = await auth_client.get(
        "/api/v1/billing/everypay/return?payment_reference=ep_ret", follow_redirects=False
    )
    assert r.status_code == 303
    assert "checkout=success" in r.headers["location"]
    await db_session.refresh(org)
    assert org.plan == "pro"


@pytest.mark.asyncio
async def test_callback_route_applies(auth_client, db_session, monkeypatch):
    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(verify=PaymentStatus("settled", "tok_c")))
    await _seed_payment(db_session, reference="ep_cb")

    r = await auth_client.post("/api/v1/billing/everypay/callback?payment_reference=ep_cb")
    assert r.status_code == 200
    assert r.json() == {"received": True, "applied": True}


# --- recurring MIT ---------------------------------------------------------


@pytest.mark.asyncio
async def test_charge_renewal_settles_and_advances(auth_client, db_session, monkeypatch):
    from datetime import date

    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(mit=PaymentStatus("settled")))
    org = await db_session.scalar(select(Organization))
    org.plan = "pro"
    org.everypay_token = "tok_live"
    org.everypay_next_charge = date(2026, 7, 1)
    await db_session.commit()

    res = await billing_svc.charge_renewal(db_session, org.id, today=date(2026, 7, 1))
    assert res["charged"] is True
    await db_session.refresh(org)
    assert org.everypay_next_charge == date(2026, 8, 1)  # advanced one month
    assert org.status == "active"


@pytest.mark.asyncio
async def test_charge_renewal_declined_suspends(auth_client, db_session, monkeypatch):
    from datetime import date

    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay(mit=PaymentStatus("failed")))
    org = await db_session.scalar(select(Organization))
    org.plan = "pro"
    org.everypay_token = "tok_live"
    org.everypay_next_charge = date(2026, 7, 1)
    await db_session.commit()

    res = await billing_svc.charge_renewal(db_session, org.id, today=date(2026, 7, 1))
    assert res["charged"] is False
    await db_session.refresh(org)
    assert org.status == "suspended"
    assert org.everypay_next_charge == date(2026, 7, 1)  # NOT advanced


@pytest.mark.asyncio
async def test_scheduler_enqueues_due_renewals(auth_client, db_session, monkeypatch):
    from datetime import date

    from app.models.job import Job

    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay())
    org = await db_session.scalar(select(Organization))
    org.plan = "pro"
    org.everypay_token = "tok_live"
    org.everypay_next_charge = date(2026, 7, 1)
    await db_session.commit()

    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 5))
    kinds = list(await db_session.scalars(select(Job.kind)))
    assert "everypay.charge_mit" in kinds


@pytest.mark.asyncio
async def test_charge_renewal_skips_non_paid_plan(auth_client, db_session, monkeypatch):
    from datetime import date

    _everypay_settings(monkeypatch)
    set_billing_provider(FakeEveryPay())
    org = await db_session.scalar(select(Organization))
    org.plan = "trial"  # price_eur == 0
    org.everypay_token = "tok_live"
    org.everypay_next_charge = date(2026, 7, 1)
    await db_session.commit()

    res = await billing_svc.charge_renewal(db_session, org.id, today=date(2026, 7, 1))
    assert res == {"charged": False, "reason": "not-paid-plan"}


@pytest.mark.asyncio
async def test_be005_a_charge_that_raises_after_settling_is_not_repeated(
    auth_client, db_session, monkeypatch
):
    """BE-005 (audit 2026-09-05): the dedupe row must be durable BEFORE the
    provider is called. It used to be flushed only and committed after the
    charge — a timeout or worker death after EveryPay had accepted the charge
    rolled the claim back and the retry charged the card again."""
    from datetime import date

    _everypay_settings(monkeypatch)

    class DiesAfterCharging(FakeEveryPay):
        def __init__(self):
            super().__init__()
            self.charges = 0

        async def charge_mit(self, *, token, amount_eur, order_reference):
            self.charges += 1  # the provider HAS accepted the charge …
            raise TimeoutError("connection dropped after the charge settled")

    provider = DiesAfterCharging()
    set_billing_provider(provider)
    org = await db_session.scalar(select(Organization))
    org.plan = "pro"
    org.everypay_token = "tok_live"
    org.everypay_next_charge = date(2026, 7, 1)
    await db_session.commit()

    with pytest.raises(TimeoutError):
        await billing_svc.charge_renewal(db_session, org.id, today=date(2026, 7, 1))
    await db_session.rollback()
    assert provider.charges == 1

    # The retry the job runner would perform: same org, same day.
    res = await billing_svc.charge_renewal(db_session, org.id, today=date(2026, 7, 1))
    assert res == {"charged": False, "reason": "duplicate"}
    assert provider.charges == 1  # the card was charged ONCE
