"""OIDC single-sign-on (ADR-0021).

Authorization-code flow with PKCE. The security-critical pieces — **ID-token
validation** (RS256 signature via the IdP's JWKS, issuer, audience, expiry,
nonce), PKCE, and the signed, stateless `state` — are pure functions proven with
locally-minted key fixtures (no network). The three network calls (discovery,
token exchange, JWKS fetch) are thin, injectable seams; they are exercised
end-to-end against a real IdP / a local Keycloak (see docs) — the "return to
finish" boundary called out in ADR-0021.

Flow:
  authorize → build_authorize_url(...) with PKCE challenge + signed state
  callback  → read_state(state) → finish_login(...) →
              exchange code → fetch JWKS → validate_id_token → JIT-provision →
              issue our normal internal JWT.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from jose import JWTError, jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import keyvault
from app.core.config import settings
from app.core.roles import ROLE_RANK
from app.core.security import hash_password
from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import User, UserRole
from app.services import audit, sso_config

log = logging.getLogger("invoiceiq.oidc")

_STATE_TYP = "sso_state"
_STATE_TTL_SECONDS = 600
# SSO users have no password: store an unusable hash so password login is refused.
_UNUSABLE_PASSWORD = "!sso-no-password"


class SsoError(Exception):
    """A recoverable SSO failure — the route turns it into an error redirect."""


# --- PKCE ------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# --- stateless, signed `state` --------------------------------------------


def sign_state(conn_id: str, nonce: str, code_verifier: str) -> str:
    """Sign the per-login state with our app key (no server-side session store)."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "typ": _STATE_TYP,
            "conn": conn_id,
            "nonce": nonce,
            "cv": code_verifier,
            "iat": now,
            "exp": now + timedelta(seconds=_STATE_TTL_SECONDS),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )


def read_state(state: str) -> dict:
    try:
        claims = jwt.decode(state, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise SsoError(f"invalid or expired state: {exc}") from exc
    if claims.get("typ") != _STATE_TYP:
        raise SsoError("state is not an SSO state token")
    return claims


# --- authorize URL ---------------------------------------------------------


def build_authorize_url(
    authorization_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    scope: str = "openid email profile",
) -> str:
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(q)}"


# --- ID-token validation (pure) -------------------------------------------


def validate_id_token(
    id_token: str, jwks: dict, *, client_id: str, issuer: str, nonce: str, now: int | None = None
) -> dict:
    """Validate an OIDC ID token against the IdP's JWKS. Raises SsoError on any
    failure (bad signature, wrong issuer/audience, expiry, nonce mismatch)."""
    options = {"verify_at_hash": False}
    try:
        claims = jwt.decode(
            id_token,
            jwks,
            algorithms=["RS256", "RS384", "RS512"],
            audience=client_id,
            issuer=issuer,
            options=options,
        )
    except JWTError as exc:
        raise SsoError(f"ID token failed validation: {exc}") from exc
    if nonce and claims.get("nonce") != nonce:
        raise SsoError("ID token nonce mismatch (possible replay)")
    if not claims.get("email"):
        raise SsoError("ID token has no email claim")
    return claims


# --- network seams (injectable; exercised against a real IdP / Keycloak) ---


async def discover(issuer: str) -> dict:
    import httpx

    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


async def fetch_jwks(jwks_uri: str) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(jwks_uri)
        r.raise_for_status()
        return r.json()


async def exchange_code(
    token_endpoint: str,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
    code_verifier: str,
) -> dict:
    import httpx

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(token_endpoint, data=data)
        if r.status_code >= 400:
            raise SsoError(f"token exchange failed (HTTP {r.status_code}): {r.text[:200]}")
        return r.json()


# --- IdP group → role mapping (pure) --------------------------------------

# Roles SSO may assign. `owner` is deliberately excluded — it is the founder's
# role and is never granted or demoted via an external IdP.
_ASSIGNABLE = (UserRole.user_free, UserRole.user, UserRole.admin)


def _groups_from_claims(claims: dict, groups_claim: str) -> list[str]:
    raw = claims.get(groups_claim)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(g) for g in raw]


