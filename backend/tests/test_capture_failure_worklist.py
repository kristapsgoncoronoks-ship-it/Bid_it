"""A failed capture must be findable without already knowing its id (H-1).

Before this worklist, a capture that failed was reachable only through
`GET /invoices/upload/{run_id}` — which you can only call if you already know
the run id. Nothing enumerated failures for a tenant. That is the worst shape a
document pipeline can take: the customer believes the document was processed, it
was not, and no screen disagrees.

These tests pin the three things that make the worklist worth having:

* the failure carries a CLASSIFIED code and a remediation an operator can act on,
  never just prose from a library;
* acknowledging is a record pinned to the failure it was made against, so a
  capture that fails AGAIN comes back rather than staying dismissed;
* what SURVIVED is stated — the original document is still stored.
"""

from __future__ import annotations

import io
import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.capture_acknowledgement import CaptureAcknowledgement
from app.models.extraction_run import ExtractionRun
from app.services import capture_failures

# Valid UTF-8 CSV: it passes the upload security gate, then fails to PARSE —
# there is no column the line-item reader recognises. A real failure, produced by
# the real pipeline, not a hand-written `status = "failed"`.
_UNPARSEABLE = b"not,an,invoice\nnothing,here,at all\n"

_GOOD = (
    b"vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    b"Fictional Fuels OU,INV-WL-1,2026-06-01,Diesel,10,1.50,15.00,21\n"
)


async def _drain(db_session, *, idle_rounds: int = 3) -> None:
    """Run the worker until the queue is EMPTY several polls in a row.

    Breaking on the first empty poll is fragile: a job enqueued moments earlier
    may not be visible yet, and the loop then exits before doing the work the
    test is about to assert on. That is a plausible source of the intermittent
    failure recorded against F-06, and it costs nothing to remove.
    """
    from app.services import jobs

    idle = 0
    for _ in range(120):
        if await jobs.run_once(db_session, "test-worker") is None:
            idle += 1
            if idle >= idle_rounds:
                return
        else:
            idle = 0


async def _upload_and_drain(auth_client, db_session, content: bytes, name: str) -> str:
    """Upload a file and run the worker, WITHOUT asserting the outcome — unlike
    the `parse_upload` fixture, which requires a successful parse."""

    r = await auth_client.post(
        "/api/v1/invoices/upload",
        files={"file": (name, io.BytesIO(content), "text/csv")},
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["extraction_run_id"]
    await _drain(db_session)
    return run_id


async def _failed_run(auth_client, db_session, name: str = "unparseable.csv") -> str:
    run_id = await _upload_and_drain(auth_client, db_session, _UNPARSEABLE, name)
    res = await auth_client.get(f"/api/v1/invoices/upload/{run_id}")
    assert res.json()["status"] == "failed", res.text
    return run_id


async def _worklist(auth_client, *, include_acknowledged: bool = False) -> dict:
    q = "?include_acknowledged=true" if include_acknowledged else ""
    r = await auth_client.get(f"/api/v1/invoices/captures/failures{q}")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_a_failed_capture_is_listed_with_a_code_and_an_operator_remediation(
    auth_client, db_session
):
    run_id = await _failed_run(auth_client, db_session)

    body = await _worklist(auth_client)

    item = next(i for i in body["items"] if i["ref_id"] == run_id)
    assert item["channel"] == "upload"
    assert item["code"] == capture_failures.MALFORMED_DOCUMENT
    # The operator is told what to DO, not just what broke. An error a user
    # cannot act on sends them to support.
    assert item["remediation"].strip()
    assert item["summary"].strip()
    # The raw library message is kept for support — but it is NOT the summary.
    assert item["detail"]
    assert item["detail"] != item["summary"]
    # This failure cannot be fixed by re-running the same bytes, and the contract
    # says so, so a screen cannot offer a retry that provably cannot work.
    assert item["retry_helps"] is False
    assert body["unacknowledged"] >= 1


@pytest.mark.asyncio
async def test_the_worklist_states_that_the_document_itself_survived(auth_client, db_session):
    """ "We could not read your file" and "we lost your file" are very different
    sentences to the person who sent it. A failure record that says only "failed"
    conflates them."""
    run_id = await _failed_run(auth_client, db_session)

    item = next(i for i in (await _worklist(auth_client))["items"] if i["ref_id"] == run_id)

    assert item["document_retained"] is True
    assert item["sha256"]


@pytest.mark.asyncio
async def test_a_lost_original_is_not_reported_as_retained(auth_client, db_session):
    """The one failure mode where the bytes did NOT survive must not claim they
    did — the remediation for it is 'send it again', which is wrong advice for
    every other code."""
    run_id = await _failed_run(auth_client, db_session)
    run = await db_session.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id))
    run.failure_code = capture_failures.STORED_FILE_MISSING
    await db_session.commit()

    item = next(i for i in (await _worklist(auth_client))["items"] if i["ref_id"] == run_id)

    assert item["document_retained"] is False
    assert item["retry_helps"] is False


