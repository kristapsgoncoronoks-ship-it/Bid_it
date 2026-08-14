"""L-4 — the guards that make a bulk action safe, proven on a real one.

Bulk operations collide with four things this codebase guarantees per record:
audit old→new, separation of duties, the opaque 404, and quota metering. The
collision is with the SHAPE of "do this to N things", so the guards live in
`services/bulk.py` once and every operation reuses them.

This proves them on bulk-acknowledging failed captures — deliberately the
lowest-risk operation available: additive, reversible, and touching no money. The
guards get exercised before anything destructive depends on them.
"""

from __future__ import annotations

import io
import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.capture_acknowledgement import CaptureAcknowledgement
from app.services import bulk

_UNPARSEABLE = b"not,an,invoice\nnothing,here,at all\n"


async def _failed_runs(auth_client, db_session, n: int) -> list[str]:
    """n genuinely failed captures, produced by the real pipeline."""
    from app.services import jobs

    ids = []
    for i in range(n):
        # Distinct BYTES per upload, not just a distinct filename: the
        # duplicate-upload guard keys on the content sha256, so identical
        # payloads would 409 from the second one onward.
        content = _UNPARSEABLE + f"row,{i},x\n".encode()
        r = await auth_client.post(
            "/api/v1/invoices/upload",
            files={"file": (f"bad{i}.csv", io.BytesIO(content), "text/csv")},
        )
        assert r.status_code == 202, r.text
        ids.append(r.json()["extraction_run_id"])
    for _ in range(60):
        if await jobs.run_once(db_session, "test-worker") is None:
            break
    for rid in ids:
        assert (await auth_client.get(f"/api/v1/invoices/upload/{rid}")).json()[
            "status"
        ] == "failed"
    return ids


def _items(ids: list[str]) -> list[dict]:
    return [{"channel": "upload", "ref_id": i} for i in ids]


async def _worklist(auth_client) -> dict:
    r = await auth_client.get("/api/v1/invoices/captures/failures")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_a_batch_applies_and_reports_an_outcome_per_record(auth_client, db_session):
    ids = await _failed_runs(auth_client, db_session, 3)

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids), "agreed_count": 3, "note": "chasing the supplier"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 3
    assert body["skipped"] == 0
    assert {o["ref_id"] for o in body["outcomes"]} == set(ids)
    assert all(o["result"] == "applied" for o in body["outcomes"])
    # The refreshed worklist comes back with it, so the caller never renders a
    # list it has just invalidated.
    assert all(i["ref_id"] not in ids for i in body["worklist"]["items"])


@pytest.mark.asyncio
async def test_a_count_mismatch_aborts_the_whole_batch_and_applies_nothing(auth_client, db_session):
    """GUARD 1, and the reason bulk actions are dangerous without it. The client
    says what it DISPLAYED; if the server's set is a different size, the list
    moved under the operator and their selection is not what they were looking
    at. It must abort — not apply to the subset it happens to have."""
    ids = await _failed_runs(auth_client, db_session, 3)

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids), "agreed_count": 5},  # the operator saw 5
    )

    assert r.status_code == 409, r.text
    assert r.json()["code"] == "bulk_count_mismatch"

    # NOTHING was written — a guard that aborts after applying half the batch
    # would be worse than no guard.
    rows = list(await db_session.scalars(select(CaptureAcknowledgement)))
    assert rows == []
    assert (await _worklist(auth_client))["unacknowledged"] == 3


@pytest.mark.asyncio
async def test_an_already_acknowledged_record_is_a_skip_not_a_failure(auth_client, db_session):
    """GUARD 2. 'Already acknowledged' is the system working. Counting it as an
    error is how error counts stop being read."""
    ids = await _failed_runs(auth_client, db_session, 2)
    first = await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{ids[0]}/acknowledge", json={"note": "seen"}
    )
    assert first.status_code == 200, first.text

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids), "agreed_count": 2},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 1
    assert body["skipped"] == 1
    assert body["failed"] == 0
    skip = next(o for o in body["outcomes"] if o["result"] == "skipped")
    assert skip["ref_id"] == ids[0]
    assert skip["reason"], "a skip with no reason is indistinguishable from a silent drop"


