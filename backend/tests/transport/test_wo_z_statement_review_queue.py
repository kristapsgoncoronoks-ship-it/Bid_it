"""WO-Z — a statement finding that outlives the response that reported it.

`statement_ingest`'s docstring said, from the slice that wrote it, that its
returned `warnings` list "IS the review surface until a persisted one exists".
Nothing enumerated what a file had been flagged for. Seeing a finding twice
meant uploading the file again.

The refused case kept less than that. When the capture gate blocks
registration the structured findings were folded into one message string and
raised, and the transaction went with them — so the ONE outcome where an
operator most needs to know which line failed recorded nothing at all.

WHAT THESE PIN
--------------
1. **A finding survives.** A registered statement's warnings are rows a later,
   independent request can read — the work order's own certification.
2. **A refusal records why.** The errors that blocked registration are
   persisted with their rule code and line, and the refusal still refuses:
   zero fuel rows, 422 on the wire.
3. **Re-uploading the same bytes does not pile up duplicates**, because a
   re-parse is a fresh verdict about the same file, not another one.
4. **…but a finding that RECURS after being resolved comes back.** That is
   what the partial index buys, and the reason the index is partial: the
   resolved row leaves the unique set so the recurrence can open its own.
5. **Two verbs, not one.** `resolved` and `dismissed` are different claims by
   a named person, both audited, and closing is single-shot.
6. **Two complaints from one source are two findings.** The obvious dedup key
   (code, line_seq) would have collapsed them — see `_fingerprint`.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.statement_finding import VatStatementFinding
from app.services import modules
from app.services.transport import statement_review
from tests.factories.transport import synthetic_eurowag_statement
from tests.transport.conftest import make_entity

V = "/api/v1"
PATH = f"{V}/transport/statements"
FINDINGS = f"{PATH}/findings"

pytestmark = pytest.mark.asyncio


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO-Z Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@woz.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _setup(client: AsyncClient, db_session):
    headers, org_id = await _register_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    return headers, org_id, entity


def _upload(content: bytes, *, filename: str = "eurowag-2026-06.csv"):
    return {"file": (filename, content, "text/csv")}


def _statement_without_a_seller_footer() -> bytes:
    """A statement that REGISTERS but leaves the parser unable to anchor a
    seller entity — the exact "ambiguity that is not fatal to the figures"
    the ingest docstring describes as warning rather than blocking."""
    return synthetic_eurowag_statement(
        seed=7, footer_lines=["Thank you for fuelling with us."]
    ).encode()


def _statement_with_a_zero_net() -> bytes:
    """A statement the capture gate REFUSES: net must be > 0."""
    return synthetic_eurowag_statement(
        seed=11,
        rows=[
            {
                "txn_date": "2026-06-03",
                "txn_time": "08:12",
                "vehicle_ref": "TRK-0001",
                "station": "Demo Fuel Hub",
                "country": "BE",
                "product": "DIESEL",
                "qty": "100.00",
                "currency": "EUR",
                "net_local": "0.00",
                "vat_local": "0.00",
                "gross_local": "0.00",
                "invoice_ref": "EW-2026-06-1",
            }
        ],
    ).encode()


async def _post(client, headers, entity, content, *, period="2026-06"):
    return await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": period},
        files=_upload(content),
    )


# --------------------------------------------------------------------------- #
# A finding survives the response
# --------------------------------------------------------------------------- #


async def test_a_registered_statements_warning_outlives_its_response(client, db_session):
    """The work order's own certification: a finding surviving a restart. The
    second request is a different request over the real route — the warning is
    read back from rows, not from anything the upload left in memory."""
    headers, org_id, entity = await _setup(client, db_session)
    content = _statement_without_a_seller_footer()

    up = await _post(client, headers, entity, content)
    assert up.status_code == 200, up.text
    assert up.json()["warnings"], "this fixture is only useful if it warns"

    queue = await client.get(FINDINGS, headers=headers)
    assert queue.status_code == 200, queue.text
    body = queue.json()
    assert body["open_count"] >= 1
    assert body["findings"], "the warning did not outlive the response"

    row = body["findings"][0]
    assert row["statement_sha256"] == hashlib.sha256(content).hexdigest()
    assert row["filename"] == "eurowag-2026-06.csv"
    assert row["period"] == "2026-06"
    assert row["outcome"] == "registered"
    assert row["severity"] == "warn"
    assert row["status"] == "open"
    # It says something. A queue of empty rows would pass every structural
    # assertion above and tell an operator nothing.
    assert row["message"].strip()


async def test_a_refusal_records_why_and_still_refuses(client, db_session):
    """The half that recorded nothing. The refusal is unchanged — 422, zero
    rows — and now leaves behind what blocked it."""
    headers, org_id, entity = await _setup(client, db_session)
    content = _statement_with_a_zero_net()

    up = await _post(client, headers, entity, content)
    assert up.status_code == 422, up.text
    assert up.json()["code"] == "capture_review_blocked"
    # The structured verdict reaches the client too, not only the queue.
    assert up.json()["findings"], up.json()

    # The refusal still refuses: nothing was registered.
    rows = (
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    ).all()
    assert rows == []

    queue = await client.get(FINDINGS, headers=headers)
    findings = queue.json()["findings"]
    assert findings, "a refused statement recorded nothing"
    assert {f["outcome"] for f in findings} == {"refused"}
    assert {f["severity"] for f in findings} == {"error"}
    # The rule that fired is named, and so is the line — the thing a message
    # string could only have carried as prose.
    assert any(f["code"].startswith("rule:") for f in findings)
    assert any(f["line_seq"] is not None for f in findings)
    assert queue.json()["refused_count"] >= 1


# --------------------------------------------------------------------------- #
# Re-parsing the same bytes
# --------------------------------------------------------------------------- #


async def test_re_uploading_the_same_statement_does_not_duplicate_its_findings(client, db_session):
    """Registering the same bytes twice is a no-op on the transactions, so it
    has to be a no-op on the queue. The latest parse's verdict REPLACES the
    previous one rather than accumulating beside it."""
    headers, org_id, entity = await _setup(client, db_session)
    content = _statement_without_a_seller_footer()

    first = await _post(client, headers, entity, content)
    assert first.status_code == 200, first.text
    after_one = (await client.get(FINDINGS, headers=headers)).json()["open_count"]

    second = await _post(client, headers, entity, content)
    assert second.status_code == 200, second.text
    after_two = (await client.get(FINDINGS, headers=headers)).json()

    assert after_two["open_count"] == after_one, (
        "a re-upload duplicated the queue: "
        f"{after_one} open findings became {after_two['open_count']}"
    )


async def test_a_resolved_finding_that_recurs_comes_back(client, db_session):
    """THE reason the unique index is partial.

    Resolve a finding, then re-upload the file that produces it. The resolved
    row is out of the unique set, so the recurrence opens a NEW one — instead
    of being swallowed as a duplicate of a complaint somebody already closed.
    That silent swallowing is the failure `capture_failures.failure_seq` was
    added to prevent on the AP side; this is the same defect refused here by
    construction."""
    headers, org_id, entity = await _setup(client, db_session)
    content = _statement_without_a_seller_footer()

    await _post(client, headers, entity, content)
    open_rows = (await client.get(FINDINGS, headers=headers)).json()["findings"]
    target = open_rows[0]

    closed = await client.post(
        f"{FINDINGS}/{target['id']}/close",
        headers=headers,
        json={"status": "resolved", "note": "Registered the seller by hand."},
    )
    assert closed.status_code == 200, closed.text
    assert (await client.get(FINDINGS, headers=headers)).json()["open_count"] == 0

    # The same file again — the finding is true again, so it must be visible again.
    again = await _post(client, headers, entity, content)
    assert again.status_code == 200, again.text
    reopened = (await client.get(FINDINGS, headers=headers)).json()
    assert reopened["open_count"] >= 1, "a recurrence was swallowed by the dedup key"
    assert reopened["findings"][0]["id"] != target["id"], (
        "the recurrence reused the resolved row instead of opening its own"
    )

    # …and the resolved one is still there, with who closed it and why.
    history = (await client.get(f"{FINDINGS}?status_filter=resolved", headers=headers)).json()
    assert [f["id"] for f in history["findings"]] == [target["id"]]
    assert history["findings"][0]["resolved_by"]
    assert history["findings"][0]["resolution_note"] == "Registered the seller by hand."


# --------------------------------------------------------------------------- #
# The resolution verbs
# --------------------------------------------------------------------------- #


async def test_resolving_and_dismissing_are_different_claims_and_both_audited(client, db_session):
    """A single "done" would have destroyed, at the moment it was cheapest to
    record, the difference between "I dealt with it" and "it did not need
    dealing with"."""
    headers, org_id, entity = await _setup(client, db_session)
    await _post(client, headers, entity, _statement_without_a_seller_footer())
    rows = (await client.get(FINDINGS, headers=headers)).json()["findings"]
    assert rows

    got = await client.post(
        f"{FINDINGS}/{rows[0]['id']}/close",
        headers=headers,
        json={"status": "dismissed", "note": "This network never prints a seller footer."},
    )
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "dismissed"
    assert got.json()["resolved_by"]
    assert got.json()["resolved_at"]

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org_id,
                AuditEvent.action == "transport.statement_finding_closed",
            )
        )
    ).all()
    assert len(events) == 1
    meta = json.loads(events[0].meta or "{}")
    # WHICH verb was claimed is the part a later reader cannot reconstruct from
    # the row's absence from the queue, so it is what the event has to carry.
    assert meta["resolution"] == "dismissed"
    assert meta["note"] == "This network never prints a seller footer."


