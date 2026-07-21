"""GDPR right-to-erasure / DSAR (Phase 4, ADR-0020): preview classification,
pseudonymise-not-delete, statutory/integrity retention, legal-hold block,
object-byte cleanup, audit, and route gating."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.models.audit import AuditEvent
from app.models.email_intake import InboundInvoice
from app.models.expense import ExpenseReport
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.models.user import User
from app.services import documents, privacy, retention

SUBJECT = "jane@bigco.com"


async def _org_id(db):
    return await db.scalar(select(Organization.id))


async def _seed_subject(db, org_id, *, with_attachment=True):
    """Create data belonging to SUBJECT across several locations."""
    u = User(org_id=org_id, email=SUBJECT, name="Jane Doe", role="user", hashed_password="x")
    db.add(u)
    await db.flush()
    db.add(ExpenseReport(org_id=org_id, employee_id=u.id, employee_name="Jane Doe", title="Trip"))
    sha = None
    if with_attachment:
        sha, _ = await documents.store(
            documents.EMAIL_ATTACHMENTS, org_id, b"attach", "application/pdf"
        )
    db.add(
        InboundInvoice(
            org_id=org_id,
            received_at=datetime.now(UTC),
            filename="a.pdf",
            status="pending",
            from_addr=SUBJECT,
            sha256=sha,
        )
    )
    db.add(
        IssuedInvoice(
            org_id=org_id,
            number="INV-1",
            issue_date=date(2026, 1, 1),
            buyer_name="Jane Doe",
            buyer_email=SUBJECT,
            seller_json="{}",
        )
    )
    await db.commit()
    return u, sha


# --- preview ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_classifies_locations(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _seed_subject(db_session, org_id)

    rep = await privacy.preview(db_session, org_id, SUBJECT)
    by = {l.key: l for l in rep.locations}
    assert rep.executed is False and rep.on_hold is False
    assert by["users"].action == "erase" and by["users"].matched == 1
    assert by["inbound_email"].action == "erase" and by["inbound_email"].matched == 1
    assert by["expenses"].action == "erase" and by["expenses"].matched == 1
    # Statutory / integrity locations are retained, with a reason.
    assert by["issued_invoices"].action == "retain" and by["issued_invoices"].matched == 1
    assert by["audit_trail"].action == "retain"
    assert by["issued_invoices"].reason and "statutory" in by["issued_invoices"].reason.lower()


# --- erase -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_erase_pseudonymises_and_retains(auth_client, db_session):
    org_id = await _org_id(db_session)
    u, sha = await _seed_subject(db_session, org_id)

    rep = await privacy.erase(db_session, org_id, SUBJECT, actor_email="dpo@acme.io")
    assert rep.executed is True

    # User row KEPT but pseudonymised + deactivated.
    await db_session.refresh(u)
    assert u.email.startswith("erased-") and u.email.endswith("@erased.invalid")
    assert u.name == "Erased user" and u.is_active is False

    # Expense author name redacted.
    ename = await db_session.scalar(select(ExpenseReport.employee_name))
    assert ename == "Erased user"

    # Inbound record + attachment bytes gone.
    assert await db_session.scalar(select(func.count()).select_from(InboundInvoice)) == 0
    from app.core import storage

    with pytest.raises(storage.StorageError):
        await documents.load(documents.EMAIL_ATTACHMENTS, org_id, sha)

    # Issued invoice RETAINED (statutory) — buyer identity untouched.
    inv_email = await db_session.scalar(select(IssuedInvoice.buyer_email))
    assert inv_email == SUBJECT


@pytest.mark.asyncio
async def test_erase_is_audited_with_hashed_subject(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _seed_subject(db_session, org_id)
    await privacy.erase(db_session, org_id, SUBJECT, actor_email="dpo@acme.io")

    ev = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org_id, AuditEvent.action == "privacy.erasure"
        )
    )
    assert ev is not None
    assert SUBJECT not in (ev.meta or "")  # cleartext email never stored
    assert '"erased"' in ev.meta and '"retained"' in ev.meta


# --- legal hold ------------------------------------------------------------


@pytest.mark.asyncio
async def test_legal_hold_blocks_erasure(auth_client, db_session):
    org_id = await _org_id(db_session)
    u, _ = await _seed_subject(db_session, org_id)
    await retention.place_hold(db_session, org_id, reason="Litigation", actor_email="a@a.io")

    rep = await privacy.erase(db_session, org_id, SUBJECT, actor_email="dpo@acme.io")
    assert rep.executed is False and rep.on_hold is True
    assert all(l.action in ("blocked", "retain") for l in rep.locations)

    await db_session.refresh(u)
    assert u.email == SUBJECT and u.is_active is True  # untouched under hold


# --- routes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_preview_then_erase(auth_client, db_session):
    org_id = await _org_id(db_session)
    await _seed_subject(db_session, org_id)

    p = await auth_client.post("/api/v1/privacy/erasure/preview", json={"email": SUBJECT})
    assert p.status_code == 200 and p.json()["executed"] is False

    e = await auth_client.post("/api/v1/privacy/erasure", json={"email": SUBJECT})
    body = e.json()
    assert e.status_code == 200 and body["executed"] is True
    users_loc = next(l for l in body["locations"] if l["key"] == "users")
    assert users_loc["matched"] == 1 and users_loc["action"] == "erase"


@pytest.mark.asyncio
async def test_erasure_requires_admin(auth_client, db_session):
    from app.models.user import UserRole

    user = await db_session.scalar(select(User))
    user.role = UserRole.user
    await db_session.commit()
    r = await auth_client.post("/api/v1/privacy/erasure", json={"email": SUBJECT})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_unknown_subject_is_noop(auth_client, db_session):
    org_id = await _org_id(db_session)
    rep = await privacy.preview(db_session, org_id, "nobody@nowhere.com")
    assert all(l.matched == 0 for l in rep.locations)


@pytest.mark.asyncio
async def test_erasure_is_tenant_scoped(auth_client, db_session, client):
    org_id = await _org_id(db_session)
    await _seed_subject(db_session, org_id)

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
    # The other tenant erasing SUBJECT sees nothing (SUBJECT isn't theirs).
    r = await client.post("/api/v1/privacy/erasure", json={"email": SUBJECT})
    assert all(l["matched"] == 0 for l in r.json()["locations"])

    # Acme's subject data is intact.
    assert await db_session.scalar(select(func.count()).select_from(InboundInvoice)) == 1
