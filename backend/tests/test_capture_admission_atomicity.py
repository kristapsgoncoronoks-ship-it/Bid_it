"""Failure after registry/capture/job/meter writes must roll back the whole file."""

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.extraction_run import ExtractionRun
from app.models.job import Job
from app.models.organization import Organization
from app.models.usage import UsageCounter
from app.services import access, extraction, jobs

CSV = b"description,quantity,unit_price,tax_rate\nSynthetic atomic admission,1,100,21\n"


class AdmissionFault(RuntimeError):
    pass


async def counts(db):
    return [
        await db.scalar(select(func.count()).select_from(model))
        for model in (Document, ExtractionRun, Job, UsageCounter)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_at", ["queue", "meter"])
async def test_admission_failure_after_native_write_rolls_back_every_fact(
    auth_client, db_session, monkeypatch, fault_at
):
    original = jobs.enqueue if fault_at == "queue" else access.record_usage

    async def fail_after_write(*args, **kwargs):
        await original(*args, **kwargs)
        raise AdmissionFault("Synthetic failure after native database write")

    owner, method = (jobs, "enqueue") if fault_at == "queue" else (access, "record_usage")
    monkeypatch.setattr(owner, method, fail_after_write)
    with pytest.raises(AdmissionFault):
        await auth_client.post(
            "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
        )
    assert await counts(db_session) == [0, 0, 0, 0]
    monkeypatch.setattr(owner, method, original)
    retry = await auth_client.post(
        "/api/v1/invoices/upload", files={"file": ("source.csv", CSV, "text/csv")}
    )
    assert retry.status_code == 202, retry.text
    assert await counts(db_session) == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_failed_batch_file_does_not_erase_an_earlier_accepted_file(
    auth_client, db_session, monkeypatch
):
    original = access.record_usage
    calls = 0

    async def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        await original(*args, **kwargs)
        if calls == 2:
            raise AdmissionFault("Synthetic second-file metering failure")

    monkeypatch.setattr(access, "record_usage", fail_second)
    with pytest.raises(AdmissionFault):
        await auth_client.post(
            "/api/v1/invoices/upload/batch",
            files=[
                ("files", ("first.csv", CSV, "text/csv")),
                ("files", ("second.csv", CSV + b"\n", "text/csv")),
            ],
        )
    assert await counts(db_session) == [1, 1, 1, 1]
    assert await db_session.scalar(select(UsageCounter.count)) == 1
    run = await db_session.scalar(select(ExtractionRun))
    assert run.source_filename == "first.csv"


@pytest.mark.asyncio
async def test_service_callers_can_participate_without_committing(auth_client, db_session):
    org_id = await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))
    run = await extraction.start_capture(
        db_session, org_id, filename="source.csv", sha256="a" * 64, commit=False
    )
    run_id = run.id
    assert run_id
    await access.record_usage(db_session, org_id, "upload", commit=False)
    await db_session.rollback()
    assert await counts(db_session) == [0, 0, 0, 0]
