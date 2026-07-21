"""Data residency / region-pinning (Phase 4, ADR-0022): tenant region assignment,
the enforcement backstop (421 for a wrong-region request), and off-by-default."""

import pytest
from sqlalchemy import select

from app.core import residency
from app.models.organization import Organization

# --- helper ----------------------------------------------------------------


def test_region_ok_off_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enforce_region_pinning", False)
    assert residency.region_ok("anything") is True


def test_region_ok_matches_when_enforced(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enforce_region_pinning", True)
    monkeypatch.setattr(settings, "service_region", "eu")
    assert residency.region_ok("eu") is True
    assert residency.region_ok("us") is False


# --- assignment ------------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_assigns_default_region(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "service_region", "us")
    monkeypatch.setattr(settings, "default_tenant_region", None)  # falls back to service_region

    r = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "RegionCo",
            "name": "R",
            "email": "r@region.co",
            "password": "supersecret2",
        },
    )
    assert r.status_code == 201
    assert r.json()["organization"]["region"] == "us"


@pytest.mark.asyncio
async def test_me_exposes_region(auth_client):
    r = await auth_client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["organization"]["region"] == "eu"  # default service_region


# --- enforcement backstop --------------------------------------------------


@pytest.mark.asyncio
async def test_off_by_default_serves_any_region(auth_client, db_session):
    # Tenant pinned to 'us', but enforcement off → still served.
    org = await db_session.scalar(select(Organization))
    org.region = "us"
    await db_session.commit()
    r = await auth_client.get("/api/v1/auth/me")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_same_region_allowed_when_enforced(auth_client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enforce_region_pinning", True)
    monkeypatch.setattr(settings, "service_region", "eu")
    # Acme is 'eu' by default → allowed.
    r = await auth_client.get("/api/v1/auth/me")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_wrong_region_request_refused(auth_client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enforce_region_pinning", True)
    monkeypatch.setattr(settings, "service_region", "eu")
    org = await db_session.scalar(select(Organization))
    org.region = "us"
    await db_session.commit()

    r = await auth_client.get("/api/v1/auth/me")
    assert r.status_code == 421
    assert r.headers.get("x-tenant-region") == "us"


@pytest.mark.asyncio
async def test_wrong_region_login_refused(auth_client, client, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "enforce_region_pinning", True)
    monkeypatch.setattr(settings, "service_region", "eu")
    org = await db_session.scalar(select(Organization))
    org.region = "ap"
    await db_session.commit()

    r = await client.post(
        "/api/v1/auth/login", json={"email": "owner@acme.io", "password": "supersecret"}
    )
    assert r.status_code == 421