@pytest.mark.asyncio
async def test_a_successful_capture_is_not_on_the_worklist(auth_client, db_session, parse_upload):
    """Guard rail: the queue must not fill with captures that worked, or the
    operator stops reading it."""
    ok_run = (
        await auth_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("good.csv", io.BytesIO(_GOOD), "text/csv")},
        )
    ).json()["extraction_run_id"]

    await _drain(db_session)

    body = await _worklist(auth_client)

    assert all(i["ref_id"] != ok_run for i in body["items"]), body


@pytest.mark.asyncio
async def test_acknowledging_records_who_and_when_and_hides_the_item(auth_client, db_session):
    run_id = await _failed_run(auth_client, db_session)

    r = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{run_id}/acknowledge",
        json={"note": "supplier is re-sending as PDF"},
    )
    assert r.status_code == 200, r.text

    assert all(i["ref_id"] != run_id for i in r.json()["items"]), r.text

    shown = next(
        i
        for i in (await _worklist(auth_client, include_acknowledged=True))["items"]
        if i["ref_id"] == run_id
    )
    assert shown["acknowledgement_note"] == "supplier is re-sending as PDF"
    assert shown["acknowledged_by"]  # taken from the session, never from the body
    assert shown["acknowledged_at"]

    # A record, not a boolean flipped on the capture.
    rows = list(
        await db_session.scalars(
            select(CaptureAcknowledgement).where(CaptureAcknowledgement.ref_id == run_id)
        )
    )
    assert len(rows) == 1
    assert rows[0].failure_seen_at is not None


@pytest.mark.asyncio
async def test_a_capture_that_fails_again_returns_to_the_worklist(auth_client, db_session):
    """THE invariant of the acknowledgement design. An ack covers the failure it
    was made against — not the document forever. Without the `failure_seen_at`
    comparison a retried-and-re-failed capture inherits the old dismissal and
    disappears, which is exactly the silence this worklist exists to break."""
    run_id = await _failed_run(auth_client, db_session)
    ack = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{run_id}/acknowledge",
        json={"note": "looked at it"},
    )
    assert ack.status_code == 200, ack.text
    assert all(i["ref_id"] != run_id for i in ack.json()["items"])

    # Retry the same (still unparseable) bytes — it fails again.

    retry = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")
    assert retry.status_code == 202, retry.text
    await _drain(db_session)
    assert (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()["status"] == "failed"

    body = await _worklist(auth_client)

    item = next((i for i in body["items"] if i["ref_id"] == run_id), None)
    assert item is not None, "a NEW failure was silenced by an OLD acknowledgement"
    assert item["acknowledged_at"] is None


@pytest.mark.asyncio
async def test_a_retry_that_succeeds_clears_the_failure(auth_client, db_session):
    """A stale code would leave the worklist explaining a failure that no longer
    exists."""
    run_id = await _failed_run(auth_client, db_session)
    # Replace the stored bytes with a parseable file, keyed by the run's sha, so
    # the SAME run re-parses successfully.
    run = await db_session.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id))
    from app.services import documents
    from app.services.extraction import sha256_hex

    await documents.store(documents.UPLOADS, run.org_id, _GOOD, "text/csv", db=db_session)
    run.source_sha256 = sha256_hex(_GOOD)
    await db_session.commit()

    retry = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")
    assert retry.status_code == 202, retry.text
    await _drain(db_session)
    assert (await auth_client.get(f"/api/v1/invoices/upload/{run_id}")).json()["status"] == "parsed"

    body = await _worklist(auth_client, include_acknowledged=True)

    assert all(i["ref_id"] != run_id for i in body["items"]), body
    await db_session.refresh(run)
    assert run.failure_code is None


@pytest.mark.asyncio
async def test_the_same_document_failing_twice_reads_as_a_repeat_not_two_problems(
    auth_client, db_session
):
    a = await _failed_run(auth_client, db_session, "first.csv")
    b = await _failed_run(auth_client, db_session, "second.csv")

    body = await _worklist(auth_client)

    items = {i["ref_id"]: i for i in body["items"]}
    assert items[a]["repeat_count"] == 2
    assert items[b]["repeat_count"] == 2
    # One systemic cause reads as ONE group line, not as N rows the operator has
    # to infer a pattern from.
    group = next(g for g in body["groups"] if g["code"] == capture_failures.MALFORMED_DOCUMENT)
    assert group["count"] >= 2
    assert group["unacknowledged"] >= 2


