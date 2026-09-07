# Flask → InvoiceIQ Lead Developer Orders

Reference: `pallets/flask@d318b683471101618febed18996405ad26462110`
InvoiceIQ base: `525470a154d9f2b85941daca783f0a3499ec6526`

## Order 1 — FLASK-P2-01 JWT signing-purpose separation + rotation

Patch:
`patches/flask-jwt-signing-key-rotation.patch`

Before merge, verify whether production still uses `kek_provider=local`.
If it does, the patch is still safe because it does not change the KEK source,
but existing SEC-KEK-001 (independent production KEK) remains recommended.

Required targeted checks:

```bash
cd backend
python -m pytest -q \
  tests/test_sessions.py \
  tests/test_sso_oidc.py \
  tests/test_config.py \
  tests/test_production_config.py

ruff check \
  app/core/config.py \
  app/core/security.py \
  app/services/oidc.py \
  tests/test_sessions.py \
  tests/test_sso_oidc.py

ruff format --check \
  app/core/config.py \
  app/core/security.py \
  app/services/oidc.py \
  tests/test_sessions.py \
  tests/test_sso_oidc.py

mypy app/core
```

If the named config test files differ on current main, run the actual Settings /
production-validation modules containing `_validate_production`.

Required behavioral cases:
1. historical config (JWT_SIGNING_KEY unset) behaves byte-compatibly;
2. current key signs new access token;
3. old access token verifies under fallback;
4. fallback removal kills old signature;
5. DB-session revocation still kills old-key token immediately;
6. OIDC state survives normal staged promotion;
7. compromised/removed fallback fails;
8. tamper/expiry remain rejected.

Production rotation runbook:
- normal rotation: current K0 + fallback K1 pre-stage if desired → current K1,
  fallback K0 → retain >24h → remove K0;
- compromise: current K1, NO K0 fallback, revoke sessions.

Never log keys or return which key validated.

## Order 2 — FLASK-P2-02 route topology guard

Patch:
`patches/flask-route-topology-guard.patch`

Required check:

```bash
cd backend
python -m pytest -q tests/test_route_topology.py tests/test_openapi_truth.py
ruff check tests/test_route_topology.py
ruff format --check tests/test_route_topology.py
```

The actual app must produce zero shadow failures.
Do not add an allowlist until a genuinely intentional shadow exists; if one ever
does, document why the literal route is intentionally unreachable.

## Order 3 — FLASK-P3-01 warning ratchet

Do NOT edit pytest.ini yet.

Run:

```bash
cd backend
python -m pytest -W default::DeprecationWarning 2>&1 | tee deprecation-warnings.txt
```

Classify/fix warnings, then replace blanket ignore with `error` plus exact
third-party exceptions.

See:
`docs/reference/FLASK-P3-01-deprecation-ratchet.md`

## Non-orders

- no Flask dependency;
- no WSGI;
- no Blueprint wrapper;
- no request proxy layer;
- no signed-cookie session migration;
- no app-factory refactor;
- no runtime plugin system;
- no minimum-version dependency matrix;
- no signal-bus replacement for durable jobs.
