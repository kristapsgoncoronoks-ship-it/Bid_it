# Decisions needed — where your involvement is required

This is the running register of work that is **built to a boundary in code** but
**cannot be finished without an external decision, credential, or infrastructure
commitment**. Each item states what's done, what's blocked, and exactly what I
need from you. Update the **Status** when a decision lands.

Legend: 🔓 ready for your input · ⏳ waiting on you · ✅ resolved

---

## Decisions taken — 2026-08-12 (the path to a pilot)

Asked because each answer changed what gets built, not to confirm a plan.

| | Decision | What it settles |
|---|---|---|
| Beta scope | **Supervised pilot, named clients** — not an open beta | The bar is: close the three open money defects + pass a restore drill. Real-data validation, PII deny-list, a11y and load work move to the open-beta gate, not this one. |
| Fee rate | **15% of recovered VAT, €50 minimum** | Unblocks filing (`resolve_fee_rate` refuses until a rate exists). Per-client overrides still apply; this is the default, not a ceiling. |
| Late-payment interest | **Fix the defects only** — do NOT build the EU statutory model now | Scope stays: stop the double-billing, stop the cross-currency sum. Directive 2011/7/EU (ECB reference + 8pp, resetting 1 Jan / 1 Jul, plus the €40 Art. 6 recovery fee) is NOT modelled, so the platform under-claims where a creditor is statutorily entitled to more. That is a deliberate, recorded gap — reopen it before the open beta. |
| Backup / restore (R14) | **Infrastructure DR + a real drill** — not app-owned tooling | Hostinger snapshots plus the `pg_dump`/`tar` commands already in the deploy docs. The deliverable is a *documented restore that actually ran*, not a backup that has never been read back. No new product code. |

**Still needed from the owner, not decidable here:**

1. **GitHub Actions runners.** Every run on every branch fails in ~1s with no
   logs — an account/billing condition. Until it clears there is no independent
   verification of anything, and the CI-gated deploy cannot fire.
2. **One real supplier statement and one real fuel invoice** (redacted is fine).
   Every fixture in the suite is synthetic by design; the pilot bar does not
   require real-data validation, but a single genuine document would be the
   cheapest evidence available that capture works outside our own imagination.
3. **Running the deploy.** Production is still on `15116e1`. Nothing shipped
   today is live until `DEPLOY-RUNBOOK-2026-08-12.md` is executed.

---

## Decisions taken — 2026-08-15 (deletion, the recycle bin, the platform archive)

Reasoning in [`design/deletion-and-archive.md`](design/deletion-and-archive.md)
and [`design/platform-archive.md`](design/platform-archive.md).

- **The 30-day purge stays ON**, offered with a one-line off switch as the
  recommendation. The owner chose to keep the promise the UI makes. That choice
  is what moved the archive from last to next.
- **The bin extends to every entity** — expenses, expense reports,
  issued-invoice attachments, recurring schedules.
- **An invoice in a FILED VAT claim:** add a real invoice→claim-line link, then
  REFUSE the delete. Supersedes an earlier proposal withdrawn because it rested
  on a heuristic string match and would have misfired in both directions.
- **The archive keeps the record AND the source document.**
- **Archive read access:** the CLIENT's own company owner, read-only, own org —
  plus platform staff under a distinct grant a platform admin does NOT hold by
  default: named individual, time-boxed, reason recorded at every read.
- **Retention: 3 years included; longer is a PAID extension.** `expires_at` is
  stamped at write time so lowering the setting can never reach backwards into
  records already kept under a longer promise.

### Still open, and what each blocks

**A. Statutory retention floors, per country — needs the owner's accountant.**
Baltic law commonly requires source documents kept LONGER than three years, so a
client who does not extend loses records they were obliged to keep. The
obligation is theirs, not the platform's, but it lands on them at the worst
moment. Mitigation designed and half-built: nothing leaves without the owner
being told first (`archive.expiring_soon`). **Blocks** any customer-facing
retention claim and the DPA clause.

**B. Does the retention extension ride on the plan ladder, or sell standalone?
— DECIDED 2026-09-05: it RIDES THE LADDER.** Business and Enterprise include
7-year archive retention as a plan attribute (`plans.Plan.archive_retention_years`);
every other tier keeps the included 3. Buying longer retention IS upgrading,
which the existing checkout already handles — no add-on price, no second
payment flow, no new correlation table. The pricing doc already framed
retention as the up-tier lever and listed no archive add-on, so this is the
reading it was pointing at.

