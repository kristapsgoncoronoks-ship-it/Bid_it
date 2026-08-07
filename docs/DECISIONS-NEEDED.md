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
usage reporting. Nothing charges until keys are set. **WO-47** fixed the
quota model this billing go-live will meter against — usage caps now key off
the org's `plan` (`plan_policies`), not the acting user's role — so the
metering substrate is ready independent of when credentials land.

**Blocked / needs you:**
- **Live credentials:** Stripe secret + webhook signing secret + per-plan Price
  IDs; EveryPay API username/secret + processing account.
- **VAT process:** we are **seller-of-record** (not a merchant-of-record), so EU
  VAT registration + remittance/filing is a **finance/legal task**. Stripe Tax can
  *calculate* it — *decision: enable Stripe Tax, and own the filing process.*
- **Metered pricing:** create the Stripe **Billing Meter** and give me its
  `event_name` (→ `STRIPE_METER_UPLOAD`).

---

## 2a. Reconcile the plan ladder (M2 / H1.2)
**Status:** 🔓  ·  **Raised by:** WO-47 — a pricing/business decision,
deliberately **not** decided in code.

**Situation:** two conflicting plan ladders exist in the repo simultaneously.
`backend/app/services/plans.py::PLANS` (the ladder actually enforced in code —
seats, module entitlements, and, since WO-47, usage quotas) has
**trial / starter / pro / enterprise** at **€0 / €29 / €99 / custom**, seats
3/2/10/200. `docs/product/pricing-hypothesis.md` proposes a *different* ladder:
**Free / Starter €39 / Team €99 / Business €249 / Enterprise**, plus a
per-seat **Practice** partner plan for accountancy firms. They name different
plans at different price points for the same market. Engineering cannot pick
between a shipped-in-code ladder and a hypothesis document — that is a
commercial decision about what the product actually charges.

**Blocked / needs you:**
- **Pick one ladder** (or a reconciled merge of the two) as the SINGLE source
  of truth — plan keys, seat counts, prices, and which add-on modules each
  tier unlocks.
- Decide whether the accountancy-practice **Practice** plan ships in this
  milestone or is deferred.
- Once decided, engineering implements it in `plans.py::PLANS` (which, since
  WO-47, is also where the per-plan usage-quota defaults live —
  `Plan.monthly_invoice_limit`/`monthly_upload_limit`) and deletes the other
  ladder from `docs/product/pricing-hypothesis.md` (or marks it explicitly
  superseded).

**Interim state (WO-47):** the quota-enforcement FIX does not wait on this —
it uses whichever ladder is live in `plans.py` today (currently
trial/starter/pro/enterprise) as the quota key, carrying forward the
pre-existing numeric limits (10/20 → trial, 1000/2000 → starter, unlimited →
pro/enterprise) as *indicative* defaults, sysadmin-overridable per plan. When
this decision lands, only `plans.py::PLANS` (and its `monthly_invoice_limit`/
`monthly_upload_limit` fields) needs to change — the enforcement mechanism
itself (`app/services/access.py`) is already plan-keyed and requires no
further rework.

---

## 2b. Dogfood subscription billing — activation steps + VAT placeholder (M2 / H1.6)
**Status:** 🔓 (built, inert until activated)  ·  **Built by:** WO-48  ·  **ADR:** [0013](architecture/adr/0013-billing-metering.md)

**Built:** `app/services/platform_billing.py` — while no live billing provider is configured
(`settings.dogfood_billing_enabled`), it invoices every OTHER paying tenant, once per calendar month,
through the platform's OWN accounts-receivable module (the same `issuer.py`/`issued_service.py`/
send/dunning surface every tenant already uses on their own customers). This is the M2 exit-criteria
fallback: revenue is not blocked on Stripe/EveryPay credentials landing. The feature is **OFF by
default** (`platform_org_id` unset) and does nothing until switched on.

**Blocked / needs you, to ACTIVATE it (not to build it — this is operational, not code):**
- **Designate the operator's own organization.** Create/sign up an org through the ordinary flow
  (exactly like any tenant) and set `PLATFORM_ORG_ID` to its id. This is a config value, not a
  business decision — the platform can use whichever org the operator already manages.
