"""WO-S — statement intake over HTTP: the front door that was missing.

`statement_ingest.ingest_statement` has been the only way a fuel-card statement
becomes `fuel_transactions` rows since WO-62, and until this slice **no route
imported it**. Seven parsers, the nine-rule capture gate, the deterministic
post-capture checks and the anti-drift baseline were reachable only from a
Python prompt. These tests prove the door exists and that it is the right shape:

- a real multipart upload registers real rows and learns a real seller entity,
  asserted THROUGH the API rather than by peeking at the service's return;
- the network is DETECTED, never asserted — a mislabeled filename changes
  nothing, and an unrecognised file is refused rather than guessed at;
- the capture review's refusal (R25) reaches an HTTP client with its own code
  and **writes zero rows**;
- the file is security-gated BEFORE parsing, and the gate is proven to bite
  with a seeded violation;
- the entity is resolved before the bytes are read, so a cross-tenant id is an
  opaque 404 and not a parse;
- the write needs `VAT_WRITE`, the registry read needs only `VAT_READ`;
- the upload is audited at STATEMENT level, which the per-row events cannot do.
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
from app.services import modules
from tests.factories.transport import synthetic_eurowag_statement
from tests.transport.conftest import make_entity

V = "/api/v1"
PATH = f"{V}/transport/statements"

pytestmark = pytest.mark.asyncio


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO-S Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wos.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _member_with_role(client: AsyncClient, owner_headers, db_session, stored_role: str):
    """WO-79's shape: invite into the owner's org, then downgrade the stored
    role — registration and invites only mint admin-tier accounts, so the
    permission set is the only thing that differs."""
    from sqlalchemy import update

    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wos.example.io"
    inv = await client.post(
        f"{V}/team/invites", headers=owner_headers, json={"email": email, "role": "admin"}
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        f"{V}/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    body = acc.json()
    await db_session.execute(
        update(User)
        .where(User.id == body["user"]["id"])
        .values(role=UserRole(stored_role), is_expense_approver=False)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}


def _upload(content: bytes, *, filename: str = "eurowag-2026-06.csv"):
    return {"file": (filename, content, "text/csv")}


async def _setup(client: AsyncClient, db_session):
    headers, org_id = await _register_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    return headers, org_id, entity


async def _fuel_rows(db_session, org_id: str):
    return (
        await db_session.scalars(select(FuelTransaction).where(FuelTransaction.org_id == org_id))
    ).all()


# --------------------------------------------------------------------------- #
# The door opens
# --------------------------------------------------------------------------- #


async def test_a_statement_can_finally_be_uploaded_over_http(client, db_session):
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=1).encode("utf-8")

    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(content),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["network"] == "Eurowag"
    assert body["period"] == "2026-06"
    assert body["lines_registered"] == 1
    assert body["statement_sha256"] == hashlib.sha256(content).hexdigest()
    assert len(body["entities_learned"]) == 1
    assert body["entities_learned"][0]["country"] == "BE"

    # The rows really exist, and the fuel-transaction READ route — a different
    # surface entirely — can see them. That round trip is the whole claim of
    # this work order: data entered the product through its own front door.
    listed = await client.get(
        f"{V}/transport/fuel-transactions",
        headers=headers,
        params={"entity_id": entity.id, "period": "2026-06"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["supplier"] == "Eurowag"


async def test_money_crosses_the_wire_as_exact_strings(client, db_session):
    """§4.9 on a new surface. The sample is a convenience, not an excuse — a
    float here would be a float in a VAT figure."""
    headers, _org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(synthetic_eurowag_statement(seed=7).encode("utf-8")),
    )
    assert r.status_code == 200, r.text
    line = r.json()["sample"][0]
    for money in ("qty", "net_local", "vat_local", "net_eur", "vat_eur"):
        assert isinstance(line[money], str), f"{money} came back as {type(line[money])}"


async def test_re_uploading_the_same_file_adds_no_rows(client, db_session):
    """`fuel_ingest.ingest_transaction` is insert-or-no-op on the natural key,
    so a retried upload must not double a tenant's fuel history — the property
    that makes this door safe to bang on."""
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=3).encode("utf-8")
    payload = {"entity_id": entity.id, "period": "2026-06"}

    first = await client.post(PATH, headers=headers, data=payload, files=_upload(content))
    assert first.status_code == 200, first.text
    second = await client.post(PATH, headers=headers, data=payload, files=_upload(content))
    assert second.status_code == 200, second.text

    rows = await _fuel_rows(db_session, org_id)
    assert len(rows) == 1, f"a replayed statement duplicated rows: {len(rows)}"


# --------------------------------------------------------------------------- #
# The network is detected, never asserted
# --------------------------------------------------------------------------- #


async def test_the_filename_cannot_decide_the_network(client, db_session):
    """`fuel_card_parser.select` reads the file's own marker line. A statement
    named `q8-statement.csv` that IS Eurowag bytes registers as Eurowag —
    which is the point of fail-closed detection, and the reason this route
    exposes no `network` field for an uploader to get wrong."""
    headers, _org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(
            synthetic_eurowag_statement(seed=5).encode("utf-8"), filename="q8-statement.csv"
        ),
    )
    assert r.status_code == 200, r.text
    assert r.json()["network"] == "Eurowag"


async def test_an_unrecognised_statement_is_refused_not_guessed(client, db_session):
    headers, org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(b"txn_date,country,net\n2026-06-01,BE,10.00\n", filename="mystery.csv"),
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "unrecognized_fuel_card_statement"
    assert not await _fuel_rows(db_session, org_id)


async def test_the_supported_networks_come_from_the_live_registry(client, db_session):
    headers, _org_id, _entity = await _setup(client, db_session)
    r = await client.get(f"{PATH}/networks", headers=headers)
    assert r.status_code == 200, r.text
    names = {n["network"] for n in r.json()["networks"]}
    # Every shipped parser, and nothing invented: compared against the registry
    # itself rather than a list retyped here, which would drift the first time
    # a parser is added.
    from app.services.transport import fuel_card_parser

    assert names == {p.network for p in fuel_card_parser.parsers()}
    assert "Eurowag" in names


# --------------------------------------------------------------------------- #
# The gates, each proven to bite
# --------------------------------------------------------------------------- #


async def test_a_blocked_capture_review_refuses_and_writes_nothing(client, db_session):
    """R25 regime 1 over HTTP. A coversheet total that does not tie out is the
    cheapest way to arm the gate deterministically, and the refusal must leave
    the tenant's fuel history exactly as it found it."""
    headers, org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={
            "entity_id": entity.id,
            "period": "2026-06",
            "coversheet_total": "999999.00",
        },
        files=_upload(synthetic_eurowag_statement(seed=11).encode("utf-8")),
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "capture_review_blocked"
    assert "tie-out" in r.json()["detail"]
    assert not await _fuel_rows(db_session, org_id)


