"""Audit-log export (Phase 4): CSV/JSON evidence export with chain columns,
formula-injection-safe, AUDIT_READ-gated, tenant-scoped, filterable."""

import csv
import io
import json

import pytest
from sqlalchemy import select

from app.models.user import User, UserRole
from app.services import audit, audit_export


async def _emit(auth_client, n=3):
    """Generate audit events by creating invoices (records `invoice.create`)."""
    for i in range(n):
        r = await auth_client.post(
            "/api/v1/invoices",
            json={
                "vendor_name": f"Vendor {i}",
                "invoice_number": f"INV-{i}",
                "issue_date": "2026-05-10",
                "line_items": [
                    {"description": "Item", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
                ],
            },
        )
        assert r.status_code == 201, r.text


# --- pure rendering --------------------------------------------------------


def test_csv_injection_safe():
    from app.models.audit import AuditEvent

    e = AuditEvent(
        org_id="o",
        seq=1,
        action="x",
        at_ms=0,
        hash="h",
        actor_email="=cmd()",
        meta='{"k":"v"}',
        prev_hash=None,
    )
    text = audit_export.to_csv([e])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == audit_export.CSV_HEADER
    # actor_email cell (index 2) neutralised.
    assert rows[1][2].startswith("'=")


def test_json_includes_chain_and_events():
    from app.models.audit import AuditEvent
    from app.services.audit import ChainStatus

    e = AuditEvent(
        org_id="o",
        seq=1,
        action="invoice.create",
        at_ms=1_700_000_000_000,
        hash="abc",
        actor_email="a@a.io",
        meta='{"n":1}',
        prev_hash=None,
        target_type="invoice",
        target_id="42",
    )
    doc = json.loads(
        audit_export.to_json([e], ChainStatus(True, 1), org_id="o", generated_ms=1_700_000_000_000)
    )
    assert doc["chain"] == {"verified": True, "events": 1, "broken_at_seq": None, "detail": None}
    assert doc["events"][0]["action"] == "invoice.create"
    assert doc["events"][0]["meta"] == {"n": 1}  # parsed, not a string
    assert doc["events"][0]["hash"] == "abc"


# --- service ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_events_ascending(auth_client, db_session):
    from app.models.organization import Organization

    await _emit(auth_client, 3)
    org_id = await db_session.scalar(select(Organization.id))
    events = await audit.export_events(db_session, org_id)
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)  # chronological


# --- route -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_route(auth_client):
    await _emit(auth_client, 2)
    r = await auth_client.get("/api/v1/audit/export?fmt=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="audit-log-' in r.headers["content-disposition"]
    assert r.headers["x-audit-chain-verified"] == "true"
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == audit_export.CSV_HEADER
    assert len(rows) > 1


@pytest.mark.asyncio
async def test_json_route(auth_client):
    await _emit(auth_client, 2)
    r = await auth_client.get("/api/v1/audit/export?fmt=json")
    assert r.status_code == 200
    doc = r.json()
    assert doc["chain"]["verified"] is True
    assert doc["event_count"] == len(doc["events"]) >= 1


@pytest.mark.asyncio
async def test_unknown_format(auth_client):
    r = await auth_client.get("/api/v1/audit/export?fmt=pdf")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bad_date(auth_client):
    r = await auth_client.get("/api/v1/audit/export?fmt=csv&from=not-a-date")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_date_filter_excludes_future_window(auth_client):
    await _emit(auth_client, 2)
    # A window entirely in the past → no events.
    r = await auth_client.get("/api/v1/audit/export?fmt=csv&from=2000-01-01&to=2000-12-31")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows == [audit_export.CSV_HEADER]  # header only
    assert r.headers["x-audit-event-count"] == "0"


@pytest.mark.asyncio
async def test_action_filter(auth_client):
    await _emit(auth_client, 2)
    r = await auth_client.get("/api/v1/audit/export?fmt=json&action=invoice.create")
    doc = r.json()
    assert doc["events"]
    assert all(e["action"] == "invoice.create" for e in doc["events"])


@pytest.mark.asyncio
async def test_export_requires_audit_read(auth_client, db_session):
    # A read-only member has no AUDIT_READ → export refused.
    user = await db_session.scalar(select(User))
    user.role = UserRole.user_free
    await db_session.commit()
    assert (await auth_client.get("/api/v1/audit/export?fmt=csv")).status_code == 403

    # An administrator HOLDS AUDIT_READ → allowed past the guard.
    user.role = UserRole.admin
    await db_session.commit()
    assert (await auth_client.get("/api/v1/audit/export?fmt=csv")).status_code == 200


@pytest.mark.asyncio
async def test_export_is_tenant_scoped(auth_client, client):
    await _emit(auth_client, 2)
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "O",
            "email": "o@o.io",
            "password": "supersecret2",
        },
    )
    client.headers["Authorization"] = f"Bearer {reg.json()['token']['access_token']}"
    r = await client.get("/api/v1/audit/export?fmt=json")
    # New tenant sees only its own events, never Acme's invoice.create.
    doc = r.json()
    assert all(e["action"] != "invoice.create" for e in doc["events"])