What the decision changes in code (WO-AD): `archive.retention_years()` takes the
MAX of the included tier, the org's plan attribute and any staff override, so a
misconfigured plan can never shorten what a staff grant promised; and a plan
change RE-STAMPS existing archived rows, extend-only, exactly as a staff grant
does — an upgrade that only protected invoices deleted afterwards would be
worthless at the moment it is bought, which is right after a pre-expiry notice
about records already archived. The pre-expiry email now names the upgrade
instead of saying "ask us".

*Original blocker, retained for the record:* blocked by §2a — the ladder itself
was unresolved. §2a was decided 2026-08-15, which made this answerable.

**C. Does the archive follow a client who leaves? — DECIDED 2026-08-15: it
SURVIVES for the full retention period. Loose ends DECIDED 2026-08-16:** the
pre-expiry notices keep going to the last recorded owner address, and an
ex-client can request a one-time EXPORT of their archive; no live login is
retained. (The export mechanism is buildable work; the notice recipient falls
out of the notice feature itself.) Statutory retention outlives the
commercial relationship, which is the usual legal position. **This must be in the
DPA before a client signs** — retaining an ex-client's records on a basis they
never agreed to is the one version of this that is indefensible. Two consequences
worth building for: an ex-client's owner arguably still needs read access to
their own archive (they cannot log in), and the pre-expiry notice has nobody to
send to. Both are open.

---

## Decisions taken — 2026-08-08

Four answered in session. Recorded verbatim in intent, with what each one
actually costs to build. Two went beyond the options offered and opened new
work rather than closing a question — flagged as such rather than trimmed to
fit.

**§10 transport pricing — DECIDED: contingency fee on recovered VAT
(no-win-no-fee).** Unblocks G2.9. The claim already carries frozen
`fee_pct`/`fee_min`/`fee_eur` columns, so the engine has somewhere to land.
**Still open: the actual percentage and any minimum.** Building the mechanism
with the rate as an org-level setting that FAILS CLOSED when unset — a fee
figure is what a client is charged, so no default may be invented (the excise
placeholder precedent does not transfer: a labelled indicative rate on an
advisory figure is not the same as a live charge). **BUILT by WO-95** (G2.9,
R13): C11's formula and resolution chain, the `vat_fee_rates` table, and the
freeze inside `lock.submit_claim`. §10 records exactly what is left — the
percentage and the minimum, nothing else, and until they are typed no claim can
be filed.

**§11 unmatched bucket — DECIDED: keep the line grain, carry the supplier
list.** ✅ BUILT (WO-L, 2026-08-26): `vat_claim_lines.unmatched_suppliers`,
set at build time, rendered as a work-item hint. These lines are already refused at submit by
R3, so no filed document changes; the list serves the preparation surface.

**§12 supplier overcharge — DECIDED, and WIDENED.** The ignore half is
✅ BUILT (WO-L, 2026-08-26): audited `ignored` with a required reason +
reinstate. The reliability rating remains design-gated (WO-Q). The answer given: an
overcharge must be VISIBLE, and the operator decides whether to react or
ignore it. That resolves the abandonment question — an explicit, audited
`ignore` action, not a silent dead end. It also adds a NEW requirement not in
the register: **every supplier carries a reliability rating**, computed from
multiple criteria, three of them named — overcharges, exchange-rate treatment,
and lines charged that were never agreed. This is G4.7's deferred "supplier
reliability" board, now with its criteria specified by the owner. It needs its
own order and a design pass: what each criterion contributes, over what window,
and how a rating is presented so it reads as evidence rather than a verdict on
a counterparty. **Design pass DONE 2026-08-27**
([docs/design/supplier-reliability-rating.md](./design/supplier-reliability-rating.md)):
rolling 12 months, derived-only (no new tables), three-value bands per
criterion with disclosed thresholds, overall = worst band, ignored
claim-backs still count as evidence. ONE question parked for the owner,
non-blocking: may the rating appear on the claim-back demand letter /
evidence packet? Recommendation NO (it would change the artifact's legal
character); the build proceeds web-only either way.