@pytest.mark.asyncio
async def test_applied_ids_describe_what_changed_not_what_was_requested(auth_client, db_session):
    """GUARD 3. The reversal basis is derived MECHANICALLY from the write. A
    hand-authored inverse drifts from the forward path the first time either
    changes, and a wrong undo is worse than none."""
    ids = await _failed_runs(auth_client, db_session, 2)
    await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{ids[0]}/acknowledge", json={"note": "seen"}
    )

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids), "agreed_count": 2},
    )

    body = r.json()
    assert body["applied_ids"] == [ids[1]], "applied_ids echoed the request instead of the write"
    assert ids[0] not in body["applied_ids"]


@pytest.mark.asyncio
async def test_the_batch_writes_one_audit_event_carrying_the_reversal_basis(
    auth_client, db_session
):
    ids = await _failed_runs(auth_client, db_session, 2)

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids), "agreed_count": 2, "note": "batch"},
    )
    assert r.status_code == 200, r.text

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "capture.failures_bulk_acknowledged")
        )
    )
    assert len(events) == 1, "a batch must be one audit event, not N or zero"
    meta = json.loads(events[0].meta)  # audit meta is JSON TEXT
    assert set(meta["applied_ids"]) == set(ids)
    assert meta["applied"] == 2
    assert meta["selection"] == "explicit"


@pytest.mark.asyncio
async def test_a_batch_that_applies_nothing_writes_no_audit_event(auth_client, db_session):
    """An audit event for a no-op is a lie about a change that never happened."""
    ids = await _failed_runs(auth_client, db_session, 1)
    await auth_client.post(
        f"/api/v1/invoices/captures/failures/upload/{ids[0]}/acknowledge", json={"note": "seen"}
    )

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids), "agreed_count": 1},
    )
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 0

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "capture.failures_bulk_acknowledged")
        )
    )
    assert events == []


@pytest.mark.asyncio
async def test_a_script_may_omit_the_agreed_count(auth_client, db_session):
    """The guard protects a HUMAN against a list that moved. A script has no
    displayed list; making the count mandatory would only teach callers to echo
    len(ids) back, which guards nothing."""
    ids = await _failed_runs(auth_client, db_session, 2)

    r = await auth_client.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": _items(ids)},  # no agreed_count
    )

    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 2


@pytest.mark.asyncio
async def test_bulk_acknowledge_needs_the_write_permission(role_client):
    employee = await role_client("user")  # EMPLOYEE — INVOICE_READ, no INVOICE_WRITE

    r = await employee.post(
        "/api/v1/invoices/captures/failures/acknowledge",
        json={"items": [{"channel": "upload", "ref_id": "x"}], "agreed_count": 1},
    )

    assert r.status_code == 403, r.text


# --------------------------------------------------------------------------- #
# The guard machinery itself — unit level, no HTTP.
# --------------------------------------------------------------------------- #


def test_a_filter_selection_is_refused_for_an_irreversible_action():
    """GUARD 4. 'Everything matching this filter' is a set the human never
    enumerated and cannot verify. For anything that cannot be undone, only an
    explicit list is accepted."""
    bulk.require_explicit_selection(bulk.Selection.EXPLICIT, action="Deleting invoices")

    with pytest.raises(Exception) as exc:
        bulk.require_explicit_selection(bulk.Selection.FILTER, action="Deleting invoices")
    assert exc.value.code == "bulk_filter_not_allowed"
    # The message must name the action, or the operator cannot tell what was refused.
    assert "Deleting invoices" in exc.value.message


def test_duplicate_ids_are_collapsed_rather_than_double_applied():
    """A client that sends the same id twice meant it once. Applying twice would
    double-count the audit trail."""
    assert bulk.normalise_ids(["a", "b", "a"]) == ["a", "b"]


def test_an_empty_or_oversized_selection_is_refused():
    with pytest.raises(Exception) as empty:
        bulk.normalise_ids([])
    assert empty.value.code == "bulk_empty_selection"

    with pytest.raises(Exception) as many:
        bulk.normalise_ids([str(i) for i in range(bulk.MAX_BATCH + 1)])
    assert many.value.code == "bulk_too_many"


def test_the_agreed_count_compares_against_the_deduplicated_set():
    """The count the operator saw is a count of DISTINCT rows. Comparing against
    the raw list would make a duplicated id look like a moved list."""
    bulk.check_agreed_count(bulk.normalise_ids(["a", "b", "a"]), 2)

    with pytest.raises(Exception) as exc:
        bulk.check_agreed_count(bulk.normalise_ids(["a", "b"]), 3)
    assert exc.value.code == "bulk_count_mismatch"
