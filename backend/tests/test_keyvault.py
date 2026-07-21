"""Application-level secret encryption (ADR-0016): AES-256-GCM seal/unseal,
AAD binding, tamper + wrong-key detection, BYOK, and the SSO client-secret is
sealed at rest."""
import base64

import pytest

from app.core import keyvault


def test_roundtrip():
    t = keyvault.seal("s3cr3t", aad="ctx")
    assert keyvault.is_sealed(t) and t != "s3cr3t"
    assert keyvault.unseal(t, aad="ctx") == "s3cr3t"


def test_nonce_randomised_but_both_unseal():
    a = keyvault.seal("x", aad="c")
    b = keyvault.seal("x", aad="c")
    assert a != b                                  # random nonce per seal
    assert keyvault.unseal(a, aad="c") == keyvault.unseal(b, aad="c") == "x"


def test_wrong_aad_rejected():
    t = keyvault.seal("v", aad="purpose-a")
    with pytest.raises(keyvault.KeyvaultError):
        keyvault.unseal(t, aad="purpose-b")


def test_tampered_ciphertext_rejected():
    t = keyvault.seal("v", aad="c")
    tampered = t[:-2] + ("AA" if not t.endswith("AA") else "BB")
    with pytest.raises(keyvault.KeyvaultError):
        keyvault.unseal(tampered, aad="c")


def test_wrong_key_rejected(monkeypatch):
    from app.core.config import settings
    t = keyvault.seal("v", aad="c")
    # Rotate the KEK (BYOK) → the old ciphertext no longer unseals.
    monkeypatch.setattr(settings, "kek_key", base64.b64encode(b"k" * 32).decode())
    with pytest.raises(keyvault.KeyvaultError):
        keyvault.unseal(t, aad="c")


def test_byok_key(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "kek_key", base64.b64encode(b"z" * 32).decode())
    t = keyvault.seal("v", aad="c")
    assert keyvault.unseal(t, aad="c") == "v"


def test_bad_byok_key_length(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "kek_key", base64.b64encode(b"short").decode())
    with pytest.raises(keyvault.KeyvaultError):
        keyvault.seal("v", aad="c")


def test_read_secret_tolerant():
    assert keyvault.read_secret(None) is None
    assert keyvault.read_secret("plaintext", aad="c") == "plaintext"   # pass-through
    sealed = keyvault.seal("real", aad="c")
    assert keyvault.read_secret(sealed, aad="c") == "real"


def test_unseal_non_sealed_raises():
    with pytest.raises(keyvault.KeyvaultError):
        keyvault.unseal("not-a-token", aad="c")


# --- integration: SSO client secret sealed at rest -------------------------

@pytest.mark.asyncio
async def test_sso_client_secret_sealed_in_db(auth_client, db_session):
    from sqlalchemy import select

    from app.models.sso import SsoConnection
    from app.services import sso_config

    r = await auth_client.put("/api/v1/sso/connection", json={
        "slug": "acme", "issuer": "https://idp.example.com",
        "client_id": "cid", "client_secret": "plaintext-secret",
    })
    assert r.status_code == 200
    assert "client_secret" not in r.json()          # never returned
    assert r.json()["has_client_secret"] is True

    conn = await db_session.scalar(select(SsoConnection))
    await db_session.refresh(conn)
    # Stored value is sealed, not the plaintext.
    assert conn.client_secret != "plaintext-secret"
    assert keyvault.is_sealed(conn.client_secret)
    assert keyvault.unseal(conn.client_secret, aad=sso_config.CLIENT_SECRET_AAD) == "plaintext-secret"
