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

import ast
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
    """Check response owners and route forwarding, independent of file count.

    CAP-RESOURCE centralized ten response sites in read_upload; requiring nine
    files with the constant rejected that refactor without testing any response.
    Actual 413 behavior is covered by the route/ingress boundary regressions.
    """
    from starlette import status

    from app.api.upload_read import read_upload
    from tests.test_upload_resource_bounds import CASES

    assert status.HTTP_413_CONTENT_TOO_LARGE == 413
    owners = {
        "api/upload_read.py": "read_upload",
        "api/routes/invoices.py": "_admit_one_upload",
        "api/routes/issued.py": "add_issued_attachment",
        "api/attachment_body_limit.py": "__call__",
    }
    for relative, function in owners.items():
        assert _uses_named_413((APP / relative).read_text("utf-8"), function), relative
    for module, name, _, _ in CASES:
        assert module.read_upload is read_upload, f"{module.__name__}.{name} bypasses HTTP adapter"
        assert "read_upload" in getattr(module, name).__code__.co_names


def _uses_named_413(source: str, function: str) -> bool:
    target = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
    )
    return any(
        isinstance(node, ast.Attribute) and node.attr == "HTTP_413_CONTENT_TOO_LARGE"
        for node in ast.walk(target)
    )


def test_named_413_guard_rejects_deprecated_and_literal_replacements():
    for expression in (f"status.{OLD}", "413"):
        assert not _uses_named_413(f"def respond():\n return {expression}", "respond")
    assert _uses_named_413("def respond():\n return status.HTTP_413_CONTENT_TOO_LARGE", "respond")
