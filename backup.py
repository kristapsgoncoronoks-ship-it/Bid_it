"""
BACKUP & HARDENING - protects the system of record (3 databases + documents/ +
audit history that lives inside the database files).

    python3 backup.py                  snapshot -> backups/ffs_YYYYmmdd_HHMMSS.zip
                                       (includes write-once CSV export of every
                                        audit_log + SHA-256 manifest; rotates, keeps 14)
    python3 backup.py --verify <zip>   check every file against the manifest
    python3 backup.py --restore <zip> --to <dir>   restore snapshot to a folder
    python3 backup.py --harden         restrictive permissions on data files

Policy: run after every monthly close and before any bulk change; sync the
backups/ folder to versioned storage (OneDrive/SharePoint) so history survives
machine loss. Audit CSVs inside each snapshot are the tamper-evidence copy of
the change log (an attacker editing audit_log in the live DB cannot edit
yesterday's snapshot).
"""
import os, sys, csv, json, hashlib, sqlite3, zipfile, glob, io, tempfile, time
from datetime import datetime
from datetime import timezone as _tz

WORKDIR = os.path.dirname(os.path.abspath(__file__))
BACKUPDIR = os.path.join(WORKDIR, "backups")
KEEP = 14
DATA = ["customers.db", "suppliers.db", "fuel_history.db", "vat_claims.db", "security.db",
        "data_lake.db", "import_log.db"]
EXTRA_DIRS = ["documents", "data_lake"]
EXCLUDE_PREFIX = ("backups", "__pycache__", ".secret_key")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


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
    except sqlite3.OperationalError:
        w.writerow(["(no audit_log in this database)"])
    con.close()
    return out.getvalue().encode()


def snapshot():
    os.makedirs(BACKUPDIR, exist_ok=True)
    ts = datetime.now(_tz.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUPDIR, f"ffs_{ts}.zip")
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
                except OSError: pass
            z.writestr(f"data/{db}", raw); manifest[f"data/{db}"] = _sha(raw)
            a = _audit_csv(db)
            z.writestr(f"audit_export/{db}.audit.csv", a)
            manifest[f"audit_export/{db}.audit.csv"] = _sha(a)
        for d in EXTRA_DIRS:
            for f in glob.glob(os.path.join(WORKDIR, d, "*")):
                arc = f"{d}/{os.path.basename(f)}"
                raw = open(f, "rb").read()
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
    # rotation
    snaps = sorted(glob.glob(os.path.join(BACKUPDIR, "ffs_*.zip")))
    for old in snaps[:-KEEP]:
        os.remove(old)
    return path, len(manifest)


def last_snapshot():
    """(path, mtime_epoch) of the newest snapshot, or (None, None)."""
    snaps = sorted(glob.glob(os.path.join(BACKUPDIR, "ffs_*.zip")))
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


def verify(path):
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("MANIFEST.sha256.json"))
        bad = [a for a, h in manifest.items() if _sha(z.read(a)) != h]
    return bad


def restore(path, target):
    os.makedirs(target, exist_ok=True)
    target_real = os.path.realpath(target)
    with zipfile.ZipFile(path) as z:
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
        bad = verify(sys.argv[sys.argv.index("--verify") + 1])
        print("VERIFY:", "OK - all hashes match" if not bad else f"FAILED: {bad}")
    elif "--restore" in sys.argv:
        src = sys.argv[sys.argv.index("--restore") + 1]
        to = sys.argv[sys.argv.index("--to") + 1]
        print("restored to", restore(src, to))
    elif "--harden" in sys.argv:
        for f, m in harden():
            print(f"  chmod {m}  {f}")
    else:
        path, n = snapshot()
        print(f"snapshot: {path} ({n} files in manifest, keeping last {KEEP})")