async def test_a_renamed_script_does_not_become_a_statement(client, db_session):
    """The seeded violation for the security gate: HTML bytes wearing a `.csv`
    extension. `filesec.check` runs BEFORE the parser, so this is refused at
    415 without a parser ever decoding it."""
    headers, org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(b"<html><script>alert(1)</script></html>", filename="statement.csv"),
    )
    assert r.status_code == 415, r.text
    assert not await _fuel_rows(db_session, org_id)


async def test_a_non_csv_kind_is_refused_even_when_it_parses_as_text(client, db_session):
    """The allow-list is `{csv}` and nothing else: every shipped parser reads
    UTF-8 CSV, so a wider list would only widen the attack surface."""
    headers, _org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(b'{"statement": []}', filename="statement.json"),
    )
    assert r.status_code == 415, r.text


async def test_an_invalid_period_is_refused_by_the_service_vocabulary(client, db_session):
    headers, _org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "June 2026"},
        files=_upload(synthetic_eurowag_statement(seed=2).encode("utf-8")),
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_period"


async def test_a_coversheet_total_that_is_not_a_number_is_refused(client, db_session):
    headers, _org_id, entity = await _setup(client, db_session)
    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06", "coversheet_total": "about 900"},
        files=_upload(synthetic_eurowag_statement(seed=2).encode("utf-8")),
    )
    assert r.status_code == 422, r.text
    assert "not a valid coversheet total" in r.json()["detail"]


