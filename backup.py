"""
BACKUP & HARDENING - protects the system of record (3 databases + documents/ +
audit history that lives inside the database files).

    python3 backup.py                  snapshot -> backups/ffs_YYYYmmdd_HHMMSS.zip
                                       (includes write-once CSV export of every
                                        audit_log + SHA-256 manifest; rotates, keeps 14)
    python3 backup.py --verify <zip>   check every file against the manifest
    python3 backup.py --restore <zip> --to <dir>   restore snapshot to a folder
    python3 backup.py --harden         restrictive permissions on data files

Off-site copy: set FFS_BACKUP_SYNC_DIR (or the admin 'backup_sync_dir' setting)
to a mounted OneDrive/SharePoint/NAS folder. Every snapshot is then best-effort
copied off-machine (same 14-deep rotation), so the history survives disk loss.
A sync failure is logged but NEVER fails or undoes the local snapshot.

Encryption-at-rest (OPT-IN, default OFF — exporter-held key, Art. 32 / Schrems-II
supplementary measure): set FFS_BACKUP_KEY (or the admin 'backup_key' setting) to a
base64 (std OR urlsafe) string of EXACTLY 32 bytes. When set, every snapshot is
AES-256-GCM encrypted at rest and written as backups/ffs_<ts>.zip.enc (the off-site
copy is therefore ciphertext too); with NO key set, snapshots are byte-identical to
before (plaintext ffs_<ts>.zip). This key is SEPARATE from FFS_KEK_KEY (portal-credential
custody) so it can be rotated/escrowed independently. CARDINAL RULE: hold the backup key
OFF the backup target — a key stored alongside the ciphertext defeats the entire control.
A set-but-malformed key FAILS LOUD (never silently downgrades to plaintext). Caveat: the
zip is encrypted whole-in-memory (chunked/streaming GCM is out of scope), so a snapshot
much larger than available RAM is not supported by the encrypted path.

Policy: run after every monthly close and before any bulk change; sync the
backups/ folder to versioned storage (OneDrive/SharePoint) so history survives
machine loss. Audit CSVs inside each snapshot are the tamper-evidence copy of
the change log (an attacker editing audit_log in the live DB cannot edit
yesterday's snapshot).
"""
import os, sys, csv, json, hashlib, sqlite3, zipfile, glob, io, tempfile, time

import db
import applog
import keyvault
from datetime import datetime
from datetime import timezone as _tz

log = applog.get("backup")

# Encryption-at-rest blob header (distinct from keyvault's "FFSv1" envelope magic).
_ENC_MAGIC = b"FFSBK1"      # versioned backup-blob marker (1 = this format)
_ENC_NONCE = 12             # AES-GCM standard nonce length
_ENC_AAD = b"ffs-backup"    # GCM additional-authenticated-data binding the blob to backups

WORKDIR = os.path.dirname(os.path.abspath(__file__))
BACKUPDIR = os.path.join(WORKDIR, "backups")
KEEP = 14
DATA = ["customers.db", "suppliers.db", "fuel_history.db", "vat_claims.db", "security.db",
        "data_lake.db", "import_log.db"]
EXTRA_DIRS = ["documents", "data_lake"]
EXCLUDE_PREFIX = ("backups", "__pycache__", ".secret_key")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def backup_key():
    """Resolve the 32-byte backup encryption key, or None when encryption is disabled.

    FFS_BACKUP_KEY (environment) wins; otherwise the admin 'backup_key' setting (auth
    imported lazily so the CLI / plain imports stay light). The value is base64 (std or
    urlsafe) of EXACTLY 32 bytes, decoded via keyvault._decode_b64_key. Returns None when
    unset (= encryption off, plaintext snapshots as before). A SET-but-malformed key
    RAISES (fail loud) — a misconfigured backup key must NEVER silently fall back to
    plaintext. Never logs the key."""
    raw = os.environ.get("FFS_BACKUP_KEY")
    if raw is None:
        try:
            import auth
            raw = auth.get_setting("backup_key", "") or ""
        except Exception as e:
            log.debug("backup_key: could not read backup_key setting: %s", e)
            raw = ""
    if not (raw or "").strip():
        return None
    # Set-but-malformed -> ValueError from _decode_b64_key (fail loud, do not log the key).
    return keyvault._decode_b64_key(raw)


