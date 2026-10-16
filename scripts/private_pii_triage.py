#!/usr/bin/env python3
"""Prepare a private, masked history inventory; never approve or rewrite history.

All findings remain unresolved. A test-shaped path is a review hint, not proof
of synthetic provenance. Named human verification and the owner-held denylist
remain required. Output must live outside the checkout and is mode 0600.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

import pii_scan


def inventory(root: Path, entries: list, salt: str, allowed: set) -> dict:
    def git(*args: str, data: bytes | None = None) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args], input=data)

    objects = {}
    for line in git("rev-list", "--objects", "--all").decode().splitlines():
        sha, _, hint = line.partition(" ")
        objects.setdefault(sha, hint)
    types = git("cat-file", "--batch-check", data=("\n".join(objects) + "\n").encode())
    rows = []
    scanned = 0
    for line in types.decode().splitlines():
        sha, kind, size = line.split()
        if kind != "blob" or int(size) > pii_scan.MAX_FILE_BYTES:
            continue
        scanned += 1
        content = git("cat-file", "blob", sha)
        if b"\0" in content[:8192]:
            continue
        findings = pii_scan.scan_text(
            content.decode(errors="replace"), entries, salt, allowed, "blob"
        )
        if findings:
            path = objects[sha]
            rows.append(
                {
                    "blob": sha,
                    "path_hint": path,
                    "findings": findings,
                    "classification": "unresolved",
                    "review_hint": "test-path candidate; provenance unverified"
                    if "test" in Path(path).parts or "tests" in Path(path).parts
                    else "manual provenance review required",
                    "refs": [],
                }
            )
    affected = {row["blob"]: row for row in rows}
    refs = git("for-each-ref", "--format=%(refname)").decode().splitlines()
    for ref in refs:
        present = {
            line.split(" ", 1)[0]
            for line in git("rev-list", "--objects", ref).decode().splitlines()
        }
        for sha in present & affected.keys():
            affected[sha]["refs"].append(ref)
    return {
        "head": git("rev-parse", "HEAD").decode().strip(),
        "scanned_blobs": scanned,
        "refs_scanned": len(refs),
        "affected_blobs": len(rows),
        "finding_rows": sum(len(row["findings"]) for row in rows),
        "classification_counts": dict(Counter(row["classification"] for row in rows)),
        "denylist_entries": len(entries),
        "limitations": [
            "No identifier values or source snippets are stored.",
            "Path hints are not an exhaustive rename/path history.",
            "All matching blobs remain unresolved; fixture paths do not prove synthetic data.",
            "No allowlist, denylist, ref or history has been modified.",
        ],
        "blobs": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=pii_scan.REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    if output.is_relative_to(root):
        parser.error("Private triage output must be outside the checkout")
    entries, salt = pii_scan.load_denylist(pii_scan.DEFAULT_DENYLIST)
    allowed = pii_scan.load_allowlist(pii_scan.DEFAULT_ALLOWLIST)
    result = inventory(root, entries, salt, allowed)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(
        f"Private inventory: {result['finding_rows']} masked rows in {result['affected_blobs']} blobs; all unresolved."
    )


if __name__ == "__main__":
    main()
