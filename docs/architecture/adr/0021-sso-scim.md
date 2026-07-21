# ADR-0021 — Enterprise SSO (OIDC → SCIM → SAML)

**Status:** Accepted — **OIDC implemented**; SCIM + SAML in progress. This ADR is the standing record for the whole identity-federation program and, critically, **where the fixture-proven build stops and a real IdP is required to finish.**

## Context
Enterprise buyers gate purchase on SSO (central identity, MFA at the IdP, instant offboarding) and, increasingly, SCIM auto-provisioning. Our own auth is email/password → internal JWT (ADR-0005). SSO must layer on **without** changing anything downstream: it resolves a person to a `User` row in the right tenant and then issues our normal JWT. Each tenant brings **its own** IdP, so connection config is per-org.

## Why fixtures, not fake
SSO is a security handshake; almost all its value is correct interop **and correct rejection** of forged input. So we split each protocol into:
- **pure, security-critical logic** — proven offline with locally-minted key fixtures (accept a valid token/assertion, reject tampered / wrong-audience / wrong-issuer / expired / replayed), and
- **thin network seams** — discovery, token exchange, JWKS/metadata fetch — exercised end-to-end against a real IdP / a local **Keycloak** container.

We build and land the first half with full test evidence; the second half is the explicit **"return to finish"** boundary. We do **not** ship an unvalidated assertion path — for auth, a fake is an authentication bypass, not a harmless stub.

## OIDC — implemented (this increment)
Authorization-code flow with **PKCE** and a **stateless, signed `state`** (JWT under the app key — no session store):
- `services/oidc.py`: `pkce_pair`, `build_authorize_url`, `sign_state`/`read_state`, and **`validate_id_token`** (RS256 via the IdP JWKS, issuer, audience, expiry, **nonce**) — all pure. `discover` / `exchange_code` / `fetch_jwks` are the injectable network seams. `finish_login` orchestrates + **JIT-provisions** (match by email in the connection's org; create with `default_role` if enabled; **reject an email that belongs to another workspace**; enforce an optional `allowed_domain`). JIT users get an **unusable password** so password login is refused.
- Routes: public `GET /auth/sso/{slug}/authorize` (302 → IdP) and `GET /auth/sso/callback` (verify → JIT → 302 to the SPA with the internal token in the fragment; any failure → the login page). Admin `GET/PUT/DELETE /sso/connection` (client secret write-only, never returned). Frontend: SSO login affordance, `/sso/callback` page, admin config panel.
- Model/migration: `sso_connections` (tenant-scoped, RLS).
- **Tests (21):** ID-token validation (1 accept + 6 rejections incl. wrong-key), PKCE/state, authorize-URL, JIT (create/match/cross-org/domain/jit-off), routes (authorize 302, callback issues token, error redirect), admin config + secret protection.
- **Return to finish (needs a real IdP / Keycloak):** the live discovery + token-exchange + JWKS HTTP round-trips (the seams above), a smoke test against Keycloak, and refresh-token / `id_token_hint` logout. Not required for the offline security proof; required before GA.

## SCIM — implemented
Token-gated **SCIM 2.0 `Users`** endpoints (`/scim/v2/Users`, POST/GET/PUT/PATCH/DELETE) the tenant's IdP calls to create / update / **deactivate** users — the offboarding story. Authenticated by a per-connection bearer token (only its sha256 stored; minted once via `POST /sso/scim/token`); a dependency resolves the token → connection and scopes the request to that org (tenant guard + RLS). Deactivation (`active=false` via PATCH, or DELETE) is a **soft** delete so audit attribution + FKs survive. `services/scim.py` holds the resource mapping + CRUD; errors render in the SCIM error schema. **Tests (10):** token required/hashed, create/list(filter)/get/replace/patch-deactivate/delete-soft, missing-userName 400, cross-tenant isolation, admin token mint. **Return to finish (needs a real IdP):** `Groups`, and the paging / PATCH-dialect quirks between Okta and Entra.

## SAML — SP request side built; assertion consumption is the boundary
The **safe, offline-provable half** is implemented in `services/saml.py`: `build_authn_request` (SP-initiated `<samlp:AuthnRequest>`), `redirect_binding_url` (HTTP-Redirect: raw-DEFLATE + base64 + urlencode, roundtrip-tested), and `sp_metadata_xml` (our SP metadata for the customer to register). Config lives on `sso_connections` (`saml_sso_url`, `saml_idp_entity_id`, `saml_idp_cert`). Routes: `GET /auth/sso/{slug}/saml/metadata` (SP metadata) and `GET /auth/sso/{slug}/saml/login` (redirect to the IdP with a fresh AuthnRequest). **Tests (8):** AuthnRequest structure, redirect-binding roundtrip, SP metadata, metadata/login routes, and the boundary.

**The boundary — assertion consumption (`POST /auth/sso/saml/acs`)** is deliberately **NOT** implemented: `saml.consume_assertion()` raises `SamlNotReady` and the route returns **501**. Validating a signed SAML `Response` (XML-DSig signature, exclusive canonicalization, **signature-wrapping** defences, conditions/audience/NotOnOrAfter) with hand-rolled code is an authentication bypass, and **no vetted XML-DSig library is installed** (no pysaml2/xmlsec/lxml). Finishing it requires pinning that library + a real IdP's metadata — the final "return to finish" step. No unvalidated assertion path ships.

## Security notes / risks
- **Client secret at rest:** stored on `sso_connections` for the multi-tenant config; **MUST move to the envelope-encrypted secret store (ADR-0016) before GA** — tracked here.
- **Open-redirect / token leak:** the callback only ever redirects to our configured `sso_post_login_url`; the token rides the fragment (not sent to servers/logs).
- **Cross-tenant takeover:** JIT refuses an email already owned by another workspace; domain allow-listing further constrains it.
- **Replay:** nonce is required and checked; `state` is signed + short-TTL.

## Revisit when
Finishing against a real IdP (OIDC seams + SCIM dialects + SAML), moving the client secret to the KMS-backed store, or adding IdP-group→role mapping and SP-initiated logout.
