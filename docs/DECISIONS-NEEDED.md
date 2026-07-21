# Decisions needed — where your involvement is required

This is the running register of work that is **built to a boundary in code** but
**cannot be finished without an external decision, credential, or infrastructure
commitment**. Each item states what's done, what's blocked, and exactly what I
need from you. Update the **Status** when a decision lands.

Legend: 🔓 ready for your input · ⏳ waiting on you · ✅ resolved

---

## 1. Enterprise SSO / SCIM / SAML — finish against a real IdP
**Status:** 🔓  ·  **ADR:** [0021](architecture/adr/0021-sso-scim.md)

**Built:** OIDC login + PKCE + ID-token validation (proven offline with key
fixtures) + JIT provisioning + IdP-group→role mapping; SCIM 2.0 Users
(create/update/deactivate); SAML SP request-side (AuthnRequest, redirect binding,
SP metadata).

**Blocked / needs you:**
- A **dev IdP** (Okta or Microsoft Entra developer tenant) **or** your OK to run a
  **local Keycloak** — to exercise the live OIDC HTTP seams (discovery, token
  exchange, JWKS) end-to-end. *Decision: which, and provide access if a real IdP.*
- **SAML assertion validation** is deliberately unimplemented (a hand-rolled
  XML-DSig validator is an auth bypass). Finishing needs approval to **add a
  vetted library** (`pysaml2` / `xmlsec`, which needs system `libxmlsec1`) + a
  real IdP's metadata. *Decision: green-light the dependency + IdP.*
- **SCIM Groups** + Okta-vs-Entra paging/PATCH dialect quirks — only provable
  against a real IdP.

---

## 2. Billing go-live (Stripe + EveryPay)
**Status:** 🔓  ·  **ADR:** [0013](architecture/adr/0013-billing-metering.md)

**Built:** Both providers behind one seam — Stripe (Checkout + Portal + signed
webhook), EveryPay (hosted page + server-side verify + MIT recurring), metered
usage reporting. Nothing charges until keys are set.

**Blocked / needs you:**
- **Live credentials:** Stripe secret + webhook signing secret + per-plan Price
  IDs; EveryPay API username/secret + processing account.
- **VAT process:** we are **seller-of-record** (not a merchant-of-record), so EU
  VAT registration + remittance/filing is a **finance/legal task**. Stripe Tax can
  *calculate* it — *decision: enable Stripe Tax, and own the filing process.*
- **Metered pricing:** create the Stripe **Billing Meter** and give me its
  `event_name` (→ `STRIPE_METER_UPLOAD`).

---

## 3. Accounting/ERP exporters — DATEV & SAF-T
**Status:** 🔓  ·  **ADR:** [0013 context / export hub]

**Built:** generic + Xero + QuickBooks CSV exports. DATEV + SAF-T deliberately
deferred (they must map to a **real framework**, not a guess).

**Blocked / needs you:**
- **Which markets first?** DATEV needs the German **SKR03/SKR04** chart + the
  EXTF spec; SAF-T needs a **per-country profile** (PT/PL/NO/… each differ).
- Provide (or point me at) the **account/tax-code mapping** for the first target
  market so the exporter is correct rather than plausible.

---

## 4. Data residency — the multi-region data plane
**Status:** 🔓  ·  **ADR:** [0022](architecture/adr/0022-data-residency.md)

**Built:** the **app seam** — per-tenant `region`, a `service_region` per
deployment, and a fail-closed enforcement backstop (421 for a wrong-region
request). Off by default.

**Blocked / needs you:**
- A commitment to stand up a **second region** (region-local Postgres, object
  storage, backups + per-region LB routing) — **infrastructure**, not app code —
  before turning on `ENFORCE_REGION_PINNING`.
- **Tenant relocation** policy (moving a tenant between regions is a data
  migration, not a field flip) — model it only if/when needed.

---

## 5. Secrets at rest — production KEK provider
**Status:** 🔓  ·  **ADR:** [0016](architecture/adr/0016-config-secrets.md)

**Built (this session):** application-level envelope encryption
(`core/keyvault.py`, AES-256-GCM) with the SSO OAuth **client secret encrypted at
rest**; KEK defaults to one derived from the app secret, or BYOK via env.

**Blocked / needs you:**
- **Production KEK provider decision:** stay on the env/BYOK key, or wire a
  **cloud KMS** (AWS KMS / GCP KMS / Azure Key Vault). *Decision: which, and
  provide the key/role.*

---

## 6. Public API GA
**Status:** 🔓  ·  **ADR:** [0015](architecture/adr/0015-api-strategy.md)

**Built:** REST + OpenAPI under `/api/v1`; per-process rate limiting + auth
brute-force guard.

**Blocked / needs you (product decisions):**
- **Scoped API keys** — a non-user principal design (routes currently assume the
  caller is a real user row). *Decision: confirm the key-vs-user model + scopes.*
- **Refresh-token rotation + short access-token TTL** before public GA — confirm
  the session policy (TTLs, rotation, revocation UX).
- **Distributed rate limiting** (shared store/Redis) — only when a metric shows
  the per-replica ceiling is insufficient. *Decision gated on a real signal.*

---

## 7. Compliance certification (SOC 2 / ISO 27001)
**Status:** 🔓

**Built:** the technical substrate — Postgres RLS tenant isolation, hash-chained
audit trail + export, data retention + legal hold, GDPR erasure, encrypted
secrets.

**Blocked / needs you:** a business decision to **pursue certification** (engage
an auditor, evidence collection, access reviews, vendor register) — process, not
code.

---

*Not blocked — I can keep building these without you:* enhancements to shipped
features, tests/coverage, docs, and any of the above up to its stated boundary.
Tell me which to prioritise next.
