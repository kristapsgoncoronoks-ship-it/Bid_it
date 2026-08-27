"""WO-X — AP arrives in batches, and a long capture must not look like a stuck one.

Two halves of one complaint about throughput.

X1 — THE BATCH DOOR
-------------------
Every capture endpoint took a single `UploadFile` and the upload screen read
`files?.[0]`, so an envelope of nine supplier invoices was nine round trips.
What these pin is not "it accepts a list" but the two properties that make a
batch honest:

1. **Partial by design.** One refused file leaves the rest queued and reports
   its own reason. Failing the request on the first bad file would throw away
   real work to report something the caller can act on file by file.
2. **The quota counts DOCUMENTS.** Admission — quota included — runs per file.
   A per-request check would let a 40-file drop through a plan with 3 uploads
   left, which is the entire purpose of the limit. This is the property a
   naive `list[UploadFile]` version of the endpoint silently loses.

X2 — HONEST PROGRESS
--------------------
The poll answered with four words: queued, running, parsed, failed. A 3-page
text PDF and a 40-page scan look identical while they run — one takes a
second, the other takes minutes — so the operator cannot tell a long job from
a hung one.

What these pin:

3. **Progress is durable AS IT HAPPENS**, not summarised at the end. The
   flagship test polls the row from another session *while the parser is still
   running* and sees the page count advance. A progress field written only on
   completion would pass a weaker test and fix nothing.
4. **It only moves forward**, so a fallback (the text layer came up short, run
   OCR) and a late report cannot make the screen say an earlier phase again.
5. **A percent is reported only where something measured it.** The tempting
   alternative — mapping stages onto invented numbers so the bar always moves —
   is a claim about remaining time that nothing measured.
6. **A retry starts its progress over**, or the second attempt would inherit
   `done` from the first, be unable to report anything (progress only moves
   forward), and look finished the instant it was queued.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from app.models.extraction_run import ExtractionRun
from app.models.organization import Organization
from app.schemas.invoice import InvoiceCreate, ParsedInvoiceDraft
from app.services import access, capture_progress, documents, extraction

pytestmark = pytest.mark.asyncio

CSV_HEADER = b"description,category,quantity,unit_price,tax_rate,vendor,invoice_number,issue_date\n"


def _csv(marker: str) -> bytes:
    """A distinct, genuinely parseable CSV per file — distinct bytes matter, or
    the duplicate advisory would refuse the second file of every batch."""
    return (
        CSV_HEADER
        + f"Diesel {marker},fuel,1,100.00,21,Fuel Depot,INV-{marker},2026-07-01\n".encode()
    )


def _part(name: str, body: bytes, content_type: str = "text/csv") -> tuple:
    return ("files", (name, body, content_type))


# --------------------------------------------------------------------------- #
# X1 — the batch door
# --------------------------------------------------------------------------- #


async def test_n_files_become_n_runs(auth_client, db_session):
    r = await auth_client.post(
        "/api/v1/invoices/upload/batch",
        files=[_part("a.csv", _csv("A")), _part("b.csv", _csv("B")), _part("c.csv", _csv("C"))],
    )
    assert r.status_code == 202, r.text
    body = r.json()

    assert (body["accepted"], body["rejected"]) == (3, 0)
    # Outcomes come back in the order the files were sent — the caller lines
    # them up against their own list without matching on anything.
    assert [o["filename"] for o in body["outcomes"]] == ["a.csv", "b.csv", "c.csv"]
    ids = [o["extraction_run_id"] for o in body["outcomes"]]
    assert len(set(ids)) == 3 and all(ids)

    rows = list(await db_session.scalars(select(ExtractionRun).where(ExtractionRun.id.in_(ids))))
    assert len(rows) == 3
    assert {row.status for row in rows} == {"queued"}


async def test_one_bad_file_does_not_take_the_good_ones_down(auth_client, db_session):
    """The defect a request-level failure would create: eight accepted captures
    discarded to report the ninth."""
    r = await auth_client.post(
        "/api/v1/invoices/upload/batch",
        files=[
            _part("good-1.csv", _csv("G1")),
            _part("payload.exe", b"MZ\x90\x00 not an invoice", "application/octet-stream"),
            _part("good-2.csv", _csv("G2")),
        ],
    )
    assert r.status_code == 202, r.text
    body = r.json()

    assert (body["accepted"], body["rejected"]) == (2, 1)
    bad = body["outcomes"][1]
    assert bad["filename"] == "payload.exe"
    assert bad["accepted"] is False
    assert bad["code"] == "unsupported_file"
    assert bad["extraction_run_id"] is None
    # The refusal explains itself; the caller does not have to guess from a code.
    assert bad["message"]
    # …and the two good files really were admitted, not merely counted.
    good_ids = [body["outcomes"][0]["extraction_run_id"], body["outcomes"][2]["extraction_run_id"]]
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ExtractionRun).where(ExtractionRun.id.in_(good_ids))
        )
        == 2
    )


async def test_the_quota_counts_documents_not_requests(auth_client, db_session):
    """THE property WO-X exists to protect.

    A plan with two uploads left, four files in one request: two are admitted
    and two are refused. Checking the quota once for the request — the shape a
    batch endpoint falls into by default — would admit all four and hand the
    workspace a free ride proportional to how many files it attaches."""
    await access.set_limits(db_session, "trial", invoice_limit=100, upload_limit=2)

    r = await auth_client.post(
        "/api/v1/invoices/upload/batch",
        files=[_part(f"{n}.csv", _csv(n)) for n in ("Q1", "Q2", "Q3", "Q4")],
    )
    assert r.status_code == 202, r.text
    body = r.json()

    assert (body["accepted"], body["rejected"]) == (2, 2)
    assert [o["accepted"] for o in body["outcomes"]] == [True, True, False, False]
    assert {o["code"] for o in body["outcomes"][2:]} == {"upload_quota_reached"}
    # Refused means nothing was stored or queued for those two, not stored and
    # then forgotten.
    org_id = await db_session.scalar(select(Organization.id))
    assert (
        await db_session.scalar(
            select(func.count()).select_from(ExtractionRun).where(ExtractionRun.org_id == org_id)
        )
        == 2
    )


async def test_a_duplicate_inside_a_batch_is_an_outcome_not_an_error(auth_client):
    """Someone drops the same scan twice into one selection. The advisory that
    already exists per file keeps working, and says so per file."""
    same = _csv("DUP")
    r = await auth_client.post(
        "/api/v1/invoices/upload/batch",
        files=[_part("first.csv", same), _part("second.csv", same)],
    )
    assert r.status_code == 202, r.text
    body = r.json()

    assert (body["accepted"], body["rejected"]) == (1, 1)
    assert body["outcomes"][1]["code"] == "duplicate_upload"
    # The server's own sentence, not a re-wording: one refusal, one voice.
    assert "uploaded this file" in body["outcomes"][1]["message"]


async def test_override_admits_the_repeat(auth_client):
    same = _csv("OVR")
    await auth_client.post("/api/v1/invoices/upload", files={"file": ("x.csv", same, "text/csv")})
    r = await auth_client.post(
        "/api/v1/invoices/upload/batch?override=true", files=[_part("again.csv", same)]
    )
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1


async def test_a_batch_is_capped_and_never_empty(auth_client):
    too_many = [_part(f"{i}.csv", _csv(f"N{i}")) for i in range(30)]
    over = await auth_client.post("/api/v1/invoices/upload/batch", files=too_many)
    assert over.status_code == 422
    assert "25" in over.json()["detail"]

    # No files at all is a malformed request, not a batch of zero — FastAPI's own
    # validation refuses it before the route runs.
    empty = await auth_client.post("/api/v1/invoices/upload/batch", data={})
    assert empty.status_code == 422


async def test_every_accepted_file_queued_its_own_parse(auth_client, db_session):
    """N runs is not enough: N parses have to be queued, or the captures sit in
    `queued` for ever looking like a slow worker."""
    from app.models.job import Job

    r = await auth_client.post(
        "/api/v1/invoices/upload/batch",
        files=[_part("j1.csv", _csv("J1")), _part("j2.csv", _csv("J2"))],
    )
    ids = {o["extraction_run_id"] for o in r.json()["outcomes"]}

    queued = list(
        await db_session.scalars(select(Job).where(Job.kind == extraction.UPLOAD_EXTRACT_KIND))
    )
    assert {json.loads(j.payload_json)["run_id"] for j in queued} == ids


async def test_the_single_file_door_still_behaves_exactly_as_it_did(auth_client):
    """`/upload` now delegates to the shared admission sequence. Its contract —
    202 with a run id, 415 on a refused type — must be unchanged, because a
    refactor that quietly moves a status code breaks every existing caller."""
    ok = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("s.csv", _csv("S1"), "text/csv")}
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["extraction_run_id"]

    bad = await auth_client.post(
        "/api/v1/invoices/upload",
        files={"file": ("s.exe", b"MZ nope", "application/octet-stream")},
    )
    assert bad.status_code == 415


# --------------------------------------------------------------------------- #
# X2 — honest progress
# --------------------------------------------------------------------------- #


async def test_a_queued_capture_says_queued_and_claims_no_percent(auth_client):
    r = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("p.csv", _csv("P1"), "text/csv")}
    )
    run_id = r.json()["extraction_run_id"]

    poll = await auth_client.get(f"/api/v1/invoices/upload/{run_id}")
    assert poll.status_code == 200, poll.text
    body = poll.json()
    assert body["stage"] == capture_progress.QUEUED
    assert body["pages_done"] == 0
    assert body["pages_total"] is None
    # Nothing has measured anything yet, so there is no number to show.
    assert body["percent"] is None


def _fake_draft() -> ParsedInvoiceDraft:
    return ParsedInvoiceDraft(
        draft=InvoiceCreate(
            vendor_name="Fuel Depot",
            invoice_number="SCAN-1",
            issue_date=date(2026, 7, 1),
            line_items=[],
        ),
        method="ocr",
    )


async def test_a_long_capture_reports_advancing_pages_while_it_runs(
    auth_client, db_session, _db, monkeypatch
):
    """THE contract test. A client polling mid-parse must see the count move.

    The parser is replaced with one that reports three pages and waits for this
    test between each, so the assertions are handshakes rather than sleeps. It
    runs through the REAL path: `extract_upload` installs the real sink, the
    context is really copied into the worker thread, and the reads happen on a
    SEPARATE session — a value that only became visible at the end, or only
    inside the parser's own transaction, fails here.
    """
    reported = threading.Event()
    resume = threading.Event()

    def fake_parse(filename: str, content: bytes):
        for page in (1, 2, 3):
            capture_progress.report(capture_progress.OCR, pages_done=page, pages_total=3)
            reported.set()
            resume.wait()
            resume.clear()
        return _fake_draft()

    monkeypatch.setattr("app.services.parser.parse_invoice_file", fake_parse)

    org_id = await db_session.scalar(select(Organization.id))
    content = b"%PDF-1.4 pretend scan"
    sha = extraction.sha256_hex(content)
    await documents.store(
        documents.UPLOADS, org_id, content, "application/pdf", db=db_session, filename="scan.pdf"
    )
    run = await extraction.start_capture(db_session, org_id, filename="scan.pdf", sha256=sha)
    await db_session.commit()

    parse = asyncio.create_task(extraction.extract_upload(db_session, run.id))

    seen: list[tuple[str | None, int, int | None]] = []
    async with _db() as observer:
        for _ in range(3):
            await asyncio.to_thread(reported.wait)
            reported.clear()
            row = await observer.get(ExtractionRun, run.id)
            await observer.refresh(row)
            seen.append((row.stage, row.pages_done, row.pages_total))
            resume.set()
        await parse

    # Three distinct, ADVANCING observations — not one summary written at the end.
    assert seen == [
        (capture_progress.OCR, 1, 3),
        (capture_progress.OCR, 2, 3),
        (capture_progress.OCR, 3, 3),
    ]

    poll = await auth_client.get(f"/api/v1/invoices/upload/{run.id}")
    assert poll.json()["stage"] == capture_progress.DONE
    assert poll.json()["percent"] == 100


async def test_progress_never_moves_backwards(auth_client, db_session, _db):
    """A parser that falls back — the text layer came up short, so OCR runs —
    reports READING after OCR has already been reported on a retry-ish path. The
    screen must not travel back a phase."""
    org_id = await db_session.scalar(select(Organization.id))
    run = await extraction.start_capture(db_session, org_id, filename="x.pdf", sha256="a" * 64)
    await db_session.commit()

    sink = extraction._progress_sink(db_session, run)
    token = capture_progress.install(sink)
    try:
        await run_in_threadpool(
            lambda: (
                capture_progress.report(capture_progress.OCR, pages_done=2, pages_total=4),
                capture_progress.report(capture_progress.READING),
            )
        )
    finally:
        capture_progress.uninstall(token)

    async with _db() as observer:
        row = await observer.get(ExtractionRun, run.id)
        await observer.refresh(row)
    assert row.stage == capture_progress.OCR
    assert row.pages_done == 2


async def test_a_retry_starts_its_progress_over(auth_client, db_session):
    """A second attempt inheriting `done` from the first could never report a
    phase again — it would look finished the instant it was queued."""
    r = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("r.csv", _csv("R1"), "text/csv")}
    )
    run_id = r.json()["extraction_run_id"]
    run = await db_session.get(ExtractionRun, run_id)
    run.status = "failed"
    run.stage = capture_progress.DONE
    run.pages_done = 7
    run.pages_total = 7
    await db_session.commit()

    again = await auth_client.post(f"/api/v1/invoices/upload/{run_id}/retry")
    assert again.status_code == 202, again.text

    await db_session.refresh(run)
    assert run.stage == capture_progress.QUEUED
    assert (run.pages_done, run.pages_total) == (0, None)


# --------------------------------------------------------------------------- #
# The vocabulary itself
# --------------------------------------------------------------------------- #


async def test_percent_is_reported_only_where_something_measured_it():
    p = capture_progress.percent
    # Measured: OCR with a known page count.
    assert p(capture_progress.OCR, 10, 40, status="queued") == 25
    # Not measured: a phase with no divisible unit of work.
    assert p(capture_progress.READING, 0, None, status="queued") is None
    assert p(capture_progress.INTERPRETING, 0, None, status="queued") is None
    assert p(capture_progress.QUEUED, 0, None, status="queued") is None
    # A run from before the contract reports no stage and therefore no number.
    assert p(None, 0, None, status="queued") is None
    # 100 means finished, and only finished: the last page of a 40-page scan is
    # still not a saved draft.
    assert p(capture_progress.OCR, 40, 40, status="queued") == 99
    assert p(capture_progress.DONE, 40, 40, status="parsed") == 100
    # A failure stopped; how far it got is not progress toward anything.
    assert p(capture_progress.OCR, 12, 40, status="failed") is None


async def test_the_stage_order_is_the_forward_rule():
    fwd = capture_progress.is_forward
    assert fwd(capture_progress.READING, capture_progress.OCR)
    assert fwd(capture_progress.OCR, capture_progress.OCR)  # same phase, new page
    assert not fwd(capture_progress.OCR, capture_progress.READING)
    assert not fwd(capture_progress.DONE, capture_progress.INTERPRETING)
    # Nothing to contradict on a run that predates the contract.
    assert fwd(None, capture_progress.OCR)


async def test_a_report_with_no_sink_installed_is_a_no_op():
    """Every other caller of the parser — email intake, the synchronous paths —
    runs with no sink. A parse must not depend on one existing."""
    capture_progress.report(capture_progress.OCR, pages_done=1, pages_total=2)


async def test_a_sink_that_fails_cannot_fail_the_parse():
    """A document that was read correctly but whose progress row could not be
    written is a SUCCESSFUL capture. Trading a real outcome for a cosmetic one
    would be the worst possible ranking of the two."""

    def broken(stage, done, total):
        raise RuntimeError("the progress row is unreachable")

    token = capture_progress.install(broken)
    try:
        capture_progress.report(capture_progress.OCR, pages_done=1, pages_total=2)
    finally:
        capture_progress.uninstall(token)


async def test_the_screen_has_a_name_for_every_stage_the_server_can_report():
    """A drift gate, not a style check.

    The server sends codes; the SPA owns the wording. A stage added on one side
    only would reach the operator as a raw identifier — the same class of
    silent expiry as a tenancy exemption nobody revisits. This recomputes the
    coverage from both live sources instead of asserting it in prose.
    """
    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "captureStages.ts"
    assert src.exists(), f"the SPA's stage labels moved: {src}"
    text = src.read_text()
    literal = re.search(r"CAPTURE_STAGE_LABELS = \{(.*?)\} as const;", text, re.DOTALL)
    assert literal, "the SPA's stage-label object changed shape"
    named = set(re.findall(r"^\s+(\w+):", literal.group(1), re.MULTILINE))
    assert set(capture_progress.STAGES) <= named, (
        f"stages the screen cannot name: {sorted(set(capture_progress.STAGES) - named)}"
    )
    # …and nothing the screen names that the server cannot send.
    assert named <= set(capture_progress.STAGES), (
        f"labels for stages that do not exist: {sorted(named - set(capture_progress.STAGES))}"
    )


async def test_the_batch_cap_is_stated_once():
    """The number in the refusal message is the constant, so the sentence cannot
    drift from the rule."""
    from app.api.routes.invoices import MAX_BATCH_FILES

    assert MAX_BATCH_FILES == 25


async def test_a_batch_run_belongs_to_the_caller_and_nobody_else(auth_client, db_session):
    org_id = await db_session.scalar(select(Organization.id))
    r = await auth_client.post(
        "/api/v1/invoices/upload/batch", files=[_part("t.csv", _csv(uuid.uuid4().hex[:6]))]
    )
    run_id = r.json()["outcomes"][0]["extraction_run_id"]
    row = await db_session.get(ExtractionRun, run_id)
    assert row.org_id == org_id
