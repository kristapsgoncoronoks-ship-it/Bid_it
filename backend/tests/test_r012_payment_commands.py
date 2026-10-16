"""R012 cash commands: replay, exact balance, corrections and atomic effects."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.issued_invoice import IssuedInvoice
from app.models.job import Job
from app.models.organization import Organization
from app.models.payment import Payment
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.services import audit, jobs
from tests.test_r011_invoice_commands import create

KEY = {"Idempotency-Key": "r012-payment-01"}


def cash(invoice, amount="20.00", **kwargs):
    return {
        "amount": amount,
        "currency": invoice["currency"],
        "expected_outstanding": invoice["outstanding"],
        **kwargs,
    }


async def endpoint(db_session):
    org_id = await db_session.scalar(select(Organization.id))
    db_session.add(
        WebhookEndpoint(
            org_id=org_id,
            url="https://synthetic.example.test/events",
            secret="synthetic-test-secret",
            events="issued.payment",
        )
    )
    await db_session.commit()


async def post(auth_client, invoice, body=None, key=KEY):
    return await auth_client.post(
        f"/api/v1/issued/{invoice['id']}/payments", json=body or cash(invoice), headers=key
    )


async def counts(db_session):
    return tuple(
        [
            await db_session.scalar(select(func.count()).select_from(model).where(condition))
            for model, condition in (
                (Payment, Payment.command_key.is_not(None)),
                (AuditEvent, AuditEvent.action == audit.A.ISSUED_PAYMENT),
                (WebhookDelivery, WebhookDelivery.event_type == "issued.payment"),
                (Job, Job.kind == "webhook.deliver"),
            )
        ]
    )


async def test_replay_lookup_and_secondary_effects_once(auth_client, db_session):
    invoice = await create(auth_client)
    await endpoint(db_session)
    first = await post(auth_client, invoice)
    assert first.status_code == 200, first.text
    again = await post(auth_client, invoice)
    assert again.status_code == 200, again.text
    assert again.json()["payment"]["id"] == first.json()["payment"]["id"]
    assert again.json()["invoice"]["amount_paid"] == "20.00"
    lookup = await auth_client.get(
        f"/api/v1/issued/{invoice['id']}/payment-commands/r012-payment-01"
    )
    assert lookup.status_code == 200 and lookup.json()["payment"] == first.json()["payment"]
    assert await counts(db_session) == (1, 1, 1, 1)
    changed = await post(auth_client, invoice, cash(invoice, "25"))
    assert changed.status_code == 409 and changed.json()["code"] == "command_payload_conflict"
    assert await counts(db_session) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    "body,code",
    [
        ({"amount": "0"}, 422),
        ({"amount": "-1"}, 422),
        ({"amount": "0.001"}, 422),
        ({"amount": "9999999999999"}, 422),
        ({"currency": "USD"}, 409),
        ({"expected_outstanding": "1"}, 409),
        ({"amount": "999"}, 409),
    ],
)
async def test_invalid_currency_stale_balance_and_amount_refused(
    auth_client, db_session, body, code
):
    invoice = await create(auth_client)
    response = await post(auth_client, invoice, cash(invoice, **body))
    assert response.status_code == code, response.text
    assert await counts(db_session) == (0, 0, 0, 0)


async def test_effective_remaining_balance_after_partial_credit(auth_client):
    invoice = await create(auth_client)
    credit = await auth_client.post(
        f"/api/v1/issued/{invoice['id']}/credit-note",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "lines": [
                {
                    "description": "Partial credit",
                    "quantity": "1",
                    "unit_price": "20",
                    "vat_rate": "21",
                }
            ],
        },
    )
    assert credit.status_code == 201, credit.text
    current = (await auth_client.get(f"/api/v1/issued/{invoice['id']}")).json()
    assert Decimal(current["outstanding"]) < Decimal(current["total"])
    paid = await post(auth_client, current, cash(current, current["outstanding"]))
    assert paid.status_code == 200 and paid.json()["invoice"]["status"] == "paid", paid.text


async def test_reasoned_reversal_replay_and_ledger_cache(auth_client, db_session):
    invoice = await create(auth_client)
    await endpoint(db_session)
    paid = (await post(auth_client, invoice)).json()
    url = f"/api/v1/issued/{invoice['id']}/payments/{paid['payment']['id']}/reverse"
    body = {
        "reason": "  Wrong receipt entered  ",
        "currency": "EUR",
        "expected_outstanding": paid["invoice"]["outstanding"],
    }
    blank = await auth_client.post(
        url, json={**body, "reason": "  "}, headers={"Idempotency-Key": "reverse-blank"}
    )
    assert blank.status_code == 422
    first = await auth_client.post(url, json=body, headers={"Idempotency-Key": "reverse-01"})
    assert first.status_code == 200, first.text
    reversal = first.json()["payment"]
    assert reversal["amount"] == "-20.00" and reversal["reversal_of_id"] == paid["payment"]["id"]
    assert reversal["note"] == "Wrong receipt entered"
    again = await auth_client.post(url, json=body, headers={"Idempotency-Key": "reverse-01"})
    assert again.status_code == 200 and again.json()["payment"]["id"] == reversal["id"]
    twice = await auth_client.post(
        url,
        json={**body, "expected_outstanding": invoice["outstanding"]},
        headers={"Idempotency-Key": "reverse-02"},
    )
    assert twice.status_code == 409 and twice.json()["code"] == "payment_already_reversed"
    history = (await auth_client.get(f"/api/v1/issued/{invoice['id']}/payments")).json()
    assert len(history) == 2 and sum(Decimal(p["amount"]) for p in history) == 0
    assert first.json()["invoice"]["amount_paid"] == "0.00"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == audit.A.ISSUED_PAYMENT_REVERSED)
        )
        == 1
    )
    assert await counts(db_session) == (2, 1, 2, 2)
    replay = await post(auth_client, invoice)
    assert replay.status_code == 200
    assert replay.json()["payment"]["id"] == paid["payment"]["id"]
    assert replay.json()["invoice"]["amount_paid"] == "0.00"
    assert await counts(db_session) == (2, 1, 2, 2)


async def test_legacy_replay_is_noop_and_decrease_requires_correction(auth_client, db_session):
    invoice = await create(auth_client)
    await endpoint(db_session)
    url = f"/api/v1/issued/{invoice['id']}/payment"
    for _ in range(2):
        response = await auth_client.patch(
            url, json={"amount_paid": "20", "paid_date": "2026-10-12"}
        )
        assert response.status_code == 200, response.text
    assert await counts(db_session) == (1, 1, 1, 1)
    refused = await auth_client.patch(url, json={"amount_paid": "10"})
    assert refused.status_code == 409 and refused.json()["code"] == "payment_reversal_required"
    noop = await auth_client.patch(url, json={"amount_paid": "20", "paid_date": "2026-10-13"})
    assert noop.status_code == 200 and noop.json()["paid_date"] == "2026-10-12"
    assert await counts(db_session) == (1, 1, 1, 1)


@pytest.mark.parametrize("failure", ["audit", "job"])
async def test_secondary_failure_rolls_back_cash(auth_client, db_session, monkeypatch, failure):
    invoice = await create(auth_client)
    await endpoint(db_session)
    if failure == "audit":

        async def fail(*args, **kwargs):
            return None

        monkeypatch.setattr(audit, "record", fail)
    else:

        async def fail(*args, **kwargs):
            raise RuntimeError("Synthetic job failure")

        monkeypatch.setattr(jobs, "enqueue", fail)
    with pytest.raises(RuntimeError):
        await post(auth_client, invoice)
    assert await counts(db_session) == (0, 0, 0, 0)
    current = await db_session.scalar(
        select(IssuedInvoice).where(IssuedInvoice.id == invoice["id"])
    )
    assert current.amount_paid == 0


async def test_scope_and_payment_permissions(auth_client, db_session, monkeypatch):
    invoice = await create(auth_client)
    stale = await post(auth_client, invoice, key={**KEY, "X-Expected-Org-ID": "changed"})
    assert stale.status_code == 409 and stale.json()["code"] == "identity_changed"
    from app.core import authz

    user = await db_session.scalar(select(User))
    # Prove the structural permission itself, independent of roles that happen
    # to hold both issuing.write and payment.write today.
    original = authz.permissions_for
    monkeypatch.setattr(
        authz, "permissions_for", lambda u: original(u) - {authz.Permission.PAYMENT_WRITE}
    )
    assert (await post(auth_client, invoice)).status_code == 403
    assert (
        await auth_client.patch(
            f"/api/v1/issued/{invoice['id']}/payment", json={"amount_paid": "20"}
        )
    ).status_code == 403
    assert user is not None


async def test_other_invoice_or_tenant_cannot_lookup_command(auth_client, client):
    invoice = await create(auth_client)
    assert (await post(auth_client, invoice)).status_code == 200
    other = await create(auth_client)
    assert (
        await auth_client.get(f"/api/v1/issued/{other['id']}/payment-commands/r012-payment-01")
    ).status_code == 404

    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "Other",
            "email": "other-r012@example.com",
            "password": "synthetic-test-password",
        },
    )
    client.headers["Authorization"] = "Bearer " + registered.json()["token"]["access_token"]
    assert (await client.put("/api/v1/modules/issuing", json={"enabled": True})).status_code == 200
    assert (
        await client.get(f"/api/v1/issued/{invoice['id']}/payment-commands/r012-payment-01")
    ).status_code == 404


async def test_receipt_linked_cash_stays_in_allocation_workflow(auth_client, db_session):
    from datetime import date

    from app.models.receipt import Receipt

    invoice = await create(auth_client)
    paid = (await post(auth_client, invoice)).json()
    entry = await db_session.get(Payment, paid["payment"]["id"])
    receipt = Receipt(org_id=entry.org_id, amount=Decimal("20"), received_on=date.today())
    db_session.add(receipt)
    await db_session.flush()
    entry.receipt_id = receipt.id  # Synthetic origin fixture, never a production edit.
    await db_session.commit()
    response = await auth_client.post(
        f"/api/v1/issued/{invoice['id']}/payments/{entry.id}/reverse",
        json={
            "reason": "Mistake",
            "currency": "EUR",
            "expected_outstanding": paid["invoice"]["outstanding"],
        },
        headers={"Idempotency-Key": "reverse-linked"},
    )
    assert response.status_code == 409 and response.json()["code"] == "payment_receipt_linked"


async def test_unlinked_historical_negative_needs_review(auth_client, db_session):
    from datetime import date

    invoice = await create(auth_client)
    paid = (await post(auth_client, invoice)).json()
    row = await db_session.get(IssuedInvoice, invoice["id"])
    db_session.add(
        Payment(
            org_id=row.org_id, issued_invoice_id=row.id, amount=Decimal("-1"), paid_on=date.today()
        )
    )
    db_session.add(
        Payment(
            org_id=row.org_id, issued_invoice_id=row.id, amount=Decimal("30"), paid_on=date.today()
        )
    )
    row.amount_paid = Decimal("49")
    await db_session.commit()
    current = (await auth_client.get(f"/api/v1/issued/{invoice['id']}")).json()
    response = await auth_client.post(
        f"/api/v1/issued/{invoice['id']}/payments/{paid['payment']['id']}/reverse",
        json={
            "reason": "Mistake",
            "currency": "EUR",
            "expected_outstanding": current["outstanding"],
        },
        headers={"Idempotency-Key": "reverse-historical"},
    )
    assert response.status_code == 409 and response.json()["code"] == "payment_history_review"


async def test_payment_read_permission_is_structural(auth_client, monkeypatch):
    from app.core import authz

    invoice = await create(auth_client)
    assert (await post(auth_client, invoice)).status_code == 200
    original = authz.permissions_for
    monkeypatch.setattr(
        authz, "permissions_for", lambda u: original(u) - {authz.Permission.PAYMENT_READ}
    )
    assert (await auth_client.get(f"/api/v1/issued/{invoice['id']}/payments")).status_code == 403
    assert (
        await auth_client.get(f"/api/v1/issued/{invoice['id']}/payment-commands/r012-payment-01")
    ).status_code == 403


async def test_receivable_refusal_and_method_reference_capture(auth_client):
    draft = await create(auth_client, draft=True)
    refused = await post(auth_client, draft)
    assert refused.status_code == 409 and refused.json()["code"] == "payment_not_receivable"
    invoice = await create(auth_client)
    response = await post(
        auth_client,
        invoice,
        cash(invoice, method="cash", reference="Cash register 12", paid_on="2026-10-12"),
    )
    assert response.status_code == 200, response.text
    entry = response.json()["payment"]
    assert (entry["method"], entry["reference"], entry["paid_on"]) == (
        "cash",
        "Cash register 12",
        "2026-10-12",
    )
    replay = await post(
        auth_client,
        invoice,
        cash(invoice, "20", method="cash", reference="Cash register 12", paid_on="2026-10-12"),
    )
    assert replay.status_code == 200 and replay.json()["payment"]["id"] == entry["id"]


async def test_reversal_foreign_key_prevents_other_tenant_origin(auth_client, client, db_session):
    from datetime import date

    from sqlalchemy.exc import IntegrityError

    invoice = await create(auth_client)
    original = (await post(auth_client, invoice)).json()["payment"]
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "FK Other",
            "name": "Other",
            "email": "fk-r012@example.com",
            "password": "synthetic-test-password",
        },
    )
    other_org = registration.json()["organization"]["id"]
    client.headers["Authorization"] = "Bearer " + registration.json()["token"]["access_token"]
    other_invoice = await create(client)
    db_session.add(
        Payment(
            org_id=other_org,
            issued_invoice_id=other_invoice["id"],
            amount=Decimal("-20"),
            paid_on=date.today(),
            reversal_of_id=original["id"],
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