**§13 drifted evidence — DECIDED in principle, scope needs confirming.**
The VAT-claim half is ✅ BUILT (WO-L, 2026-08-26): the decision transition
with amount-true partial rejection at the frozen fee rate. The
answer given: *figures do not change after a claim is submitted; they change
only on partial rejection, when some invoices in a claim are rejected.* Two
consequences. (1) For the VAT claim this confirms the existing freeze and adds
a capability that **does not exist yet** — partial rejection, which
`status.py` already names as an unbuilt transition colliding with G2.9. (2)
The question asked was about the supplier overcharge claim-back, not the VAT
claim; the principle transfers cleanly (freeze when sent, change only when the
counterparty rejects part of it), but that reading is mine and is flagged here
rather than assumed silently.

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

**Decided 2026-09-05 (WO-AD):** the quota cap stays BLOCK-AT-THE-CAP (no
allow-and-meter overage at go-live — a customer hits the wall and upgrades
deliberately, no surprise charges); and the one-allowance-two-counters
over-grant in `plans.py` is left deliberately over-generous, documented as
intentional, to be revisited on real usage data.

**Wired 2026-09-05 (WO-AD):** `STRIPE_PRICE_BUSINESS` — the Business tier
chosen in §2a had no Stripe price-id slot, so the SPA offered a checkout that
could only 502; a plan whose price id is missing is now reported as not yet
purchasable and never offered. `STRIPE_AUTOMATIC_TAX` — the Stripe Tax
decision below was recorded but the checkout session never asked for it; it
is now a setting, off until you flip it, because enabling tax collection is a
filing commitment.

**Blocked / needs you:**
- **Live credentials:** Stripe secret + webhook signing secret + per-plan Price
  IDs (now including Business); EveryPay API username/secret + processing account.
- **VAT process:** we are **seller-of-record** (not a merchant-of-record), so EU
  VAT registration + remittance/filing is a **finance/legal task**. Stripe Tax can
  *calculate* it — *decision: enable Stripe Tax, and own the filing process.*
- **Metered pricing:** create the Stripe **Billing Meter** and give me its
  `event_name` (→ `STRIPE_METER_UPLOAD`).

---

## 2a. Reconcile the plan ladder (M2 / H1.2) — **RESOLVED 2026-08-15**

**Decision: the pricing doc's ladder.** Free €0 · Starter €39 · Team €99 ·
Business €249 · Enterprise custom · Practice (accountancy partner). Built in
`app/services/plans.py`; the original entry is kept below for the record.

Three departures from the doc, each stated in the module docstring rather than
made silently:

1. **`pro` keeps its KEY, gains the NAME "Team".** A key is an identifier stored
   in `organizations.plan`, quoted by `config.stripe_price_for` and seeded in
   `seed.py`; renaming it needs a data migration and a Stripe price remap to
   change a label. The customer-facing rename lands; the identifier does not.
2. **`trial` and `free` are both kept.** The doc describes Free as a perpetual
   micro tier AND maps the 14-day trial onto `trial`. Those cannot be one row: a
   trial that expires into nothing is not a free tier.
3. **One "Docs/mo" allowance applied to BOTH counters**, which over-grants —
   this code meters invoices and uploads separately, so Starter allows 150 of
   each rather than 150 in total. Deliberately over- rather than under-generous;
   cutting a customer off at a limit they were told they had is a refund
   conversation. **Reconciling one allowance against two counters is a metering
   decision for the billing work**, not a table edit.

Not modelled: the doc's "Entities" column. There is no entity cap in the code and
inventing one would be a silent, untested restriction on existing tenants.

**A real defect this surfaced:** `access._PLAN_META` derived "paid" from
`not p.trial`, correct only while `trial` was the sole €0 plan. Adding a
perpetual free tier made it report Free as paid. Now derived from the price.

**This unblocks** the archive's paid retention extension, which could not be
priced against a ladder nobody had chosen.

### Original entry (retained for the record)


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
**Status:** 🔶 **PARTLY RESOLVED (2026-08-08)** — the MODEL is decided and BUILT
(WO-95); **the NUMBER is still open** and is now the only thing outstanding on
this item.  ·  **Raised by:** WO-49  ·  **Built against by:** WO-95  ·  **ADR:** [0023](architecture/adr/0023-platform-evolution-and-transport-seam.md)

### What is now decided and shipped (WO-95, G2.9)

