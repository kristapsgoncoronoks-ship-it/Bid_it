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


def test_transport_services_do_not_import_other_domain_models():
    """ADR-P3 rule 2 / VAT_HARVEST E.2: transport reads the core through
    SERVICES (`invoice_service`, `vendor_service`, `documents`, `fx`, `vat`),
    never through a raw model join. A module under `services/transport/` may
    import its OWN domain's models (`app.models.transport`, `app.models.base`
    — the portable GUID type + mixins every model uses) but no other domain's
    models. This is the CI assertion ADR-0023 rule 2 promised would land in
    the same PR as the first transport module."""
    allowed_model_prefixes = ("app.models.transport", "app.models.base")
    out: list[str] = []
    for f in _modules("services/transport"):
        for imp in _imports(f):
            if imp == "app.models" or imp.startswith("app.models."):
                if not any(imp == p or imp.startswith(p + ".") for p in allowed_model_prefixes):
                    out.append(f"{f.relative_to(APP.parent)} imports {imp}")
    assert out == []


def test_transport_boundary_check_catches_a_seeded_violation(tmp_path):
    """A coverage/parity check that cannot fail proves nothing (WORK_ORDER_
    TEMPLATE guidance). Seed a file importing a foreign domain's model and
    prove the SAME detection logic `test_transport_services_do_not_import_
    other_domain_models` uses actually flags it — a drift here would mean the
    real test above could silently stop working."""
    bad = tmp_path / "bad_transport_service.py"
    bad.write_text("from app.models.vendor import Vendor\n")
    imported = _imports(bad)
    assert "app.models.vendor" in imported

    allowed_model_prefixes = ("app.models.transport", "app.models.base")
    flagged = imported and any(
        (imp == "app.models" or imp.startswith("app.models."))
        and not any(imp == p or imp.startswith(p + ".") for p in allowed_model_prefixes)
        for imp in imported
    )
    assert flagged, "the boundary-check logic failed to catch a seeded cross-domain import"


# ARCH-008 (audit 2026-09-05): the transport seam was enforced ONE way — the
# test above keeps transport from reaching other domains' models, but nothing
# kept the rest of the application from reaching transport's. The mirror rule,
# with an explicit allowlist: outside transport's own code (its services, its
# routes, its DTOs) and the two registries that must see every table (the
# models package and the tenant guard), `app.models.transport` is not imported.
# Each exception names its reason; adding one is a review decision, not a
# reflex.
TRANSPORT_MODEL_IMPORT_ALLOWLIST: dict[str, str] = {
    # ADR-P3 rule 1: the ONE nullable FK from transport INTO the AP invoice is a
    # database constraint (`ON DELETE SET NULL` on a composite key SQLite/
    # Postgres apply differently), so the purge has to clear it somewhere. The
    # import is LOCAL to `_unlink_purged_invoices`, and
    # `tests/test_recycle_bin_purge_fk.py` fails if a new table of that shape
    # is missed.
    "app/services/invoices.py": "ADR-P3 rule 1 — the purge unlinks transport's nullable FK",
}
_TRANSPORT_OWN_CODE = (
    "services/transport",
    "api/routes/transport",
    "schemas/transport_",
    "models/transport",  # transport's own tables import each other
)
# The two places that must see every table: the models package's own
# `__init__` (the declarative registry) and the tenant guard's registry. Not the
# whole models package — a relationship from an AP model to a transport table
# would be exactly the coupling the rule forbids (R7 review Q4/S3).
_MODEL_REGISTRIES = ("models/__init__.py", "core/tenant.py")


def _reaches_transport_models(imp: str) -> bool:
    return imp == "app.models.transport" or imp.startswith("app.models.transport.")


def _transport_names_reexported_by_the_registry() -> set[str]:
    """`app/models/__init__.py` re-exports the transport models at package
    level, so `from app.models import FuelTransaction` reaches a transport
    table without the string `app.models.transport` appearing anywhere. Read
    the registry once and treat those names as transport's."""
    tree = ast.parse((APP / "models" / "__init__.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and _reaches_transport_models(node.module)
        ):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _transport_reaches(pyfile: pathlib.Path, reexported: set[str]) -> list[str]:
    """Every way `pyfile` can reach a transport table: the module path, or a
    re-exported name from the registry."""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"), filename=str(pyfile))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names if _reaches_transport_models(a.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _reaches_transport_models(node.module):
                out.append(node.module)
            elif node.module == "app.models":
                out.extend(f"app.models.{a.name}" for a in node.names if a.name in reexported)
    return out


def test_other_domains_do_not_import_transport_models():
    """ARCH-008: the mirror of `test_transport_services_do_not_import_other_
    domain_models`. Everything outside transport reads transport through its
    services (`fuel_import`, `vat_claims`, `rebate_ledger`, …) or the queue —
    never through its tables."""
    reexported = _transport_names_reexported_by_the_registry()
    assert reexported, "the registry re-exports transport models; the check relies on that list"
    out: list[str] = []
    for f in sorted(APP.rglob("*.py")):
        rel = str(f.relative_to(APP)).replace("\\", "/")
        if rel.startswith(_TRANSPORT_OWN_CODE) or rel in _MODEL_REGISTRIES:
            continue
        repo_rel = f"app/{rel}"
        for imp in _transport_reaches(f, reexported):
            if repo_rel not in TRANSPORT_MODEL_IMPORT_ALLOWLIST:
                out.append(f"{repo_rel} imports {imp}")
    assert out == [], "\n".join(out)


def test_the_transport_allowlist_is_still_load_bearing():
    """An allowlist entry that no longer imports anything is stale; remove it
    rather than let the exception outlive its reason."""
    for repo_rel in TRANSPORT_MODEL_IMPORT_ALLOWLIST:
        imports = _imports(APP.parent / repo_rel)
        assert any(_reaches_transport_models(i) for i in imports), (
            f"{repo_rel} no longer imports transport models — drop it from the allowlist"
        )


def test_mirror_boundary_check_catches_a_seeded_violation(tmp_path):
    reexported = _transport_names_reexported_by_the_registry()
    bad = tmp_path / "bad_ar_service.py"
    bad.write_text("from app.models.transport.fuel_transaction import FuelTransaction\n")
    assert _transport_reaches(bad, reexported)
    # The registry re-export path: no `app.models.transport` string at all.
    sneaky = tmp_path / "sneaky_service.py"
    sneaky.write_text("from app.models import FuelTransaction, Vendor\n")
    assert _transport_reaches(sneaky, reexported) == ["app.models.FuelTransaction"]
    ok = tmp_path / "ok_service.py"
    ok.write_text("from app.services.transport import fuel_import\nfrom app.models import Vendor\n")
    assert _transport_reaches(ok, reexported) == []


def test_app_package_is_importable():
    """Guards against a boundary rule accidentally introducing a circular import."""
    import importlib

    for mod in ("app.core.errors", "app.core.config", "app.main"):
        importlib.import_module(mod)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
