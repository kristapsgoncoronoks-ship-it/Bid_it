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


# --------------------------------------------------------------------------- #
# Boot-time config guard: the inbound-email secret is mandatory in production
# --------------------------------------------------------------------------- #

_PROD_KWARGS = dict(
    environment="production",
    secret_key="a-real-32-byte-secret-value-here!!",
    database_url="postgresql+asyncpg://u:p@db/invoiceiq",
    cors_origins="https://app.invoiceiq.example",
)


def test_production_boot_fails_without_inbound_email_secret():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="inbound_email_secret"):
        Settings(**_PROD_KWARGS)


def test_production_boots_with_inbound_email_secret():
    from app.core.config import Settings

    s = Settings(**_PROD_KWARGS, inbound_email_secret="a-generated-webhook-secret")
    assert s.is_production and s.inbound_email_secret


def test_development_boot_unaffected():
    # Dev and test keep booting with zero configuration — the mandate is
    # production-only (the endpoint still fails closed at request time).
    from app.core.config import Settings

    s = Settings(environment="development")
    assert s.inbound_email_secret is None


# --- SEC-JWT-001: production refuses a signing-key setup that would sign with the
# dev default, list it as a fallback, repeat the active key, or hold duplicates.


def _prod_kwargs(**over):
    base = dict(
        environment="production",
        secret_key="a-real-secret-0123456789abcdef0123456789",
        database_url="postgresql+asyncpg://u:p@db/x",
        cors_origins="https://app.example",
        inbound_email_secret="s" * 32,
    )
    base.update(over)
    return base


def test_production_accepts_a_separate_signing_key_with_fallbacks():
    from app.core.config import Settings

    s = Settings(
        **_prod_kwargs(
            jwt_signing_key="k-new-0123456789abcdef", jwt_signing_key_fallbacks=["k-old"]
        )
    )
    assert s.active_jwt_signing_key == "k-new-0123456789abcdef"
    assert s.jwt_verification_keys == ("k-new-0123456789abcdef", "k-old")


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        (
            {"jwt_signing_key": "dev-insecure-change-me"},
            "JWT signing still uses the insecure dev default",
        ),
        (
            {"jwt_signing_key_fallbacks": ["dev-insecure-change-me"]},
            "contains the insecure dev default",
        ),
        (
            {
                "jwt_signing_key": "k1-0123456789abcdef",
                "jwt_signing_key_fallbacks": ["k1-0123456789abcdef"],
            },
            "repeats the active signing key",
        ),
        ({"jwt_signing_key_fallbacks": ["k-old", "k-old"]}, "contains duplicate keys"),
    ],
)
def test_production_refuses_unsafe_signing_key_setups(over, needle):
    from app.core.config import Settings

    with pytest.raises(ValueError, match=needle):
        Settings(**_prod_kwargs(**over))


def test_more_than_three_fallbacks_is_refused_everywhere():
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(jwt_signing_key_fallbacks=["a", "b", "c", "d"])


def test_the_active_key_is_always_verified_first():
    from app.core.config import Settings

    s = Settings(jwt_signing_key="k-new", jwt_signing_key_fallbacks=["k-old", "k-older"])
    assert s.jwt_verification_keys[0] == s.active_jwt_signing_key == "k-new"
    assert Settings().jwt_verification_keys == (Settings().secret_key,)


def test_an_expired_token_is_reported_as_expired_even_with_fallbacks(monkeypatch):
    """A-2: a signature that verifies under the active key is decisive — the
    fallback loop must not turn "expired" into "signature failed"."""
    from datetime import UTC, datetime, timedelta

    from jose import ExpiredSignatureError, jwt

    from app.core.config import settings
    from app.core.security import decode_internal_jwt

    monkeypatch.setattr(settings, "jwt_signing_key", "k-active-0123456789abcdef")
    monkeypatch.setattr(settings, "jwt_signing_key_fallbacks", ["k-old-0123456789abcdef"])
    expired = jwt.encode(
        {"sub": "x", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        "k-active-0123456789abcdef",
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(ExpiredSignatureError):
        decode_internal_jwt(expired)