- **Complete that org's issuer profile** via the existing `/issuers` screen with the REAL legal
  name/VAT number/registered address (Art. 226 minimum) — WO-48 invents none of this; it is the same
  data-entry surface every tenant fills in for their own AR.
- **The VAT rate/scheme on our own subscription invoices.** WO-48 applies a **0% placeholder**
  (`platform_subscription_vat_rate` app setting, default `0`) — it asserts no VAT treatment rather
  than guessing a jurisdiction's rate. This is the SAME seller-of-record VAT question as §2 above (we
  are seller-of-record, so registration/remittance is a finance/legal task) — once that decision
  lands, either set the real rate via the setting, or (if a scheme like reverse-charge/exempt applies)
  a small follow-up order threads the scheme through `platform_billing.bill_subscriptions`. Until then,
  every dogfood invoice generated is legally a €X + 0% VAT document — get finance's sign-off before
  relying on it for real remittance.
- **Payment collection stays manual.** WO-48 deliberately does not collect anything — an operator
  records a bank-transfer receipt via the existing `PATCH /issued/{id}/payment`, same as any tenant
  today. Automated collection is H1.4 (Stripe/EveryPay), still owner-blocked at §2.

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

## 8. Fleet Fuel decommission archive — retention / destruction (counsel)
**Status:** 🔓  ·  **Raised by:** WO-6 (PII quarantine) — a legal decision, deliberately **not** acted on in code.

**Situation:** the retired Fleet Fuel repository was deleted on 2026-07-25,
but the **owner-held decommission archive** retains its full git history —
including real client personal and commercial data as module constants
(company names, EU VAT ids, addresses, bank references, invoice numbers) and
three committed live databases (`customers.db`, `fuel_history.db`,
`suppliers.db`). Deleting the GitHub repository does **not** end the GDPR
exposure; the archive is now the sole copy of that personal data.

**Decision needed from counsel:**
- the archive's **lawful basis and retention period** (or a destruction date);
- whether a **redacted derivative** (history rewritten without the PII) should
  replace it for engineering-reference purposes;
- storage requirements meanwhile (encryption, access list, audit of access).

**Owner:** the repository owner (holder of the archive) — to engage counsel.
**Date needed by:** 2026-09-30 (before any Epic-G work could tempt an
archive consultation).
**Interim controls already in place:** the archive stays offline with the
owner; this repo's CI PII scan (`scripts/pii_scan.py`, required check) blocks
any identifier from crossing over; the identifier extract for the deny-list
(`identifiers_for_denylist.txt`) is `.gitignore`d and documented as
never-committed (`docs/transport/harvest-protocol.md`).

---

## 9. Restating expense figures a human already approved (WO-8 FX correction)
**Status:** 🔓  ·  **Raised by:** WO-8 (one FX convention) — a business decision, deliberately **not** acted on in code.

**Situation:** before WO-8, the expense-item path converted a foreign
original-currency figure by **multiplying** by the rate, while the one true
convention (ECB: units per 1 EUR) **divides** — so an affected item's stored
reporting-currency amount could be wildly wrong (100 USD at rate 1.23456
stored as 123.46 instead of 81.00). The WO-8 data migration
(`b1c3e5a7f9d1`) **corrected** affected items on reports **no human had
decided yet** (draft / submitted / returned), recomputed those reports'
totals, and printed every old→new value as the reconciliation artifact.

**Deliberately NOT corrected:** items on reports already **approved,
rejected, marked for reimbursement, or reimbursed**. Those figures were seen
and signed off by a human — and some may already have been **paid out** at
the wrong EUR value. The migration *flags* each one in its printed report
(row id, stored value, what the correct value would be) and leaves the
stored value untouched.

