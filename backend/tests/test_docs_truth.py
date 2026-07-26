"""Docs truth-up insurance (WO-10, deliberately cheap).

`README.md` and `ARCHITECTURE.md` once described a ~12-test analytics MVP
against a ~32k-LOC platform — with a bus factor of one, a lying document is
worse than no document. This guard asserts the regenerated front-door docs
exist, don't reassert the known-stale claims, and keep pointing at the real
specification. It is string-level on purpose: anything deeper would itself
drift.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

STALE_CLAIMS = ("12 tests", "analytics MVP", "minimal but scalable production MVP")


def test_front_door_docs_carry_no_stale_claims():
    for name in ("README.md", "ARCHITECTURE.md"):
        text = (REPO / name).read_text(encoding="utf-8")
        for claim in STALE_CLAIMS:
            assert claim not in text, f"{name} still contains the stale claim {claim!r}"


def test_readme_points_at_the_real_specification():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    for pointer in ("docs/architecture/adr", "docs/product", "docs/M0-exit-gate.md"):
        assert pointer in text, f"README.md lost its pointer to {pointer}"


def test_architecture_md_is_a_pointer_not_a_fork():
    """ARCHITECTURE.md must stay a short pointer to docs/architecture/ — a
    second long-form architecture document is how the last one rotted."""
    text = (REPO / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "docs/architecture/overview.md" in text
    assert len(text.splitlines()) < 60, "ARCHITECTURE.md is growing back into a fork"
