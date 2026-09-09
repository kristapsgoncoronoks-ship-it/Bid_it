"""PROD-009 — the whole-workspace data export, and the purge of what it leaves.

The audit's finding: a customer could put everything into this platform and had
no way to take it out. The only export that existed read ONE table, for a
client who had already left (WO-AI). This suite holds the three properties that
make the new one worth having:

* **Complete by construction.** The export's table set IS the tenant registry
  plus the two child tables that carry no `org_id`, asserted in both
  directions. A hand-written list would pass on the day it was written and rot
  silently; this fails the moment a table joins the product without joining the
  export.
* **Credentials do not leave.** Not "the code redacts them" — the produced ZIP
  BYTES are searched for a planted password hash, invite token, feed token,
  webhook secret and SSO client secret. A test that only checked the redaction
  helper would stay green if a second write path appeared.
* **The bytes do not outlive the link.** Found while building this: nothing had
  ever deleted a produced export. The `exports` class only grew.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from app.core.tenant import TENANT_MODELS
from app.models.archive_export import ArchiveExport
from app.models.base import Base
from app.models.email_token import PURPOSE_WORKSPACE_EXPORT
from app.models.user import User
from app.models.workspace_export import WorkspaceExport
from app.services import documents, export_artefacts, workspace_export


async def _org_id(db_session, auth_client) -> str:
    me = await auth_client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    return me.json()["organization"]["id"]


async def _seed_some_data(auth_client) -> None:
    inv = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Northwind Components OU",
            "invoice_number": "INV-EXPORT-1",
            "issue_date": "2026-09-01",
            "currency": "EUR",
            "status": "pending",
            "line_items": [
                {
                    "description": "Bearing set",
                    "category": "parts",
                    "quantity": "2",
                    "unit_price": "50.00",
                    "tax_rate": "21",
                }
            ],
        },
    )
    assert inv.status_code == 201, inv.text


async def _bytes_gone(org_id: str, sha: str) -> bool:
    """`documents.load` raises for a key that is not there, which is what a
    destroyed export looks like from the outside."""
    from app.core.storage import StorageError

    try:
        return await documents.load(documents.EXPORTS, org_id, sha) is None
    except StorageError:
        return True


def _zip_of(path: str) -> zipfile.ZipFile:
    with open(path, "rb") as fh:
        return zipfile.ZipFile(io.BytesIO(fh.read()))


# --------------------------------------------------------------------------- #
# Complete by construction
# --------------------------------------------------------------------------- #


def test_the_export_covers_every_tenant_table_in_the_mapped_schema():
    """The export's table set is EVERY table that holds tenant data.

    Derived independently of the export, from the mapper registry — the same
    source `test_tenant_registration.py` uses — because the first version of
    this test compared `_tenant_models()` against `TENANT_MODELS | CHILD_MODELS`,
    which is that function's own definition: it asserted A == A and no change to
    the product could turn it red (PROD-009 review, QA-B).

    Both directions hold: a mapped class carrying `org_id` that the export
    misses fails here, and so does a table the export names that carries no
    tenant data and has no declared reason to be in it."""
    exported = {m.__tablename__ for m in workspace_export._tenant_models()}
    org_scoped = {
        mapper.class_.__tablename__
        for mapper in Base.registry.mappers
        if "org_id" in mapper.columns.keys()
    }
    #: The two tables with no `org_id`, reached through an already-scoped
    #: parent. Named here, so adding a third to the export needs a decision.
    children = {"line_items", "issued_invoice_lines"}
    assert {m.__tablename__ for m in workspace_export.CHILD_MODELS} == children
    assert exported == org_scoped | children, {
        "missing from the export": sorted(org_scoped - exported),
        "in the export but not tenant data": sorted(exported - org_scoped - children),
    }


@pytest.mark.asyncio
async def test_every_registered_table_gets_a_file_even_when_empty(auth_client, db_session):
    """An empty table is an empty file, never a missing one: a reader must be
    able to tell 'this workspace had none' from 'this export forgot it'."""
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    path, summary = await workspace_export.build_zip_file(db_session, org_id)
    try:
        zf = _zip_of(path)
        names = set(zf.namelist())
    finally:
        import os

        os.unlink(path)
    for model in workspace_export._tenant_models():
        assert f"data/{model.__tablename__}.jsonl" in names, model.__tablename__
    assert summary["table_count"] == len(workspace_export._tenant_models())
    assert summary["row_count"] > 0


@pytest.mark.asyncio
async def test_the_export_carries_the_rows_and_keeps_money_exact(auth_client, db_session):
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    path, _ = await workspace_export.build_zip_file(db_session, org_id)
    try:
        zf = _zip_of(path)
        invoices = [json.loads(x) for x in zf.read("data/invoices.jsonl").decode().splitlines()]
        lines = [json.loads(x) for x in zf.read("data/line_items.jsonl").decode().splitlines()]
        manifest = json.loads(zf.read("manifest.json"))
        readme = zf.read("README.txt").decode()
    finally:
        import os

        os.unlink(path)
    assert [i["invoice_number"] for i in invoices] == ["INV-EXPORT-1"]
    # A string, not a float: 121.00 must survive the trip out unrounded.
    assert invoices[0]["total"] == "121.00" and isinstance(invoices[0]["total"], str)
    assert [ln["description"] for ln in lines] == ["Bearing set"]
    assert manifest["workspace_id"] == org_id
    assert manifest["tables"]["invoices"] == 1
    assert manifest["includes_deleted_rows"] is True
    assert "data/<table>.jsonl" in readme


@pytest.mark.asyncio
async def test_binned_rows_are_exported_and_say_they_are_binned(auth_client, db_session):
    """A record in the recycle bin is still the workspace's data. It goes, with
    its `deleted_at`, so the reader can tell."""
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    listed = await auth_client.get("/api/v1/invoices?page=1&page_size=10")
    invoice_id = listed.json()["items"][0]["id"]
    gone = await auth_client.delete(f"/api/v1/invoices/{invoice_id}")
    assert gone.status_code in (200, 204), gone.text
    db_session.expire_all()

    path, _ = await workspace_export.build_zip_file(db_session, org_id)
    try:
        zf = _zip_of(path)
        invoices = [json.loads(x) for x in zf.read("data/invoices.jsonl").decode().splitlines()]
    finally:
        import os

        os.unlink(path)
    assert len(invoices) == 1, "a binned invoice fell out of the export"
    assert invoices[0]["deleted_at"], "the export does not say the row was binned"


# --------------------------------------------------------------------------- #
# Credentials do not leave
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_planted_credential_appears_anywhere_in_the_zip(auth_client, db_session):
    """The strong form: plant a distinctive value in every credential column
    the workspace can hold, build the export, and search the BYTES. This stays
    honest if someone adds a second way to write a row into the file."""
    org_id = await _org_id(db_session, auth_client)
    planted = {
        "PLANTEDPASSWORDHASH": ("users", "hashed_password"),
        "PLANTEDINVITETOKEN": ("invitations", "token"),
        "PLANTEDFEEDTOKEN": ("calendar_feed_tokens", "token"),
        "PLANTEDWEBHOOKSECRET": ("webhook_endpoints", "secret"),
        "PLANTEDSSOSECRET": ("sso_connections", "client_secret"),
        "PLANTEDINTAKETOKEN": ("email_intakes", "token"),
    }
    from app.models.calendar_token import CalendarFeedToken
    from app.models.email_intake import EmailIntake
    from app.models.invitation import Invitation
    from app.models.sso import SsoConnection
    from app.models.webhook import WebhookEndpoint

    user = await db_session.scalar(select(User).where(User.org_id == org_id))
    assert user is not None
    user.hashed_password = "PLANTEDPASSWORDHASH"
    db_session.add_all(
        [
            Invitation(
                org_id=org_id,
                email="new.member@northwind-components.example",
                role="user",
                token="PLANTEDINVITETOKEN",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            ),
            CalendarFeedToken(org_id=org_id, user_id=user.id, token="PLANTEDFEEDTOKEN"),
            WebhookEndpoint(
                org_id=org_id,
                url="https://hooks.northwind-components.example/invoiceiq",
                secret="PLANTEDWEBHOOKSECRET",
                events="invoice.approved",
            ),
            SsoConnection(
                org_id=org_id,
                slug="northwind",
                protocol="oidc",
                issuer="https://id.northwind-components.example",
                client_id="invoiceiq",
                client_secret="PLANTEDSSOSECRET",
            ),
            EmailIntake(org_id=org_id, token="PLANTEDINTAKETOKEN"),
        ]
    )
    await db_session.commit()

    path, summary = await workspace_export.build_zip_file(db_session, org_id)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        zf = zipfile.ZipFile(io.BytesIO(raw))
        # Search the DECOMPRESSED member bytes, not the deflated container.
        blob = b"".join(zf.read(n) for n in zf.namelist())
    finally:
        import os

        os.unlink(path)
    for value, (table, column) in planted.items():
        assert value.encode() not in blob, f"{table}.{column} left the platform"
        assert column in summary["redacted"].get(table, []), f"{table}.{column} not declared"
    # And the rows themselves are still there — redaction, not omission.
    users = [json.loads(x) for x in zf.read("data/users.jsonl").decode().splitlines()]
    assert users and users[0]["email"] and users[0]["hashed_password"] is None


def test_every_not_secret_exception_names_a_real_column_and_a_reason():
    """The escape hatch cannot be used to wave a column through: each entry must
    point at a column that exists AND match the secret pattern (otherwise it is
    doing nothing and is misleading), and must carry a reason."""
    by_table = {
        m.__tablename__: m for m in list(TENANT_MODELS) + list(workspace_export.CHILD_MODELS)
    }
    for (table, column), reason in workspace_export._NOT_SECRET.items():
        model = by_table.get(table)
        assert model is not None, f"{table} is not an exported table"
        assert column in {c.key for c in sa_inspect(model).columns}, f"{table}.{column} is gone"
        assert workspace_export._SECRET_NAME.search(column), (
            f"{table}.{column} does not match the pattern — the exception is dead weight"
        )
        assert reason and len(reason) > 20, f"{table}.{column} has no real reason"


@pytest.mark.asyncio
async def test_a_secret_inside_free_text_does_not_leave_either(auth_client, db_session):
    """The case the column-name rule cannot see, and the review panel found.

    `email_messages.body` stores every message the workspace has sent, and
    several of those bodies ARE live credentials — a password-reset link, an
    email-verification link, an INVITATION link good for fourteen days. The
    invitation's own `invitations.token` column is redacted; without this the
    same token walked out two files away, in prose."""
    from app.models.email_message import EmailMessage

    org_id = await _org_id(db_session, auth_client)
    db_session.add(
        EmailMessage(
            org_id=org_id,
            kind="invitation",
            to_email="new.member@northwind-components.example",
            subject="You have been invited",
            body="Accept: https://app.example/accept-invite?token=PLANTEDBODYTOKEN",
            status="sent",
        )
    )
    await db_session.commit()

    path, summary = await workspace_export.build_zip_file(db_session, org_id)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        zf = zipfile.ZipFile(io.BytesIO(raw))
        blob = b"".join(zf.read(n) for n in zf.namelist())
    finally:
        import os

        os.unlink(path)
    assert b"PLANTEDBODYTOKEN" not in blob, "a live invitation link left in a message body"
    assert "body" in summary["redacted"].get("email_messages", [])
    # The row itself still goes: what was sent, to whom, when — only the text
    # that can carry a credential is held back.
    rows = [json.loads(x) for x in zf.read("data/email_messages.jsonl").decode().splitlines()]
    invite = [r for r in rows if r["kind"] == "invitation"]
    assert len(invite) == 1
    assert invite[0]["to_email"] == "new.member@northwind-components.example"
    assert invite[0]["subject"] == "You have been invited"
    assert invite[0]["body"] is None
    # And every other message in the outbox too — the registration's own
    # verification email carries a live 24-hour link.
    assert all(r["body"] is None for r in rows)


@pytest.mark.asyncio
async def test_an_export_does_not_contain_the_previous_exports(
    auth_client, db_session, monkeypatch
):
    """Every produced zip is registered as a document like any other, so
    without an exclusion export #2 embeds export #1 and #3 embeds #2: the
    artefact grows geometrically until it trips its own ceiling, and it carries
    forward rows that erasure or retention has since removed."""
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)

    async def fake_send(db, org, *, kind, to_email, subject, body):
        return None

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    first = await auth_client.post("/api/v1/workspace/export")
    out1 = await workspace_export.run_export(db_session, org_id, first.json()["id"])
    await db_session.commit()
    assert out1["status"] == "ready"

    path, summary = await workspace_export.build_zip_file(db_session, org_id)
    try:
        zf = _zip_of(path)
        names = zf.namelist()
    finally:
        import os

        os.unlink(path)
    nested = [n for n in names if n.startswith(f"{workspace_export.DOCS_DIR}/exports/")]
    assert nested == [], f"the export embedded a previous export: {nested}"
    index = json.loads(zf.read(workspace_export.DOCS_INDEX))
    assert not [e for e in index if e["kind"] == "exports"]
    assert summary["missing_documents"] == 0


@pytest.mark.asyncio
async def test_a_member_of_two_workspaces_is_still_in_this_ones_roster(
    auth_client, client, db_session
):
    """`users.org_id` is the ACTIVE-org pointer, not the roster. Filtering the
    users table by it dropped any member currently signed in to a different
    workspace — from the file whose whole purpose is completeness, while
    `memberships.jsonl` still listed them."""
    org_id = await _org_id(db_session, auth_client)
    email = "two.hats@northwind-components.example"
    await _member(auth_client, client, email, "user")
    # Their active org moves elsewhere — a second, real workspace of their own.
    from app.models.organization import Organization

    other = Organization(name="Southgate Beta OU")
    db_session.add(other)
    await db_session.flush()
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.org_id = other.id
    await db_session.commit()

    path, _ = await workspace_export.build_zip_file(db_session, org_id)
    try:
        zf = _zip_of(path)
        users = [json.loads(x) for x in zf.read("data/users.jsonl").decode().splitlines()]
        members = [json.loads(x) for x in zf.read("data/memberships.jsonl").decode().splitlines()]
    finally:
        import os

        os.unlink(path)
    assert email in [u["email"] for u in users], "a member fell out of the roster"
    assert len(users) == len(members), "users and memberships disagree about the roster"


@pytest.mark.asyncio
async def test_the_audit_chain_hashes_are_exported(auth_client, db_session):
    """The one thing that LOOKS like a secret and must go: without `hash` and
    `prev_hash` nobody could verify the exported trail was not edited."""
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    path, summary = await workspace_export.build_zip_file(db_session, org_id)
    try:
        zf = _zip_of(path)
        events = [json.loads(x) for x in zf.read("data/audit_events.jsonl").decode().splitlines()]
    finally:
        import os

        os.unlink(path)
    assert events, "no audit events in the export"
    assert all(e["hash"] for e in events)
    assert "audit_events" not in summary["redacted"]


# --------------------------------------------------------------------------- #
# The doors
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_owner_asks_worker_builds_link_works_once(auth_client, db_session, monkeypatch):
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)

    asked = await auth_client.post("/api/v1/workspace/export")
    assert asked.status_code == 202, asked.text
    export_id = asked.json()["id"]
    assert asked.json()["status"] == "queued"

    sent: list[tuple[str, str]] = []

    async def fake_send(db, org, *, kind, to_email, subject, body):
        sent.append((subject, body))

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    out = await workspace_export.run_export(db_session, org_id, export_id)
    await db_session.commit()
    assert out["status"] == "ready" and out["rows"] > 0
    assert sent, "no email was sent"
    link = [w for w in sent[0][1].split() if "/workspace/export/download/" in w][0]
    raw = link.rsplit("/", 1)[1]

    got = await auth_client.get(f"/api/v1/workspace/export/download/{raw}")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "application/zip"
    assert got.headers["x-content-type-options"] == "nosniff"
    zf = zipfile.ZipFile(io.BytesIO(got.content))
    assert "manifest.json" in zf.namelist()

    again = await auth_client.get(f"/api/v1/workspace/export/download/{raw}")
    assert again.status_code == 404, "the one-time link opened twice"


async def _member(auth_client, client, email: str, role: str) -> str:
    invited = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    assert invited.status_code in (200, 201), invited.text
    accepted = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": invited.json()["token"], "name": "Member", "password": "supersecret1"},
    )
    assert accepted.status_code in (200, 201), accepted.text
    return accepted.json()["token"]["access_token"]


@pytest.mark.parametrize("role", ["admin", "user"])
@pytest.mark.asyncio
async def test_a_non_owner_cannot_ask(auth_client, client, db_session, role):
    """The whole company's data in one file, including every member's rows, so
    the owner is the person entitled to take it.

    **`admin` is the case that matters** and the reason this is parametrised: an
    admin HOLDS `settings.manage`, so the router's permission dependency lets
    them through and only the route's own owner check refuses them. A seeded
    violation proved the point — with the owner check deleted, a test that used
    only a plain member stayed GREEN, because the permission gate had refused
    that member for an unrelated reason. The plain member is kept as the second
    case: it proves the permission gate is there too."""
    org_id = await _org_id(db_session, auth_client)
    token = await _member(auth_client, client, f"{role}.person@northwind-components.example", role)
    refused = await client.post(
        "/api/v1/workspace/export", headers={"Authorization": f"Bearer {token}"}
    )
    # 403, not "403 or 404": accepting 404 would let deleting the route
    # altogether pass as a refusal (review, QA-G).
    assert refused.status_code == 403, f"{role} was allowed to export: {refused.text}"
    assert not list(
        await db_session.scalars(select(WorkspaceExport).where(WorkspaceExport.org_id == org_id))
    )


@pytest.mark.asyncio
async def test_a_second_request_inside_the_hour_reuses_the_first(auth_client, db_session):
    first = await auth_client.post("/api/v1/workspace/export")
    second = await auth_client.post("/api/v1/workspace/export")
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_the_request_list_never_carries_a_link(auth_client):
    await auth_client.post("/api/v1/workspace/export")
    listed = await auth_client.get("/api/v1/workspace/export-requests")
    assert listed.status_code == 200, listed.text
    body = json.dumps(listed.json())
    assert "/workspace/export/download/" not in body
    # No opaque high-entropy string anywhere: a token would be one, while the
    # long human sentences this row legitimately carries (the failure reason,
    # an email address) contain spaces or an @. The first version of this line
    # simply banned every string over 40 characters, which quietly made it
    # impossible for the endpoint to ever return an `error` (review, QA-E).
    for value in listed.json()[0].values():
        if isinstance(value, str) and len(value) > 24:
            assert " " in value or "@" in value or "-" in value, value


@pytest.mark.asyncio
async def test_an_export_above_the_ceiling_fails_with_its_size(
    auth_client, db_session, monkeypatch
):
    """The bound the design admits to: the finished zip is read once to store
    it, so an export bigger than the deployment can hold is REFUSED with the
    number in the message, not an out-of-memory kill that looks like a crash."""
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    asked = await auth_client.post("/api/v1/workspace/export")
    export_id = asked.json()["id"]
    monkeypatch.setattr("app.core.config.settings.workspace_export_max_bytes", 1, raising=False)
    out = await workspace_export.run_export(db_session, org_id, export_id)
    await db_session.commit()
    assert out["status"] == "failed"
    row = await db_session.get(WorkspaceExport, export_id)
    assert row is not None and "ceiling" in (row.error or "")
    assert str(1) in (row.error or ""), "the refusal does not name the ceiling it hit"
    assert row.sha256 is None, "a refused export still stored bytes"


# --------------------------------------------------------------------------- #
# The bytes do not outlive the link
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_purge_destroys_the_bytes_of_an_expired_export_and_keeps_the_row(
    auth_client, db_session, monkeypatch
):
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    asked = await auth_client.post("/api/v1/workspace/export")

    async def fake_send(db, org, *, kind, to_email, subject, body):
        return None

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    await workspace_export.run_export(db_session, org_id, asked.json()["id"])
    await db_session.commit()
    row = await db_session.get(WorkspaceExport, asked.json()["id"])
    assert row is not None and row.sha256
    sha = row.sha256
    assert await documents.load(documents.EXPORTS, org_id, sha) is not None

    # Nothing to do while the link is alive.
    assert await export_artefacts.purge_expired(db_session, org_id) == {}

    row.link_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    purged = await export_artefacts.purge_expired(db_session, org_id)
    await db_session.commit()
    assert purged == {"workspace_exports": 1}
    assert await _bytes_gone(org_id, sha)
    db_session.expire_all()
    row = await db_session.get(WorkspaceExport, asked.json()["id"])
    assert row is not None and row.purged_at is not None
    assert row.rows and row.size, "the record of the export went with its bytes"


@pytest.mark.asyncio
async def test_the_purge_answers_for_the_archive_export_too(auth_client, db_session):
    """WO-AI's zips were the ones already accumulating; the same pass covers
    both tables so there is one place that owns 'the file dies with the link'."""
    org_id = await _org_id(db_session, auth_client)
    sha, _size = await documents.store(documents.EXPORTS, org_id, b"an old archive zip")
    row = ArchiveExport(
        org_id=org_id,
        requested_email="owner@northwind-components.example",
        requested_by_user_id=(await db_session.scalar(select(User.id))),
        status="ready",
        sha256=sha,
        link_expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(row)
    await db_session.flush()
    purged = await export_artefacts.purge_expired(db_session, org_id)
    await db_session.commit()
    assert purged == {"archive_exports": 1}
    assert await _bytes_gone(org_id, sha)
    assert row.purged_at is not None


@pytest.mark.asyncio
async def test_a_purged_export_link_is_the_same_opaque_404(auth_client, db_session, monkeypatch):
    org_id = await _org_id(db_session, auth_client)
    asked = await auth_client.post("/api/v1/workspace/export")
    sent: list[str] = []

    async def fake_send(db, org, *, kind, to_email, subject, body):
        sent.append(body)

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    await workspace_export.run_export(db_session, org_id, asked.json()["id"])
    await db_session.commit()
    raw = [w for w in sent[0].split() if "/workspace/export/download/" in w][0].rsplit("/", 1)[1]
    row = await db_session.get(WorkspaceExport, asked.json()["id"])
    assert row is not None
    row.link_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    await export_artefacts.purge_expired(db_session, org_id)
    await db_session.commit()
    got = await auth_client.get(f"/api/v1/workspace/export/download/{raw}")
    assert got.status_code == 404


@pytest.mark.asyncio
async def test_a_purged_row_is_refused_even_if_its_bytes_are_still_there(
    auth_client, db_session, monkeypatch
):
    """`purged_at` is a REFUSAL, not a note.

    Written because a seeded violation exposed the previous test as weaker than
    it read: deleting the `purged_at` filter from the download query left it
    green, since the bytes were gone and the missing object produced the 404 on
    its own. Content-addressed storage makes the resurrection real — another
    export of identical bytes re-creates the same key — so the row's own mark
    has to be the answer. Here the mark is set and the bytes are deliberately
    put back."""
    org_id = await _org_id(db_session, auth_client)
    asked = await auth_client.post("/api/v1/workspace/export")
    sent: list[str] = []

    async def fake_send(db, org, *, kind, to_email, subject, body):
        sent.append(body)

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    await workspace_export.run_export(db_session, org_id, asked.json()["id"])
    await db_session.commit()
    raw = [w for w in sent[0].split() if "/workspace/export/download/" in w][0].rsplit("/", 1)[1]

    row = await db_session.get(WorkspaceExport, asked.json()["id"])
    assert row is not None and row.sha256
    data = await documents.load(documents.EXPORTS, org_id, row.sha256)
    assert data is not None
    row.purged_at = datetime.now(UTC)
    await db_session.commit()
    # The bytes are still exactly where the key says: only the mark differs.
    assert await documents.load(documents.EXPORTS, org_id, row.sha256) is not None

    got = await auth_client.get(f"/api/v1/workspace/export/download/{raw}")
    assert got.status_code == 404, "a purged export served its bytes"


@pytest.mark.asyncio
async def test_a_link_fetches_the_export_it_was_issued_for(auth_client, db_session, monkeypatch):
    """Two exports, two links. The first link must serve the FIRST export.

    It used to resolve "the newest ready export for this person", so once a
    second export finished, the first link served the second one's bytes and
    marked it downloaded — and if the requester had switched workspaces in
    between, the token carried the wrong org entirely (review, S3)."""
    org_id = await _org_id(db_session, auth_client)
    await _seed_some_data(auth_client)
    sent: list[str] = []

    async def fake_send(db, org, *, kind, to_email, subject, body):
        sent.append(body)

    monkeypatch.setattr("app.services.mailer.send", fake_send)

    first = await auth_client.post("/api/v1/workspace/export")
    await workspace_export.run_export(db_session, org_id, first.json()["id"])
    await db_session.commit()
    row1 = await db_session.get(WorkspaceExport, first.json()["id"])
    assert row1 is not None and row1.download_token_id

    # A second export, outside the coalescing window.
    row1.created_at = datetime.now(UTC) - timedelta(hours=2)
    await db_session.commit()
    second = await auth_client.post("/api/v1/workspace/export")
    assert second.json()["id"] != first.json()["id"]
    await workspace_export.run_export(db_session, org_id, second.json()["id"])
    await db_session.commit()

    raw1 = [w for w in sent[0].split() if "/workspace/export/download/" in w][0].rsplit("/", 1)[1]
    got = await auth_client.get(f"/api/v1/workspace/export/download/{raw1}")
    assert got.status_code == 200, got.text
    db_session.expire_all()
    assert (await db_session.get(WorkspaceExport, first.json()["id"])).status == "downloaded"
    assert (await db_session.get(WorkspaceExport, second.json()["id"])).status == "ready", (
        "the first link consumed the second export"
    )


@pytest.mark.asyncio
async def test_asking_again_after_one_is_ready_queues_a_new_build(
    auth_client, db_session, monkeypatch
):
    """Coalescing covers a build IN FLIGHT, not one already delivered. Including
    `ready` meant the owner whose email went to spam got a 202 and a cheerful
    "we're building it" while nothing was queued (review, A3)."""
    org_id = await _org_id(db_session, auth_client)

    async def fake_send(db, org, *, kind, to_email, subject, body):
        return None

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    first = await auth_client.post("/api/v1/workspace/export")
    await workspace_export.run_export(db_session, org_id, first.json()["id"])
    await db_session.commit()

    again = await auth_client.post("/api/v1/workspace/export")
    assert again.status_code == 202
    assert again.json()["id"] != first.json()["id"], "a delivered export blocked a new request"
    assert again.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_a_legal_hold_suspends_the_artefact_purge(auth_client, db_session, monkeypatch):
    """ADR-0019: a hold suspends ALL purging — preservation over minimisation.
    Every other destruction path asks; this one did not, and would have
    destroyed the only assembled snapshot of the workspace as it stood when the
    matter opened (review, ARCH-A)."""
    from app.services import retention

    org_id = await _org_id(db_session, auth_client)

    async def fake_send(db, org, *, kind, to_email, subject, body):
        return None

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    asked = await auth_client.post("/api/v1/workspace/export")
    await workspace_export.run_export(db_session, org_id, asked.json()["id"])
    await db_session.commit()
    row = await db_session.get(WorkspaceExport, asked.json()["id"])
    assert row is not None and row.sha256
    sha = row.sha256
    row.link_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    await retention.place_hold(db_session, org_id, reason="Tax audit 2026", actor_email=None)
    await db_session.commit()
    assert await export_artefacts.purge_expired(db_session, org_id) == {"held": True}
    assert not await _bytes_gone(org_id, sha), "a hold did not stop the purge"

    holds = await retention.active_holds(db_session, org_id)
    await retention.release_hold(db_session, org_id, holds[0].id, actor_email=None)
    await db_session.commit()
    assert await export_artefacts.purge_expired(db_session, org_id) == {"workspace_exports": 1}
    assert await _bytes_gone(org_id, sha)


@pytest.mark.asyncio
async def test_the_purge_takes_the_registry_row_with_the_bytes(
    auth_client, db_session, monkeypatch
):
    """Leaving the `documents` row behind made the store and the registry
    disagree, and the next export then reported the workspace's own purged
    predecessor to its owner as a file that could not be found (review,
    ARCH-C)."""
    from app.models.document import Document

    org_id = await _org_id(db_session, auth_client)

    async def fake_send(db, org, *, kind, to_email, subject, body):
        return None

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    asked = await auth_client.post("/api/v1/workspace/export")
    await workspace_export.run_export(db_session, org_id, asked.json()["id"])
    await db_session.commit()
    row = await db_session.get(WorkspaceExport, asked.json()["id"])
    assert row is not None
    registered = await db_session.scalar(
        select(Document).where(Document.org_id == org_id, Document.sha256 == row.sha256)
    )
    assert registered is not None, "the export was never registered"

    row.link_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    await export_artefacts.purge_expired(db_session, org_id)
    await db_session.commit()
    assert (
        await db_session.scalar(
            select(Document).where(Document.org_id == org_id, Document.sha256 == row.sha256)
        )
    ) is None


def test_the_export_email_says_what_the_code_actually_does():
    """The template is prose about a contract, so it drifts silently. These are
    the three claims a reader acts on."""
    from app.services import mailer

    subject, body = mailer.workspace_export_email(
        workspace="Northwind Components",
        link="https://app.example/api/v1/workspace/export/download/abc",
        rows=48213,
        tables=105,
        documents=317,
        missing_documents=2,
        ttl_days=workspace_export.LINK_TTL.days,
    )
    assert "Northwind Components" in subject
    assert f"expires in {workspace_export.LINK_TTL.days} days" in body
    assert "ONCE" in body
    assert "48,213" in body and "105 tables" in body
    # The missing-documents branch, which no end-to-end test reaches.
    assert "2 referenced file(s)" in body
    assert "documents/index.json" in body
    # And it must not promise that ignoring it is safe: the message IS the
    # credential (review, PROD-F). Compared on NORMALISED whitespace — the
    # first version of this line searched the wrapped body for an unwrapped
    # phrase and could not have failed.
    flat = " ".join(body.split())
    assert "nothing has been shared" not in flat
    assert "someone with owner access" in flat


@pytest.mark.asyncio
async def test_the_purge_is_scheduled_daily_for_every_tenant():
    from app.services import job_handlers, scheduler

    assert job_handlers.EXPORT_PURGE in scheduler.DAILY_KINDS
    assert job_handlers.WORKSPACE_EXPORT not in job_handlers.USER_ENQUEUEABLE
    assert job_handlers.EXPORT_PURGE not in job_handlers.USER_ENQUEUEABLE


@pytest.mark.asyncio
async def test_the_download_token_purpose_is_its_own(auth_client, db_session, monkeypatch):
    """A workspace export link must not open an archive export, or the reverse:
    the two live behind different purposes on purpose."""
    from app.models.email_token import PURPOSE_ARCHIVE_EXPORT

    assert PURPOSE_WORKSPACE_EXPORT != PURPOSE_ARCHIVE_EXPORT
    org_id = await _org_id(db_session, auth_client)
    asked = await auth_client.post("/api/v1/workspace/export")
    sent: list[str] = []

    async def fake_send(db, org, *, kind, to_email, subject, body):
        sent.append(body)

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    await workspace_export.run_export(db_session, org_id, asked.json()["id"])
    await db_session.commit()
    raw = [w for w in sent[0].split() if "/workspace/export/download/" in w][0].rsplit("/", 1)[1]
    crossed = await auth_client.get(f"/api/v1/archive/export/download/{raw}")
    assert crossed.status_code == 404