**Decision needed:**
- whether to **restate** the flagged approved/reimbursed reports (and if so,
  whether by correcting in place with an audit event, or by a compensating
  claim/deduction on the employee's next report);
- who communicates with the affected employees / approvers;
- the cutoff (e.g. restate unpaid approvals, compensate paid ones).

**Owner:** finance lead. **Interim controls:** the multiply path is gone;
new writes follow the single divide convention with server-derived
provenance; a report with no reliable EUR value can no longer enter a
reimbursement batch or a SEPA file (it refuses, naming the line).

---

## 10. Transport module pricing tier (M3 / WO-49)
**Status:** 🔓 (built, inert until priced)  ·  **Raised by:** WO-49  ·  **ADR:** [0023](architecture/adr/0023-platform-evolution-and-transport-seam.md)

**Built:** the `transport` module entitlement (`app/services/modules.py`) —
default **OFF**, following the exact `issuing`/`expenses` plan-gated pattern.
A tenant can only self-service-enable it via `PUT /api/v1/modules/transport`
once it appears in a plan's `modules` set.

**Deliberately NOT decided:** which subscription plan(s) (`starter`/`pro`/
`enterprise`) include `transport`, and whether it carries its own price
(the harvested Fleet Fuel system charged the VAT-refund service as a
contingency fee on recovered cash, not a flat monthly add-on — see
`docs/plan/shared/specs/BA_fleet_fuel.md` C10/C11 `compute_fee`). Inventing
either would be exactly the kind of commercial fact §9 of the master context
forbids guessing. Until this is decided, `transport` is absent from every
`PLANS[...].modules` set, so `PUT /modules/transport` 402s for every plan —
a sysadmin can still turn it on for a specific tenant directly via
`modules.set_enabled` (bypassing the plan gate, the same escape hatch already
used for early-access add-ons) while the pricing decision is pending.

**Decision needed:** (a) which plan tier(s) unlock `transport`; (b) flat
monthly add-on price vs. a contingency-fee-on-recovered-VAT model (or both —
a monthly platform fee plus a success fee, mirroring the harvested spec); (c)
whether the five pilot Baltic entities the Fleet Fuel BA describes carry
forward as design partners for this vertical specifically.

**Owner:** product/pricing lead. **Interim controls:** the module is fully
built and inert — no revenue is lost by waiting, and no customer can reach it
by accident (`PUT /modules/transport` always 402s until a plan is priced).

**Update (M3 sprint, after WO-56):** this decision now also blocks G2.9 (fee
freezing, `ARCH_plan.md`) directly, not just module pricing. R13/C10/C11
(`docs/plan/shared/specs/BA_fleet_fuel.md`) describe `compute_fee` as
resolving a rate through a **per-(customer, country) override → customer
default → (0, 0)** chain — but this codebase has no established mapping from
a `VatRefundClaim` to a billable "customer" distinct from the claimant
`entity_id` (`issuer_profiles`) itself, and `app.models.customer.Customer`
(the AR sales-customer master) has no fee-rate concept today. Building G2.9
now would mean inventing BOTH the customer-identity mapping and the fee-rate
storage shape ahead of this decision — exactly the kind of commercial fact
§9 of the master context forbids guessing, and infrastructure that could be
built the wrong shape if the eventual model differs (a flat per-entity rate
vs. a true multi-client-per-org billing rate vs. no per-claim fee at all,
subscription-only). G2.6's period-end (R7) and Art. 17 minimum (R8) gates
shipped independently in WO-56 — they needed no customer/fee concept. G2.9
stays unbuilt pending this decision; the schema for it
(`vat_refund_claims.fee_pct`/`fee_min`/`fee_eur`, nullable since WO-49) is
already in place and costs nothing sitting empty.

---

## 11. Claim-line supplier attribution — what an `UNMATCHED` bucket carries (M3 / WO-79, WO-80)
**Status:** 🔓  ·  **Board:** G2.4 (`claim_lines.build_claim_lines`) / R2

**Built:** claim lines materialize at the R2 grain — one row per (invoice
reference × product group) — with the Art. 9 goods code, the EUR and local
amounts, and the resolved AP invoice where one was found
(`backend/app/services/transport/claim_lines.py`). The claim workspace renders
them at exactly that grain (`frontend/src/pages/VatClaimDetail.tsx`).

**Blocked / needs you.** A claim line carries **no supplier**, and both WO-79 and
WO-80 stopped at the same wall rather than guess one. Two facts make this a
decision and not a bug fix:

1. **The two `invoice_ref` columns mean different things.** On a claim line
   (`vat_claim_lines.invoice_ref`) it is the **resolved AP invoice number** —
   the value `invoice_match.resolve_invoice_ref` returned after matching the
   registered invoice set. On a fuel transaction
   (`fuel_transactions.invoice_ref`) it is the **raw reference read off the
   supplier's own statement** (Fleet Fuel's overloaded "note", split into
   `invoice_ref` + `provenance_note` by WO-50). They are not the same string,
   and joining a line back to its transactions on that column would be a false
   equality, not a lookup.