@pytest.mark.asyncio
async def test_acknowledging_an_unknown_reference_is_an_opaque_404(auth_client, db_session):
    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/upload/"
        "00000000-0000-0000-0000-000000000000/acknowledge",
        json={"note": "x"},
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_acknowledging_a_capture_that_did_not_fail_is_refused(
    auth_client, db_session, parse_upload
):
    """Acknowledging is for failures. A parsed capture is not one, and letting it
    through would put rows in the history describing failures that never happened."""
    ok = (
        await auth_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("good.csv", io.BytesIO(_GOOD), "text/csv")},
        )
    ).json()["extraction_run_id"]

    await _drain(db_session)

    r = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{ok}/acknowledge", json={"note": "x"}
    )

    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_acknowledging_is_audited(auth_client, db_session):
    run_id = await _failed_run(auth_client, db_session)

    r = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{run_id}/acknowledge",
        json={"note": "chasing the supplier"},
    )
    assert r.status_code == 200, r.text

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "capture.failure_acknowledged")
        )
    )
    assert len(events) == 1
    # `meta` is JSON TEXT — the audit chain hashes a string, not a dict.
    meta = json.loads(events[0].meta)
    assert meta["channel"] == "upload"
    assert meta["note"] == "chasing the supplier"
    assert events[0].target_id == run_id


@pytest.mark.asyncio
async def test_a_failure_recorded_before_the_contract_existed_claims_no_cause(
    auth_client, db_session
):
    """A row with no classified code must NOT be reported as `internal_error`.
    "We did not record why" and "something broke on our side" are different
    claims, and only one of them is true. Deriving a cause by matching words in
    an old message would manufacture a fact."""
    run_id = await _failed_run(auth_client, db_session)
    run = await db_session.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id))
    run.failure_code = None  # as written before this migration
    await db_session.commit()

    item = next(i for i in (await _worklist(auth_client))["items"] if i["ref_id"] == run_id)

    assert item["code"] == capture_failures.UNKNOWN_FAILURE
    assert item["retry_helps"] is True  # a fresh attempt WILL record why


# --------------------------------------------------------------------------- #
# The classifier and the vocabulary — unit level, no HTTP.
# --------------------------------------------------------------------------- #


def test_classification_is_driven_by_the_exception_type_not_its_wording():
    """A classifier that reads prose breaks silently the day someone rewords a
    message. `CaptureError` states its code at the site that knows the cause."""
    err = capture_failures.CaptureError(capture_failures.UNSUPPORTED_FORMAT, "anything at all")
    assert capture_failures.code_for(err) == capture_failures.UNSUPPORTED_FORMAT

    # The parse layer's documented "bad input" signal.
    assert capture_failures.code_for(ValueError("x")) == capture_failures.MALFORMED_DOCUMENT
    # Anything else escaped a provider unclassified — ours, not the document's.
    assert capture_failures.code_for(RuntimeError("x")) == capture_failures.INTERNAL_ERROR
    # A code outside the vocabulary is not passed through as if it were one.
    rogue = capture_failures.CaptureError("something_made_up", "x")
    assert capture_failures.code_for(rogue) == capture_failures.INTERNAL_ERROR


def test_an_unsupported_file_type_classifies_as_unsupported_rather_than_malformed():
    """Selection failure and parse failure need DIFFERENT advice: one says change
    the file format, the other says check the file isn't damaged."""
    from app.services import extraction_provider

    with pytest.raises(capture_failures.CaptureError) as exc:
        extraction_provider.select("statement.docx", b"PK\x03\x04")
    assert exc.value.code == capture_failures.UNSUPPORTED_FORMAT
    # Still a ValueError, so every existing `except ValueError` around the parse
    # path behaves exactly as it did.
    assert isinstance(exc.value, ValueError)


def test_every_failure_kind_carries_advice_an_operator_can_act_on():
    """Registry integrity: adding a code without an operator sentence would put a
    blank cell in front of the person who has to resolve it."""
    for code, kind in capture_failures.KINDS.items():
        assert kind.code == code
        assert kind.summary.strip() and kind.summary.endswith(".")
        assert kind.remediation.strip() and kind.remediation.endswith(".")
        # No library vocabulary leaking into operator-facing prose.
        lowered = f"{kind.summary} {kind.remediation}".lower()
        for jargon in ("traceback", "exception", "null", "sha256", "stacktrace"):
            assert jargon not in lowered, f"{code}: operator prose contains {jargon!r}"


# --------------------------------------------------------------------------- #
# Pagination (F-05). The worklist used to load every failed row for a tenant and
# do the counting and grouping in Python — correct, and growing with exactly the
# failure the screen exists to surface.
# --------------------------------------------------------------------------- #


