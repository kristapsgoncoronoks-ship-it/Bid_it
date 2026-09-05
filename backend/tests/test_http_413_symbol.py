"""The pinned Starlette deprecates `HTTP_413_REQUEST_ENTITY_TOO_LARGE` in
favour of `HTTP_413_CONTENT_TOO_LARGE` (RFC 9110's name). The rename was
carried as debt across four work orders and grew from 8 to 11 sites while it
waited — every new upload route copied the symbol its nine neighbours used,
which was the right call for one route and exactly how a deprecation spreads.

This is the structural guard that stops it coming back: the same grep-based
shape `lock.py`'s "only withdraw_claim deletes a lock row" test uses (R5).
A code review cannot be relied on to notice one more copy of a constant that
still works; a test that fails on the first copy can.
"""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
OLD = "HTTP_413_REQUEST_ENTITY_TOO_LARGE"


def test_the_deprecated_413_symbol_is_gone_from_app():
    offenders = sorted(
        str(p.relative_to(APP.parent))
        for p in APP.rglob("*.py")
        if OLD in p.read_text(encoding="utf-8")
    )
    assert offenders == [], f"deprecated {OLD} used in: {offenders}"


def test_the_replacement_is_the_symbol_the_routes_use():
    """Pin the direction of the rename, so a revert to the deprecated name
    fails here rather than as a warning nobody reads."""
    from starlette import status

    assert status.HTTP_413_CONTENT_TOO_LARGE == 413
    users = [p for p in APP.rglob("*.py") if "HTTP_413_CONTENT_TOO_LARGE" in p.read_text("utf-8")]
    assert len(users) >= 9, "the upload routes should all be on the new symbol"