def _encrypt_blob(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM encrypt the whole backup zip in memory. Layout:
        _ENC_MAGIC | nonce(12) | AES-256-GCM(key, nonce, data, aad=_ENC_AAD)."""
    nonce = os.urandom(_ENC_NONCE)
    ct = keyvault.AESGCM(key).encrypt(nonce, data, _ENC_AAD)
    return _ENC_MAGIC + nonce + ct


def _decrypt_blob(blob: bytes, key: bytes) -> bytes:
    """Reverse _encrypt_blob. Raises on a bad/short magic, truncation, or any tamper /
    wrong key (GCM tag check)."""
    blob = bytes(blob)
    if not blob.startswith(_ENC_MAGIC):
        raise ValueError("backup._decrypt_blob: not a FFSBK1 encrypted backup")
    off = len(_ENC_MAGIC)
    nonce = blob[off:off + _ENC_NONCE]; off += _ENC_NONCE
    if len(nonce) != _ENC_NONCE:
        raise ValueError("backup._decrypt_blob: truncated nonce")
    ct = blob[off:]
    return keyvault.AESGCM(key).decrypt(nonce, ct, _ENC_AAD)


def _is_encrypted(path) -> bool:
    """True iff the file at `path` begins with the encrypted-backup magic header."""
    try:
        with open(path, "rb") as f:
            return f.read(len(_ENC_MAGIC)) == _ENC_MAGIC
    except OSError:
        return False


def _audit_csv(dbfile):
    """Write-once CSV of the full audit log of one database."""
    con = sqlite3.connect(os.path.join(WORKDIR, dbfile))
    out = io.StringIO()
    w = csv.writer(out)
    try:
        cur = con.execute("SELECT id, ts, tbl, rowkey, action, old_data, new_data, "
                          "COALESCE(changed_by,'system') FROM audit_log ORDER BY id")
        w.writerow(["id", "ts", "tbl", "rowkey", "action", "old_data", "new_data", "changed_by"])
        for r in cur:
            w.writerow(r)
    except db.DBError:
        w.writerow(["(no audit_log in this database)"])
    con.close()
    return out.getvalue().encode()


def snapshot():
    os.makedirs(BACKUPDIR, exist_ok=True)
    ts = datetime.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
    # Resolve the key BEFORE building so a malformed key fails loud (and leaves no
    # half-written artifact). None => encryption disabled => plaintext as before.
    key = backup_key()
    plain_path = os.path.join(BACKUPDIR, f"ffs_{ts}.zip")
    # Build into a temp zip; if encrypting we then wrap it into <ts>.zip.enc and remove
    # the plaintext temp. With no key the temp path == the final plaintext path.
    if key is None:
        path = plain_path
    else:
        tmpfd, path = tempfile.mkstemp(prefix=f"ffs_{ts}_", suffix=".zip.tmp", dir=BACKUPDIR)
        os.close(tmpfd)
    manifest = {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        # consistent DB copies via sqlite backup API (safe while app is running)
        for db in DATA:
            src = os.path.join(WORKDIR, db)
            if not os.path.exists(src):
                continue
            # Crash-consistent snapshot via the sqlite3 .backup() API into a temp
            # file, then store that copy (rather than the live file, which may be
            # mid-write). The raw copy is the preferred restore form.
            tmpfd, tmppath = tempfile.mkstemp(prefix=f"bk_{db}_", dir=BACKUPDIR)
            os.close(tmpfd)
            try:
                src_con = sqlite3.connect(src)
                dst_con = sqlite3.connect(tmppath)
                try:
                    src_con.backup(dst_con)
                finally:
                    dst_con.close(); src_con.close()
                raw = open(tmppath, "rb").read()
            finally:
                try: os.remove(tmppath)
                except OSError as e:
                    log.debug("snapshot: temp backup cleanup failed for %s: %s", tmppath, e)
            z.writestr(f"data/{db}", raw); manifest[f"data/{db}"] = _sha(raw)
            a = _audit_csv(db)
            z.writestr(f"audit_export/{db}.audit.csv", a)
            manifest[f"audit_export/{db}.audit.csv"] = _sha(a)
        # The document vault and data lake store files under a nested logical tree
        # (<kind|customer>/.../file), so walk each EXTRA_DIR recursively — a flat glob
        # would hit a subdirectory and abort the whole backup. Archive paths keep the
        # relative tree so restore rebuilds the vault exactly.
        for d in EXTRA_DIRS:
            base = os.path.join(WORKDIR, d)
            for root, _dirs, files in os.walk(base):
                for fn in files:
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, WORKDIR).replace(os.sep, "/")
                    raw = open(full, "rb").read()
                    z.writestr(arc, raw); manifest[arc] = _sha(raw)
        for f in glob.glob(os.path.join(WORKDIR, "*.py")) + \
                 glob.glob(os.path.join(WORKDIR, "*.xlsx")) + \
                 [os.path.join(WORKDIR, "README.md"), os.path.join(WORKDIR, "SECURITY.md")]:
            if not os.path.exists(f):
                continue
            arc = f"code_reports/{os.path.basename(f)}"
            raw = open(f, "rb").read()
            z.writestr(arc, raw); manifest[arc] = _sha(raw)
        z.writestr("MANIFEST.sha256.json", json.dumps(manifest, indent=1))
    # Encryption-at-rest (opt-in): wrap the finished zip into <ts>.zip.enc and drop the
    # plaintext temp. The whole zip is encrypted in memory (see docstring size caveat).
    if key is not None:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            os.remove(path)
        except OSError as e:
            log.debug("snapshot: temp zip cleanup failed for %s: %s", path, e)
        path = plain_path + ".enc"
        blob = _encrypt_blob(raw, key)
        # atomic write: temp file + os.replace so a partial .enc never appears
        tmpfd, tmppath = tempfile.mkstemp(prefix=f"ffs_{ts}_", suffix=".enc.tmp", dir=BACKUPDIR)
        try:
            with os.fdopen(tmpfd, "wb") as f:
                f.write(blob)
            os.replace(tmppath, path)
        finally:
            if os.path.exists(tmppath):
                try: os.remove(tmppath)
                except OSError as e:
                    log.debug("snapshot: enc temp cleanup failed for %s: %s", tmppath, e)
    # rotation (match BOTH plaintext .zip and encrypted .zip.enc; name-sort = ts-sort)
    snaps = sorted(glob.glob(os.path.join(BACKUPDIR, "ffs_*.zip*")))
    for old in snaps[:-KEEP]:
        os.remove(old)
    # Best-effort off-site copy. A sync fault must never break the snapshot, so
    # sync_snapshot() already swallows + logs every error; the extra guard here is
    # belt-and-braces so the local snapshot result is returned unchanged regardless.
    try:
        sync_snapshot(path)
    except Exception as e:  # pragma: no cover - sync_snapshot is already non-raising
        log.warning("snapshot: off-site sync raised unexpectedly: %s", e)
    return path, len(manifest)


def sync_dir():
    """The configured off-machine backup directory (a mounted OneDrive/SharePoint/
    NAS folder), or "" when off-site sync is disabled. FFS_BACKUP_SYNC_DIR wins;
    otherwise the admin 'backup_sync_dir' setting (auth imported lazily so the CLI
    and plain imports stay light and work without security.db)."""
    env = os.environ.get("FFS_BACKUP_SYNC_DIR")
    if env is not None:
        return env.strip()
    try:
        import auth
        return (auth.get_setting("backup_sync_dir", "") or "").strip()
    except Exception as e:
        log.debug("sync_dir: could not read backup_sync_dir setting: %s", e)
        return ""


def sync_snapshot(path=None):
    """Copy ONE snapshot zip to the off-site sync dir (best-effort, non-fatal).
    Returns the destination path on success, or None when sync is disabled, there is
    nothing to copy, or ANY failure occurs (logged, never raised). Copies atomically
    (temp file + os.replace) then prunes the off-site dir to the newest KEEP zips."""
    d = sync_dir()
    if not d:
        return None
    path = path or last_snapshot()[0]
    if not path:
        return None
    try:
        os.makedirs(d, exist_ok=True)
        dest = os.path.join(d, os.path.basename(path))
        with open(path, "rb") as src:
            raw = src.read()
        tmpfd, tmppath = tempfile.mkstemp(prefix=".bk_sync_", dir=d)
        try:
            with os.fdopen(tmpfd, "wb") as f:
                f.write(raw)
            os.replace(tmppath, dest)
        finally:
            if os.path.exists(tmppath):
                try: os.remove(tmppath)
                except OSError as e:
                    log.debug("sync_snapshot: temp cleanup failed for %s: %s", tmppath, e)
        # off-site rotation: keep the newest KEEP (same depth as local). Match both the
        # plaintext .zip and encrypted .zip.enc forms (encrypted stays encrypted off-site).
        synced = sorted(glob.glob(os.path.join(d, "ffs_*.zip*")))
        for old in synced[:-KEEP]:
            try: os.remove(old)
            except OSError as e:
                log.debug("sync_snapshot: could not prune %s: %s", old, e)
        return dest
    except Exception as e:
        log.warning("sync_snapshot: off-site copy to %s failed: %s", d, e)
        return None


def last_synced():
    """(path, mtime_epoch) of the newest snapshot in the off-site sync dir, or
    (None, None) if sync is disabled / none present. Never raises."""
    d = sync_dir()
    if not d:
        return (None, None)
    try:
        synced = sorted(glob.glob(os.path.join(d, "ffs_*.zip*")))
        if not synced:
            return (None, None)
        return (synced[-1], os.path.getmtime(synced[-1]))
    except Exception as e:
        log.debug("last_synced: could not read sync dir %s: %s", d, e)
        return (None, None)


def last_snapshot():
    """(path, mtime_epoch) of the newest snapshot, or (None, None)."""
    snaps = sorted(glob.glob(os.path.join(BACKUPDIR, "ffs_*.zip*")))
    if not snaps:
        return (None, None)
    return (snaps[-1], os.path.getmtime(snaps[-1]))


def due(interval_hours):
    """True if a scheduled backup is due: no snapshot yet, or the newest one is
    older than interval_hours. interval_hours <= 0 means auto-backup is disabled."""
    if not interval_hours or interval_hours <= 0:
        return False
    _, mtime = last_snapshot()
    if mtime is None:
        return True
    return (time.time() - mtime) >= interval_hours * 3600


def _open_zip(path):
    """Return a zipfile.ZipFile for `path`, transparently decrypting an encrypted
    (.zip.enc / FFSBK1) snapshot in memory first. For a plaintext snapshot this opens
    the file directly (byte-identical to before). Raises a CLEAR error when the file is
    encrypted but no backup key is configured, or when decryption fails (tamper / wrong
    key) — never a bare GCM stacktrace bubbling to the caller."""
    if not _is_encrypted(path):
        return zipfile.ZipFile(path)
    key = backup_key()
    if key is None:
        raise ValueError(
            "backup: %s is encrypted but no backup key is configured "
            "(set FFS_BACKUP_KEY / 'backup_key') — cannot decrypt" % os.path.basename(path))
    with open(path, "rb") as f:
        blob = f.read()
    try:
        plain = _decrypt_blob(blob, key)
    except Exception as e:
        raise ValueError(
            "backup: cannot decrypt %s (wrong key or tampered file)"
            % os.path.basename(path)) from e
    return zipfile.ZipFile(io.BytesIO(plain))


def verify(path):
    with _open_zip(path) as z:
        manifest = json.loads(z.read("MANIFEST.sha256.json"))
        bad = [a for a, h in manifest.items() if _sha(z.read(a)) != h]
    return bad


def restore(path, target):
    os.makedirs(target, exist_ok=True)
    target_real = os.path.realpath(target)
    with _open_zip(path) as z:
        for info in z.infolist():
            if info.filename == "MANIFEST.sha256.json":
                continue
            dest_rel = info.filename
            for prefix in ("data/", "code_reports/"):
                if dest_rel.startswith(prefix):
                    dest_rel = dest_rel[len(prefix):]
            dest = os.path.join(target, dest_rel)
            # Zip Slip guard: refuse any entry that escapes the target directory.
            dest_real = os.path.realpath(dest)
            if not (dest_real == target_real or
                    dest_real.startswith(target_real + os.sep)):
                raise ValueError(
                    f"refusing to restore entry escaping target dir: {info.filename!r}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(z.read(info))
    return target


def harden():
    """Restrict access to data files. POSIX: chmod. Windows: icacls (NTFS ACLs -
    chmod is meaningless on NTFS, so we grant only the current user + SYSTEM)."""
    import subprocess
    changed = []
    win = os.name == "nt"
    targets = [(f, False) for f in DATA + [".secret_key"]] + \
              [(d, True) for d in EXTRA_DIRS + ["backups"]]
    for name, is_dir in targets:
        p = os.path.join(WORKDIR, name)
        if not os.path.exists(p):
            continue
        if win:
            try:
                user = os.environ.get("USERNAME", "")
                # remove inherited perms, grant current user + SYSTEM full control
                subprocess.run(["icacls", p, "/inheritance:r",
                                "/grant:r", f"{user}:F", "SYSTEM:F"],
                               capture_output=True, check=False)
                changed.append((name + ("/" if is_dir else ""), "ACL: user+SYSTEM"))
            except Exception as e:
                changed.append((name, f"icacls skipped: {e}"))
        else:
            os.chmod(p, 0o700 if is_dir else 0o600)
            changed.append((name + ("/" if is_dir else ""), "700" if is_dir else "600"))
    return changed


if __name__ == "__main__":
    if "--verify" in sys.argv:
        try:
            bad = verify(sys.argv[sys.argv.index("--verify") + 1])
            print("VERIFY:", "OK - all hashes match" if not bad else f"FAILED: {bad}")
        except ValueError as e:
            print("VERIFY: FAILED -", e)
    elif "--restore" in sys.argv:
        src = sys.argv[sys.argv.index("--restore") + 1]
        to = sys.argv[sys.argv.index("--to") + 1]
        print("restored to", restore(src, to))
    elif "--harden" in sys.argv:
        for f, m in harden():
            print(f"  chmod {m}  {f}")
    else:
        path, n = snapshot()
        enc = " [encrypted-at-rest]" if _is_encrypted(path) else ""
        print(f"snapshot: {path}{enc} ({n} files in manifest, keeping last {KEEP})")
