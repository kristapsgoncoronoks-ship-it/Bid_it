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

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from jwt import InvalidTokenError as JWTError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import keyvault, outbound_http
from app.core.config import settings
from app.core.roles import (
    ASSIGNABLE_ROLES,
    IDP_ASSIGNABLE_ROLES,
    ROLE_RANK,
    idp_assignable_role_values,
)
from app.core.security import decode_internal_jwt, unusable_password_hash
from app.models.organization import Organization
from app.models.sso import SsoConnection
from app.models.user import User, UserRole
from app.services import audit, memberships, sso_config, sso_transactions

log = logging.getLogger("invoiceiq.oidc")

_STATE_TYP = "sso_state"
_STATE_TTL_SECONDS = sso_transactions.TTL_SECONDS
_IDP_TIMEOUT_SECONDS = 15.0


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
        settings.active_jwt_signing_key,  # SEC-JWT-001: the same rotatable key as access tokens
        algorithm=settings.jwt_algorithm,
    )


def read_state(state: str) -> dict:
    try:
        claims = decode_internal_jwt(state)
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
    _assert_safe_idp_url(authorization_endpoint)
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
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in ("RS256", "RS384", "RS512"):
            raise SsoError("ID token requires an RSA signing algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise SsoError("ID token has no signing key ID")
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise SsoError("Invalid identity provider signing keys")
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
        if len(matches) != 1:
            raise SsoError("Unknown or ambiguous ID token signing key")
        key = matches[0]
        if (
            key.get("kty") != "RSA"
            or key.get("use", "sig") != "sig"
            or key.get("alg", header["alg"]) != header["alg"]
            or "verify" not in key.get("key_ops", ["verify"])
        ):
            raise SsoError("Invalid ID token verification key")
        public_key = jwt.PyJWK.from_dict(key, algorithm=header["alg"]).key
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256", "RS384", "RS512"],
            audience=client_id,
            issuer=issuer,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except (JWTError, ValueError, TypeError, KeyError) as exc:
        raise SsoError("ID token failed validation") from exc
    if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
        raise SsoError("ID token has no subject")
    for claim in ("exp", "iat"):
        if type(claims[claim]) is not int:
            raise SsoError("ID token timestamps must be integer seconds")
    if claims["exp"] <= claims["iat"]:
        raise SsoError("ID token expiry must follow issuance")
    if isinstance(claims["aud"], list) and len(claims["aud"]) > 1:
        if claims.get("azp") != client_id:
            raise SsoError("ID token authorized party mismatch")
    if claims.get("azp", client_id) != client_id:
        raise SsoError("ID token authorized party mismatch")
    if not nonce or claims.get("nonce") != nonce:
        raise SsoError("ID token nonce mismatch (possible replay)")
    if not isinstance(claims.get("email"), str) or not claims["email"].strip():
        raise SsoError("ID token has no email claim")
    if claims.get("email_verified") is False:
        raise SsoError("Identity provider reports an unverified email")
    return claims


# --- network seams (injectable; exercised against a real IdP / Keycloak) ---


def _assert_safe_idp_url(url: object) -> None:
    """Validate URL syntax/literals without blocking the loop on DNS.

    Every actual server request additionally resolves and pins its public
    destination through the shared transport. No preflight-DNS trust window.
    Error messages deliberately omit the URL, query and any credentials.
    """
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise SsoError("identity provider URL is invalid")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise SsoError("identity provider URL is invalid") from exc
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or "#" in url
        or "\\" in url
        or port == 0
    ):
        raise SsoError("identity provider URL requires HTTPS without credentials or fragments")
    hostname = host.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise SsoError("identity provider destination is not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return  # The transport checks every DNS answer immediately before connecting.
    if not outbound_http.is_public_address(address):
        raise SsoError("identity provider destination is not allowed")


def _validate_discovery(issuer: str, metadata: dict) -> dict:
    _assert_safe_idp_url(issuer)
    if "?" in issuer or not isinstance(metadata, dict) or metadata.get("issuer") != issuer:
        raise SsoError("identity provider metadata does not match the configured issuer")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        _assert_safe_idp_url(metadata.get(field))
    return metadata


async def _request_json(method: str, url: str, *, data: dict | None = None) -> dict:
    _assert_safe_idp_url(url)
    try:
        # Bounds DNS as well as HTTP; resolution happens off the event loop in
        # the transport. Ambient proxies must not replace the pinned transport.
        async with asyncio.timeout(_IDP_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=_IDP_TIMEOUT_SECONDS,
                transport=outbound_http.PinnedPublicAsyncHTTPTransport(),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.request(method, url, data=data)
                if not 200 <= response.status_code < 300:
                    # Providers may reflect a submitted secret/code in their body.
                    raise SsoError(
                        f"identity provider request failed (HTTP {response.status_code})"
                    )
                value = response.json()
    except (
        httpx.HTTPError,
        httpx.InvalidURL,
        outbound_http.UnsafeOutboundUrl,
        TimeoutError,
    ) as exc:
        raise SsoError(
            "identity provider is unavailable or its destination is not allowed"
        ) from exc
    except ValueError as exc:
        raise SsoError("identity provider returned an invalid response") from exc
    if not isinstance(value, dict):
        raise SsoError("identity provider returned an invalid response")
    return value


async def discover(issuer: str) -> dict:
    _assert_safe_idp_url(issuer)
    if "?" in issuer:
        raise SsoError("identity provider issuer must not contain a query")
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return _validate_discovery(issuer, await _request_json("GET", url))


async def fetch_jwks(jwks_uri: str) -> dict:
    return await _request_json("GET", jwks_uri)


async def exchange_code(
    token_endpoint: str,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
    code_verifier: str,
) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    return await _request_json("POST", token_endpoint, data=data)


# --- IdP group → role mapping (pure) --------------------------------------

# Roles SSO may assign: `roles.IDP_ASSIGNABLE_ROLES` (`ASSIGNABLE_ROLES` minus
# `owner`). ONE vocabulary (WO-AE). This used to be its own three-member tuple
# — `user_free`, `user`, `admin` — written before A1.5 made the four business
# roles assignable, and never widened. The consequence was silent: a group
# mapped to `finance_manager`, `accountant`, `approver` or `auditor` hit the
# `continue` below and the user landed unmapped, while the admin who
# configured it saw a saved mapping and no error.
_ASSIGNABLE: tuple[UserRole, ...] = IDP_ASSIGNABLE_ROLES

# Tie-break order when two mapped roles share a rank. `ROLE_RANK` puts the four
# business roles level with `user` (none holds SETTINGS_MANAGE), so "highest
# wins" alone is a tie — and before WO-AE the winner was whichever key the JSON
# happened to list first, which is not a rule anyone can state. Declaration
# order in `ASSIGNABLE_ROLES` is the rule now: later in the list wins a tie, so
# a business role beats plain `user` and the business roles order among
# themselves as `authz.Role` declares them.
_TIE_ORDER: dict[UserRole, int] = {r: i for i, r in enumerate(ASSIGNABLE_ROLES)}


def assignable_role_values() -> tuple[str, ...]:
    """The stored values an IdP may assign — the list the settings screen is
    served (`SsoConnectionOut.assignable_roles`, `GET /sso/assignable-roles`)."""
    return idp_assignable_role_values()


def _groups_from_claims(claims: dict, groups_claim: str) -> list[str]:
    raw = claims.get(groups_claim)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(g) for g in raw]


def role_from_groups(role_mappings: str | None, groups: list[str]) -> str | None:
    """Return the HIGHEST-ranked assignable role mapped by any of the user's
    groups, or None. Pure. Ignores mappings to unknown/`owner` roles (the
    schema refuses to SAVE those since WO-AE; this is defence in depth for a
    row written before it). Equal rank → later in `ASSIGNABLE_ROLES` wins."""
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
        if best is None or (ROLE_RANK[role], _TIE_ORDER[role]) > (
            ROLE_RANK[best],
            _TIE_ORDER[best],
        ):
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

    disco = _validate_discovery(connection.issuer, await discover(connection.issuer))
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
        issuer=connection.issuer,
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
    if org is None:
        raise SsoError("organization not found")
    if existing is not None:
        if existing.org_id != connection.org_id:
            raise SsoError("this email belongs to a different workspace")
        if not existing.is_active:
            raise SsoError("account is disabled")
        membership = await memberships.get(db, connection.org_id, existing.id)
        if membership is None or membership.status != "active":
            raise SsoError("membership is unavailable")
        # Role sync (IdP authoritative) — never touches an owner, never grants owner.
        if (
            connection.role_sync
            and mapped_role
            and membership.role != UserRole.owner
            and membership.role.value != mapped_role
        ):
            membership.role = UserRole(mapped_role)
            existing.role = membership.role  # legacy login/default projection only
        return existing, org

    if not connection.jit_enabled:
        raise SsoError("no matching user and just-in-time provisioning is disabled")

    # The JIT default must itself be IdP-assignable (WO-AE): a stored
    # `default_role` of `owner` — unreachable through the schema now, possible
    # in a row written before it — falls back to `user`, so the "never grants
    # owner" promise above holds on this path too, not only on the mapped one.
    role = mapped_role or (
        connection.default_role
        if connection.default_role in assignable_role_values()
        else UserRole.user.value
    )
    user = User(
        org_id=connection.org_id,
        email=email,
        name=name[:200],
        # SSO users have no password (SEC-001): the sentinel, never a hash.
        hashed_password=unusable_password_hash(),
        role=UserRole(role),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    # Dual-write the membership (Slice 6b).
    await memberships.ensure(
        db,
        org_id=connection.org_id,
        user_id=user.id,
        role=UserRole(role),
        email=user.email,
        name=user.name,
    )
    return user, org
