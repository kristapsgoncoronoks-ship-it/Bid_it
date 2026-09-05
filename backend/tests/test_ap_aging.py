"""AP aging worklist + due-date digest (Phase 16b): open payables are bucketed
into due-soon / overdue bands (thresholds crossed via an explicit `today`), the
daily digest emails the issuer only when something is due and an email is set, and
the worklist route is REPORT_READ-gated."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import ap_aging, ap_alerts

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "ap@invoiceiq.test",
}


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _org_id(db_session):
    return await db_session.scalar(select(Organization.id))


async def _scheduled_invoice(auth_client, approver, number, due):
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": number,
            "issue_date": "2025-12-01",
            "due_date": due,
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
            ],
        },
    )
    iid = r.json()["id"]
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver),
        json={"version": sub.json()["version"]},
    )
    await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
    )
    return iid


@pytest.mark.asyncio
async def test_worklist_buckets_due_soon_and_overdue(auth_client, client, db_session):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await _scheduled_invoice(auth_client, approver, "INV-OD", "2026-01-01")  # overdue
    await _scheduled_invoice(auth_client, approver, "INV-SOON", "2026-01-20")  # due soon
    org = await _org_id(db_session)
    db_session.expire_all()

    items = await ap_aging.worklist(db_session, org, today=date(2026, 1, 15))
    by_num = {i.invoice_number: i for i in items}
    assert by_num["INV-OD"].status == "overdue" and by_num["INV-OD"].bucket == "1-30"
    assert by_num["INV-SOON"].bucket == "due_soon" and by_num["INV-SOON"].status == "open"

    s = ap_aging.summarize(items)
    assert s.overdue_count == 1 and s.overdue_amount == 100
    assert s.due_soon_count == 1 and s.due_soon_amount == 100


@pytest.mark.asyncio
async def test_digest_skips_without_issuer_then_sends(auth_client, client, db_session):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await _scheduled_invoice(auth_client, approver, "INV-OD", "2026-01-01")
    org = await _org_id(db_session)
    db_session.expire_all()

    # No issuer profile yet → nothing to email to.
    r = await ap_alerts.send_digest(db_session, org, today=date(2026, 2, 1))
    assert r["sent"] == 0 and r.get("skipped_no_email") == 1 and r["overdue"] == 1

    # Set the issuer (with an email) → the digest sends.
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    db_session.expire_all()
    r2 = await ap_alerts.send_digest(db_session, org, today=date(2026, 2, 1))
    await db_session.commit()
    assert r2["sent"] == 1 and r2["overdue"] == 1


@pytest.mark.asyncio
async def test_digest_noop_when_nothing_due(auth_client, client, db_session):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await _scheduled_invoice(auth_client, approver, "INV-FUT", "2026-06-01")
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    org = await _org_id(db_session)
    db_session.expire_all()
    # Long before the due date: not overdue, not due-soon → no email.
    r = await ap_alerts.send_digest(db_session, org, today=date(2026, 1, 1))
    assert r["sent"] == 0


@pytest.mark.asyncio
async def test_ap_aging_route_and_authz(auth_client, client):
    # Owner can read the worklist.
    ok = await auth_client.get("/api/v1/analytics/ap-aging")
    assert ok.status_code == 200 and "items" in ok.json()
    # EMPLOYEE (role 'user') has no REPORT_READ → 403.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (await client.get("/api/v1/analytics/ap-aging", headers=_h(emp))).status_code == 403


@pytest.mark.asyncio
async def test_perf005_the_worklist_route_lists_the_soonest_due_and_counts_the_rest(
    auth_client, client, db_session, monkeypatch
):
    """PERF-005/010 (audit 2026-09-05): the route returned every open payable,
    unbounded. It now lists the soonest-due `WORKLIST_LIMIT` rows and says how
    many there are — and the summary counts span ALL of them, so a truncated
    list never undercounts what needs attention."""
    approver = await _member(auth_client, client, "approver@corp.io")
    for i, days in enumerate((-20, -10, -3, 2, 5)):  # three overdue, two due soon
        await _scheduled_invoice(
            auth_client, approver, f"WL-{i}", (date(2026, 9, 5) + timedelta(days=days)).isoformat()
        )
    monkeypatch.setattr(ap_aging, "WORKLIST_LIMIT", 3)
    monkeypatch.setattr(ap_aging, "date", _FixedDate)  # today = 2026-09-05 inside the service

    r = await auth_client.get("/api/v1/analytics/ap-aging")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items_total"] == 5 and body["items_limit"] == 3 and body["truncated"] is True
    assert len(body["items"]) == 3
    # Soonest-due first: the three most overdue.
    assert [it["invoice_number"] for it in body["items"]] == ["WL-0", "WL-1", "WL-2"]
    # The summary covers all five, not the three listed.
    assert body["overdue_count"] == 3 and body["due_soon_count"] == 2


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 5)
