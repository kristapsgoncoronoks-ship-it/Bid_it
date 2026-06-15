"""EnvKEK: the vendor-neutral, SDK-free production KEK provider whose 32-byte master
key is injected into the ENVIRONMENT by the deployment's secret manager / KMS
(FFS_KEK_KEY, or per-tenant FFS_KEK_KEY_<TENANT>; base64 of exactly 32 bytes).

Covers: wrap/unwrap round-trip + tamper; per-tenant key selection and fallback;
fail-LOUD on a missing/malformed key; get_kek("env") wiring through seal/open with a
DIFFERENT KEK than local (so env and local blobs cannot cross-open); and that a known
provider's construction error PROPAGATES (no silent local downgrade), while an unknown
NAME still falls back to local. All env vars set via monkeypatch.setenv so they are
auto-undone and never leak into other tests."""
import base64
import os

import pytest

import keyvault


def _b64key():
    return base64.b64encode(os.urandom(32)).decode()


# ---------------------------------------------------------------- EnvKEK core
def test_envkek_wrap_unwrap_roundtrip(monkeypatch):
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    kek = keyvault.EnvKEK()
    dek = os.urandom(32)
    assert kek.unwrap(kek.wrap(dek)) == dek


def test_envkek_tampered_wrapped_dek_fails(monkeypatch):
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    kek = keyvault.EnvKEK()
    wrapped = bytearray(kek.wrap(os.urandom(32)))
    wrapped[-1] ^= 0x01
    with pytest.raises(Exception):
        kek.unwrap(bytes(wrapped))


def test_envkek_accepts_urlsafe_b64(monkeypatch):
    monkeypatch.setenv("FFS_KEK_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    kek = keyvault.EnvKEK()
    dek = os.urandom(32)
    assert kek.unwrap(kek.wrap(dek)) == dek


# ---------------------------------------------------------------- per-tenant / BYOK
def test_envkek_per_tenant_key_used(monkeypatch):
    tenant_key = _b64key()
    monkeypatch.setenv("FFS_KEK_KEY_ACME", tenant_key)
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())   # different global key present
    kek = keyvault.EnvKEK(tenant="acme")
    # prove it used the tenant key: decode it ourselves and confirm a wrap matches
    raw = base64.b64decode(tenant_key)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    w = kek.wrap(b"\x00" * 32)
    nonce, ct = w[:12], w[12:]
    assert AESGCM(raw).decrypt(nonce, ct, b"ffs-kek-wrap") == b"\x00" * 32


def test_envkek_falls_back_to_global_when_no_tenant_key(monkeypatch):
    monkeypatch.delenv("FFS_KEK_KEY_ACME", raising=False)
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    kek = keyvault.EnvKEK(tenant="acme")          # no tenant-specific key -> global
    dek = os.urandom(32)
    assert kek.unwrap(kek.wrap(dek)) == dek


def test_envkek_tenant_key_differs_from_global_and_no_cross_unwrap(monkeypatch):
    monkeypatch.setenv("FFS_KEK_KEY_ACME", _b64key())
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    tenant_kek = keyvault.EnvKEK(tenant="acme")
    global_kek = keyvault.EnvKEK()
    dek = os.urandom(32)
    w_tenant = tenant_kek.wrap(dek)
    # the global KEK cannot unwrap a DEK wrapped under the tenant KEK (GCM auth fails)
    with pytest.raises(Exception):
        global_kek.unwrap(w_tenant)


# ---------------------------------------------------------------- fail LOUD
def test_envkek_missing_key_raises(monkeypatch):
    monkeypatch.delenv("FFS_KEK_KEY", raising=False)
    with pytest.raises((RuntimeError, ValueError)):
        keyvault.EnvKEK()


def test_envkek_non_base64_raises(monkeypatch):
    monkeypatch.setenv("FFS_KEK_KEY", "not base64 @@@@ !!!")
    with pytest.raises((RuntimeError, ValueError)):
        keyvault.EnvKEK()


def test_envkek_wrong_length_key_raises(monkeypatch):
    # valid base64 but only 16 bytes -> not a 32-byte KEK
    monkeypatch.setenv("FFS_KEK_KEY", base64.b64encode(os.urandom(16)).decode())
    with pytest.raises((RuntimeError, ValueError)):
        keyvault.EnvKEK()


# ---------------------------------------------------------------- get_kek wiring
def test_get_kek_env_returns_envkek(monkeypatch):
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    assert isinstance(keyvault.get_kek(provider="env"), keyvault.EnvKEK)


def test_get_kek_env_seal_open_roundtrip(monkeypatch):
    key = _b64key()
    monkeypatch.setenv("FFS_KEK_KEY", key)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "env" if k == "keyvault_provider" else d)
    blob = keyvault.seal("p@ssw0rd", "Q8:JUPITER")
    assert keyvault.open(blob, "Q8:JUPITER") == "p@ssw0rd"


def test_env_sealed_blob_cannot_open_under_local(monkeypatch):
    """A blob sealed under the ENV provider must NOT open under the LOCAL provider —
    proves the KEK actually changed (not just a relabel)."""
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "env" if k == "keyvault_provider" else d)
    blob = keyvault.seal("secret", "ctx")
    # now switch the provider to local; the DEK was wrapped under the env KEK
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "local" if k == "keyvault_provider" else d)
    with pytest.raises(Exception):
        keyvault.open(blob, "ctx")


def test_local_sealed_blob_cannot_open_under_env(monkeypatch):
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "local" if k == "keyvault_provider" else d)
    blob = keyvault.seal("secret", "ctx")
    monkeypatch.setenv("FFS_KEK_KEY", _b64key())
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "env" if k == "keyvault_provider" else d)
    with pytest.raises(Exception):
        keyvault.open(blob, "ctx")


def test_get_kek_env_with_no_key_propagates(monkeypatch):
    """Selecting a KNOWN provider whose construction fails must PROPAGATE — NOT silently
    fall back to LocalKEK (that would be a security downgrade)."""
    monkeypatch.delenv("FFS_KEK_KEY", raising=False)
    with pytest.raises((RuntimeError, ValueError)):
        keyvault.get_kek(provider="env")


def test_get_kek_unknown_name_still_falls_back_to_local(monkeypatch):
    # unknown NAME -> local fallback (existing behavior preserved)
    assert isinstance(keyvault.get_kek(provider="bogus_name"), keyvault.LocalKEK)


# ---------------------------------------------------------------- local regression
def test_local_provider_seal_open_unchanged(monkeypatch):
    # default local provider still round-trips (with env key absent)
    monkeypatch.delenv("FFS_KEK_KEY", raising=False)
    monkeypatch.setattr("auth.get_setting", lambda k, d=None: "local" if k == "keyvault_provider" else d)
    blob = keyvault.seal("local-secret", "ctx")
    assert keyvault.open(blob, "ctx") == "local-secret"
    assert isinstance(keyvault.get_kek(), keyvault.LocalKEK)
