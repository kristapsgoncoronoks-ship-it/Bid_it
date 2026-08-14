"""A retry must not silently destroy a human's corrections.

`POST /invoices/upload/{run_id}/retry` deletes every `ExtractionField` row for
the run so the re-parse can write fresh provenance. That is correct for a failed
capture. It is not correct for a capture a human has already reviewed: the
`reviewed_value` rows they typed are deleted with the rest, and the guard did
not cover it — it refused only when the run had become an invoice or was
`saved`, so a capture that is parsed AND reviewed BUT NOT YET SAVED was in scope.

The audit chain records each correction, so the loss was forensically
recoverable — and silent in the live record, which is the part that matters to
the person who typed them.

The fix is not to preserve the values across the re-parse. A re-parse may use a
different provider and produce a different field set, so re-applying an old
correction could attach a human's decision to a field they never saw. Discarding
is the honest behaviour; what was missing is that the human must choose it.
"""

from __future__ import annotations

import io
import json

import pytest
from sqlalchemy import select

from app.models.extraction_field import ExtractionField

_CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Fictional Fuels OU,INV-RETRY-1,2026-06-01,Diesel,10,1.50,15.00,21\n"
)


def _files(name: str = "retry.csv") -> dict:
    return {"file": (name, io.BytesIO(_CSV.encode()), "text/csv")}


async def _reviewed_run(auth_client, parse_upload) -> str:
    """A capture that is parsed and carries a human correction, but is unsaved —
    exactly the state the guard did not cover."""
    run_id = (await parse_upload(auth_client, _files()))["extraction_run_id"]
    r = await auth_client.post(
        f"/api/v1/invoices/captures/{run_id}/review",
        json={"fields": [{"field": "vendor_name", "reviewed_value": "Fictional Fuels OÜ"}]},
    )
    assert r.status_code == 200, r.text
    return run_id


@pytest.mark.asyncio
async def test_retry_refuses_a_capture_that_carries_human_corrections(
    auth_client, parse_upload
):
    run_id = await _reviewed_run(auth_client, parse_upload)

    r = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")

    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "capture_has_review"
    # The refusal must tell the operator how to proceed deliberately, or they
    # will simply not be able to retry a capture they legitimately want re-parsed.
    assert "discard_review" in body["detail"]


@pytest.mark.asyncio
async def test_the_corrections_survive_a_refused_retry(auth_client, parse_upload):
    """The refusal must be a true no-op. A guard that rejects the request after
    already deleting the rows would be worse than no guard at all."""
    run_id = await _reviewed_run(auth_client, parse_upload)

    await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")

    r = await auth_client.get(f"/api/v1/invoices/captures/{run_id}/fields")
    assert r.status_code == 200
    vendor = [f for f in r.json() if f["field"] == "vendor_name" and f["line_index"] is None][0]
    assert vendor["reviewed_value"] == "Fictional Fuels OÜ"


@pytest.mark.asyncio
async def test_an_explicit_discard_is_allowed_and_clears_the_review(
    auth_client, parse_upload, db_session
):
    """Deliberate destruction stays possible — it just has to be asked for."""
    run_id = await _reviewed_run(auth_client, parse_upload)

    r = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry?discard_review=true")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"

    rows = (
        await db_session.scalars(
            select(ExtractionField).where(ExtractionField.extraction_run_id == run_id)
        )
    ).all()
    assert rows == [], "the retry re-parses from scratch, so old provenance is cleared"


@pytest.mark.asyncio
async def test_a_capture_with_no_corrections_still_retries_without_ceremony(
    auth_client, parse_upload
):
    """The guard must not tax the ordinary case. A capture nobody has touched is
    the reason this endpoint exists."""
    run_id = (await parse_upload(auth_client, _files()))["extraction_run_id"]

    r = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")

    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_the_discard_is_audited(auth_client, parse_upload, db_session):
    """Destroying a human's work is exactly the kind of act the chain exists for:
    who discarded, on which capture, and how many corrections went."""
    from app.models.audit import AuditEvent

    run_id = await _reviewed_run(auth_client, parse_upload)
    await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry?discard_review=true")

    events = (
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "capture.review_discarded")
        )
    ).all()
    assert len(events) == 1, "an explicit discard writes exactly one event"
    assert events[0].target_id == run_id
    # `meta` is stored as JSON TEXT, not a mapping — the chain hashes a string.
    assert json.loads(events[0].meta)["discarded_reviews"] == 1
