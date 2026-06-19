"""Proves the first app.py->blueprint slice (routes/analytics_api.py) is a ZERO-behaviour
-change move: identical URLs, unchanged authorization, and full endpoint-coverage.

The five read-only JSON analytics twins moved onto the `analytics_api` Blueprint. Their
URL paths must be byte-identical and their access posture (login-only / OPEN_ENDPOINTS,
processor-visible) must be exactly as before the move.
"""
import auth
import app as A

# (dotted endpoint name on the blueprint, unchanged URL path)
MOVED = [
    ("analytics_api.api_periods",  "/api/periods"),
    ("analytics_api.api_benchmark", "/api/benchmark"),
    ("analytics_api.api_compare",  "/api/compare"),
    ("analytics_api.api_h2h",      "/api/headtohead"),
    ("analytics_api.api_entities", "/api/entities"),
]


def test_moved_endpoints_keep_same_urls():
    """url_for the NEW dotted endpoint name resolves to the SAME path; the bare
    pre-move name no longer resolves (so any stray old reference would fail loudly)."""
    from flask import url_for
    from werkzeug.routing.exceptions import BuildError
    with A.app.test_request_context():
        for ep, path in MOVED:
            assert url_for(ep) == path, ep
        for ep, _path in MOVED:
            bare = ep.split(".", 1)[1]
            try:
                url_for(bare)
                assert False, f"bare endpoint {bare!r} should no longer resolve"
            except BuildError:
                pass


def test_moved_endpoints_are_classified_open():
    """The dotted names are classified login-only (OPEN_ENDPOINTS) and the coverage
    self-check leaves nothing unclassified — i.e. no auth blind spot was introduced."""
    for ep, _path in MOVED:
        assert ep in A.OPEN_ENDPOINTS, ep
    assert A._assert_endpoint_coverage() == set()


def test_moved_routes_still_guarded_login_required():
    """Unauthenticated access still redirects to /login (the global _guard hook applies
    to blueprint routes too)."""
    c = A.app.test_client()           # not logged in
    for _ep, path in MOVED:
        r = c.get(path)
        assert r.status_code == 302 and "/login" in r.headers.get("Location", ""), path


def test_processor_access_unchanged(admin_session):
    """A processor (no admin) still gets 200 on every moved route — these are ungated
    analytics reads, exactly as before the blueprint move."""
    auth.add_user("bp_proc", "Pw!23456", role="processor")
    cp = A.app.test_client()
    assert cp.post("/login", data={"username": "bp_proc",
                                   "password": "Pw!23456"}).status_code == 302
    for _ep, path in MOVED:
        assert cp.get(path).status_code == 200, path


def test_url_resolves_to_dotted_endpoint():
    """The URL map resolves each moved path to its DOTTED blueprint endpoint name — the
    exact value request.endpoint takes inside a request, which the before/after hooks
    (_guard etc.) key off when applying authorization."""
    adapter = A.app.url_map.bind("localhost")
    for ep, path in MOVED:
        matched_ep, _args = adapter.match(path, method="GET")
        assert matched_ep == ep, (path, matched_ep, ep)
