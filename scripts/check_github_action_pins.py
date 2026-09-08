#!/usr/bin/env python3
"""SEC-SC-001 (Personal-Security-Checklist / Paperless / Stirling / Flask cycles,
reference integration R3, 2026-09-07): every external GitHub Action must be
referenced by a full 40-hex commit SHA, never a mutable tag.

A tag (`actions/checkout@v7`) can be moved by whoever controls the upstream
repository — or by whoever compromises it — and every workflow that names it
runs the new code on the next push, with the token permissions of the job. A
commit SHA cannot move. Keep the `# vN` comment after the SHA: Dependabot's
`github-actions` ecosystem reads it to propose the next pinned bump.

Stdlib only; run from anywhere: `python scripts/check_github_action_pins.py`.
Exit 1 with one line per offending `uses:`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USE_RE = re.compile(r"^\s*-?\s*uses:\s*([^#\s]+)")
FULL_SHA_RE = re.compile(r"^[^@\s]+@[0-9a-fA-F]{40}$")


def unpinned(workflows: Path = WORKFLOWS) -> list[str]:
    """Every `uses:` that is neither a local action (`./…`) nor `owner/repo@<40 hex>`."""
    errors: list[str] = []
    for path in sorted(workflows.glob("*.y*ml")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = USE_RE.search(line)
            if not m:
                continue
            ref = m.group(1)
            if ref.startswith("./"):
                continue
            if not FULL_SHA_RE.fullmatch(ref):
                rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
                errors.append(f"{rel}:{line_no}: mutable/unpinned action: {ref}")
    return errors


def main() -> int:
    errors = unpinned()
    if errors:
        print("github-action-pins: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print("github-action-pins: PASS — every external action uses a full 40-char SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