async def _n_failed(auth_client, db_session, n: int) -> list[str]:

    ids = []
    for i in range(n):
        content = _UNPARSEABLE + f"row,{i},x\n".encode()  # distinct bytes: the dup guard
        r = await auth_client.post(
            "/api/v1/invoices/upload",
            files={"file": (f"p{i}.csv", io.BytesIO(content), "text/csv")},
        )
        assert r.status_code == 202, r.text
        ids.append(r.json()["extraction_run_id"])
    await _drain(db_session)
    return ids


@pytest.mark.asyncio
async def test_the_header_counts_the_whole_set_not_the_page(auth_client, db_session):
    """A header that counted only the rows in front of you would say "3 problems"
    on page one of forty. The items are the page; the numbers are the truth about
    all of it."""
    await _n_failed(auth_client, db_session, 7)

    r = await auth_client.get("/api/v1/invoices/captures/failures?page=1&page_size=3")

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 3
    assert body["total"] == 7, "total described the page instead of the set"
    assert body["unacknowledged"] == 7
    group = next(g for g in body["groups"] if g["code"] == capture_failures.MALFORMED_DOCUMENT)
    assert group["count"] == 7, "the grouping counted the page instead of the set"


@pytest.mark.asyncio
async def test_a_second_page_returns_different_records(auth_client, db_session):
    await _n_failed(auth_client, db_session, 5)

    first = (await auth_client.get("/api/v1/invoices/captures/failures?page=1&page_size=2")).json()
    second = (await auth_client.get("/api/v1/invoices/captures/failures?page=2&page_size=2")).json()

    a = {i["ref_id"] for i in first["items"]}
    b = {i["ref_id"] for i in second["items"]}
    assert len(a) == 2 and len(b) == 2
    assert not (a & b), "the same record appeared on two pages"
    assert first["total"] == second["total"] == 5


@pytest.mark.asyncio
async def test_the_sql_filter_and_the_python_rule_agree_about_coverage(auth_client, db_session):
    """The "is this acknowledgement still current" rule now exists twice: in SQL,
    so the database can filter and count on it, and in Python, so an item can
    report who acknowledged it. If they disagree the page and its header disagree,
    which is why both are driven here rather than trusted."""
    ids = await _n_failed(auth_client, db_session, 2)
    ack = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{ids[0]}/acknowledge", json={"note": "seen"}
    )
    assert ack.status_code == 200, ack.text

    # SQL: the acknowledged one is filtered out and not counted.
    hidden = (await auth_client.get("/api/v1/invoices/captures/failures")).json()
    assert {i["ref_id"] for i in hidden["items"]} == {ids[1]}
    assert hidden["total"] == 1

    # Python: the same record, when shown, reports itself as acknowledged.
    shown = (
        await auth_client.get("/api/v1/invoices/captures/failures?include_acknowledged=true")
    ).json()
    item = next(i for i in shown["items"] if i["ref_id"] == ids[0])
    assert item["acknowledged_at"] is not None
    assert shown["total"] == 2
    assert shown["unacknowledged"] == 1, "the two rules disagree about what is covered"


@pytest.mark.asyncio
async def test_coverage_is_decided_by_the_failure_SEQUENCE_not_the_clock(auth_client, db_session):
    """F-06. An acknowledgement used to be pinned to a wall-clock timestamp, and
    coverage was `ack.failure_seen_at >= failed_at`. A re-failure recorded in the
    SAME tick therefore compared equal and was treated as already covered, so a
    genuine new failure stayed hidden — the exact silence this worklist exists to
    break.

    This drives the collision directly rather than hoping to catch it by timing:
    the acknowledgement and the new failure are given the SAME timestamp, and the
    item must still come back."""
    from datetime import UTC, datetime

    from app.models.capture_acknowledgement import CaptureAcknowledgement

    run_id = await _failed_run(auth_client, db_session)
    ack = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{run_id}/acknowledge", json={"note": "seen"}
    )
    assert ack.status_code == 200, ack.text
    assert all(i["ref_id"] != run_id for i in (await _worklist(auth_client))["items"])

    run = await db_session.scalar(select(ExtractionRun).where(ExtractionRun.id == run_id))
    row = await db_session.scalar(
        select(CaptureAcknowledgement).where(CaptureAcknowledgement.ref_id == run_id)
    )
    # The pathological case: identical timestamps, but a NEW failure event.
    same_instant = datetime.now(UTC)
    row.failure_seen_at = same_instant
    run.updated_at = same_instant
    run.failure_seq = (run.failure_seq or 0) + 1
    await db_session.commit()

    body = await _worklist(auth_client)

    assert any(i["ref_id"] == run_id for i in body["items"]), (
        "a new failure sharing the acknowledgement's timestamp stayed hidden"
    )
    assert body["unacknowledged"] >= 1