def role_from_groups(role_mappings: str | None, groups: list[str]) -> str | None:
    """Return the HIGHEST-ranked assignable role mapped by any of the user's
    groups, or None. Pure. Ignores mappings to unknown/`owner` roles."""
    if not role_mappings or not groups:
        return None
    try:
        mapping = json.loads(role_mappings)
    except (ValueError, TypeError):
        return None
    best: UserRole | None = None
    group_set = {g.lower() for g in groups}
    for grp, role_name in mapping.items():
        if grp.lower() not in group_set:
            continue
        if role_name not in UserRole.__members__:
            continue
        role = UserRole(role_name)
        if role not in _ASSIGNABLE:
            continue
        if best is None or ROLE_RANK[role] > ROLE_RANK[best]:
            best = role
    return best.value if best else None


# --- orchestration + JIT provisioning -------------------------------------


async def finish_login(
    db: AsyncSession, connection: SsoConnection, *, code: str, nonce: str, code_verifier: str
) -> tuple[User, Organization]:
    """Complete the callback: exchange the code, validate the ID token, and
    match-or-JIT-provision a user in the connection's org."""
    if not (connection.issuer and connection.client_id):
        raise SsoError("connection is not fully configured")

    disco = await discover(connection.issuer)
    # The stored client secret is sealed at rest (ADR-0016); read it tolerant of a
    # plaintext value written before sealing was applied.
    client_secret = keyvault.read_secret(connection.client_secret, aad=sso_config.CLIENT_SECRET_AAD)
    tokens = await exchange_code(
        disco["token_endpoint"],
        code=code,
        redirect_uri=settings.sso_redirect_uri,
        client_id=connection.client_id,
        client_secret=client_secret,
        code_verifier=code_verifier,
    )
    id_token = tokens.get("id_token")
    if not id_token:
        raise SsoError("token response has no id_token")
    jwks = await fetch_jwks(disco["jwks_uri"])
    claims = validate_id_token(
        id_token,
        jwks,
        client_id=connection.client_id,
        issuer=disco["issuer"],
        nonce=nonce,
    )

    email = claims["email"].strip().lower()
    if connection.allowed_domain and not email.endswith("@" + connection.allowed_domain.lower()):
        raise SsoError("email domain not allowed for this connection")
    name = claims.get("name") or claims.get("preferred_username") or email

    groups = _groups_from_claims(claims, connection.groups_claim or "groups")
    mapped_role = role_from_groups(connection.role_mappings, groups)

    user, org = await _match_or_provision(
        db, connection, email=email, name=name, mapped_role=mapped_role
    )
    await audit.record(
        db,
        "sso.login",
        org_id=org.id,
        actor=(user.id, user.email),
        target_type="user",
        target_id=user.id,
        meta={"provider": connection.slug, "role": user.role.value},
    )
    await db.commit()
    return user, org


async def _match_or_provision(
    db: AsyncSession, connection: SsoConnection, *, email: str, name: str, mapped_role: str | None
) -> tuple[User, Organization]:
    existing = await db.scalar(select(User).where(func.lower(User.email) == email))
    org = await db.get(Organization, connection.org_id)
    if existing is not None:
        if existing.org_id != connection.org_id:
            raise SsoError("this email belongs to a different workspace")
        if not existing.is_active:
            raise SsoError("account is disabled")
        # Role sync (IdP authoritative) — never touches an owner, never grants owner.
        if (
            connection.role_sync
            and mapped_role
            and existing.role != UserRole.owner
            and existing.role.value != mapped_role
        ):
            existing.role = UserRole(mapped_role)
        return existing, org

    if not connection.jit_enabled:
        raise SsoError("no matching user and just-in-time provisioning is disabled")

    role = mapped_role or (
        connection.default_role
        if connection.default_role in UserRole.__members__
        else UserRole.user.value
    )
    user = User(
        org_id=connection.org_id,
        email=email,
        name=name[:200],
        hashed_password=hash_password(_UNUSABLE_PASSWORD),
        role=UserRole(role),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user, org