async def test_a_finding_can_only_be_closed_once(client, db_session):
    """Single-shot, so `resolved_by` always names the person who actually made
    the call rather than whoever pressed the button last."""
    headers, org_id, entity = await _setup(client, db_session)
    await _post(client, headers, entity, _statement_without_a_seller_footer())
    target = (await client.get(FINDINGS, headers=headers)).json()["findings"][0]

    first = await client.post(
        f"{FINDINGS}/{target['id']}/close", headers=headers, json={"status": "resolved"}
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"{FINDINGS}/{target['id']}/close", headers=headers, json={"status": "dismissed"}
    )
    assert second.status_code == 409
    assert second.json()["code"] == "finding_already_closed"


async def test_the_queue_is_readable_without_write_rights_but_closing_is_not(client, db_session):
    """Seeing what a file was flagged for changes nothing; putting your name to
    a judgement about it does. An unknown finding is an opaque 404 rather than
    a 403 that would confirm the id exists in some other workspace."""
    headers, org_id, entity = await _setup(client, db_session)
    await _post(client, headers, entity, _statement_without_a_seller_footer())

    other, _ = await _register_org(client)
    theirs = await client.get(FINDINGS, headers=other)
    assert theirs.status_code == 200
    assert theirs.json()["findings"] == []

    mine = (await client.get(FINDINGS, headers=headers)).json()["findings"][0]
    cross = await client.post(
        f"{FINDINGS}/{mine['id']}/close", headers=other, json={"status": "resolved"}
    )
    assert cross.status_code == 404, cross.text


