# ADR-0005 — Stateless JWT authentication

**Status:** Accepted

## Context
Stateless API replicas must authenticate requests without a shared session store, while allowing immediate privilege changes and a path to enterprise SSO.

## Selected approach
**Stateless JWT bearer tokens** (HS256, signed with the env secret). The token carries only `sub` (user id); **role and org are re-read from the DB every request** so revocation/downgrade takes effect immediately and the token can't assert privileges. Login by email; passwords bcrypt-hashed. SSO/SAML + SCIM planned for Enterprise, with JWT remaining the internal session token.

## Alternatives considered
- **Server-side sessions (cookie + store)** — needs a shared session store (Redis/DB), adds a stateful dependency; CSRF surface.
- **Opaque tokens + introspection** — a DB/round-trip per request anyway; we already re-read the user, so JWT + DB-read gives the same freshness without an introspection endpoint.
- **Third-party auth (Auth0/Cognito) from day one** — outsources a core trust boundary + cost + residency questions before we need it.

## Why appropriate
No session store to run; any replica validates a token; re-reading identity per request removes the classic "stale JWT claims" problem. Simple now, pluggable to an IdP later.

## Risks
- Token theft (XSS) → tokens in memory (not localStorage where avoidable), strict CSP, short TTL + refresh rotation (to formalise before public API GA).
- Secret compromise → rotate signing key; envelope-managed; KMS-backed.

## Revisit when
Enterprise SSO/SAML/SCIM is required (add an IdP integration), or a public API needs fine-grained scoped tokens/OAuth client-credentials (add alongside, not instead).
