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


async def _upload_and_drain(auth_client, db_session, content: bytes, name: str) -> str:
    """Upload a file and run the worker, WITHOUT asserting the outcome — unlike
    the `parse_upload` fixture, which requires a successful parse."""
    from app.services import jobs

    r = await auth_client.post(
        "/api/v1/invoices/upload",
        files={"file": (name, io.BytesIO(content), "text/csv")},
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["extraction_run_id"]
    for _ in range(30):
        if await jobs.run_once(db_session, "test-worker") is None:
            break
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
    from app.services import jobs

    for _ in range(30):
        if await jobs.run_once(db_session, "test-worker") is None:
            break

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
    from app.services import jobs

    retry = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")
    assert retry.status_code == 202, retry.text
    for _ in range(30):
        if await jobs.run_once(db_session, "test-worker") is None:
            break
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
    from app.services import documents, jobs
    from app.services.extraction import sha256_hex

    await documents.store(documents.UPLOADS, run.org_id, _GOOD, "text/csv", db=db_session)
    run.source_sha256 = sha256_hex(_GOOD)
    await db_session.commit()

    retry = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")
    assert retry.status_code == 202, retry.text
    for _ in range(30):
        if await jobs.run_once(db_session, "test-worker") is None:
            break
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
    from app.services import jobs

    for _ in range(30):
        if await jobs.run_once(db_session, "test-worker") is None:
            break

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
