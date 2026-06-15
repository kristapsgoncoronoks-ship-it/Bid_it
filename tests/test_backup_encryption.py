"""Tests for OPT-IN encryption-at-rest of backup snapshots (backup.backup_key /
_encrypt_blob / _decrypt_blob / _is_encrypted and the snapshot()/verify()/restore()/
rotation paths). Hermetic: a tmp BACKUPDIR/WORKDIR, the real backups/ folder is never
touched and no demo DBs are read. The CARDINAL constraint under test is ZERO regression
when no key is set — see the no-key cases at the bottom and tests/test_backup_sync.py."""
import base64
import importlib
import os

import pytest

# A deterministic, valid 32-byte key (base64 std). A SECOND, different key drives the
# wrong-key decrypt case.
KEY32 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
OTHER_KEY32 = base64.b64encode(b"ZZZZ56789abcdefZZZZ56789abcdefZZ").decode()

# A recognizable plaintext marker we seed into WORKDIR; it must NOT be findable in an
# encrypted snapshot's ciphertext (proves the bytes are actually encrypted, not just
# renamed). Long + unusual so a chance collision is implausible.
SENTINEL = b"PLAINTEXT_SENTINEL_FFS_DO_NOT_LEAK_0xDEADBEEF"


@pytest.fixture()
def bk(tmp_path, monkeypatch):
    """backup module driven against an isolated tmp BACKUPDIR/WORKDIR, no DBs/extra
    dirs, with a single sentinel code-report file so the manifest is non-empty and the
    ciphertext-leak check has a known marker to look for. Off-site sync + backup key are
    both cleared from the env so each test opts in explicitly."""
    import backup
    importlib.reload(backup)
    work = tmp_path / "app"; work.mkdir()
    monkeypatch.setattr(backup, "WORKDIR", str(work))
    monkeypatch.setattr(backup, "BACKUPDIR", str(tmp_path / "backups"))
    os.makedirs(backup.BACKUPDIR, exist_ok=True)
    monkeypatch.setattr(backup, "DATA", [])            # no DBs to copy
    monkeypatch.setattr(backup, "EXTRA_DIRS", [])
    # seed one code-report file holding the sentinel (snapshot() globs WORKDIR/*.py)
    with open(os.path.join(work, "marker.py"), "wb") as f:
        f.write(b"# " + SENTINEL + b"\n")
    monkeypatch.delenv("FFS_BACKUP_SYNC_DIR", raising=False)
    monkeypatch.delenv("FFS_BACKUP_KEY", raising=False)
    return backup


def _stub_auth_setting(monkeypatch, val):
    """Stub the lazily-imported auth so backup.backup_key()'s setting branch returns val."""
    import sys, types
    stub = types.ModuleType("auth")
    stub.get_setting = lambda k, default="": (val if val else default)
    monkeypatch.setitem(sys.modules, "auth", stub)


# ---------------------------------------------------------------- backup_key()
def test_backup_key_unset_is_none(bk, monkeypatch):
    _stub_auth_setting(monkeypatch, "")                # env unset + setting empty
    assert bk.backup_key() is None


