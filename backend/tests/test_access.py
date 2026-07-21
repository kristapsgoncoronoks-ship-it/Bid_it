"""Four user groups + the system matrix (per-role usage limits) + quota gating."""

import pytest


def _inv(n):
    return {
        "vendor_name": "Acme",
        "invoice_number": f"INV-{n}",
        "issue_date": "2026-07-01",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "c",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }


async def _member(auth_client, client, email, role):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    assert inv.status_code == 201, inv.text
    token = inv.json()["token"]
    acc = await client.post(
        "/api/v1/auth/accept-invite", json={"token": token, "name": "M", "password": "supersecret"}
    )
    return acc.json()["token"]["access_token"]


def _h(t):
    return {"Authorization": f"Bearer {t}"}


async def _make_platform_op(db_session, email="owner@acme.io"):
    """Promote a user to platform operator (the only role that may edit the global
    matrix). get_current_user reloads the user each request, so this takes effect
    immediately for the existing token."""
    from sqlalchemy import update

    from app.models.user import User

    await db_session.execute(update(User).where(User.email == email).values(is_platform_admin=True))
    await db_session.commit()


@pytest.mark.asyncio
async def test_registrant_is_company_owner_not_sysadmin(auth_client):
    # The first registrant is the OWNER of their company, never a system admin.
    me = (await auth_client.get("/api/v1/auth/me")).json()
    assert me["user"]["role"] == "owner"
    assert me["user"]["is_platform_admin"] is False


@pytest.mark.asyncio
async def test_matrix_has_four_roles_with_defaults(auth_client):
    m = {r["role"]: r for r in (await auth_client.get("/api/v1/access/matrix")).json()}
    assert set(m) == {"user_free", "user", "admin", "owner"}
    assert m["user_free"]["monthly_invoice_limit"] == 10
    assert m["user_free"]["paid"] is False
    assert m["user"]["paid"] is True
    assert m["admin"]["monthly_invoice_limit"] == 0  # unlimited


@pytest.mark.asyncio
async def test_only_platform_operator_edits_the_global_matrix(auth_client, client, db_session):
    # A plain user cannot edit the matrix.
    u = await _member(auth_client, client, "u@acme.io", "user")
    assert (
        await client.put(
            "/api/v1/access/matrix/user_free",
            json={"monthly_invoice_limit": 5, "monthly_upload_limit": 5},
            headers=_h(u),
        )
    ).status_code == 403

    # NEITHER can a company OWNER — the matrix is global, not their company's.
    denied = await auth_client.put(
        "/api/v1/access/matrix/user_free",
        json={"monthly_invoice_limit": 2, "monthly_upload_limit": 9},
    )
    assert denied.status_code == 403

    # Only a platform operator can.
    await _make_platform_op(db_session)
    ok = await auth_client.put(
        "/api/v1/access/matrix/user_free",
        json={"monthly_invoice_limit": 2, "monthly_upload_limit": 9},
    )
    assert ok.status_code == 200
    assert ok.json()["monthly_invoice_limit"] == 2


@pytest.mark.asyncio
async def test_free_user_invoice_limit_enforced(auth_client, client, db_session):
    # Tighten the free limit to 2, then a free user hits it on the 3rd invoice.
    await _make_platform_op(db_session)
    await auth_client.put(
        "/api/v1/access/matrix/user_free",
        json={"monthly_invoice_limit": 2, "monthly_upload_limit": 5},
    )
    free = await _member(auth_client, client, "free@acme.io", "user_free")

    assert (
        await client.post("/api/v1/invoices", json=_inv(1), headers=_h(free))
    ).status_code == 201
    assert (
        await client.post("/api/v1/invoices", json=_inv(2), headers=_h(free))
    ).status_code == 201
    blocked = await client.post("/api/v1/invoices", json=_inv(3), headers=_h(free))
    assert blocked.status_code == 402
    assert "limit" in blocked.json()["detail"].lower()

    # Usage endpoint reflects the cap (counts the org's 2 invoices this month).
    usage = (await client.get("/api/v1/access/usage", headers=_h(free))).json()
    assert (
        usage["invoice_limit"] == 2
        and usage["invoices_remaining"] == 0
        and usage["unlimited"] is False
    )


@pytest.mark.asyncio
async def test_admin_is_unlimited(auth_client, client, db_session):
    await _make_platform_op(db_session)
    await auth_client.put(
        "/api/v1/access/matrix/user_free",
        json={"monthly_invoice_limit": 1, "monthly_upload_limit": 1},
    )
    admin = await _member(auth_client, client, "admin@acme.io", "admin")
    # Admin has a 0 (unlimited) limit → can create beyond the free cap.
    for n in range(3):
        r = await client.post("/api/v1/invoices", json=_inv(100 + n), headers=_h(admin))
        assert r.status_code == 201, r.text
    usage = (await client.get("/api/v1/access/usage", headers=_h(admin))).json()
    assert usage["unlimited"] is True and usage["invoices_remaining"] is None


@pytest.mark.asyncio
async def test_admin_cannot_manage_users_but_owner_can(auth_client, client):
    admin = await _member(auth_client, client, "admin2@acme.io", "admin")
    # Admin can reach the admin panel (e.g. toggle a module) …
    assert (
        await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(admin))
    ).status_code == 200
    # … but cannot manage users/roles (owner-only).
    invite = await client.post(
        "/api/v1/team/invites", json={"email": "x@acme.io", "role": "user"}, headers=_h(admin)
    )
    assert invite.status_code == 403
    # The company owner can.
    assert (
        await auth_client.post("/api/v1/team/invites", json={"email": "y@acme.io", "role": "user"})
    ).status_code == 201
