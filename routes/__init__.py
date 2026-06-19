"""
routes/ — Flask BLUEPRINT package (incremental split of the monolithic app.py).

WHY THIS EXISTS
---------------
app.py grew to ~15k lines. We are splitting it group-by-group into blueprints so
each cohesive route group lives in its own module, WITHOUT changing any behaviour.
This package holds those blueprints; app.py imports and registers each one.

THE PATTERN (so the next group is mechanical)
---------------------------------------------
1. Pick a small, cohesive route group. Create routes/<group>.py defining a
   `Blueprint("<bp_name>", __name__)` and move the view functions onto it
   (decorate with `@bp.route(...)` instead of `@app.route(...)`), keeping each
   URL PATH BYTE-IDENTICAL — only the Flask ENDPOINT NAME changes.

2. ENDPOINT-NAME REMAP: a view `foo` on blueprint `bp_name` is now reachable as
   the endpoint `"bp_name.foo"` (NOT "foo"). EVERY place that refers to the
   endpoint BY NAME must move in lockstep — this is the security-critical part,
   because the authorization layer keys off endpoint names:
     - the classification sets in app.py: PERM_BY_ENDPOINT, ADMIN_ONLY,
       OPEN_ENDPOINTS, API_V1_SCOPE
     - the MODULES map / _ENDPOINT_MODULE
     - any `request.endpoint == "<name>"` / `in (...)` checks in the before/after
       hooks (_guard, _cloudflare_origin_lock, _api_v1_guard/_meter)
     - every `url_for("<name>")` in code AND templates  -> `url_for("bp.<name>")`
   The startup self-check `_assert_endpoint_coverage()` (run under
   FFS_STRICT_ENDPOINTS=1) fails closed if any endpoint is left unclassified,
   which catches a missed rename immediately.

   URLs do NOT change, so hardcoded nav `href="/dash"` links and client JS that
   fetch("/api/...") keep working untouched. Only `url_for`/classification use
   the new dotted endpoint name.

3. SHARED HELPERS without import cycles: app.py imports this package, so a
   blueprint module must NOT `import app` (that would be circular). A blueprint
   reaches shared state by importing the LEAF modules directly (e.g. `queries`,
   `dataproduct`, `money`, `markupsafe.escape`) — never through app.py. The
   global before/after hooks (_guard, CSRF, actor set/reset, origin lock,
   coverage check) are registered on the `app` object itself, so they ALSO run
   for blueprint routes unchanged — no per-blueprint wiring needed.

CURRENT BLUEPRINTS
------------------
- analytics_api (routes/analytics_api.py): the read-only JSON analytics twins
  (/api/periods, /api/benchmark, /api/compare, /api/headtohead, /api/entities).
  Chosen as the FIRST slice because it is small, self-contained (depends only on
  `dataproduct` + `queries`), login-only (OPEN_ENDPOINTS), and referenced
  nowhere by url_for — the lowest-risk possible proof of the pattern.
"""
