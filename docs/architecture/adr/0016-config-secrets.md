# ADR-0016 — Env config + envelope-encrypted secrets (KMS-backed)

**Status:** Accepted

## Context
Deployments differ only by configuration; secrets (signing key, DB creds, provider keys, stored portal credentials) must never be committed or logged, and must be rotatable and residency-safe.

## Selected approach
**12-factor configuration** — every deployment value from the environment (`core/config.Settings`), `.env` for local only. Secrets injected from the platform secret store (k8s Secrets / cloud secret manager). **Application-stored secrets** (portal credentials) use **envelope encryption**: per-secret AES-256-GCM DEK wrapped by a KEK, AAD-bound to context, KEK provider pluggable (`local`/`env`/BYOK → KMS/HSM target). **GCM auth failures raise; plaintext secrets/IBANs are never logged.**

## Alternatives considered
- **Secrets in config files / DB plaintext** — unacceptable; leak on backup/log.
- **A single symmetric app key for all secrets** — no per-secret isolation, hard rotation, one key compromises all.
- **Vault (HashiCorp) from day one** — strong, but an extra system to run pre-scale; the pluggable KEK provider reaches it later.

## Why appropriate
Env config keeps the image environment-agnostic; envelope encryption gives per-secret isolation + rotatable KEK + BYOK for tenants who need it (so we can't bulk-decrypt one tenant's secrets); KMS-backing is a config change, not a rewrite.

## Risks
- KEK loss/rotation on a populated store → documented re-wrap migration; KMS-managed lifecycle.
- Misconfigured provider → fail-loud on missing keys in production.

## Implementation status
**Shipped:** `core/keyvault.py` — app-level secret sealing with **AES-256-GCM**, a random 96-bit nonce per seal, and **AAD binding** (a ciphertext can't be lifted between fields); GCM auth failures **raise** `KeyvaultError` (never silently return ""). KEK provider is pluggable: `local` (derived from `secret_key`, default) or **BYOK** (`kek_key`, base64 32 bytes). First consumer: the **SSO OAuth client secret is sealed at rest** (`sso_config` seals on write, `oidc` unseals on use, the API never returns it). Tests cover roundtrip, AAD binding, tamper + wrong-key rejection, BYOK, and the SSO integration.

**Deferred (the fuller design above):** a **per-secret DEK** wrapped by the KEK (true envelope) and a **cloud-KMS** provider — this module is the seam that swaps in. The production KEK-provider choice is a deployment decision (docs/DECISIONS-NEEDED.md §5). Today's single-KEK GCM is honest and sufficient for the current secret surface.

## Revisit when
Multi-region/BYOK-per-tenant requirements arrive (move KEK to per-tenant KMS keys), or a dedicated secrets manager (Vault) is warranted operationally.