2. **An `UNMATCHED` line aggregates MULTIPLE suppliers.** Every transaction that
   resolved to no registered invoice groups into one `UNMATCHED` line per
   product group — so a single line can span Q8, BP and DKV at once. There is no
   one supplier to display, and picking the first, the largest or the
   alphabetically-first would be an invented attribution on a surface that ends
   up in a **filing** (Art. 8(2) requires the supplier's name, address and VAT
   number **per invoice**).

**Decision needed:** what should an `UNMATCHED` multi-supplier bucket carry on a
filing surface? The options, none of them obviously right:

- **(a) Split the grain.** Make the line key `(supplier, invoice_ref,
  product_group)` so an `UNMATCHED` line exists per supplier and always has
  exactly one. Truest to Art. 8(2), but it changes the R2 grain the harvested
  spec states, and it multiplies the unresolved-line count an operator sees.
- **(b) Keep the grain, add a supplier LIST.** The line stays as-is and carries
  the distinct suppliers behind it (a column or a derived field). Honest, shows
  the operator exactly what to go fix — but a claim line that names three
  suppliers cannot be filed, so the UI must present it as a work item, not a
  claim row.
- **(c) Keep the grain, show nothing.** Today's behaviour, made explicit: an
  `UNMATCHED` line names no supplier because it has none. Cheapest, and safe
  (R3 already refuses to file any synthetic line) — but it leaves the operator
  with no route from "this line is unresolved" to "here is who to chase".

**Owner:** product, with the VAT filing lead. **Interim controls:** none of this
is reachable in a filing — `claim_gates.is_synthetic()` treats `UNMATCHED` as
synthetic and R3's wired consumers (the lock gate, the workbook and the evidence
pack) all refuse a pack containing one. The gap is an operator-workflow gap, not
a correctness one, and nothing is being filed wrongly while it waits.

---

## 12. Abandoning a supplier overcharge claim-back before it is sent (M5 / WO-82)

**Context.** `BA_fleet_fuel.md` §4.5 and R41 give the claim-back lifecycle as a
single line: `detected → packaged → claimed → recovered | rejected |
written_off`. WO-82 implements exactly that chain, LITERALLY — the three
outcomes are reachable only from `claimed`, because that is the only shape the
harvested text draws and inventing an edge is master-context §10 territory (the
WO-73 precedent, where `inactive` is terminal because no re-onboarding edge was
harvested).

That leaves one real operational move with nowhere to go: a breach is
`detected`, an operator looks at it, and decides **not to pursue it** — the gap
is €12, or the supplier relationship is worth more than the claim, or the term
was mis-keyed and the "breach" is an artefact. Today that claim-back can only be
walked forward through `packaged` and `claimed` (i.e. told the system a demand
was sent when none was) or left sitting in `detected` forever, quietly inflating
the worklist.

**Decision needed:** should `written_off` also be reachable from `detected` and
`packaged`?

- **(a) Yes — allow `detected → written_off` and `packaged → written_off`.**
  Matches how a chase list is actually worked, and keeps `recovered_total`'s
  denominator honest (an abandoned item is closed, not pending). It adds two
  edges the harvested text does not draw.
- **(b) No — keep the chain literal.** Anything not worth claiming should not
  have been opened; `open_claim` is deliberate and reversible only by not
  calling it. Costs nothing, but leaves stale `detected` rows.
- **(c) A separate `abandoned` state.** Cleanest semantically ("we never asked"
  is not "we asked and gave up"), but it invents vocabulary the spec does not
  have, which is exactly what §10 forbids without this decision.

**Owner:** product, with whoever will actually work the supplier chase list.
**Interim controls:** none of this can produce a wrong number. Only `recovered`
books cash, it is bounded by the detected evidence, and a stale `detected` row
contributes €0 to `recovered_total` — the north star the dashboard reports. The
gap is worklist hygiene, not correctness.

---

*Not blocked — I can keep building these without you:* enhancements to shipped
features, tests/coverage, docs, and any of the above up to its stated boundary.
Tell me which to prioritise next.
