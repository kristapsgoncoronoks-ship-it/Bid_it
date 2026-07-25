#!/usr/bin/env python3
"""Build/refresh the hashed PII deny-list from the owner-held identifier file.

The Fleet Fuel repository was deleted on 2026-07-25; the real identifiers it
contained survive only in the owner-held decommission archive as
``identifiers_for_denylist.txt``. This helper turns that file into the
committed, hash-only ``scripts/pii_denylist.json`` WITHOUT the plaintext ever
entering git (the input file is .gitignored defensively and must live outside
the repo or be deleted immediately after use).

Input format — one identifier per line, tab-separated:

    kind<TAB>label<TAB>value

* ``kind``  — e.g. company_name, vat_id, iban, reg_number, address,
  invoice_number, email, phone, contact_name.
* ``label`` — non-identifying handle reported on a hit, e.g.
  ``customer-1-name``. It must never identify the subject.
* ``value`` — the raw identifier. It is normalised (lower-cased, all
  whitespace stripped) and hashed as sha256(salt + normalised); the
  plaintext is NEVER written to the output.

Lines starting with ``#`` and blank lines are skipped.

Usage:

    PII_SCAN_SALT=<the CI secret> python scripts/pii_denylist_build.py \
        /secure/path/identifiers_for_denylist.txt

The salt MUST be the same value stored as the ``PII_SCAN_SALT`` CI secret,
otherwise the scanner's hashes will never match. Fail-closed: a missing salt
or input file aborts; nothing is half-written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DENYLIST_PATH = REPO_ROOT / "scripts" / "pii_denylist.json"
SALT_ENV = "PII_SCAN_SALT"


def normalize(token: str) -> str:
    """Must mirror pii_scan.normalize exactly (lower-case, strip whitespace)."""
    return re.sub(r"\s+", "", token.lower())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "identifiers",
        type=Path,
        nargs="?",
        default=REPO_ROOT / "identifiers_for_denylist.txt",
        help="owner-held TSV of real identifiers (kind<TAB>label<TAB>value)",
    )
    ap.add_argument("--out", type=Path, default=DENYLIST_PATH)
    args = ap.parse_args(argv)

    salt = os.environ.get(SALT_ENV, "")
    if not salt:
        print(f"ERROR: ${SALT_ENV} is unset — refusing to hash.", file=sys.stderr)
        return 2
    if not args.identifiers.is_file():
        print(f"ERROR: identifier file not found: {args.identifiers}", file=sys.stderr)
        return 2

    try:
        current = json.loads(args.out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"ERROR: cannot read existing deny-list at {args.out}", file=sys.stderr)
        return 2

    entries: list[dict] = []
    seen_labels: set[str] = set()
    for lineno, line in enumerate(
        args.identifiers.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not all(p.strip() for p in parts):
            print(
                f"ERROR: line {lineno}: expected kind<TAB>label<TAB>value",
                file=sys.stderr,
            )
            return 2
        kind, label, value = (p.strip() for p in parts)
        if label in seen_labels:
            print(f"ERROR: line {lineno}: duplicate label {label!r}", file=sys.stderr)
            return 2
        seen_labels.add(label)
        norm = normalize(value)
        entries.append(
            {
                "label": label,
                "len": len(norm),
                "sha256": hashlib.sha256((salt + norm).encode("utf-8")).hexdigest(),
                "kind": kind,
            }
        )

    current["entries"] = sorted(entries, key=lambda e: str(e["label"]))
    args.out.write_text(
        json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(entries)} hashed entries to {args.out} (no plaintext).")
    print(
        "REMINDER: the identifier file must never be committed — move it back "
        "to secure storage or delete it now."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
