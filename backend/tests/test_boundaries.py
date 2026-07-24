"""Architectural boundary tests — enforce the module layering the architecture
docs claim, so a well-meaning import can't quietly erode it.

The layers, bottom-up:

    models   — ORM tables + enums. The bottom. Depend on nothing app-specific.
    core     — cross-cutting infrastructure (config, db, tenant, storage,
               errors, observability). May use models; must not know about
               services or the web layer.
    services — business logic. May use core + models; must NOT import the API
               (web) layer — that would invert the dependency.
    api      — FastAPI routes. The top; may import anything below.

These are verified by static AST inspection of the import statements (no code is
executed), so the test is fast and catches the violation at the source line.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _imports(pyfile: pathlib.Path) -> set[str]:
    """Every dotted module referenced by `import x` / `from x import ...`."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _modules(subpkg: str) -> list[pathlib.Path]:
    return sorted((APP / subpkg).rglob("*.py"))


def _violations(subpkg: str, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for f in _modules(subpkg):
        for imp in _imports(f):
            if any(imp == p or imp.startswith(p + ".") for p in forbidden_prefixes):
                out.append(f"{f.relative_to(APP.parent)} imports {imp}")
    return out


def test_models_do_not_import_services_or_api():
    assert _violations("models", ("app.services", "app.api")) == []


def test_core_does_not_import_services_or_api():
    """core is infrastructure — it sits UNDER services and the web layer."""
    assert _violations("core", ("app.services", "app.api")) == []


def test_services_do_not_import_the_web_layer():
    """A service raising an HTTP error belongs to the API layer, not the service.
    Services signal failure with app.core.errors.AppError instead."""
    assert _violations("services", ("app.api",)) == []


def test_app_package_is_importable():
    """Guards against a boundary rule accidentally introducing a circular import."""
    import importlib

    for mod in ("app.core.errors", "app.core.config", "app.main"):
        importlib.import_module(mod)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
