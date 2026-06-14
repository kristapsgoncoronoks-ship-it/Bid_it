"""Envelope-encryption credential custody: the keyvault primitive (seal/open,
AAD binding, tamper detection, pluggable KEK seam, LocalKEK wrap/unwrap) and the
portal_scraper integration — including backward-compat with LEGACY single-key
Fernet credential blobs and their migration via reencrypt_legacy()."""
import importlib

import pytest

import keyvault


# ---------------------------------------------------------------- keyvault core
def test_seal_open_roundtrip():
    blob = keyvault.seal("p@ssw0rd", "Q8:JUPITER")
    assert keyvault.open(blob, "Q8:JUPITER") == "p@ssw0rd"


def test_seal_open_empty_string():
    blob = keyvault.seal("", "ctx")
    assert keyvault.open(blob, "ctx") == ""


def test_aad_mismatch_fails():
    blob = keyvault.seal("secret", "Q8:JUPITER")
    # opening with a DIFFERENT aad must fail the GCM tag — prevents replaying a
    # blob into another (supplier, entity) row.
    with pytest.raises(Exception):
        keyvault.open(blob, "Q8:OMUSS")


def test_single_byte_tamper_fails():
    blob = bytearray(keyvault.seal("secret", "ctx"))
    blob[-1] ^= 0x01                      # flip a ciphertext bit
    with pytest.raises(Exception):
        keyvault.open(bytes(blob), "ctx")


def test_tamper_wrapped_dek_fails():
    blob = bytearray(keyvault.seal("secret", "ctx"))
    # offset 5 (magic) + 2 (u16 len) lands in the wrapped-DEK region
    blob[8] ^= 0x01
    with pytest.raises(Exception):
        keyvault.open(bytes(blob), "ctx")


def test_is_envelope_true_for_sealed():
    assert keyvault.is_envelope(keyvault.seal("x", "ctx")) is True


def test_is_envelope_false_for_fernet_and_random():
    # a legacy Fernet token is urlsafe-base64 starting with 'gAAAAA'
    from cryptography.fernet import Fernet
    tok = Fernet(Fernet.generate_key()).encrypt(b"hi")
    assert keyvault.is_envelope(tok) is False
    import os
    assert keyvault.is_envelope(os.urandom(64)) is False
    assert keyvault.is_envelope(None) is False


def test_random_dek_two_seals_differ_same_plaintext():
    a = keyvault.seal("same", "ctx")
    b = keyvault.seal("same", "ctx")
    assert a != b                          # random DEK + nonce per seal
    assert keyvault.open(a, "ctx") == keyvault.open(b, "ctx") == "same"


def test_plaintext_not_in_blob():
    blob = keyvault.seal("TopSecretValue123", "ctx")
    assert b"TopSecretValue123" not in blob


# ---------------------------------------------------------------- KEK seam
def test_pluggable_kek_provider(monkeypatch):
    calls = {"wrap": 0, "unwrap": 0}

    class FakeKEK(keyvault.KEK):
        # trivial reversible "wrap": XOR each byte with 0x5A and tag a marker so we
        # can prove the seal path actually went through THIS provider.
        def wrap(self, dek):
            calls["wrap"] += 1
            return b"FAKE" + bytes(b ^ 0x5A for b in dek)

        def unwrap(self, wrapped):
            calls["unwrap"] += 1
            assert wrapped.startswith(b"FAKE")
            return bytes(b ^ 0x5A for b in wrapped[4:])

    monkeypatch.setattr(keyvault, "get_kek", lambda provider=None, tenant=None: FakeKEK())
    blob = keyvault.seal("hello", "ctx")
    assert keyvault.open(blob, "ctx") == "hello"
    assert calls["wrap"] == 1 and calls["unwrap"] == 1


def test_localkek_wrap_unwrap_roundtrip():
    import os
    kek = keyvault.LocalKEK()
    dek = os.urandom(32)
    assert kek.unwrap(kek.wrap(dek)) == dek


def test_localkek_tampered_wrapped_dek_fails():
    import os
    kek = keyvault.LocalKEK()
    wrapped = bytearray(kek.wrap(os.urandom(32)))
    wrapped[-1] ^= 0x01
    with pytest.raises(Exception):
        kek.unwrap(bytes(wrapped))


def test_get_kek_defaults_to_local():
    assert isinstance(keyvault.get_kek(), keyvault.LocalKEK)


