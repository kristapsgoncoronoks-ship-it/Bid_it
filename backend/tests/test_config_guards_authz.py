"""The admin-configuration endpoints enforce through the authz service.

The ad-hoc `is_admin_or_above` role checks on the org-configuration surfaces
(settings, modules, issuer, fx, currencies, tax-codes, documents, integrity,
jobs, retention, email, webhooks, sso, privacy) were migrated onto the single
`authz.require(..., SETTINGS_MANAGE)` choke point. This proves the migration is
behavior-preserving: ADMINISTRATOR holds SETTINGS_MANAGE (allowed, exactly as
`is_admin_or_above` allowed admin), while EMPLOYEE and READ_ONLY do not (403,
exactly as they were denied before) — but now the matrix governs it, so the
role expansion applies automatically.
"""

import pytest
from sqlalchemy import select, update

from app.models.organization import Organization
from app.models.user import User, UserRole

# Representative migrated endpoints — each guarded solely by SETTINGS_MANAGE.
_CONFIG_ENDPOINTS = [
    ("GET", "/api/v1/documents", None),
    ("GET", "/api/v1/retention", None),
    ("PUT", "/api/v1/settings/validation", {}),
    ("POST", "/api/v1/integrity/documents/verify", {}),
]


async def _set_role(db_session, role: UserRole) -> None:
    org = await db_session.scalar(select(Organization.id))
    await db_session.execute(update(User).where(User.org_id == org).values(role=role))
    await db_session.commit()


async def _call(auth_client, method: str, path: str, body):
    if method == "GET":
        return await auth_client.get(path)
    if method == "PUT":
        return await auth_client.put(path, json=body)
    return await auth_client.post(path, json=body)


@pytest.mark.asyncio
async def test_administrator_passes_the_config_guard(auth_client, db_session):
    # ADMINISTRATOR holds SETTINGS_MANAGE → never blocked at the guard (a non-403
    # status; 200/400/422 = the request reached the handler).
    await _set_role(db_session, UserRole.admin)
    for method, path, body in _CONFIG_ENDPOINTS:
        r = await _call(auth_client, method, path, body)
        assert r.status_code != 403, f"admin blocked at {method} {path}: {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_employee_is_denied_by_the_config_guard(auth_client, db_session):
    # EMPLOYEE (stored 'user') has no SETTINGS_MANAGE → 403 at the authz gate.
    await _set_role(db_session, UserRole.user)
    for method, path, body in _CONFIG_ENDPOINTS:
        r = await _call(auth_client, method, path, body)
        assert r.status_code == 403, f"employee reached {method} {path}: {r.status_code}"


@pytest.mark.asyncio
async def test_read_only_is_denied_by_the_config_guard(auth_client, db_session):
    # READ_ONLY (stored 'user_free') is likewise denied.
    await _set_role(db_session, UserRole.user_free)
    for method, path, body in _CONFIG_ENDPOINTS:
        r = await _call(auth_client, method, path, body)
        assert r.status_code == 403, f"read-only reached {method} {path}: {r.status_code}"
