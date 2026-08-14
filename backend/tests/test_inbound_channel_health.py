"""An inbound channel must not be able to die while every screen stays green (H-2).

The failure mode is always the same shape: success is inferred from a DOCUMENT
COUNT, so "no invoices arrived today" and "nothing could get through today"
produce identical output, and the operator's natural heuristic — it's quiet, must
be a slow week — never fires.

These tests pin the four properties that make the health record worth keeping:

* the outcome of an ATTEMPT is recorded independently of whether documents came
  out of it;
* it is recorded pessimistically FIRST, so a crash mid-delivery cannot leave the
  channel looking healthy;
* a delivery whose attachments were all REFUSED is a healthy channel, not a
  broken one — those are H-1's subject;
* quiet is only called "broken" against a cadence someone actually stated.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.inbound_channel_health import InboundChannelHealth
from app.services import inbound_health

SECRET = "test-inbound-webhook-secret"
HDR = {"X-Inbound-Secret": SECRET}

CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Globex Ltd,INV-HEALTH-1,2026-06-01,Widgets,10,5.00,50.00,21\n"
)


@pytest.fixture(autouse=True)
def _inbound_secret(monkeypatch):
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


async def _deliver(client, token: str, attachments: list[dict]) -> object:
    return await client.post(
        "/api/v1/email/inbound",
        json={"token": token, "subject": "Invoice", "attachments": attachments},
        headers=HDR,
    )


async def _health(auth_client) -> dict:
    r = await auth_client.get("/api/v1/email/health")
    assert r.status_code == 200, r.text
    return {c["channel"]: c for c in r.json()}


@pytest.mark.asyncio
async def test_a_channel_never_used_says_so_rather_than_being_absent(auth_client):
    """A channel missing from the list is indistinguishable from a healthy one.
    Absence has to be stated."""
    await _activate(auth_client)

    email = (await _health(auth_client))["email"]

    assert email["state"] == "never_used"
    assert email["headline"]
    assert email["last_success_at"] is None


@pytest.mark.asyncio
async def test_a_successful_delivery_is_recorded_as_a_success(auth_client, client):
    await _activate(auth_client)
    token = await _token(auth_client)

    r = await _deliver(client, token, [_att("inv.csv", CSV, "text/csv")])
    assert r.status_code == 200, r.text

    email = (await _health(auth_client))["email"]
    assert email["state"] == "ok"
    assert email["last_success_at"]
    assert email["consecutive_failures"] == 0
    assert email["days_since_success"] == 0


@pytest.mark.asyncio
async def test_a_delivery_whose_documents_were_all_refused_is_still_a_healthy_channel(
    auth_client, client
):
    """The message reached us — the channel did its job. Counting a refused
    attachment as a channel failure would raise an alarm about the pipe when the
    problem is the contents, and send the operator to the wrong screen."""
    await _activate(auth_client)
    token = await _token(auth_client)

    # An executable is refused outright by the security gate.
    r = await _deliver(client, token, [_att("invoice.exe", "MZ\x90\x00 not an invoice")])
    assert r.status_code == 200, r.text
    assert r.json()["rejected"] == 1
    assert r.json()["queued"] == 0

    email = (await _health(auth_client))["email"]
    assert email["state"] == "ok", "a refused DOCUMENT was reported as a broken CHANNEL"
    assert email["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_a_delivery_refused_because_the_module_is_off_is_a_channel_failure(
    auth_client, client, db_session
):
    """This one IS the channel: nothing can get through until a human acts, so it
    is sticky and one occurrence is already the alarm."""
    await _activate(auth_client)
    token = await _token(auth_client)
    off = await auth_client.put("/api/v1/modules/email_intake", json={"enabled": False})
    assert off.status_code == 200, off.text

    r = await _deliver(client, token, [_att("inv.csv", CSV, "text/csv")])
    assert r.status_code == 403, r.text

    row = await db_session.scalar(
        select(InboundChannelHealth).where(InboundChannelHealth.channel == "email")
    )
    assert row is not None, "a refused delivery left no trace at all"
    assert row.last_error_kind == inbound_health.ERR_NOT_ACTIVATED
    assert row.consecutive_failures >= 1


@pytest.mark.asyncio
async def test_a_sticky_failure_alarms_on_the_first_occurrence(auth_client, client, db_session):
    """A rotated credential or a switched-off module does not fix itself. Waiting
    for N occurrences of a failure that will never stop is waiting forever."""
    await _activate(auth_client)
    row = InboundChannelHealth(
        org_id=(await _org_id(db_session)),
        channel="email",
        last_attempt_at=datetime.now(UTC),
        last_success_at=datetime.now(UTC) - timedelta(days=2),
        consecutive_failures=1,
        last_error_kind=inbound_health.ERR_AUTH,
        last_error_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()

    email = (await _health(auth_client))["email"]

    assert email["state"] == "failing"
    assert email["last_error_text"]
    # The headline still tells them when it last worked — "it is broken" without
    # "and it has been broken since Tuesday" is half the story.
    assert "2 days ago" in email["headline"]


@pytest.mark.asyncio
async def test_a_single_transient_failure_does_not_alarm(auth_client, client, db_session):
    """Alarming on one blip trains the operator to ignore the screen."""
    await _activate(auth_client)
    db_session.add(
        InboundChannelHealth(
            org_id=(await _org_id(db_session)),
            channel="email",
            last_attempt_at=datetime.now(UTC),
            last_success_at=datetime.now(UTC),
            consecutive_failures=1,
            last_error_kind=inbound_health.ERR_INTERNAL,
            last_error_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    email = (await _health(auth_client))["email"]

    assert email["state"] == "ok"


@pytest.mark.asyncio
async def test_quiet_is_not_called_broken_without_a_stated_cadence(auth_client, db_session):
    """One customer's fortnight of quiet is another's outage. With no stated
    cadence we report the elapsed time as a FACT and make no accusation — a
    guessed threshold produces alarms nobody trusts."""
    await _activate(auth_client)
    db_session.add(
        InboundChannelHealth(
            org_id=(await _org_id(db_session)),
            channel="email",
            last_attempt_at=datetime.now(UTC) - timedelta(days=45),
            last_success_at=datetime.now(UTC) - timedelta(days=45),
            consecutive_failures=0,
        )
    )
    await db_session.commit()

    email = (await _health(auth_client))["email"]

    assert email["state"] == "ok"
    assert email["overdue"] is False
    assert email["days_since_success"] == 45
    assert "45 days ago" in email["headline"]


@pytest.mark.asyncio
async def test_quiet_past_a_stated_cadence_is_reported_as_silent(auth_client, db_session):
    await _activate(auth_client)
    db_session.add(
        InboundChannelHealth(
            org_id=(await _org_id(db_session)),
            channel="email",
            last_attempt_at=datetime.now(UTC) - timedelta(days=45),
            last_success_at=datetime.now(UTC) - timedelta(days=45),
            consecutive_failures=0,
        )
    )
    await db_session.commit()

    set_ = await auth_client.put("/api/v1/email/health/email", json={"expected_cadence_days": 7})
    assert set_.status_code == 200, set_.text

    email = (await _health(auth_client))["email"]
    assert email["state"] == "silent"
    assert email["overdue"] is True
    assert "45 days" in email["headline"]


@pytest.mark.asyncio
async def test_withdrawing_the_cadence_withdraws_the_accusation(auth_client, db_session):
    """`null` must mean "we no longer claim to know", not "reset to a default"."""
    await _activate(auth_client)
    db_session.add(
        InboundChannelHealth(
            org_id=(await _org_id(db_session)),
            channel="email",
            last_attempt_at=datetime.now(UTC) - timedelta(days=45),
            last_success_at=datetime.now(UTC) - timedelta(days=45),
            consecutive_failures=0,
            expected_cadence_days=7,
        )
    )
    await db_session.commit()
    assert (await _health(auth_client))["email"]["state"] == "silent"

    r = await auth_client.put("/api/v1/email/health/email", json={"expected_cadence_days": None})
    assert r.status_code == 200, r.text

    email = (await _health(auth_client))["email"]
    assert email["expected_cadence_days"] is None
    assert email["overdue"] is False
    assert email["state"] == "ok"


@pytest.mark.asyncio
async def test_a_malformed_delivery_is_classified_and_stores_nothing(
    auth_client, client, db_session
):
    """The 422 is unchanged and no attachment is written — but the channel now
    records WHY, instead of the delivery vanishing without trace."""
    await _activate(auth_client)
    token = await _token(auth_client)

    r = await _deliver(
        client,
        token,
        [
            _att("good.csv", CSV, "text/csv"),
            {"filename": "bad.csv", "content_type": "text/csv", "content_base64": "!!!not-b64!!!"},
        ],
    )
    assert r.status_code == 422, r.text

    from app.models.email_intake import InboundInvoice

    stored = list(await db_session.scalars(select(InboundInvoice)))
    assert stored == [], "a malformed delivery half-committed its attachments"

    row = await db_session.scalar(
        select(InboundChannelHealth).where(InboundChannelHealth.channel == "email")
    )
    assert row is not None
    assert row.last_error_kind == inbound_health.ERR_MALFORMED


@pytest.mark.asyncio
async def test_an_attempt_that_never_reports_an_outcome_is_not_counted_as_healthy(
    auth_client, client, db_session, monkeypatch
):
    """THE ordering guarantee. The health row is written pessimistically BEFORE
    the document work, so a delivery that crashes halfway leaves the channel
    marked not-succeeded. Recording the outcome at the END is silent about
    exactly the deliveries that went most wrong — the code that would record them
    is the code that did not run."""
    await _activate(auth_client)
    token = await _token(auth_client)
    # Establish a healthy baseline first, so the assertion below is about the
    # crash and not about a channel that was never used.
    ok = await _deliver(client, token, [_att("inv.csv", CSV, "text/csv")])
    assert ok.status_code == 200, ok.text
    assert (await _health(auth_client))["email"]["state"] == "ok"

    from app.services import email_intake as intake_svc

    async def _boom(*a, **k):
        raise RuntimeError("storage exploded mid-delivery")

    monkeypatch.setattr(intake_svc, "process_attachment", _boom)

    with pytest.raises(RuntimeError):
        await _deliver(client, token, [_att("inv2.csv", CSV, "text/csv")])

    row = await db_session.scalar(
        select(InboundChannelHealth).where(InboundChannelHealth.channel == "email")
    )
    await db_session.refresh(row)
    assert row.consecutive_failures >= 1, "a crashed delivery still looked healthy"
    assert row.last_error_kind == inbound_health.ERR_INCOMPLETE


@pytest.mark.asyncio
async def test_setting_the_cadence_is_audited_old_to_new(auth_client, db_session):
    """It changes when the workspace will be warned its intake is silent, so a
    later "why did nobody tell us?" has an answer."""
    import json

    from app.models.audit import AuditEvent

    await _activate(auth_client)
    assert (
        await auth_client.put("/api/v1/email/health/email", json={"expected_cadence_days": 7})
    ).status_code == 200
    assert (
        await auth_client.put("/api/v1/email/health/email", json={"expected_cadence_days": 30})
    ).status_code == 200

    events = list(
        await db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "inbound.cadence_changed")
            .order_by(AuditEvent.created_at.asc())
        )
    )
    assert len(events) == 2
    assert json.loads(events[0].meta) == {"old": None, "new": 7}
    assert json.loads(events[1].meta) == {"old": 7, "new": 30}


@pytest.mark.asyncio
async def test_an_unknown_channel_is_refused(auth_client):
    await _activate(auth_client)
    r = await auth_client.put(
        "/api/v1/email/health/carrier-pigeon", json={"expected_cadence_days": 7}
    )
    assert r.status_code == 404, r.text


async def _org_id(db_session) -> str:
    from app.models.organization import Organization

    return (await db_session.scalars(select(Organization.id))).first()


# --------------------------------------------------------------------------- #
# Remote I/O deadlines (S2-7). The harvest found NO timeouts anywhere in their
# remote I/O. Ours all have them — so this is a guard rail, not a fix: it fails
# if someone adds a new call without one.
# --------------------------------------------------------------------------- #


def test_every_remote_io_call_sets_a_timeout():
    """A remote call with no deadline holds a worker until the far end decides to
    answer, which may be never. The rule is cheap to keep and impossible to
    remember, so it is asserted structurally over the source."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    # Call sites that open a connection to something outside this process.
    patterns = [
        re.compile(r"httpx\.AsyncClient\("),
        re.compile(r"httpx\.Client\("),
        re.compile(r"smtplib\.SMTP(?:_SSL)?\("),
        re.compile(r"urllib\.request\.urlopen\("),
    ]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat in patterns:
                m = pat.search(line)
                if not m:
                    continue
                # The timeout may sit on the same line or in the wrapped call —
                # look at the call's own text, up to its closing paren.
                tail = text[text.index(line) : text.index(line) + 400]
                if "timeout" not in tail.split(")")[0] + ")":
                    offenders.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    assert not offenders, "remote I/O without a timeout:\n" + "\n".join(offenders)