def test_get_kek_unknown_provider_falls_back_to_local():
    assert isinstance(keyvault.get_kek(provider="does-not-exist"), keyvault.LocalKEK)


# ---------------------------------------------------------------- portal integration
@pytest.fixture()
def ps(tmp_path, monkeypatch):
    import portal_scraper
    importlib.reload(portal_scraper)
    monkeypatch.setattr(portal_scraper, "DB", str(tmp_path / "portal.db"))
    portal_scraper._SCHEMA_READY.clear()
    return portal_scraper


def test_credentials_sealed_as_envelope_and_no_plaintext(ps):
    ps.set_credentials("Q8", "JUPITER", "user@x.com", "p@ssw0rd", extra={"acct": "9912"})
    got = ps.get_credentials("Q8", "JUPITER")
    assert got["secret"] == "p@ssw0rd"
    assert got["extra"] == {"acct": "9912"}
    import sqlite3
    row = sqlite3.connect(ps.DB).execute(
        "SELECT secret_enc, extra FROM portal_credentials").fetchone()
    sec, ext = bytes(row[0]), bytes(row[1])
    assert keyvault.is_envelope(sec) and keyvault.is_envelope(ext)
    assert b"p@ssw0rd" not in sec and b"9912" not in ext


def test_aad_binding_other_row_cannot_decrypt(ps):
    ps.set_credentials("Q8", "JUPITER", "u", "secretA")
    import sqlite3
    blob = bytes(sqlite3.connect(ps.DB).execute(
        "SELECT secret_enc FROM portal_credentials WHERE entity='JUPITER'").fetchone()[0])
    # decrypting JUPITER's blob under a DIFFERENT row's aad must fail (AAD binding)
    with pytest.raises(Exception):
        keyvault.open(blob, ps._aad("Q8", "OMUSS"))
    # the correct aad still works
    assert keyvault.open(blob, ps._aad("Q8", "JUPITER")) == "secretA"


# ---------------------------------------------------------------- BACKWARD-COMPAT
def _seed_legacy_row(ps, supplier, entity, username, secret, extra=None):
    """Write a row whose secret_enc/extra are LEGACY single-key Fernet tokens,
    exactly as the pre-envelope code stored them."""
    f = ps._fernet()
    import json
    sec = f.encrypt((secret or "").encode("utf-8"))
    ext = f.encrypt(json.dumps(extra).encode("utf-8")) if extra is not None else None
    con = ps.connect()
    con.execute("""INSERT INTO portal_credentials (supplier, entity, username, secret_enc, extra)
                   VALUES (?,?,?,?,?)""",
                (supplier.upper(), entity, username, sec, ext))
    con.commit(); con.close()


def test_legacy_fernet_still_decrypts(ps):
    _seed_legacy_row(ps, "Q8", "LEGACY", "u", "oldsecret", extra={"acct": "1"})
    got = ps.get_credentials("Q8", "LEGACY")
    assert got["secret"] == "oldsecret"
    assert got["extra"] == {"acct": "1"}
    # confirm it really was stored as a non-envelope (legacy) blob
    import sqlite3
    raw = bytes(sqlite3.connect(ps.DB).execute(
        "SELECT secret_enc FROM portal_credentials WHERE entity='LEGACY'").fetchone()[0])
    assert not keyvault.is_envelope(raw)


def test_reencrypt_legacy_upgrades_to_envelope(ps):
    _seed_legacy_row(ps, "Q8", "LEGACY", "u", "oldsecret", extra={"acct": "1"})
    ps.set_credentials("Q8", "NEW", "u", "newsecret")     # already-envelope row
    upgraded, total = ps.reencrypt_legacy()
    assert upgraded == 1 and total == 2
    import sqlite3
    con = sqlite3.connect(ps.DB)
    sec = bytes(con.execute(
        "SELECT secret_enc FROM portal_credentials WHERE entity='LEGACY'").fetchone()[0])
    assert keyvault.is_envelope(sec)                      # now an envelope blob
    # the secret still reads back correctly after migration
    assert ps.get_credentials("Q8", "LEGACY")["secret"] == "oldsecret"
    assert ps.get_credentials("Q8", "LEGACY")["extra"] == {"acct": "1"}
    # idempotent: a second pass upgrades nothing
    assert ps.reencrypt_legacy()[0] == 0