# --------------------------------------------------------------------------- #
# The dedup key itself
# --------------------------------------------------------------------------- #


async def test_two_complaints_from_one_source_are_two_findings(client, db_session):
    """The bug the obvious key would have shipped.

    Two prose findings from the same producer share a code and carry no line
    number, so under (code, line_seq) the second would have been refused as a
    duplicate of the first and an operator would have lost a real finding to an
    index. What makes two rows the same row is that they SAY the same thing."""
    headers, org_id, entity = await _setup(client, db_session)
    sha = "f" * 64
    rows = await statement_review.record_registered(
        db_session,
        org_id,
        statement_sha256=sha,
        filename="two-notes.csv",
        network="Eurowag",
        period="2026-06",
        entity_id=entity.id,
        warnings=["capture check: one thing", "capture check: a different thing"],
    )
    await db_session.commit()
    assert len(rows) == 2
    assert rows[0].code == rows[1].code == statement_review.CODE_CAPTURE_CHECK
    assert rows[0].fingerprint != rows[1].fingerprint

    stored = (
        await db_session.scalars(
            select(VatStatementFinding).where(VatStatementFinding.statement_sha256 == sha)
        )
    ).all()
    assert len(stored) == 2


async def test_the_identical_complaint_twice_is_one_finding(client, db_session):
    """The other half of the same rule: the same sentence about the same
    statement is one row, whichever parse produced it."""
    headers, org_id, entity = await _setup(client, db_session)
    sha = "e" * 64
    for _ in range(2):
        await statement_review.record_registered(
            db_session,
            org_id,
            statement_sha256=sha,
            filename="same-note.csv",
            network="Eurowag",
            period="2026-06",
            entity_id=entity.id,
            warnings=["capture check: the very same thing"],
        )
        await db_session.commit()

    stored = (
        await db_session.scalars(
            select(VatStatementFinding).where(VatStatementFinding.statement_sha256 == sha)
        )
    ).all()
    assert len(stored) == 1


async def test_an_unknown_resolution_is_refused(client, db_session):
    """The closed statuses are a closed set. A queue that accepted any word as
    a resolution would make its own history unreadable."""
    headers, org_id, entity = await _setup(client, db_session)
    await _post(client, headers, entity, _statement_without_a_seller_footer())
    target = (await client.get(FINDINGS, headers=headers)).json()["findings"][0]

    bad = await client.post(
        f"{FINDINGS}/{target['id']}/close", headers=headers, json={"status": "ignored"}
    )
    assert bad.status_code == 422