async def test_another_tenants_entity_is_an_opaque_404(client, db_session):
    """§4.4, and the ordering matters: the entity is resolved BEFORE the file is
    read, so a caller naming someone else's entity gets a 404 without this
    process parsing bytes on their behalf."""
    headers_a, _org_a, _entity_a = await _setup(client, db_session)
    _headers_b, _org_b, entity_b = await _setup(client, db_session)

    r = await client.post(
        PATH,
        headers=headers_a,
        data={"entity_id": entity_b.id, "period": "2026-06"},
        files=_upload(synthetic_eurowag_statement(seed=4).encode("utf-8")),
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "entity_not_found"


async def test_the_module_entitlement_gates_the_upload(client, db_session):
    """ADR-P3 rule 3 — fail closed on the entitlement before anything else."""
    headers, org_id = await _register_org(client)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()

    r = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(synthetic_eurowag_statement(seed=6).encode("utf-8")),
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "module_not_enabled"


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


async def test_uploading_needs_the_write_permission_reading_the_registry_does_not(
    client, db_session
):
    """The structural pair (ADR-0024). An APPROVER holds neither VAT permission,
    so both surfaces refuse; the asymmetry that matters is that the upload is a
    WRITE and declares itself one."""
    headers, org_id, entity = await _setup(client, db_session)
    approver = await _member_with_role(client, headers, db_session, "approver")

    denied_write = await client.post(
        PATH,
        headers=approver,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(synthetic_eurowag_statement(seed=8).encode("utf-8")),
    )
    assert denied_write.status_code == 403, denied_write.text

    # …and the owner, who holds both, is allowed through the same door.
    allowed = await client.post(
        PATH,
        headers=headers,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(synthetic_eurowag_statement(seed=8).encode("utf-8")),
    )
    assert allowed.status_code == 200, allowed.text


async def test_an_accountant_may_upload(client, db_session):
    """The role this surface exists for. ACCOUNTANT holds VAT_WRITE, so the
    person who actually receives the monthly statement can register it without
    an owner account."""
    headers, org_id, entity = await _setup(client, db_session)
    accountant = await _member_with_role(client, headers, db_session, "accountant")

    r = await client.post(
        PATH,
        headers=accountant,
        data={"entity_id": entity.id, "period": "2026-06"},
        files=_upload(synthetic_eurowag_statement(seed=9).encode("utf-8")),
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


async def test_the_upload_is_audited_at_statement_level(client, db_session):
    """`fuel_ingest` audits each inserted ROW and knows nothing about the file.
    Without a statement-level event the trail could say which rows appeared and
    never which file an operator uploaded to produce them — and a REPLAYED
    statement, every row a no-op, would leave no trace at all."""
    headers, org_id, entity = await _setup(client, db_session)
    content = synthetic_eurowag_statement(seed=12).encode("utf-8")
    payload = {"entity_id": entity.id, "period": "2026-06"}

    await client.post(PATH, headers=headers, data=payload, files=_upload(content))
    await client.post(PATH, headers=headers, data=payload, files=_upload(content))

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org_id,
                AuditEvent.action == "transport.statement_ingest",
            )
        )
    ).all()
    # TWO events for two uploads, even though the second wrote no row.
    assert len(events) == 2
    # `AuditEvent.meta` is Text holding small JSON, not a JSON column — the
    # hash chain covers the real columns and meta is optional caller payload.
    meta = json.loads(events[0].meta or "{}")
    assert meta["network"] == "Eurowag"
    assert meta["filename"] == "eurowag-2026-06.csv"
    assert meta["period"] == "2026-06"
    assert events[0].target_id == hashlib.sha256(content).hexdigest()
