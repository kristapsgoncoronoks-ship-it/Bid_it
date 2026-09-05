"""WO-AD — billing go-live wiring: retention rides the ladder, and the two
go-live gaps the premise check found.

WHAT THESE TESTS ARE FOR
-------------------------
DECISIONS-NEEDED §1.B was decided 2026-09-05: longer archive retention is a PLAN
ATTRIBUTE (Business/Enterprise carry 7 years), not a standalone add-on. That
turns "buy the extension" into "upgrade", which the existing checkout already
handles — so the software this order adds is small, and every piece of it is a
promise that has to hold in a specific direction:

1. `retention_years` is a MAX of the included floor, the plan and any staff
   override. Never a min: a misconfigured plan or a cleared override must not
   quietly shorten what a client was told.
2. A plan change RE-STAMPS existing archived rows, extend-only, on every path
   a plan can change through — the Stripe webhook, the EveryPay settle, and the
   in-app switch. An upgrade that protected only invoices deleted afterwards
   would be worthless at the moment it is bought (right after a pre-expiry
   notice about records already archived).
3. A downgrade re-stamps NOTHING. Rows keep the longer expiry they were
   promised.
4. The SPA must never offer a checkout that can only fail: a PRICED plan whose
   provider price id is missing reports `purchasable: false`. This is the gap
   that made Business — chosen for the ladder 2026-08-15 — a 502 button.
5. Stripe Tax is a flag the owner flips, not a side effect of a secret.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.archived_invoice import ArchivedInvoice
from app.models.organization import Organization
from app.services import archive, mailer, plans
from app.services import billing as billing_svc
from app.services.billing_provider import (
    CheckoutSession,
    StripeProvider,
    SubscriptionEvent,
    set_billing_provider,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _acme(db_session) -> Organization:
    org = await db_session.scalar(select(Organization).where(Organization.name == "Acme"))
    assert org is not None
    return org


def _row(org_id: str, number: str) -> ArchivedInvoice:
    """Archived 400 days ago under the 3-year floor: expires ~2 years from now."""
    archived = NOW - timedelta(days=400)
    return ArchivedInvoice(
        org_id=org_id,
        original_invoice_id=f"00000000-0000-0000-0000-{abs(hash(number)) % 10**12:012d}",
        invoice_number=number,
        vendor_name="Fictional Fuels OU",
        currency="EUR",
        line_items_json="[]",
        archived_at=archived,
        expires_at=archived + timedelta(days=365 * 3),
        expiry_notified_at=NOW - timedelta(days=1),
    )


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_billing_provider(None)


# --------------------------------------------------------------------------- #
# 1. retention_years is a MAX of three
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ad_retention_is_the_max_of_floor_plan_and_override(auth_client, db_session):
    org = await _acme(db_session)

    org.plan = "free"
    org.archive_retention_years = None
    await db_session.flush()
    assert await archive.retention_years(db_session, org.id) == archive.INCLUDED_RETENTION_YEARS

    org.plan = "business"
    await db_session.flush()
    assert await archive.retention_years(db_session, org.id) == 7

    # A staff override BELOW the plan is ignored — the plan's promise wins.
    org.archive_retention_years = 5
    await db_session.flush()
    assert await archive.retention_years(db_session, org.id) == 7

    # A staff override ABOVE the plan still wins — grants remain possible.
    org.plan = "free"
    org.archive_retention_years = 10
    await db_session.flush()
    assert await archive.retention_years(db_session, org.id) == 10


def test_wo_ad_the_ladder_carries_the_decision():
    """DECISIONS §1.B verbatim: Business and Enterprise carry 7; the rest keep
    the included 3. `practice` is deliberately at the floor — a partner plan's
    retention was not part of the decision and is not invented here."""
    assert plans.PLANS["business"].archive_retention_years == 7
    assert plans.PLANS["enterprise"].archive_retention_years == 7
    for key in ("trial", "free", "starter", "pro", "practice"):
        assert plans.PLANS[key].archive_retention_years == archive.INCLUDED_RETENTION_YEARS, key
    assert plans.longest_archive_retention_years() == 7


# --------------------------------------------------------------------------- #
# 2. A plan change re-stamps, extend-only, on every path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ad_a_stripe_upgrade_re_stamps_existing_rows(auth_client, db_session):
    """The webhook path. The row was archived under 3 years and already
    NOTICED; after the upgrade it is kept 7 years from its OWN archived_at and
    the notice stamp is cleared so a fresh notice precedes the new expiry."""
    org = await _acme(db_session)
    org.plan = "free"
    org.stripe_customer_id = "cus_fake123"
    row = _row(org.id, "LADDER-1")
    db_session.add(row)
    await db_session.commit()

    ev = SubscriptionEvent(
        "evt_up", "checkout.session.completed", "cus_fake123", "sub_1", "business", "active"
    )
    assert await billing_svc.apply_subscription_event(db_session, ev) is True
    await db_session.commit()

    await db_session.refresh(row)
    assert row.expires_at == row.archived_at + timedelta(days=365 * 7)
    assert row.expiry_notified_at is None


@pytest.mark.asyncio
async def test_wo_ad_an_in_app_switch_re_stamps_existing_rows(auth_client, db_session):
    """The `PUT /billing/plan` path (billing not connected → in-app switch)."""
    org = await _acme(db_session)
    org.plan = "free"
    row = _row(org.id, "LADDER-2")
    db_session.add(row)
    await db_session.commit()

    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "business"})
    assert r.status_code == 200, r.text

    await db_session.refresh(row)
    assert row.expires_at == row.archived_at + timedelta(days=365 * 7)


@pytest.mark.asyncio
async def test_wo_ad_a_downgrade_re_stamps_nothing(auth_client, db_session):
    """Direction matters. A row kept under a 7-year promise keeps it when the
    org drops back to a 3-year plan — lowering a setting must never reach
    backwards and destroy records already kept under a longer promise."""
    org = await _acme(db_session)
    org.plan = "business"
    row = _row(org.id, "LADDER-3")
    row.expires_at = row.archived_at + timedelta(days=365 * 7)
    db_session.add(row)
    await db_session.commit()

    r = await auth_client.put("/api/v1/billing/plan", json={"plan": "free"})
    assert r.status_code == 200, r.text

    await db_session.refresh(row)
    assert row.expires_at == row.archived_at + timedelta(days=365 * 7)


@pytest.mark.asyncio
async def test_wo_ad_clearing_a_staff_override_keeps_the_plan_promise(auth_client, db_session):
    """Before WO-AD, `apply_retention_override(None)` re-stamped nothing because
    the override was the ONLY source of a longer window. Now the plan is one
    too: clearing the override on a Business org extends rows to the plan's 7,
    never shortens them."""
    org = await _acme(db_session)
    org.plan = "business"
    org.archive_retention_years = 10
    row = _row(org.id, "LADDER-4")
    db_session.add(row)
    await db_session.commit()

    result = await archive.apply_retention_override(db_session, org.id, None)
    await db_session.commit()

    assert result["effective_years"] == 7
    assert result["rows_extended"] == 1
    await db_session.refresh(row)
    assert row.expires_at == row.archived_at + timedelta(days=365 * 7)


# --------------------------------------------------------------------------- #
# 3. The SPA must never be offered a checkout that can only fail
# --------------------------------------------------------------------------- #


def test_wo_ad_purchasable_matrix(monkeypatch):
    # No provider: every listed price is an in-app switch.
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "everypay_api_username", None)
    assert settings.plan_purchasable("business", 249) is True
    assert settings.plan_purchasable("free", 0) is True

    # Stripe active: a priced plan needs its price id; free plans never do.
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro")
    monkeypatch.setattr(settings, "stripe_price_business", None)
    assert settings.plan_purchasable("pro", 99) is True
    assert settings.plan_purchasable("business", 249) is False
    assert settings.plan_purchasable("free", 0) is True
    monkeypatch.setattr(settings, "stripe_price_business", "price_business")
    assert settings.plan_purchasable("business", 249) is True

    # EveryPay is amount-based: any listed price is sellable.
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "everypay_api_username", "u")
    monkeypatch.setattr(settings, "everypay_api_secret", "s")
    monkeypatch.setattr(settings, "everypay_account_name", "EUR3D1")
    assert settings.plan_purchasable("business", 249) is True


@pytest.mark.asyncio
async def test_wo_ad_billing_read_reports_purchasable_and_retention(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro")
    monkeypatch.setattr(settings, "stripe_price_business", None)

    r = await auth_client.get("/api/v1/billing")
    assert r.status_code == 200, r.text
    by_key = {p["key"]: p for p in r.json()["available_plans"]}
    assert by_key["pro"]["purchasable"] is True
    assert by_key["business"]["purchasable"] is False
    assert by_key["free"]["purchasable"] is True
    assert by_key["business"]["archive_retention_years"] == 7
    assert by_key["starter"]["archive_retention_years"] == 3


@pytest.mark.asyncio
async def test_wo_ad_the_archive_screen_is_told_what_an_upgrade_buys(auth_client):
    r = await auth_client.get("/api/v1/archive")
    assert r.status_code == 200, r.text
    assert r.json()["longest_plan_retention_years"] == 7


# --------------------------------------------------------------------------- #
# 4. Stripe Tax is a flag, and the notice names the upgrade
# --------------------------------------------------------------------------- #


class _Capture:
    """A stand-in for the `stripe` module: records the kwargs Checkout got."""

    def __init__(self):
        self.kwargs: dict | None = None
        outer = self

        class _Session:
            @staticmethod
            def create(**kwargs):
                outer.kwargs = kwargs
                return type("S", (), {"url": "https://checkout.test/x", "id": "cs_x"})()

        class _Checkout:
            Session = _Session

        self.checkout = _Checkout()


@pytest.mark.asyncio
async def test_wo_ad_stripe_tax_is_requested_only_when_the_owner_flips_it(monkeypatch):
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro")
    provider = object.__new__(StripeProvider)
    capture = _Capture()
    provider._stripe = capture

    monkeypatch.setattr(settings, "stripe_automatic_tax", False)
    out = await provider.start_checkout(
        org_id="o", plan_key="pro", amount_eur=99.0, order_reference="ref", customer_id="cus"
    )
    assert isinstance(out, CheckoutSession)
    assert capture.kwargs is not None and "automatic_tax" not in capture.kwargs

    monkeypatch.setattr(settings, "stripe_automatic_tax", True)
    await provider.start_checkout(
        org_id="o", plan_key="pro", amount_eur=99.0, order_reference="ref", customer_id="cus"
    )
    assert capture.kwargs is not None
    assert capture.kwargs["automatic_tax"] == {"enabled": True}


def test_wo_ad_the_expiry_notice_names_the_upgrade_not_a_conversation():
    _subject, body = mailer.archive_expiry_email(
        count=2, earliest=NOW, notice_days=60, retention_years=3, examples=[("INV-1", "Acme")]
    )
    assert "Plan & billing" in body
    assert "ask us" not in body
    # The promise that makes the upgrade worth buying after a notice: it reaches
    # records already archived, not only new ones.
    assert "these records too" in body
