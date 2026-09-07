"""Structural API-route topology guard (FLASK-P2-02, reference integration 2026-09-07).

Starlette/FastAPI dispatch is registration-order sensitive: a parameter route
registered before a later literal route consumes that literal first, e.g.
`/issued/{invoice_id}` before `/issued/recurring` would answer `GET
/issued/recurring` with "invoice 'recurring' not found". The routers carry a
manual "register the specific route first" comment for exactly this; this test
turns that tribal knowledge into a structural invariant checked on every run.

Zero allowlist by design: a shadowed literal route is unreachable, and there is
no legitimate reason to register one.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.main import app

_NON_DISPATCH_METHODS = frozenset({"HEAD", "OPTIONS"})


def _methods(route: APIRoute) -> frozenset[str]:
    return frozenset(route.methods or ()) - _NON_DISPATCH_METHODS


def _static_shadow_failures(application: FastAPI) -> list[str]:
    routes = [r for r in application.routes if isinstance(r, APIRoute)]
    failures: list[str] = []

    for later_index, later in enumerate(routes):
        # A literal route is the victim: the operator believes the exact URL
        # exists, but an earlier parameter route intercepts it.
        if "{" in later.path:
            continue
        later_methods = _methods(later)
        if not later_methods:
            continue
        for earlier in routes[:later_index]:
            if "{" not in earlier.path:
                continue
            shared_methods = later_methods & _methods(earlier)
            if not shared_methods:
                continue
            # Starlette already compiled the real converter-aware regex; reuse
            # it rather than re-interpreting `:int`, `:path`, `:uuid` by hand.
            if earlier.path_regex.fullmatch(later.path):
                failures.append(
                    f"{','.join(sorted(shared_methods))} {later.path!r} is shadowed by "
                    f"earlier {earlier.path!r}"
                )
    return failures


def test_no_literal_api_route_is_shadowed_by_an_earlier_parameter_route():
    failures = _static_shadow_failures(app)
    assert not failures, (
        "Route registration order makes literal endpoints unreachable. Register "
        "the specific route before the catch-all parameter route, or narrow the "
        "converter.\n  - " + "\n  - ".join(failures)
    )


def test_route_topology_guard_detects_the_failure_it_claims_to_detect():
    """Seeded violation on a fixture app: the guard must name the shadowed pair."""
    fixture = FastAPI()

    @fixture.get("/items/{item_id}")
    async def by_id(item_id: str):
        return {"id": item_id}

    @fixture.get("/items/special")
    async def special():
        return {"special": True}

    failures = _static_shadow_failures(fixture)
    assert len(failures) == 1
    assert "/items/special" in failures[0]
    assert "/items/{item_id}" in failures[0]


def test_route_topology_guard_ignores_different_methods_and_correct_order():
    fixture = FastAPI()

    @fixture.get("/items/special")
    async def special():
        return {"special": True}

    @fixture.get("/items/{item_id}")
    async def by_id(item_id: str):
        return {"id": item_id}

    @fixture.delete("/items/{item_id}")
    async def delete_by_id(item_id: str):
        return {"deleted": item_id}

    @fixture.post("/items/archive")  # POST is not shadowed by the GET/DELETE parameter routes
    async def archive():
        return {"ok": True}

    assert _static_shadow_failures(fixture) == []
