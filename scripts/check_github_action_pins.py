#!/usr/bin/env python3
"""Fail if an external GitHub Action is not pinned to a full 40-character SHA."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USE_RE = re.compile(r"^\s*uses:\s*([^#\s]+)")
FULL_SHA_RE = re.compile(r"^[^@\s]+@[0-9a-fA-F]{40}$")


def main() -> int:
    errors: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USE_RE.search(line)
            if match is None:
                continue
            ref = match.group(1)
            if ref.startswith("./"):
                continue
            if not FULL_SHA_RE.fullmatch(ref):
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_no}: mutable/unpinned action: {ref}"
                )

    if errors:
        print("github-action-pins: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    print("github-action-pins: PASS - every external action uses a full SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