The 2026-08-08 answer — **a contingency fee on recovered VAT, no-win-no-fee** —
is implemented end to end. `app/services/transport/fee.py` carries C11's formula
(`max(pct% × base, min)`, returning C11's own `(fee, basis)` pair) and its
resolution chain, widened by the org-level STANDARD rung this decision and R40
both name:

    per-(customer, country) override  ->  customer default  ->  org standard  ->  REFUSE

Rates live in the new tenant table `vat_fee_rates`; `lock.submit_claim` resolves
one as its LAST gate and freezes `fee_pct`/`fee_min`/`fee_eur` onto the claim in
the same flush as the VAT base, the locks and the status flip (C10). A filed
claim is never re-rated — changing or even DELETING the configured rate
afterwards leaves all three columns untouched (R13's acceptance line, asserted
both ways).

The blocker this item recorded in its M3 update is **retired**: it said the
codebase had *"no established mapping from a `VatRefundClaim` to a billable
'customer'"*. WO-73 had already shipped one — `VatCustomerLifecycle`, keyed
`(org, entity_id)`, literally named for the customer, and gating every
submission. The claimant entity IS the client; no second identity was invented.

### What is still open — precisely

**Only two numbers, and they are the two the decision explicitly deferred:**

1. **the standard contingency PERCENTAGE** (`fee_pct`), and
2. **the standard per-declaration MINIMUM** (`fee_min`), if there is to be one
   (a minimum of €0.00 is a legitimate answer, and is stored as such).

Both are typed per org through `fee.set_rate`, and a per-client or
per-(client, country) override can differ from whatever standard is chosen. So
the answer needed is a STARTING standard, not a policy that binds every client.

**Until they are typed, the engine FAILS CLOSED**: an org with the transport
module enabled and no configured rate cannot submit a claim — 409
`fee_rate_not_configured`, with a message naming what to configure. That is
deliberate and is argued in `fee.py`'s module docstring. Two candidate defaults
were available and both were refused: `BA_fleet_fuel.md` Appendix B's
`pricing_fee_pct 15%` (a figure from the retired system, not a decision about
this product) and C11's own terminal `(0, 0)` rung (which would freeze
`fee_eur = 0.00` — a positive assertion that a filing earns nothing, which
nobody made). The diesel-excise placeholder precedent does not transfer: that
rate is advisory, labelled as indicative on every surface, and belongs to a
member state; this one is ours and binding, and the first place a wrong one
surfaces is an invoice a client pays.

**Note that this is now an OPERATIONAL blocker, not only a commercial one.** No
transport claim can be filed by any tenant until a rate is typed. The narrowest
unblock is a single number for the standard rung; the rest of the mechanism
needs nothing further.

### What remains undecided beyond the rate

- **(a) which plan tier(s) unlock `transport`** — unchanged, still open; the
  module is still absent from every `PLANS[...].modules` set, so
  `PUT /modules/transport` still 402s and a sysadmin escape hatch is still the
  only way in.
- **(b) a flat monthly add-on price alongside the success fee** — the harvested
  model prices the module at €0/mo and monetises the contingency instead
  (`BA_fleet_fuel.md` line 125); whether this product does the same, or charges
  both, is still open. Nothing in WO-95 assumes either.
- **(c) the five pilot Baltic entities as design partners** — unchanged.
- **(d) `payout_to` / fee invoicing** (C12: fee receivable vs deduct-and-remit,
  `F<year>-<NNNN>` numbering) — a separate board, deliberately untouched by
  WO-95, and it needs (b) settled first.
- **(e) partial rejection** (see §13) — the transition that would recompute a
  frozen fee over a reduced base. WO-95 leaves a documented seam and no
  implementation.

### Original entry (retained for the record)

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

> **Superseded by WO-95 (2026-08-10).** Both halves of that reasoning have since
> resolved. The customer-identity mapping was NOT invented — WO-73 had already
> shipped `VatCustomerLifecycle`, keyed `(org, entity_id)` and named for the
> customer, which `lock.submit_claim` already gates on. The fee-rate storage
> shape is `vat_fee_rates`, built to C11's own three rungs plus the org-level
> standard the 2026-08-08 decision names, with the terminal `(0, 0)` rung
> replaced by a refusal rather than a guess. The three claim columns are no
> longer empty.

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

## 13. Re-snapshotting a supplier overcharge claim-back whose evidence has moved (M5 / WO-83)

