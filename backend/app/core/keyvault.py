"""Application-level secret encryption (ADR-0016).

Encrypts secrets we must store and later USE (unlike passwords, which are hashed
one-way) — e.g. a tenant's SSO OAuth client secret. AES-256-GCM (authenticated:
tamper → decrypt fails loud) with a random 96-bit nonce per seal. The AAD binds a
ciphertext to its purpose so a value can't be lifted from one field into another.

Key management (the KEK):
- default (`kek_provider=local`) — a 32-byte KEK derived from the app `secret_key`
  (SHA-256). Fine for single-node / dev; rotating the app secret invalidates
  sealed values, so BYOK is preferred in production.
- BYOK (`kek_key` set) — a base64-encoded 32-byte key from config/secret store.
- **Scale path (documented, ADR-0016):** a cloud KMS (AWS/GCP/Azure) wrapping a
  per-secret DEK (true envelope). This module is the seam that swaps in.

Sealed format: ``kv1.<base64(nonce(12) || ciphertext||tag)>``. Never logs
plaintext; GCM auth failures RAISE `KeyvaultError` (never silently return "").
"""
from __future__ import annotations

import base64
import hashlib

from app.core.config import settings

_PREFIX = "kv1."


class KeyvaultError(Exception):
    """Sealing/unsealing failed (bad key, tampered ciphertext, wrong context)."""


def _kek() -> bytes:
    """Resolve the 32-byte key-encryption key from config (computed per call so a
    config change / test override is honoured)."""
    if settings.kek_key:
        try:
            raw = base64.b64decode(settings.kek_key)
        except Exception as exc:  # noqa: BLE001
            raise KeyvaultError("kek_key is not valid base64") from exc
        if len(raw) != 32:
            raise KeyvaultError("kek_key must decode to exactly 32 bytes")
        return raw
    # local provider: derive from the app secret.
    return hashlib.sha256(settings.secret_key.encode()).digest()


def is_sealed(value: str | None) -> bool:
    return bool(value) and value.startswith(_PREFIX)


def seal(plaintext: str, *, aad: str = "") -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os

    nonce = os.urandom(12)
    ct = AESGCM(_kek()).encrypt(nonce, plaintext.encode(), aad.encode())
    return _PREFIX + base64.b64encode(nonce + ct).decode()


def unseal(token: str, *, aad: str = "") -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_sealed(token):
        raise KeyvaultError("value is not a sealed secret")
    try:
        blob = base64.b64decode(token[len(_PREFIX):])
        nonce, ct = blob[:12], blob[12:]
        return AESGCM(_kek()).decrypt(nonce, ct, aad.encode()).decode()
    except KeyvaultError:
        raise
    except Exception as exc:  # noqa: BLE001 - bad key / tamper / truncation
        raise KeyvaultError("could not unseal secret (wrong key or tampered)") from exc


def read_secret(value: str | None, *, aad: str = "") -> str | None:
    """Tolerant read: unseal a sealed value; pass a plaintext value through
    (transition safety for a secret written before sealing was applied)."""
    if value is None:
        return None
    return unseal(value, aad=aad) if is_sealed(value) else value
