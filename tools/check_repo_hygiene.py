#!/usr/bin/env python3
"""Repo-hygiene guard (CI). Fails the build when the working repo tracks something it
should not — so a real client document, a secret, or a stray DB can never be committed.

Two checks over `git ls-files`:
  1. FORBIDDEN — secrets/certs/credential DBs (.secret_key, *.pem, *.pfx, *.p12,
     security.db) must NEVER be tracked. Any hit fails hard.
  2. DATA FILES — every tracked .pdf/.db/.xlsx/.xls must be listed in
     tools/data_fixtures.allow (the known demo fixtures). A new, un-allow-listed data
     file fails the build (add it to the allowlist in the same commit if it is an
     intentional fixture). This is what enforces the documents/ gitignore hygiene fix.

Pure stdlib + git. Run from anywhere inside the repo. Exit 0 = clean, 1 = violation.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # repo root (tools/ is one level down)
ALLOWLIST = os.path.join(HERE, "data_fixtures.allow")

FORBIDDEN_NAMES = {".secret_key", "security.db"}
FORBIDDEN_EXT = {".pem", ".pfx", ".p12"}
DATA_EXT = {".pdf", ".db", ".xlsx", ".xls"}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [ln for ln in out.stdout.splitlines() if ln]


def load_allow():
    allow = set()
    if os.path.exists(ALLOWLIST):
        with open(ALLOWLIST, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    allow.add(ln)
    return allow


def main():
    files = tracked_files()
    errors = []

    # 1) secrets / certs / credential DBs — never tracked
    for f in files:
        base = os.path.basename(f)
        ext = os.path.splitext(f)[1].lower()
        if base in FORBIDDEN_NAMES or ext in FORBIDDEN_EXT:
            errors.append("SECRET/CERT is tracked (never commit this): %s" % f)

    # 2) data files must be allow-listed demo fixtures
    allow = load_allow()
    data = sorted(f for f in files if os.path.splitext(f)[1].lower() in DATA_EXT)
    for f in data:
        if f not in allow:
            errors.append(
                "DATA FILE not allow-listed: %s\n"
                "      -> if this is a REAL client document, do NOT commit it (the live\n"
                "         vault documents/ is gitignored). If it is an intentional demo\n"
                "         fixture, add its path to tools/data_fixtures.allow in this commit." % f)

    if errors:
        sys.stderr.write("REPO HYGIENE CHECK FAILED:\n")
        for e in errors:
            sys.stderr.write("  - %s\n" % e)
        return 1

    print("repo hygiene OK — %d allow-listed data fixtures, no tracked secrets/certs." % len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
