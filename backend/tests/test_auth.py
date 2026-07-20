import pytest


@pytest.mark.asyncio
async def test_register_login_me(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Acme",
            "name": "Owner",
            "email": "owner@acme.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["user"]["role"] == "sysadmin"
    assert data["organization"]["name"] == "Acme"

    # duplicate email rejected
    dup = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other",
            "name": "X",
            "email": "owner@acme.io",
            "password": "supersecret",
        },
    )
    assert dup.status_code == 409

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@acme.io", "password": "supersecret"},
    )
    assert login.status_code == 200
    token = login.json()["token"]["access_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@acme.io"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Acme",
            "name": "Owner",
            "email": "owner@acme.io",
            "password": "supersecret",
        },
    )
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "owner@acme.io", "password": "wrong"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_protected_requires_token(client):
    r = await client.get("/api/v1/analytics/summary")
    assert r.status_code == 401
