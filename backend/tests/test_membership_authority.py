"""B1.5 — memberships are the authoritative org relationship (WO-11).

`users.org_id` is only the ACTIVE-ORG pointer (repointed by org-switching).
These tests prove the contract step: a member whose active org is ELSEWHERE is
still visible to their other orgs everywhere it matters (SCIM roster/offboarding,
reimbursement payee bank details, expense-approver resolution, the GDPR/DSAR
scan, and the ORM tenant guard itself) — while a NON-member stays at zero rows /
opaque 404. Overlapping fixtures on purpose: the adversary rows carry
identical-looking data in another org.
"""

import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import func, select

from app.core.tenant import reset_current_org, set_current_org
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.services import privacy, scim

_NS = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)
ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _acme_org_id(db):
    return await db.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _member(auth_client, client, email, name="Member"):
    """Invite + accept a member into the auth_client org (Acme). Returns
    (token, user_id)."""
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": "user"})
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": name, "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    return acc.json()["token"]["access_token"], acc.json()["user"]["id"]


async def _switch_away(client, member_token, member_email, *, host_email="host@elsewhere.io"):
    """Give the member a second org ('Elsewhere') and make it their ACTIVE org,
    so from Acme's perspective they are a member active in another org.
    Returns the Elsewhere org id."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Elsewhere",
            "name": "Host",
            "email": host_email,
            "password": "supersecret",
        },
    )
    assert reg.status_code == 201, reg.text
    host_tok = reg.json()["token"]["access_token"]
    elsewhere = reg.json()["organization"]["id"]
    inv = await client.post(
        "/api/v1/team/invites",
        json={"email": member_email, "role": "user"},
        headers=_h(host_tok),
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    sw = await client.post(f"/api/v1/auth/switch-org/{elsewhere}", headers=_h(member_token))
    assert sw.status_code == 200, sw.text
    return elsewhere


async def _scim_token(db, org_id, slug="acme"):
    from app.models.sso import SsoConnection

    db.add(
        SsoConnection(
            org_id=org_id,
            slug=slug,
            protocol="oidc",
            enabled=True,
            default_role="user",
            scim_enabled=False,
        )
    )
    await db.commit()
    return await scim.set_token(db, org_id)


# --- SCIM ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scim_lists_member_active_in_another_org(auth_client, client, db_session):
    tok, uid = await _member(auth_client, client, "away@corp.example")
    await _switch_away(client, tok, "away@corp.example")
    org_id = await _acme_org_id(db_session)
    scim_tok = await _scim_token(db_session, org_id)

    lst = await auth_client.get("/api/v1/scim/v2/Users", headers=_h(scim_tok))
    assert lst.status_code == 200, lst.text
    ids = [u["id"] for u in lst.json()["Resources"]]
    assert uid in ids  # the switched-away member is still Acme's to provision
    # And the filter form finds them too.
    f = await auth_client.get(
        '/api/v1/scim/v2/Users?filter=userName eq "away@corp.example"', headers=_h(scim_tok)
    )
    assert f.json()["totalResults"] == 1


@pytest.mark.asyncio
async def test_scim_get_patch_deactivate_member_active_elsewhere(auth_client, client, db_session):
    tok, uid = await _member(auth_client, client, "off@corp.example")
    elsewhere = await _switch_away(client, tok, "off@corp.example")
    org_id = await _acme_org_id(db_session)
    scim_tok = await _scim_token(db_session, org_id)

    g = await auth_client.get(f"/api/v1/scim/v2/Users/{uid}", headers=_h(scim_tok))
    assert g.status_code == 200 and g.json()["id"] == uid

    # IdP offboarding must reach the switched-away member.
    p = await auth_client.patch(
        f"/api/v1/scim/v2/Users/{uid}",
        headers=_h(scim_tok),
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    assert p.status_code == 200 and p.json()["active"] is False
    user = await db_session.scalar(select(User).where(User.id == uid))
    assert user.is_active is False
    m_acme = await db_session.scalar(
        select(Membership).where(Membership.user_id == uid, Membership.org_id == org_id)
    )
    assert m_acme.status == "suspended"
    # Their standing in the OTHER org is not Acme's to touch.
    m_else = await db_session.scalar(
        select(Membership).where(Membership.user_id == uid, Membership.org_id == elsewhere)
    )
    assert m_else.status == "active"


@pytest.mark.asyncio
async def test_scim_nonmember_is_opaque_404(auth_client, client, db_session):
    # An account that exists ONLY in another org (no Acme membership).
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Foreign",
            "name": "F",
            "email": "foreign@corp.example",
            "password": "supersecret",
        },
    )
    foreign_uid = reg.json()["user"]["id"]
    org_id = await _acme_org_id(db_session)
    scim_tok = await _scim_token(db_session, org_id)

    g = await auth_client.get(f"/api/v1/scim/v2/Users/{foreign_uid}", headers=_h(scim_tok))
    assert g.status_code == 404  # opaque: non-member ≡ nonexistent
    lst = await auth_client.get("/api/v1/scim/v2/Users", headers=_h(scim_tok))
    assert foreign_uid not in [u["id"] for u in lst.json()["Resources"]]


@pytest.mark.asyncio
async def test_scim_create_conflict_for_email_in_another_workspace_is_409(
    auth_client, client, db_session
):
    await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Foreign",
            "name": "F",
            "email": "taken@corp.example",
            "password": "supersecret",
        },
    )
    org_id = await _acme_org_id(db_session)
    scim_tok = await _scim_token(db_session, org_id)

    c = await auth_client.post(
        "/api/v1/scim/v2/Users", headers=_h(scim_tok), json={"userName": "taken@corp.example"}
    )
    assert c.status_code == 409, c.text
    assert "another workspace" in c.json()["detail"]
    # No second row was created for that email.
    n = await db_session.scalar(
        select(func.count()).select_from(User).where(User.email == "taken@corp.example")
    )
    assert n == 1


@pytest.mark.asyncio
async def test_scim_create_reactivates_existing_member_active_elsewhere(
    auth_client, client, db_session
):
    tok, uid = await _member(auth_client, client, "rejoin@corp.example")
    await _switch_away(client, tok, "rejoin@corp.example")
    org_id = await _acme_org_id(db_session)
    scim_tok = await _scim_token(db_session, org_id)

    # IdP re-provisions the same email → the existing member, not a duplicate.
    c = await auth_client.post(
        "/api/v1/scim/v2/Users", headers=_h(scim_tok), json={"userName": "rejoin@corp.example"}
    )
    assert c.status_code == 201, c.text
    assert c.json()["id"] == uid


# --- reimbursement payee resolution ----------------------------------------


@pytest.mark.asyncio
async def test_reimbursement_sepa_pays_member_active_elsewhere(auth_client, client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    emp, _uid = await _member(auth_client, client, "payee@corp.example", name="Jane Payee")
    await client.put(
        "/api/v1/auth/bank-details",
        headers=_h(emp),
        json={"iban": "DE89370400440532013000", "bic": "COBADEFFXXX"},
    )
    # File + approve + batch the expense while active in Acme.
    r = await client.post(
        "/api/v1/expenses",
        headers=_h(emp),
        json={
            "title": "Trip",
            "currency": "EUR",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "travel",
                    "description": "Flight",
                    "amount": "300.00",
                    "vat_amount": "0",
                }
            ],
        },
    )
    rid = r.json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client meeting"},
            headers=_h(emp),
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("receipt.png", _PNG, "image/png")},
            headers=_h(emp),
        )
    assert (await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))).status_code == 200
    assert (
        await auth_client.post(f"/api/v1/expenses/{rid}/decision", json={"action": "approve"})
    ).status_code == 200
    b = await auth_client.post("/api/v1/reimbursements", json={"report_ids": [rid]})
    assert b.status_code == 201, b.text
    bid = b.json()["id"]

    # NOW the employee switches their active org away — payday must still find
    # their IBAN (a dropped payee is an unpaid person nobody was told about).
    await _switch_away(client, emp, "payee@corp.example")
    resp = await auth_client.get(f"/api/v1/reimbursements/{bid}/sepa")
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Skipped"] == "0"
    root = ET.fromstring(resp.text)
    txs = root.findall(".//p:CdtTrfTxInf", _NS)
    assert len(txs) == 1
    assert txs[0].find("./p:CdtrAcct/p:Id/p:IBAN", _NS).text == "DE89370400440532013000"


# --- expense-approver e-mail resolution ------------------------------------


@pytest.mark.asyncio
async def test_expense_approver_email_resolved_for_member_active_elsewhere(auth_client, client):
    assert (
        await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    ).status_code == 200
    appr, appr_id = await _member(auth_client, client, "approver@corp.example", name="Appr")
    emp, _ = await _member(auth_client, client, "filer@corp.example", name="Filer")
    assert (
        await auth_client.patch(
            f"/api/v1/team/members/{appr_id}", json={"is_expense_approver": True}
        )
    ).status_code == 200
    p = await auth_client.post(
        "/api/v1/expenses/approval-policies", json={"name": "One", "approver_ids": [appr_id]}
    )
    assert p.status_code == 201, p.text

    # The approver switches away BEFORE the report is submitted.
    await _switch_away(client, appr, "approver@corp.example")

    r = await client.post(
        "/api/v1/expenses",
        headers=_h(emp),
        json={
            "title": "Trip",
            "items": [
                {
                    "spend_date": "2026-05-01",
                    "category": "meals",
                    "description": "Dinner",
                    "amount": "42.00",
                    "vat_amount": "0",
                }
            ],
        },
    )
    rid = r.json()["id"]
    rep = (await client.get(f"/api/v1/expenses/{rid}", headers=_h(emp))).json()
    for it in rep["items"]:
        await client.patch(
            f"/api/v1/expenses/{rid}/items/{it['id']}",
            json={"comment": "Client visit"},
            headers=_h(emp),
        )
        await client.post(
            f"/api/v1/expenses/{rid}/items/{it['id']}/receipt",
            files={"file": ("r.png", _PNG, "image/png")},
            headers=_h(emp),
        )
    s = await client.post(f"/api/v1/expenses/{rid}/submit", headers=_h(emp))
    assert s.status_code == 200, s.text

    detail = (await auth_client.get(f"/api/v1/expenses/{rid}")).json()
    steps = detail["approval_steps"]
    assert steps and steps[0]["approver_id"] == appr_id
    assert steps[0]["approver_email"] == "approver@corp.example"


# --- GDPR / DSAR scan ------------------------------------------------------


@pytest.mark.asyncio
async def test_privacy_scan_counts_member_active_elsewhere(auth_client, client, db_session):
    tok, _uid = await _member(auth_client, client, "subject@corp.example")
    await _switch_away(client, tok, "subject@corp.example")
    org_id = await _acme_org_id(db_session)

    rep = await privacy.preview(db_session, org_id, "subject@corp.example")
    users_loc = next(loc for loc in rep.locations if loc.key == "users")
    assert users_loc.matched == 1  # the DSAR reaches this org's member


@pytest.mark.asyncio
async def test_privacy_scan_ignores_nonmember(auth_client, client, db_session):
    # Same-looking subject in ANOTHER org, no Acme membership → 0 for Acme.
    await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Foreign",
            "name": "Jane",
            "email": "subject@corp.example",
            "password": "supersecret",
        },
    )
    org_id = await _acme_org_id(db_session)
    rep = await privacy.preview(db_session, org_id, "subject@corp.example")
    users_loc = next(loc for loc in rep.locations if loc.key == "users")
    assert users_loc.matched == 0


# --- the ORM tenant guard itself -------------------------------------------


@pytest.mark.asyncio
async def test_tenant_guard_users_scope_is_membership_driven(auth_client, client, db_session):
    """With the guard active, a bare `select(User)` returns exactly the org's
    MEMBERS — including one active elsewhere — and zero non-members (self-test
    that the guard still bites after B1.5)."""
    tok, member_uid = await _member(auth_client, client, "guard@corp.example")
    await _switch_away(client, tok, "guard@corp.example")
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Foreign",
            "name": "F",
            "email": "nonmember@corp.example",
            "password": "supersecret",
        },
    )
    nonmember_uid = reg.json()["user"]["id"]
    org_id = await _acme_org_id(db_session)

    token = set_current_org(org_id)
    try:
        rows = list(await db_session.scalars(select(User)))
    finally:
        reset_current_org(token)
    ids = {u.id for u in rows}
    assert member_uid in ids  # member active elsewhere: visible
    assert nonmember_uid not in ids  # non-member: zero rows
    emails = {u.email for u in rows}
    assert "nonmember@corp.example" not in emails