**Context.** `overcharge.open_claim` FREEZES the detected euro onto the
claim-back — *"the euro the demand letter quotes"* — while
`contract_audit.audit()` stays LIVE over `fuel_transactions`. That freeze is
deliberate and right (the G2.5/ADR-P3 reasoning: a figure quoted to a supplier
must not move underneath the operator). But a later re-ingest, a corrected line
or an edited contract term can make the live line source no longer reproduce the
frozen figure.

WO-83's two send-ready artifacts REFUSE in that state
(`overcharge_evidence_drift`, 409): a demand letter quoting €8,000 whose
attached evidence sums to €6,500 is exactly the misleading document R41's
*"both artifacts show identical lines and totals"* acceptance exists to prevent,
and choosing either figure silently would be worse than refusing. That is the
correct fail-CLOSED behaviour — but it leaves the claim-back with **no way
forward**: the harvested chain has no re-snapshot edge, and inventing one is
master-context §10 territory.

**Decision needed:** how does an operator resolve a drifted claim-back?

- **(a) A `refresh` action on a `detected` claim-back** — re-runs the audit and
  re-freezes `detected_eur` (audited old→new), refused once the claim-back has
  been `packaged` (a figure already sent must never move). Smallest change,
  keeps the freeze meaningful exactly where it matters.
