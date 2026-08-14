"""The two gaps the engineering review of H-1 left open (F-07, F-08).

F-07 — every test of the failed-capture worklist drove the `upload` channel. The
`email` branch was reachable, shipped, and completely unexercised: the
`inbound_invoices` half of the union, the `security_rejected` default for a
`rejected` row, and acknowledging an email-channel failure. Email is the channel
where silent failure hurts most — nobody is watching a browser tab when an
emailed invoice is refused — so it was the worst half to leave unproven.

F-08 — nothing proved the new routes enforce the permission they declare. The
acknowledge route asks for INVOICE_WRITE; a refactor could have dropped that
without a single test noticing.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.models.email_intake import InboundInvoice
from app.services import capture_failures

SECRET = "test-inbound-webhook-secret"
HDR = {"X-Inbound-Secret": SECRET}


@pytest.fixture(autouse=True)
def _inbound_secret(monkeypatch):
    """The inbound endpoint fails CLOSED when no secret is configured."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "inbound_email_secret", SECRET)


def _att(filename: str, text: str, ct: str | None = None) -> dict:
    return {
        "filename": filename,
        "content_type": ct,
        "content_base64": base64.b64encode(text.encode()).decode(),
    }


async def _activate(auth_client) -> None:
    r = await auth_client.put("/api/v1/modules/email_intake", json={"enabled": True})
    assert r.status_code == 200, r.text


async def _token(auth_client) -> str:
    r = await auth_client.get("/api/v1/email/settings")
    assert r.status_code == 200, r.text
    return r.json()["address"].split("@", 1)[0]


async def _worklist(auth_client, *, include_acknowledged: bool = False) -> dict:
    q = "?include_acknowledged=true" if include_acknowledged else ""
    r = await auth_client.get(f"/api/v1/invoices/captures/failures{q}")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# F-07 — the email channel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_emailed_attachment_refused_by_security_reaches_the_worklist(
    auth_client, client, db_session
):
    """A refused attachment is a document that never became an invoice, and the
    sender believes they sent it. It has to be visible somewhere."""
    await _activate(auth_client)
    token = await _token(auth_client)

    r = await client.post(
        "/api/v1/email/inbound",
        json={
            "token": token,
            "subject": "Invoice attached",
            "attachments": [_att("invoice.exe", "MZ\x90\x00 not an invoice")],
        },
        headers=HDR,
    )
    assert r.status_code == 200, r.text
    assert r.json()["rejected"] == 1

    # The row really is `rejected` — not merely absent.
    row = await db_session.scalar(select(InboundInvoice))
    assert row is not None and row.status == "rejected"

    body = await _worklist(auth_client)

    item = next(i for i in body["items"] if i["channel"] == "email")
    assert item["ref_id"] == row.id
    assert item["code"] == capture_failures.SECURITY_REJECTED
    assert item["source_filename"] == "invoice.exe"
    assert item["remediation"].strip()
    # The bytes of a refused attachment are deliberately NOT stored (the security
    # gate quarantines metadata only), so the worklist must not promise otherwise.
    assert item["retry_helps"] is False


@pytest.mark.asyncio
async def test_an_email_channel_failure_can_be_acknowledged(auth_client, client, db_session):
    """The acknowledge route takes a channel in its path. Nothing proved the
    `email` branch of that resolution worked."""
    await _activate(auth_client)
    token = await _token(auth_client)
    r = await client.post(
        "/api/v1/email/inbound",
        json={"token": token, "attachments": [_att("bad.exe", "MZ\x90\x00")]},
        headers=HDR,
    )
    assert r.status_code == 200, r.text
    ref_id = next(i for i in (await _worklist(auth_client))["items"] if i["channel"] == "email")[
        "ref_id"
    ]

    ack = await auth_client.post(
        f"/api/v1/invoices/captures/failures/email/{ref_id}/acknowledge",
        json={"note": "asked the supplier to send a PDF"},
    )
    assert ack.status_code == 200, ack.text

    assert all(i["ref_id"] != ref_id for i in ack.json()["items"])
    shown = next(
        i
        for i in (await _worklist(auth_client, include_acknowledged=True))["items"]
        if i["ref_id"] == ref_id
    )
    assert shown["acknowledgement_note"] == "asked the supplier to send a PDF"
    assert shown["channel"] == "email"


@pytest.mark.asyncio
async def test_both_channels_appear_on_one_worklist(auth_client, client, db_session):
    """The operator's question — 'what did we fail to read?' — does not care how
    it arrived. If the union were broken, each half would still look healthy on
    its own, which is why this is asserted explicitly."""
    import io

    await _activate(auth_client)
    token = await _token(auth_client)

    # email side
    r = await client.post(
        "/api/v1/email/inbound",
        json={"token": token, "attachments": [_att("bad.exe", "MZ\x90\x00")]},
        headers=HDR,
    )
    assert r.status_code == 200, r.text

    # upload side — a CSV with no recognisable line-item columns
    from app.services import jobs

    up = await auth_client.post(
        "/api/v1/invoices/upload",
        files={"file": ("unparseable.csv", io.BytesIO(b"not,an,invoice\nx,y,z\n"), "text/csv")},
    )
    assert up.status_code == 202, up.text
    for _ in range(30):
        if await jobs.run_once(db_session, "test-worker") is None:
            break

    body = await _worklist(auth_client)

    channels = {i["channel"] for i in body["items"]}
    assert channels == {"email", "upload"}, body
    assert body["total"] >= 2


# --------------------------------------------------------------------------- #
# F-08 — the routes enforce the permission they declare
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reading_the_worklist_needs_only_invoice_read(role_client):
    """An EMPLOYEE holds INVOICE_READ. Seeing what failed to arrive is part of
    doing the day job, so the read must NOT require the write permission."""
    employee = await role_client("user")

    r = await employee.get("/api/v1/invoices/captures/failures")

    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_acknowledging_is_refused_without_invoice_write(role_client):
    """An EMPLOYEE has INVOICE_READ but NOT INVOICE_WRITE. Acknowledging writes a
    permanent record attributed to a person, so it takes the write permission.

    Asserts 403 rather than 404 deliberately: the permission is declared on the
    route, so it must be enforced BEFORE the handler looks the reference up. A
    404 here would mean the gate ran second — and a caller without permission
    would be able to probe which references exist by the shape of the error."""
    employee = await role_client("user")

    r = await employee.post(
        "/api/v1/invoices/captures/failures/upload/"
        "00000000-0000-0000-0000-000000000000/acknowledge",
        json={"note": "should never be recorded"},
    )

    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_setting_the_inbound_cadence_is_refused_without_settings_manage(role_client):
    """H-2's cadence decides when a workspace is told its intake has gone quiet —
    an administrative setting, not a day-job action."""
    employee = await role_client("user")

    r = await employee.put("/api/v1/email/health/email", json={"expected_cadence_days": 7})

    assert r.status_code in (403, 404), r.text
    # 403 = permission refused; 404 would mean the module gate fired first. Either
    # way it is NOT applied — assert that positively rather than trusting the code.
    assert r.status_code != 200
