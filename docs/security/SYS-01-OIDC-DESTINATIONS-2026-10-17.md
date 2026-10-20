# SYS-01 — OIDC destination validation and issuer binding

AI-assisted diagnosis, implementation, regression tests and evidence drafting.
Continues the confirmed finding at `5477dc5`. Implemented locally; human/lead review,
fresh exact-head CI and real enterprise IdP acceptance remain required. No merge,
deployment, dependency upgrade, schema change or security waiver is part of this repair.

## Problem and resulting behavior

A discovery-supplied token endpoint was used without any URL check, transmitting
the client secret, authorization code and PKCE verifier before ID-token validation.
Discovery/JWKS used preflight DNS checks, then an ordinary client which could resolve
a different address or select an ambient proxy. Token errors included provider body
text, which could reflect submitted credentials. The discovery document's issuer
also replaced the configured issuer as the token-validation expectation.

All three server request paths now use the existing
`outbound_http.PinnedPublicAsyncHTTPTransport`: resolve off the event loop, require
every answer to be public, and connect to one vetted address with the original Host
and TLS SNI. Client proxies from the environment are disabled for these requests;
TLS verification stays enabled and transport CA configuration still applies.
Redirects are refused rather than followed. HTTPS syntax, credentials, fragments,
whitespace/backslashes, invalid ports and non-public literal destinations are checked
before I/O. DNS failure and private/mixed DNS answers become safe SSO failures.

Discovery requires a JSON object with an issuer exactly equal to the configured
issuer, plus valid authorization/token/JWKS endpoint URLs. `finish_login` rechecks
that metadata before unsealing the client secret and validates the final token
against the configured issuer. Separate public HTTPS endpoint hosts, custom ports
and endpoint queries remain supported. Issuer queries/fragments are refused.

A 15-second async deadline encloses resolution and HTTP work. Canceled resolution
cannot resume into a late credential transmission. Status, JSON and transport errors
produce `SsoError` without provider body text or submitted secrets in the message.
The authorization route retains its generic 502 response; callback refusal retains
the login error redirect and clears the browser cookie. A consumed transaction
requires starting a fresh login. No JIT user or session is created on refusal.

## Evidence and acceptance

[Machine-readable repair evidence](evidence/SYS-01-OIDC-DESTINATIONS-2026-10-17.json)
records hashes and outcomes without secrets or token values.

- **44 initial regressions failed against the original source**, then all passed.
  These cover private/metadata/literal/credential-bearing destinations, DNS changes
  and mixed answers, failed resolution, environment-proxy bypass, preserved Host/SNI,
  redirects, reflected-error secrecy, invalid metadata, callback recovery and a
  valid multi-host provider with real RSA/JWKS/JIT validation. Only the final socket
  seam is faked in the HTTPX/pinning tests; ASGI callback tests use the actual routes,
  persisted one-use state, sessions and tenant-scoped database logic.
- **48 focused cases passed** after adding invalid-JSON, issuer-query and request/
  off-loop DNS deadline checks. Timed-out DNS completes its owned test thread without
  initiating a later request. One initial artificially short test deadline could
  expire during client initialization; the corrected test exercises an entered
  stalled request under a 0.2-second injected deadline. The production deadline
  remains 15 seconds; no existing budget or gate changed.
- **188 related backend checks passed**, covering existing OIDC/PKCE/browser binding,
  group-role mapping, encrypted secrets, SCIM, SAML, outbound guards and blocking-I/O
  handling. The subsequent full suite includes the additional DNS-deadline case.
- **88 required PostgreSQL checks passed**, 56.10 seconds, using the actual CI file
  list with migrated PG17 and a non-superuser/NOBYPASSRLS role. This preserves
  offboarding, one-use/concurrency, tenant and financial guards; CI retains PG16.
- **Real synthetic HTTPS probe passed on Python 3.12 and Python 3.14.8.** An owned
  loopback TLS provider served discovery/token/JWKS and a locally signed RSA ID
  token. Only the test resolver and final socket mapping redirect a synthetic
  vetted public IP to that listener; actual HTTP, certificate verification, Host,
  SNI and token validation run. Private DNS prevented any connection; private token
  metadata was rejected after the discovery GET and before any POST. No customer
  data or external IdP was contacted. Python 3.14 used the existing runtime image
  with the exact amended OIDC module mounted read-only, UID 10001, dropped
  capabilities and no-new-privileges; this is not a published rebuilt release image.

- **Full backend run:** 3,722 passed, 71 skipped, one structural failure in
  791.31 seconds; 3,221 development/synthetic HMAC-key warnings remained visible.
  The remote-I/O detector stops at the first closing parenthesis and missed the
  timeout keyword after the nested transport constructor. The unchanged timeout
  keyword now appears first. **203 post-correction checks passed**, 53.44 seconds,
  including that guard, all 48 destination cases and the related SSO/protocol checks.
  The whole suite was not repeated locally for this keyword-only reorder; fresh
  exact-head CI remains required. No test or timeout was weakened.
- Ruff/format **808 files**, mypy **418 source files**, **nine docs/PG-registration
  checks**, action pins, generated control view, current-tree structural PII,
  document links and source/probe hash consistency passed. **3,794 tests collected**;
  README scale updated. Frontend/dependency/lock/schema files are unchanged.

## Compatibility, recovery and limits

The configured issuer is an OIDC identifier, not a base URL to silently normalize.
Correct a mismatched path, port or trailing slash in settings to match provider
metadata. Endpoint hosts need not equal the issuer host: many legitimate providers
separate login, token and signing-key services. Malformed/incomplete metadata, HTTP
endpoints, private addresses and environment-proxy-only routes now refuse; configure
a valid publicly reachable HTTPS provider and restart the login. No private-IdP
exception or new network policy was invented. An administrator can still edit the
existing connection settings; no saved configuration or sealed secret was rewritten.

Browser authorization redirects use the validated metadata URL; the browser owns
its own DNS/connection and is not pinned by the server transport. This repair's
connect-time guarantees apply to the three server-side calls. The async deadline
cancels the request but cannot kill a blocking OS resolver thread; a late result
cannot resume sending. Response buffering/JSON allocation and aggregate DNS/thread
budgets are unchanged, not newly certified resource ceilings.

Real IdP interoperability, refresh/logout, private IdP/proxy policy and production
KEK acceptance remain external decisions from ADR-0021/Decisions. Synthetic TLS
is not Okta/Entra/Keycloak production acceptance. Public IdP compromise can still
produce malicious but correctly signed claims within its configured trust boundary;
this repair prevents private-network routing and issuer substitution, not every
provider compromise. Existing role, nonce, audience, browser binding and tenant
checks remain required.

Deploy the compatible API code on every replica only after normal review/gates.
No migration, key rotation or backfill is needed. Rollback would restore unsafe
network/issuer behavior; prefer a forward repair. Existing image, historical PII
and owner-dependent release gates remain open. SYS-02 Compose readiness is next.
