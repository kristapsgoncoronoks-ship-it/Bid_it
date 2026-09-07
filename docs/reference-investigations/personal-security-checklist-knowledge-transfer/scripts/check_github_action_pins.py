#!/usr/bin/env python3
"""Fail if an external GitHub Action is not pinned to a full 40-char SHA."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USE_RE = re.compile(r"^\s*uses:\s*([^#\s]+)")
FULL_SHA_RE = re.compile(r"^[^@\s]+@[0-9a-fA-F]{40}$")

errors: list[str] = []
for path in sorted(WORKFLOWS.glob("*.y*ml")):
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = USE_RE.search(line)
        if not m:
            continue
        ref = m.group(1)
        if ref.startswith("./"):
            continue
        # Docker container images are not steps[*].uses action refs here; this checks action refs.
        if not FULL_SHA_RE.fullmatch(ref):
            errors.append(f"{path.relative_to(ROOT)}:{line_no}: mutable/unpinned action: {ref}")

if errors:
    print("github-action-pins: FAIL", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)
    raise SystemExit(1)

print("github-action-pins: PASS — every external action uses a full 40-char SHA")
