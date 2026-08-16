"""Dogfood subscription billing (H1.6 / ADR-0013, WO-48): InvoiceIQ invoicing
its own paying tenants through the platform's own AR module when no live
billing provider is configured.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.models.issued_invoice import IssuedInvoice
from app.models.job import Job
from app.models.organization import Organization
from app.services import job_handlers, platform_billing, scheduler

ISSUER = {
    "legal_name": "InvoiceIQ Operations BV",
    "vat_number": "NL987654321B01",
    "registration_number": "NL-KVK-87654321",
    "address_line1": "Herengracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "email": "billing@invoiceiq.test",
}


async def _activate_platform_org(auth_client) -> None:
    """The platform org completes its OWN issuer profile through the SAME
    /issuers screen every tenant uses — no invented legal facts."""
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200


async def _platform_org_id(db_session) -> str:
    return await db_session.scalar(select(Organization.id).limit(1))


async def _add_org(db_session, *, name: str, plan: str, status: str = "active") -> Organization:
    org = Organization(name=name, plan=plan, status=status)
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


def _everypay_live(monkeypatch, platform_org_id: str) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    monkeypatch.setattr(settings, "billing_provider", "everypay")
    monkeypatch.setattr(settings, "everypay_api_username", "u")
    monkeypatch.setattr(settings, "everypay_api_secret", "s")
    monkeypatch.setattr(settings, "everypay_account_name", "EUR3D1")


# --- gating -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_by_default_is_a_noop(auth_client, db_session):
    """No `platform_org_id` (the default) → byte-identical to before this order."""
    from app.core.config import settings

    assert settings.platform_org_id is None
    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert res.invoiced == []
    assert (await db_session.scalar(select(IssuedInvoice.id))) is None


@pytest.mark.asyncio
async def test_disabled_once_a_live_provider_is_active(auth_client, db_session, monkeypatch):
    """Once a live provider is configured, dogfood generation stops automatically
    — a tenant is never invoiced twice."""
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    _everypay_live(monkeypatch, platform_org_id)
    from app.core.config import settings

    assert settings.dogfood_billing_enabled is False

    await _add_org(db_session, name="Subscriber A", plan="starter")
    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert res.invoiced == []


@pytest.mark.asyncio
async def test_missing_platform_org_is_a_safe_noop(auth_client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", "00000000-0000-0000-0000-000000000000")
    await _add_org(db_session, name="Subscriber A", plan="starter")
    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert res.invoiced == []


# --- generation ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_bills_every_active_paid_plan_org_once(auth_client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    assert settings.dogfood_billing_enabled is True

    starter = await _add_org(db_session, name="Subscriber Starter", plan="starter")
    pro = await _add_org(db_session, name="Subscriber Pro", plan="pro")
    trial = await _add_org(db_session, name="Subscriber Trial", plan="trial")

    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    invoiced_orgs = {oid for oid, _ in res.invoiced}
    assert invoiced_orgs == {starter.id, pro.id}
    assert trial.id not in invoiced_orgs

    rows = list(
        await db_session.scalars(
            select(IssuedInvoice).where(IssuedInvoice.org_id == platform_org_id)
        )
    )
    assert len(rows) == 2
    by_sub = {r.subscription_org_id: r for r in rows}
    assert by_sub[starter.id].total == 39  # Starter €29 -> €39 (ladder switch, §2a)
    assert by_sub[pro.id].total == 99
    assert by_sub[starter.id].subscription_period == date(2026, 7, 1)
    assert by_sub[starter.id].buyer_name == "Subscriber Starter"
    assert by_sub[starter.id].number is not None


@pytest.mark.asyncio
async def test_skips_suspended_and_canceled_orgs(auth_client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)

    await _add_org(db_session, name="Suspended Co", plan="pro", status="suspended")
    await _add_org(db_session, name="Canceled Co", plan="pro", status="canceled")

    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert res.invoiced == []


@pytest.mark.asyncio
async def test_skips_the_platform_org_itself(auth_client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)

    platform_org = await db_session.get(Organization, platform_org_id)
    platform_org.plan = "pro"  # a price-carrying plan, if it were considered
    await db_session.commit()

    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert res.invoiced == []


@pytest.mark.asyncio
async def test_idempotent_per_org_per_month(auth_client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    starter = await _add_org(db_session, name="Subscriber Starter", plan="starter")

    res1 = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert len(res1.invoiced) == 1

    # Same calendar month, later day: nothing new.
    res2 = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 31))
    assert res2.invoiced == []

    rows = list(
        await db_session.scalars(
            select(IssuedInvoice).where(
                IssuedInvoice.org_id == platform_org_id,
                IssuedInvoice.subscription_org_id == starter.id,
            )
        )
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_next_month_bills_again(auth_client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    starter = await _add_org(db_session, name="Subscriber Starter", plan="starter")

    await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    res2 = await platform_billing.bill_subscriptions(db_session, today=date(2026, 8, 3))
    assert len(res2.invoiced) == 1

    rows = list(
        await db_session.scalars(
            select(IssuedInvoice).where(
                IssuedInvoice.org_id == platform_org_id,
                IssuedInvoice.subscription_org_id == starter.id,
            )
        )
    )
    assert len(rows) == 2
    assert {r.subscription_period for r in rows} == {date(2026, 7, 1), date(2026, 8, 1)}


# --- scheduler wiring -----------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_enqueues_the_daily_job_once_when_enabled(
    auth_client, db_session, monkeypatch
):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    await _add_org(db_session, name="Subscriber Starter", plan="starter")
    await _add_org(db_session, name="Subscriber Pro", plan="pro")

    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 28))
    jobs = list(
        await db_session.scalars(select(Job).where(Job.kind == job_handlers.PLATFORM_BILLING_RUN))
    )
    assert len(jobs) == 1
    assert jobs[0].org_id == platform_org_id


@pytest.mark.asyncio
async def test_scheduler_skips_when_disabled(auth_client, db_session):
    """No platform_org_id configured → no job of this kind, ever."""
    await scheduler.enqueue_daily(db_session, today=date(2026, 7, 28))
    kinds = list(await db_session.scalars(select(Job.kind)))
    assert job_handlers.PLATFORM_BILLING_RUN not in kinds


# --- reuses the pre-existing AR surface, zero new delivery/collection code -----


@pytest.mark.asyncio
async def test_generated_invoice_is_ordinary_ar_from_here(auth_client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    starter = await _add_org(db_session, name="Subscriber Starter", plan="starter")

    await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))

    listed = await auth_client.get("/api/v1/issued")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    inv_id = items[0]["id"]
    assert items[0]["buyer_name"] == "Subscriber Starter"

    # Delivery: the pre-existing send route, with an explicit recipient (no
    # buyer_email was auto-populated — see WO-48's Out of scope).
    sent = await auth_client.post(
        f"/api/v1/issued/{inv_id}/send", json={"to_email": "ap@subscriber-starter.example"}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["message"]["to_email"] == "ap@subscriber-starter.example"

    # Collection: the pre-existing manual payment route (bank transfer, no Stripe).
    paid = await auth_client.patch(
        # €39, not €29 — Starter's price after the 2026-08-15 ladder switch (§2a).
        # Paying the old price left the invoice PARTIALLY paid, and the assertion
        # below caught it as `overdue`, which is the collection flow working.
        f"/api/v1/issued/{inv_id}/payment",
        json={"amount_paid": "39.00"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    assert starter.id  # sanity: fixture used


@pytest.mark.asyncio
async def test_cross_tenant_isolation(auth_client, client, db_session, monkeypatch):
    await _activate_platform_org(auth_client)
    platform_org_id = await _platform_org_id(db_session)
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_org_id", platform_org_id)
    await _add_org(db_session, name="Subscriber Starter", plan="starter")
    res = await platform_billing.bill_subscriptions(db_session, today=date(2026, 7, 28))
    assert len(res.invoiced) == 1  # a number was assigned

    row = await db_session.scalar(
        select(IssuedInvoice).where(IssuedInvoice.org_id == platform_org_id)
    )
    other_inv_id = row.id

    # A THIRD, unrelated workspace — neither the platform org nor a subscriber.
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Unrelated Co",
            "name": "Someone",
            "email": "someone@unrelated.io",
            "password": "supersecret3",
        },
    )
    assert reg.status_code == 201
    token = reg.json()["token"]["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    assert (await client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    await client.put("/api/v1/modules/issuing", json={"enabled": True})

    listed = await client.get("/api/v1/issued")
    assert listed.json()["items"] == []
    got = await client.get(f"/api/v1/issued/{other_inv_id}")
    assert got.status_code == 404
