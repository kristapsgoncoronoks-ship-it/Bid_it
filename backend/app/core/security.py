"""Password hashing and JWT creation/validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# bcrypt hashes only the first 72 bytes of the (UTF-8-encoded) password. We
# truncate explicitly to that limit: it preserves the historical passlib
# behaviour (so hashes created before this change still verify) and avoids the
# ValueError that bcrypt >= 5 raises on over-length input. Output stays the
# standard "$2b$" modular-crypt format, so existing stored hashes are unaffected.
_BCRYPT_MAX_BYTES = 72


def _pw_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_pw_bytes(plain), bcrypt.gensalt()).decode("ascii")


# SEC-001. An account that must never sign in with a password (SSO/SCIM
# provisioned) stores THIS, not a hash. It used to store
# `hash_password("!sso-no-password")` — a real bcrypt hash of a string that is
# in the public source tree, so the literal itself was a working password for
# every IdP-provisioned user, admins included, on `POST /auth/login`. A hash
# that can be verified is a password; the only unusable value is one that is
# not a hash at all. `"!"` is not modular-crypt, so `bcrypt.checkpw` raises and
# `verify_password` answers False for every input — and the prefix is refused
# explicitly below so the guarantee does not rest on the library's error path.
UNUSABLE_PASSWORD_HASH = "!"


def unusable_password_hash() -> str:
    """The stored value for an account with NO password (SSO/SCIM). Never a
    hash: see `UNUSABLE_PASSWORD_HASH`."""
    return UNUSABLE_PASSWORD_HASH


def has_usable_password(hashed: str | None) -> bool:
    """True only for a real bcrypt hash. `None`, the unusable sentinel and any
    non-modular-crypt string are all "no password"."""
    return hashed is not None and hashed.startswith("$2")


# The two literals the provisioning paths used to hash (SEC-001). Kept ONLY so
# the data migration can recognise the hashes they produced and retire them,
# and so `verify_password` can refuse them as PLAINTEXT on every login: a row
# the bounded migration did not reach (an org that deleted its SSO connection
# after provisioning) still carries a verifiable hash of one of these, and the
# refusal below is what makes that residue harmless. Nothing may hash them
# again — `test_sec001_unusable_password.py` greps for it.
LEGACY_UNUSABLE_LITERALS: tuple[str, ...] = ("!sso-no-password", "!scim-no-password")


def _checkpw(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def verify_password(plain: str, hashed: str | None) -> bool:
    if hashed is None or not has_usable_password(hashed):
        return False
    if plain in LEGACY_UNUSABLE_LITERALS:
        # SEC-001: the retired provisioning literals are never a password,
        # whatever hash the row still carries (see LEGACY_UNUSABLE_LITERALS).
        return False
    return _checkpw(plain, hashed)


def is_legacy_unusable_hash(hashed: str | None) -> bool:
    """Does this stored hash verify against one of the retired literals — i.e.
    is it the backdoor SEC-001 closed? Costs one bcrypt check per literal.
    Uses the raw check on purpose: `verify_password` refuses the literals."""
    if hashed is None or not has_usable_password(hashed):
        return False
    return any(_checkpw(lit, hashed) for lit in LEGACY_UNUSABLE_LITERALS)


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    """Create a signed JWT. `subject` is the user id; `extra` adds claims (e.g. org)."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