def test_backup_key_env_wins(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    _stub_auth_setting(monkeypatch, OTHER_KEY32)       # setting differs; env must win
    assert bk.backup_key() == base64.b64decode(KEY32)


def test_backup_key_from_setting(bk, monkeypatch):
    monkeypatch.delenv("FFS_BACKUP_KEY", raising=False)
    _stub_auth_setting(monkeypatch, KEY32)
    assert bk.backup_key() == base64.b64decode(KEY32)


def test_backup_key_malformed_raises(bk, monkeypatch):
    # Set-but-malformed must FAIL LOUD, never silently fall back to plaintext.
    monkeypatch.setenv("FFS_BACKUP_KEY", "not-a-valid-32-byte-base64-key!!")
    with pytest.raises(ValueError):
        bk.backup_key()


# ---------------------------------------------------------------- blob helpers
def test_encrypt_decrypt_roundtrip(bk):
    key = base64.b64decode(KEY32)
    blob = bk._encrypt_blob(b"hello world", key)
    assert blob.startswith(bk._ENC_MAGIC)
    assert bk._decrypt_blob(blob, key) == b"hello world"


def test_decrypt_wrong_key_raises(bk):
    blob = bk._encrypt_blob(b"secret", base64.b64decode(KEY32))
    with pytest.raises(Exception):
        bk._decrypt_blob(blob, base64.b64decode(OTHER_KEY32))


def test_decrypt_tampered_raises(bk):
    key = base64.b64decode(KEY32)
    blob = bytearray(bk._encrypt_blob(b"secret", key))
    blob[-1] ^= 0x01                                   # flip a ciphertext byte
    with pytest.raises(Exception):
        bk._decrypt_blob(bytes(blob), key)


def test_is_encrypted_distinguishes(bk, tmp_path):
    enc = tmp_path / "e.bin"; enc.write_bytes(bk._ENC_MAGIC + b"....")
    plain = tmp_path / "p.bin"; plain.write_bytes(b"PK\x03\x04rest")
    assert bk._is_encrypted(str(enc)) is True
    assert bk._is_encrypted(str(plain)) is False
    assert bk._is_encrypted(str(tmp_path / "missing")) is False


# ---------------------------------------------------------------- encrypted snapshot
def test_snapshot_encrypts_when_key_set(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, n = bk.snapshot()
    assert path.endswith(".zip.enc")
    assert n >= 1
    # no plaintext .zip left behind, only the .enc artifact
    files = os.listdir(bk.BACKUPDIR)
    assert any(f.endswith(".zip.enc") for f in files)
    assert not any(f.endswith(".zip") for f in files)
    # magic header present and the plaintext sentinel is NOT in the ciphertext
    blob = open(path, "rb").read()
    assert blob.startswith(bk._ENC_MAGIC)
    assert SENTINEL not in blob
    assert bk._is_encrypted(path) is True


def test_verify_passes_on_encrypted(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, _ = bk.snapshot()
    assert bk.verify(path) == []                       # all hashes match


def test_restore_decrypts_and_extracts(bk, monkeypatch, tmp_path):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, _ = bk.snapshot()
    target = tmp_path / "restored"
    bk.restore(path, str(target))
    # the seeded code-report file came back with its plaintext sentinel intact
    out = target / "marker.py"
    assert out.exists()
    assert SENTINEL in out.read_bytes()


def test_last_snapshot_and_rotation_see_enc(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    # pre-seed > KEEP encrypted snapshots directly so rotation has work to do
    for i in range(bk.KEEP + 3):
        p = os.path.join(bk.BACKUPDIR, f"ffs_202601{i:02d}_000000.zip.enc")
        with open(p, "wb") as f:
            f.write(bk._ENC_MAGIC + b"x")
    path, _ = bk.snapshot()                            # triggers rotation
    remaining = sorted(f for f in os.listdir(bk.BACKUPDIR) if f.startswith("ffs_"))
    assert len(remaining) == bk.KEEP                   # pruned to KEEP
    ls_path, ls_mtime = bk.last_snapshot()
    assert ls_path.endswith(".zip.enc") and ls_mtime is not None
    assert os.path.basename(path) == os.path.basename(ls_path)


# ---------------------------------------------------------------- wrong/missing key
def test_verify_encrypted_no_key_clear_error(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, _ = bk.snapshot()
    monkeypatch.delenv("FFS_BACKUP_KEY", raising=False)
    _stub_auth_setting(monkeypatch, "")                # truly no key now
    with pytest.raises(ValueError) as ei:
        bk.verify(path)
    assert "no backup key" in str(ei.value) or "cannot decrypt" in str(ei.value)


def test_restore_encrypted_no_key_clear_error(bk, monkeypatch, tmp_path):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, _ = bk.snapshot()
    monkeypatch.delenv("FFS_BACKUP_KEY", raising=False)
    _stub_auth_setting(monkeypatch, "")
    with pytest.raises(ValueError):
        bk.restore(path, str(tmp_path / "out"))


def test_verify_wrong_key_clear_error(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, _ = bk.snapshot()
    monkeypatch.setenv("FFS_BACKUP_KEY", OTHER_KEY32)  # different valid key
    with pytest.raises(ValueError) as ei:
        bk.verify(path)
    assert "cannot decrypt" in str(ei.value)


def test_verify_flipped_byte_fails(bk, monkeypatch):
    monkeypatch.setenv("FFS_BACKUP_KEY", KEY32)
    path, _ = bk.snapshot()
    blob = bytearray(open(path, "rb").read())
    blob[-1] ^= 0x01                                   # corrupt ciphertext
    with open(path, "wb") as f:
        f.write(bytes(blob))
    with pytest.raises(ValueError):
        bk.verify(path)


# ---------------------------------------------------------------- ZERO regression (no key)
def test_snapshot_plaintext_when_no_key(bk, monkeypatch):
    monkeypatch.delenv("FFS_BACKUP_KEY", raising=False)
    _stub_auth_setting(monkeypatch, "")
    path, n = bk.snapshot()
    assert path.endswith(".zip") and not path.endswith(".zip.enc")
    assert bk._is_encrypted(path) is False
    blob = open(path, "rb").read()
    assert blob.startswith(b"PK")                      # a real, plaintext zip
    # cleartext content is recoverable from the (DEFLATE-compressed) plaintext zip
    import zipfile, io
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert SENTINEL in z.read("code_reports/marker.py")


def test_verify_restore_plaintext_no_key(bk, monkeypatch, tmp_path):
    monkeypatch.delenv("FFS_BACKUP_KEY", raising=False)
    _stub_auth_setting(monkeypatch, "")
    path, _ = bk.snapshot()
    assert bk.verify(path) == []                       # behaves exactly as before
    target = tmp_path / "restored"
    bk.restore(path, str(target))
    assert (target / "marker.py").exists()
    assert SENTINEL in (target / "marker.py").read_bytes()
