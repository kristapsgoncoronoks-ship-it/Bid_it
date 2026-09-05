"""WO-AF — statement-byte vaulting: a finding points at a file that exists.

Before this slice `statement_ingest` digested the upload and WO-Z keyed every
review finding, the extraction baseline and the audit trail on that digest —
and the bytes themselves were never stored (`documents.store` was absent from
the path). "Line 4: net must be greater than zero" named a line in a file
nobody could open.

What must hold now:

- a REGISTERED upload vaults the original through the one choke point
  (`documents.store`, prefix `statements`), catalogues it in the document
  registry under the tenant, and serves it back byte-identical, inert, audited;
- a REFUSED upload vaults it too — the refusal is exactly when the operator
  needs the file — and the finding says the file is available;
- re-uploading the same bytes leaves ONE catalog row (content-addressed);
- another tenant's digest, a malformed digest and a digest never vaulted (a
  finding from before WO-AF) are all opaque 404s — the catalog is the gate, the
  object store is never read on a guess;
- `VAT_READ` suffices to download, as it does to read the queue.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.document import Document
from app.models.transport.statement_finding import VatStatementFinding
from app.services import documents, modules
from tests.factories.transport import synthetic_eurowag_statement
from tests.transport.conftest import make_entity
from tests.transport.test_wo_s_statement_routes import (
    _member_with_role,
    _register_org,
    _upload,
)
from tests.transport.test_wo_z_statement_review_queue import _statement_with_a_zero_net

V = "/api/v1"
PATH = f"{V}/transport/statements"

pytestmark = pytest.mark.asyncio


async def _setup(client: AsyncClient, db_session):
    headers, org_id = await _register_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    return headers, org_id, entity


async def _catalog_rows(db_session, org_id: str) -> list[Document]:
    return list(
        await db_session.scalars(
            select(Document).where(Document.org_id == org_id, Document.kind == documents.STATEMENTS)
        )
    )


async def test_a_registered_statement_is_vaulted_catalogued_and_served_back(client, db_session):
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=7).encode("utf-8")
    sha = hashlib.sha256(content).hexdigest()

    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(content, filename="eurowag-2026-06.csv"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["statement_sha256"] == sha

    # The catalog knows the file, under its own kind, with the name it came in as.
    rows = await _catalog_rows(db_session, org_id)
    assert [(d.sha256, d.filename, d.size, d.mime) for d in rows] == [
        (sha, "eurowag-2026-06.csv", len(content), "text/csv")
    ]
    # And the bytes are really there, under the one choke point's key.
    assert await documents.load(documents.STATEMENTS, org_id, sha) == content

    got = await client.get(f"{PATH}/{sha}/file", headers=headers)
    assert got.status_code == 200, got.text
    assert got.content == content
    assert got.headers["content-type"].startswith("application/octet-stream")
    assert got.headers["content-disposition"] == 'attachment; filename="eurowag-2026-06.csv"'
    assert got.headers["x-content-type-options"] == "nosniff"

    # Audited as a document download, naming the statement.
    ev = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "document.download",
            AuditEvent.target_id == sha,
        )
    )
    assert ev is not None
    assert ev.target_type == "fuel_statement"


async def test_a_refused_statement_is_vaulted_too_and_its_finding_offers_the_file(
    client, db_session
):
    headers, org_id, entity = await _setup(client, db_session)
    # WO-Z's refused fixture: a zero net trips the capture gate's `net_positive`
    # rule, so nothing registers.
    content = _statement_with_a_zero_net()
    sha = hashlib.sha256(content).hexdigest()

    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(content, filename="eurowag-2026-06-bad.csv"),
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "capture_review_blocked"

    # The refusal rolled the ingest back — but the file is on file, catalogued
    # in the transaction the refusal branch committed.
    rows = await _catalog_rows(db_session, org_id)
    assert [d.sha256 for d in rows] == [sha]
    finding = await db_session.scalar(
        select(VatStatementFinding).where(
            VatStatementFinding.org_id == org_id, VatStatementFinding.statement_sha256 == sha
        )
    )
    assert finding is not None and finding.outcome == "refused"

    queue = await client.get(f"{PATH}/findings", headers=headers)
    assert queue.status_code == 200
    mine = [f for f in queue.json()["findings"] if f["statement_sha256"] == sha]
    assert mine and all(f["file_available"] is True for f in mine)

    got = await client.get(f"{PATH}/{sha}/file", headers=headers)
    assert got.status_code == 200
    assert got.content == content


async def test_re_uploading_the_same_bytes_keeps_one_catalog_row(client, db_session):
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=11).encode("utf-8")
    for _ in range(2):
        r = await client.post(
            PATH,
            headers=headers,
            data={"entity_id": entity.id, "period": "2026-06"},
            files=_upload(content),
        )
        assert r.status_code == 200, r.text
    assert len(await _catalog_rows(db_session, org_id)) == 1


async def test_a_finding_from_before_vaulting_does_not_promise_a_file(client, db_session):
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=5).encode("utf-8")
    sha = hashlib.sha256(content).hexdigest()
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(content),
    )
    assert r.status_code == 200, r.text
    # Simulate a pre-WO-AF world: the finding exists, the catalog row does not.
    for row in await _catalog_rows(db_session, org_id):
        await db_session.delete(row)
    await db_session.commit()

    queue = await client.get(f"{PATH}/findings", headers=headers, params={"status_filter": "open"})
    for f in queue.json()["findings"]:
        if f["statement_sha256"] == sha:
            assert f["file_available"] is False
    got = await client.get(f"{PATH}/{sha}/file", headers=headers)
    assert got.status_code == 404
    assert got.json()["code"] == "statement_not_found"


async def test_another_tenants_statement_and_a_malformed_digest_are_opaque_404s(client, db_session):
    headers_a, org_a, entity_a = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=13).encode("utf-8")
    sha = hashlib.sha256(content).hexdigest()
    r = await client.post(
        PATH,
        headers=headers_a,
        data={"entity_id": entity_a.id, "period": "2026-06"},
        files=_upload(content),
    )
    assert r.status_code == 200, r.text

    headers_b, _org_b = await _register_org(client)
    foreign = await client.get(f"{PATH}/{sha}/file", headers=headers_b)
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["code"] == "statement_not_found"

    for bad in ("not-a-digest", "A" * 64, uuid.uuid4().hex):
        got = await client.get(f"{PATH}/{bad}/file", headers=headers_a)
        assert got.status_code == 404, (bad, got.text)


async def test_read_permission_is_enough_to_download(client, db_session):
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=17).encode("utf-8")
    sha = hashlib.sha256(content).hexdigest()
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(content),
    )
    assert r.status_code == 200, r.text

    # READ_ONLY holds VAT_READ and nothing that writes.
    reader = await _member_with_role(client, headers, db_session, "user_free")
    got = await client.get(f"{PATH}/{sha}/file", headers=reader)
    assert got.status_code == 200, got.text
    assert got.content == content
    # EMPLOYEE holds no VAT permission at all → 403 at the router.
    employee = await _member_with_role(client, headers, db_session, "user")
    assert (await client.get(f"{PATH}/{sha}/file", headers=employee)).status_code == 403
