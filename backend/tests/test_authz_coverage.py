"""Structural authorization coverage (ADR-0024) — asserted in BOTH directions.

Forward: every route either declares a permission (a `require_perm(...)` /
platform-admin dependency carrying `authz.PERMISSIONS_ATTR`) or appears in the
explicitly-reviewed `PUBLIC_ROUTES` allow-list WITH a reason. An unclassified
route names itself in the failure — it cannot ship.

Reverse: every `PUBLIC_ROUTES` entry resolves to a live route, so the allow-list
can never hold stale/phantom classifications (the Fleet Fuel `share_revoke`
defect: classified in two structures, existing in none).

The checker itself is proven by a self-test on a scratch app — never by mounting
a fixture route on the real app.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

import app.api.routes as routes_pkg
from app.api.deps import require_perm
from app.core.authz import PERMISSIONS_ATTR, PUBLIC_ROUTES, Permission
from app.core.config import settings
from app.main import app as real_app

# The route surface is large; if enumeration ever silently broke (e.g. a FastAPI
# upgrade changing router internals) an empty list would vacuously "pass", so the
# forward test also asserts we saw at least this many routes.
MIN_EXPECTED_ROUTES = 200


def declared_permissions_of(route: APIRoute) -> list[object]:
    """Every permission declaration reachable from this route: its own and its
    router's `dependencies=[...]` plus any handler-parameter dependency — read
    off the introspection attribute, executing nothing."""
    found: list[object] = []

    def walk(dep) -> None:
        perms = getattr(getattr(dep, "call", None), PERMISSIONS_ATTR, None)
        if perms:
            found.extend(perms)
        for sub in getattr(dep, "dependencies", []):
            walk(sub)

    walk(route.dependant)
    return found


def _flatten(router) -> list[APIRoute]:
    """Every `APIRoute` reachable from `router`, following nested inclusions.

    A route module that is a PACKAGE (`app/api/routes/transport/`) aggregates
    its slices with `include_router`, and FastAPI >= 0.13x records each of
    those lazily as a `_IncludedRouter` wrapper rather than copying the child's
    `APIRoute`s up — so a naive `router.routes` scan sees the package's routes
    as ZERO and every one of them silently escapes this coverage check. That is
    exactly the unclassified-route failure mode ADR-0024 exists to prevent, so
    the walk recurses through `original_router` (the wrapper's own handle on
    the child) instead of trusting the parent's flat list. Child paths already
    carry their full prefix (the prefix is applied when a route is added to the
    child router), so nothing is re-joined here.

    Version-robustness: the recursion keys off the presence of
    `original_router`/`routes`, never off the private wrapper CLASS, so a
    future FastAPI that goes back to eager flattening still enumerates
    correctly (the `isinstance(APIRoute)` branch simply matches first).
    """
    found: list[APIRoute] = []
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            found.extend(_flatten(nested))
    return found


def iter_app_routes() -> list[tuple[str, APIRoute]]:
    """Every (full mounted path, APIRoute) the application serves.

    Enumerated from the route MODULES (each module's router holds `APIRoute`s
    with the prefix already applied, plus — for a package — nested included
    routers, see `_flatten`) plus the app-level meta routes; deliberately NOT
    from FastAPI's lazily-flattened `app.routes` internals, which changed shape
    across versions. `api_router` mounts every module router under
    `settings.api_v1_prefix`."""
    entries: list[tuple[str, APIRoute]] = []
    for route in real_app.routes:
        if isinstance(route, APIRoute):
            entries.append((route.path, route))  # /health, /metrics, ...
    for mod_info in pkgutil.iter_modules(routes_pkg.__path__):
        module = importlib.import_module(f"app.api.routes.{mod_info.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in _flatten(router):
            entries.append((settings.api_v1_prefix + route.path, route))
    return entries


def unclassified_routes(entries: list[tuple[str, APIRoute]]) -> list[str]:
    """The coverage checker: routes with neither a declared permission nor a
    complete PUBLIC_ROUTES entry (a missing or empty reason both fail)."""
    missing: list[str] = []
    for path, route in entries:
        if declared_permissions_of(route):
            continue
        for method in sorted(route.methods):
            reason = PUBLIC_ROUTES.get((method, path))
            if not reason:
                missing.append(f"{method} {path}")
    return missing


def test_every_route_declares_a_permission_or_is_public():
    entries = iter_app_routes()
    assert len(entries) >= MIN_EXPECTED_ROUTES, (
        f"route enumeration looks broken: only {len(entries)} routes found"
    )
    missing = unclassified_routes(entries)
    assert missing == [], (
        "UNCLASSIFIED routes — declare a permission via require_perm(...) or add "
        f"a reasoned PUBLIC_ROUTES entry: {missing}"
    )


def test_public_routes_allowlist_has_no_stale_entries():
    """Reverse direction: an allow-list entry must point at a LIVE route."""
    live: set[tuple[str, str]] = set()
    for path, route in iter_app_routes():
        for method in route.methods:
            live.add((method, path))
    stale = sorted(k for k in PUBLIC_ROUTES if k not in live)
    assert stale == [], f"PUBLIC_ROUTES entries with no live route (remove them): {stale}"


def test_every_public_route_entry_states_a_reason():
    unreasoned = sorted(
        f"{m} {p}" for (m, p), reason in PUBLIC_ROUTES.items() if not (reason and reason.strip())
    )
    assert unreasoned == [], f"PUBLIC_ROUTES entries without a reason: {unreasoned}"


def test_coverage_checker_detects_an_unclassified_route():
    """Prove the checker works: a scratch app (NEVER the real one) with one
    unclassified route and one properly-declared route — the checker must flag
    exactly the unclassified one."""
    scratch_router = APIRouter(prefix="/scratch")

    @scratch_router.get("/open")
    async def open_route():  # pragma: no cover - never called
        return {}

    @scratch_router.post("/gated", dependencies=[Depends(require_perm(Permission.INVOICE_READ))])
    async def gated_route():  # pragma: no cover - never called
        return {}

    scratch_app = FastAPI()
    # Attach the routes directly (include_router is lazy in current FastAPI);
    # the router's own .routes are plain APIRoutes with the prefix applied.
    entries = [(r.path, r) for r in scratch_router.routes if isinstance(r, APIRoute)]
    assert scratch_app.routes is not real_app.routes  # paranoia: not the real app
    assert unclassified_routes(entries) == ["GET /scratch/open"]


def test_enumeration_sees_through_a_nested_included_router():
    """Regression (WO-77): a route module that is a PACKAGE aggregates its
    slices with `include_router`, which current FastAPI records LAZILY — the
    parent's `.routes` holds wrapper objects, not the child's `APIRoute`s. A
    naive scan therefore saw a package's whole route surface as ZERO and every
    route in it escaped this coverage check unnoticed (`MIN_EXPECTED_ROUTES`
    cannot catch it — the total stays comfortably large).

    Proven on a scratch parent/child pair, never the real app."""
    child = APIRouter(prefix="/child")

    @child.get("/leaf", dependencies=[Depends(require_perm(Permission.INVOICE_READ))])
    async def leaf():  # pragma: no cover - never called
        return {}

    parent = APIRouter()
    parent.include_router(child)

    assert [r for r in parent.routes if isinstance(r, APIRoute)] == [], (
        "this test is vacuous unless inclusion is lazy in this FastAPI version"
    )
    flat = _flatten(parent)
    assert [r.path for r in flat] == ["/child/leaf"]
    assert declared_permissions_of(flat[0]) == [Permission.INVOICE_READ]


def test_transport_package_routes_are_inside_the_coverage_net():
    """The concrete consequence of the fix above: every `api/routes/transport/*`
    route (the WO-76 claim lifecycle + the WO-77 admin/config and filing-artifact
    surfaces) is enumerated AND declares a permission. Asserted by count as well
    as by classification, so silently dropping the package again fails here."""
    transport = [(p, r) for p, r in iter_app_routes() if "/transport/" in p]
    assert len(transport) >= 30, f"transport routes vanished from enumeration: {len(transport)}"
    undeclared = sorted(
        f"{sorted(r.methods)[0]} {p}" for p, r in transport if not declared_permissions_of(r)
    )
    assert undeclared == [], f"transport routes with no declared permission: {undeclared}"


def test_declared_permissions_are_introspectable_without_execution():
    """`require_perm` carries its declaration on the callable (ADR-0024), so CI
    can enumerate coverage without invoking any dependency."""
    dep = require_perm(Permission.INVOICE_READ, Permission.EXPORT_RUN)
    assert dep.permissions == (Permission.INVOICE_READ, Permission.EXPORT_RUN)
    assert getattr(dep, PERMISSIONS_ATTR) == (Permission.INVOICE_READ, Permission.EXPORT_RUN)