- **(b) Close and re-open** — allow a drifted claim-back to be abandoned (which
  needs §12's decision first) and a fresh one opened on the same natural key.
  No new semantics, but the audit trail records a chase that never happened.
- **(c) Leave it** — the operator fixes the underlying line or term until the
  figures agree again. Costs nothing, but a genuinely superseded claim-back can
  become permanently unsendable.

**Owner:** product, with whoever works the supplier chase list (the same person
§12 needs).
**Interim controls:** nothing here can produce a wrong number — the refusal is
the control. A drifted claim-back contributes €0 to `recovered_total` until it
reaches `recovered`, the live figure is always available at
`GET /transport/overcharges/audit`, and the EVIDENCE PACKET is not affected by
the decision (it refuses on drift for the same reason the letter does: it is the
letter's own enclosure).

---

## 14. Diesel excise — the two questions the spec itself leaves open (M5 / WO-91)

`BA_fleet_fuel.md` §9.2 records both of these as open, and WO-91 shipped G4.6
without deciding either (master-context §10). Neither blocks the feature: the
figure is explicitly advisory and says so on every surface it appears on.

**(a) Who owns the real per-country statutory rates** (§9.2 item 13, verbatim:
*"The excise rates are a single EUR 30/1,000 L placeholder for all seven
countries. Who owns the real per-country statutory rates, and how often do they
change (quarterly, per the research)?"*). The platform ships §2.4's own
placeholder, labels it as one on every surface (`excise.RATE_CAVEAT`,
`is_override`, the workbook's "Rate source" column) and lets an operator type
the verified rate per country. What is undecided is whether the OPERATOR
maintains those seven rates centrally as reference data, or each client does.
Options: **(a)** a platform-maintained rate table with a quarterly review owner;
**(b)** client-maintained only, as shipped; **(c)** a licensed rate feed.

**(b) Who confirms eligibility** (§9.2 item 14, verbatim: *"Who confirms
eligibility for excise (vehicle >= 7.5 t, carrier registration)? It is
deliberately not modelled."*). WO-91 makes the non-assertion structural rather
than modelling the conditions — one `ELIGIBILITY_STATEMENT`, a required
`eligibility_asserted: false`, and no claim vocabulary anywhere on the surface.
Modelling it would mean holding vehicle weights and carrier registrations per
entity, which is a data-collection commitment, not a code change.

**A smaller consequence of (a) worth deciding with it:** `set_rate` accepts only
the seven states the spec records as operating the regime, so a state whose
regime LAPSES can be re-rated but not switched off. An `active` flag would fix
it; no such lifecycle is harvested anywhere in the spec, so WO-91 recorded the
gap rather than inventing one.

**Owner:** product, with whoever would own the rate review.
**Interim controls:** the figure asserts no eligibility and no entitlement, in a
constant every surface renders; a state with no rate produces no row rather than
a EUR 0.00 one; and the customs packet refuses to render at all when there is
nothing to file.

---

*Not blocked — I can keep building these without you:* enhancements to shipped
features, tests/coverage, docs, and any of the above up to its stated boundary.
Tell me which to prioritise next.



## 15. Expense reports record a euro with no provenance (WO-V)

**Not blocking.** WO-V extended the FX triple guard to `invoices` and found a
gap in the expenses domain that it deliberately did not close, because closing
it touches money people have already approved and been paid.

**What is true today.** `expense_reports` carries `total_eur` — a converted
figure — and **no `fx_source` column at all**. Nothing records HOW that euro was
arrived at. §4.15's whole premise is that a converted amount is meaningless
without its rate, and this is the one table that holds a converted amount and
cannot say. (`expense_items` DOES carry `fx_rate`/`fx_source`; the report-level
total does not.)

**Why a constraint cannot fix it.** There is nothing for a CHECK to contradict.
Fixing it means ADDING `fx_source` (probably `fx_rate` too), which raises the
only genuinely hard question:

> **What provenance do the EXISTING report rows get?**

They cannot honestly be `ecb` — nobody recorded which rate was used. They cannot
be `unknown` either, because `unknown` means "the euro is NULL", and NULLing
`total_eur` on reports that have been approved, reimbursed and reconciled would
delete a figure people acted on.

**Options, with the trade recorded rather than hidden:**

1. **Backfill `stated`** — "the figure as recorded at the time", which is
   literally what happened: a human or an earlier code path produced it. Honest
   about the euro, silent about the rate. Cheapest, and no number moves.
2. **Add a fifth provenance, `legacy`** — says exactly "recorded before this
   product tracked FX provenance". Most truthful; costs a change to the closed
   enum that ADR-0010 deliberately keeps small, and every reader must learn it.
3. **Leave historical rows NULL and constrain only new ones** — a
   `created_at >`-style predicate. Truthful, but it puts a date in a CHECK
   constraint, which ages badly.

**Recommendation: option 1** for the backfill, plus the constraint on new rows.
`stated` already means "the document or claimant stated the conversion", which
is the closest true statement about a figure a person entered. It changes no
euro and needs no new vocabulary.

*Nothing is blocked on this.* Expense FX is correct at the ITEM level today, and
the reports' euros are not wrong — merely unaudited.

---

## 16. Inbound email attachments still hard-delete (WO-V)

**Not blocking, and smaller than §15.** Every other delete in this product goes
through a 30-day recycle bin. Inbound email attachments do not: the retention
purge destroys `inbound_invoices` rows and their bytes outright, because
`InboundInvoice` has no `deleted_at` column, so the bin literally cannot hold
it. WO-V routed EXPENSES through the bin (after teaching the bin to destroy
bytes at purge, which it never did) and stopped there rather than pretend.

**The work, if wanted:** two columns + a migration + a `bin.KINDS` entry with a
`bytes_of` hook for the attachment + `SOFT_DELETE_MODELS` registration. Small
and well-understood — it is queued rather than open.

**The only real question is whether it is WANTED.** An inbound mail attachment
is the rawest possible input: it has already been parsed into an invoice (which
IS binned and archived), and keeping a second recoverable copy of every emailed
PDF for 30 extra days is storage and GDPR surface for a document the product has
already extracted. A defensible answer is "no — this one is correctly a hard
delete, and the docstring should say so permanently."

---
---

## 17. The refund-estimate funnel: should `/estimate` accept an ANONYMOUS upload? (G4.8 / R43, WO-AC)

**Status: OPEN. The software ships authenticated; the public variant is not
built, and must not be built without an explicit decision.**

### What the spec says

`BA_fleet_fuel.md` §2.3 describes `/estimate` as an **acquisition wedge** —
*"Upload last quarter → see your refund opportunity"* — in-memory only, no
product-DB write, `recoverable_eur = vat_eur`, *"a sales preview, never a filed
figure"*, with an optional prospect handoff. The word "acquisition" implies a
stranger on a marketing site, not a logged-in operator.

It is worth noting what the spec does NOT say. The row directly above it marks
`/value` **"LOGIN-ONLY for any role incl. read-only `user`"** — so the harvest
DOES mark authentication where it means it, and `/estimate` carries no such
marker. That is suggestive of a public endpoint. It is not decisive, and it is
not a mandate.

### Why this is a decision and not an implementation detail

This codebase has a public-route allowlist (`app.core.authz`), every entry
carries a written justification, and `test_authz_coverage.py` enforces that a
route is either permission-gated or explicitly listed. Every entry in it today
is one of exactly two things:

1. an infrastructure probe that touches no tenant data (`/health`, `/metrics`), or
2. **token-authenticated** — the calendar feed, the portal magic link, the
   invite and reset links. In each, *the token IS the credential*, it is
   revocable, and it serves only its own owner's data.

An anonymous `/estimate` would be **the first route in this system where an
unauthenticated stranger causes the server to parse a file they supply.** That
is a different category of exposure from anything currently public here:
resource consumption on demand, and untrusted bytes reaching the fuel-card
parsers. `filesec.check` and the size cap help; they are not the same as
requiring a credential, and this system auto-deploys to production on every
push to `main`.

### What was built instead, and why it is not a fudge

WO-AC ships the funnel **authenticated** (`VAT_READ`, like the rest of the
transport vertical). Every specified behaviour is there: the in-memory parse
with no product-DB write, per-country aggregation, the Art. 17
minimum-threshold flag, the R53 *"indicative — verify before relying"* framing,
and the optional prospect handoff through `customer_lifecycle.add_prospect`.

The acquisition workflow this actually serves is a real one: a salesperson
inside the workspace runs the estimate for a lead they are onboarding, and
hands off to a prospect record in one click. What is missing is only the
self-service marketing-site variant.

### The decision needed

Choose one, explicitly:

- **(a) Keep it authenticated.** No further work. The marketing site links to a
  contact form rather than an upload.
- **(b) Add an anonymous public variant.** This needs, at minimum: a rate limit
  keyed on IP, a tighter size cap than the authenticated one, a decision on
  whether uploaded bytes are ever retained (the spec says no product-DB write,
  which helps), and an explicit `authz` allowlist entry with its own
  justification. It should be its own work order with those controls as its
  certification, not a flag flipped on the existing route.

**Do not resolve this by inference from the spec's silence.** The harvested
system was a different deployment with different exposure; this one deploys to
production automatically.

## 18. What happens to a paying tenant whose card is declined? (PROD-001, audit 2026-09-05)

**Today, mechanically:** `billing.charge_renewal` (EveryPay) and the Stripe
status map (`past_due` / `unpaid` / `incomplete` / `paused` → `suspended`) set
`org.status = "suspended"` on the FIRST failed attempt — before Stripe's own
smart-retry window — and every data route then answers 401 to every member on
their next request. No email is sent on that path.

**What the audit fixed without a decision (the defect):** the one person who
could fix the card could not reach the screen that takes it — `/auth/me` and
`/billing/*` 401'd too, so the SPA could not even boot. They answer for a
suspended org now (owner only for billing; a canceled org stays locked out),
and the shell renders a single destination, Plan & billing, with the reason
stated. Nothing about WHEN a tenant is suspended changed.

**The decision:** an SME whose card expired mid-month is locked out of its own
invoices at the moment it is least sympathetic. Options:

1. **Grace period, full access** — the org becomes `delinquent` (new status)
   for N days with a persistent banner and an emailed dunning ladder (day
   0 / 3 / 7 / 14 is the convention); `suspended` only after the ladder
   exhausts. *Recommended; N = 14.*
2. **Grace period, read-only** — as (1) but the org cannot create or send
   anything while delinquent. Safer commercially, more code (a read-only
   permission mode does not exist today).
3. **Keep suspend-at-first-failure** — the current behaviour, now with a
   reachable billing screen. Cheapest; harshest.

Needed from the owner: which option, N, and whether the dunning emails go to
the owner only or to every admin. Engineering builds nothing until answered.

## 19. The 14-day trial exists in three documents and in no code (PROD-002)

`plans.py` says "the 14-day full-feature trial … expires onto `free`", the
pricing doc sells it, and `DEFAULT_PLAN = "trial"` — but `organizations` has no
trial-start or trial-end column, registration stamps no date, and no job
downgrades anyone. Every self-serve signup gets `issuing`, `expenses`,
`email_intake` and `budget` for ever, capped only at 10 invoices / 20 uploads a
month. There is no conversion moment and therefore no funnel to measure.

Decision: (a) build the clock — `trial_ends_at` at registration, a daily job
that downgrades expired trials to `free` and emails at T-3 and T-0; or (b) drop
the 14-day language and call the default plan what it is, a permanent free tier
with a low cap. Either is fine; the code contradicting the documents is not.
*Recommended: (a), because the pricing hypothesis depends on a conversion event.*

## 20. Owner actions the 2026-09-05 audit could not perform from the repository

These are done when the owner does them; nothing in the repo can.

- **Host `deploy.sh`** on the VPS still runs `up -d --build && docker image
  prune -f` unless replaced with the version in `docs/DEPLOY-HOSTINGER.md`
  (preflight, verified backups, health gate). Until then the CI auto-deploy
  takes no backup and prunes its own rollback image.
- **`DEPLOY_HEALTH_URL`** repository variable (the public `/health/ready`) —
  until set, the deploy job warns instead of failing when the site is down.
- **`scripts/backup.sh` cron line** on the VPS, and an `RCLONE_REMOTE` so the
  backups leave the box.
- **`TRUSTED_PROXY_COUNT=1`** is now in the compose file; the next deploy picks
  it up — verify the audit log's `ip` column shows real visitor addresses after.
- **Branch protection on `main`** (required checks incl. `frontend-e2e`,
  require a PR) — cannot be asserted from inside the repo.
- **DPA, terms, privacy notice, Art. 30 record, sub-processor list** (PROD-007)
  — counsel; engineering then adds versioned acceptance at registration.
- **Seller-of-record VAT** (§2) and the **grace policy** (§18) and **trial**
  (§19) above.

## 2026-08-16 — the retention/deletion-chain reconciliation (P0-2)

Four questions asked and answered in one sitting:

1. **Retention purge routes invoices THROUGH the chain.** No second destruction
   path: the policy soft-deletes into the recycle bin (`deleted_by` = "retention
   policy"), and the ordinary 30-days → archive → 3-years pipeline takes over.
   Expenses and email attachments keep the direct hard delete until the bin
   learns those entities (approved, tracked).
2. **The archive keeps its full 3 years regardless of a shorter tenant policy.**
   It is the platform's compliance backstop and deliberately outlives client-side
   deletion. **Must be stated in the DPA** (basis: statutory accounting
   retention) — agreed, never discovered.
3. **Ex-client:** notices to the last owner address + one-time export on request.
4. **Next build:** the pre-expiry notice + paid retention extension.


---

## 2026-08-16 — the full project lifecycle (owner vision, needs decisions before phases 4–5)

The owner's complete arc is recorded in `docs/design/project-profitability.md`
§5a: open → offer/estimate → contract → invoicing per contract → costs →
acceptance & handover → final invoicing, with **standardized contract and
acceptance templates for customers**. Phases 1–3 need nothing from this section;
these block phases 4–5 only.

**All three ANSWERED by the owner, 2026-08-16 (same day):**

1. **Templates → a lawyer will work on the standardized contract and
   acceptance texts.** The review path is committed, not hypothetical. What
   engineering still needs before phase 5 ships defaults: the reviewed base
   texts themselves, and the jurisdictions/languages they cover — the build
   can proceed against per-org custom templates in the meantime, because the
   template MACHINERY (placeholders, prefill, per-org storage, PDF render) is
   identical whether the text came from our lawyer or the client's.
2. **Final invoicing is ADJUSTABLE/DYNAMIC, by owner requirement.** The
   invoicing plan's computed remainder is a STARTING POINT, never a locked
   figure: unexpected costs, damages and claims — in either party's favour —
   are added as explicit, labelled adjustment lines on the final invoice. The
   design consequence (recorded in the design doc §5a): the final invoice
   reconciles as contracted sum ± named adjustments = final total, so the P&L
   explains the difference instead of hiding it, and a negative adjustment
   large enough to flip the sign becomes a credit note through the existing
   machinery rather than a negative invoice. (Whether issuing the final
   invoice is GATED on acceptance stays as recommended: linked by default,
   gate as a per-org toggle — adjustability makes a hard gate even less
   appropriate, since the adjusting party needs control of the moment.)
3. **Offer numbering logic is set by the client** — a per-org configurable
   scheme (prefix/pattern/counter), not a hardcoded gap-free series. The
   platform enforces exactly one thing regardless of the chosen scheme:
   uniqueness per org, so an offer reference is never ambiguous. No
   invoice-grade locking machinery unless a client's scheme needs it.
