# Task selection

**Selected: R5(b) — close the Enterprise self-upgrade-for-free bypass in `PUT /billing/plan`.**

Rationale (per `docs/audit/remediation-roadmap.md` Milestone B, priority order):
- R4 (P1) is CLOSED (WO-30). R5 (P1) is next in the roadmap's own listed order and is explicitly
  split by the roadmap itself into two parts: "(a) **Business decision** … this is a GTM decision,
  not purely engineering" and "(b) **Regardless of that decision**, close the Enterprise
  self-upgrade bypass … independent of `billing_enabled`" (roadmap lines 260-266). Part (a) is
  tracked as decision-gated in `docs/DECISIONS-NEEDED.md` item 2 ("Billing go-live (Stripe +
  EveryPay)") — live credentials, VAT seller-of-record process, Stripe metered-billing setup —
  none of which this session can supply or decide. Part (b) has no such dependency: it is a pure
  guard-condition fix with a fully engineering-scoped acceptance criteria list (roadmap lines
  274-282, items 1-2 engineering; item 3 documents that (a) stays open rather than resolving it;
  item 4 is frontend UX only).
- Confirmed NOT already done: `backend/app/api/routes/billing.py:73` still reads
  `if settings.billing_enabled and target.price_eur:` — for the `enterprise` plan
  (`app/services/plans.py` `price_eur=None`), this condition is `False` regardless of
  `settings.billing_enabled` (`None` is falsy in Python), so `change_plan` falls through to the
  seat/module checks and sets `org.plan = "enterprise"` directly. Reproduced below (red-then-green).
- Confirmed NOT blocked: no schema/migration, no tenancy change, no other WO depends on it, R6/R7/
  R14/R16/R18 (the rest of Milestone B) are independent of and lower-priority than R5 in the
  roadmap's stated order.
- Skipped R6 (SoD, P2) / R16 / R18 (P2 UX) in favor of R5 (P1) per rule (d) "follow the roadmap's
  own stated priority order" — R5 is P1, everything else remaining in Milestone B is P2.
- No WO file for R5(b) exists yet under `docs/plan/plan-a/wo/` (checked: WO-01..WO-30 present,
  31 is free).

---

**WORK ORDER 31 — Close the Enterprise self-upgrade-for-free billing bypass (board R5(b)). Effort S (1d). Priority P1. Milestone B. Depends on: nothing.**

### Objective and business value
`PUT /billing/plan` (`backend/app/api/routes/billing.py::change_plan`, line 73) gates a self-service
plan switch on `settings.billing_enabled and target.price_eur`. `plans.py`'s `enterprise` plan has
`price_eur=None` ("contact us" / custom pricing), and `None` is falsy — so the guard is `False`
for Enterprise **unconditionally**, independent of whether `billing_enabled` is `True` or `False`.
Any org owner (an owner is created automatically as the creator of a freshly self-registered org,
reachable from the public `register` endpoint with no vetting) can call
`PUT /billing/plan {"plan": "enterprise"}` and receive 200 with `org.plan == "enterprise"` —
200 seats and every add-on module, for €0, in a **fully-wired, live-Stripe deployment**, with zero
test coverage catching it. This is worse than "billing isn't wired yet" (a known, accepted gap):
it is a bypass that *survives* wiring billing, silently defeating the whole point of turning
billing on.

Business value: this closes a free-upgrade path that would let any self-registered account walk
away with the top-tier entitlement (200 seats, every module) at zero cost even after the company
starts charging for it — directly costing revenue the moment self-serve billing goes live, and
undermining the credibility of "billing is on" to a paying customer who notices a competitor/
churned user is running Enterprise for free. Fixing it now (independent of the GTM billing-go-live
decision) means the moment (a) is resolved and a real provider key is set, there is no leftover
trap.

### Scope
**In scope:**
- `backend/app/api/routes/billing.py::change_plan` — add an unconditional guard: a self-service
  `PUT /billing/plan` to any plan with `price_eur is None` is refused (409), regardless of
  `settings.billing_enabled`.
- `backend/tests/test_billing_stripe.py` — red-then-green proof, both `billing_enabled=True` and
  `billing_enabled=False` configurations.
- `frontend/src/pages/Billing.tsx` — replace the actionable "Switch to Enterprise" button with a
  non-mutating "Contact sales" affordance for any plan with `price_eur === null`, so the UI never
  offers a self-service action the server will now refuse.
- `docs/architecture/adr/0013-billing-metering.md` — record the fix under "Status of
  implementation"; explicitly note the GTM decision (R5(a) / `DECISIONS-NEEDED.md` item 2) remains
  OPEN and is not resolved by this change.
- `docs/audit/remediation-roadmap.md` — mark R5(b) closed, citing WO-31; leave R5(a) open exactly
  as-is (still gated on `DECISIONS-NEEDED.md` item 2).

**Out of scope:**
- R5(a) itself — deciding whether/when to wire a live Stripe/EveryPay key, or whether pilots are
  invoiced manually. That stays in `docs/DECISIONS-NEEDED.md` item 2, untouched.
- Any change to the *paid, priced-plan* checkout/webhook flow (Stripe/EveryPay providers,
  `start_checkout`, `apply_subscription_event`) — untouched, already covered by
  `test_billing_stripe.py`/`test_billing_everypay.py`.
- R6/R7/R14/R16/R18 — separate roadmap items, separate work orders.
- Building an actual "contact sales" backend flow (lead capture, email routing) — not specified by
  the roadmap acceptance criteria; the frontend change is presentational only (no new mutation, no
  new endpoint).

### Files to touch
| File | Change |
|---|---|
| `backend/app/api/routes/billing.py` | `change_plan`: add unconditional `target.price_eur is None` guard before the existing `billing_enabled` paid-plan guard |
| `backend/tests/test_billing_stripe.py` | add `test_enterprise_self_upgrade_blocked_billing_disabled` and `test_enterprise_self_upgrade_blocked_billing_enabled` |
| `frontend/src/pages/Billing.tsx` | `choosePlan`/render: a plan with `price_eur === null` (and not current) renders a disabled "Contact sales" control instead of an active `btn-primary` that calls `change.mutate` |
| `docs/architecture/adr/0013-billing-metering.md` | append a line under "Status of implementation" recording the fix; note R5(a) still open |
| `docs/audit/remediation-roadmap.md` | mark R5(b) closed under the R5 entry, citing WO-31; R5(a) stays open |
> All paths verified to exist except none are new.

### Implementation guidance
1. Reproduce first (red): add a test that, with `settings.billing_enabled` left at its default
   (`False`/no provider key), calls `PUT /api/v1/billing/plan {"plan": "enterprise"}` against a
   fresh trial-plan org and asserts it currently returns `200` with `org.plan == "enterprise"` —
   confirm this is the CURRENT (buggy) behavior before touching `billing.py`, then convert the
   assertion to the fixed expectation (`409`) as the "green" state, per master-context §9's
   red-then-green discipline for a correctness bug. Do the same with `billing_enabled=True`
   (monkeypatch `stripe_secret_key`, matching the existing `test_paid_plan_switch_blocked_when_billing_enabled`
   pattern) — that path already 409s today by coincidence (`billing_enabled and target.price_eur`
   is `True and None` → `False`... wait: confirm exact truth table before writing the test; do not
   assume, verify by running the CURRENT test against CURRENT code first).
2. Fix: in `change_plan`, immediately after resolving `target = plans.plan_for(body.plan)`, add:
   ```python
   if target.price_eur is None:
       raise HTTPException(
           status.HTTP_409_CONFLICT,
           f"{target.name} is not self-service — contact sales to switch to this plan.",
       )
   ```
   placed BEFORE the existing `if settings.billing_enabled and target.price_eur:` block (that
   block becomes dead for `price_eur is None` targets since this new check returns first, which is
   fine — an explicit early guard reads clearer than relying on operator precedence between the two
   conditions). Do not touch the seat/module downgrade-guard logic below it, and do not touch the
   `price_eur == 0` (trial) path — `0 is None` is `False`, so trial/free plans are unaffected and
   remain directly self-service-switchable exactly as today (this is deliberate existing behavior,
   not part of this fix).
3. Verify the priced-paid-plan tests (`pro`, `starter`) are unaffected — `target.price_eur` for
   those is a positive int, not `None`, so the new guard never fires for them.
4. Frontend: in `Billing.tsx`, compute `const selfService = p.price_eur !== null;` per plan card.
   When `!selfService && !current`: render a `<span>`/disabled button reading "Contact sales" (no
   `onClick`, not wired to `change.mutate` or `checkout.mutate`) instead of the current
   `btn-primary`/`"Switch to Enterprise"` button. Keep the existing `current` ("Current plan") and
   priced-plan (`Subscribe`/`Switch`) branches unchanged.
5. Update `docs/architecture/adr/0013-billing-metering.md`'s "Status of implementation" paragraph
   with one sentence recording the fix (guard is unconditional on `price_eur is None`, independent
   of `billing_enabled`) and one sentence that the GTM question in `DECISIONS-NEEDED.md` item 2 is
   still open and NOT resolved by this change (do not invent a resolution).
6. Update `docs/audit/remediation-roadmap.md`: mark the R5(b) engineering half closed with a
   "Closed by WO-31" note under the existing R5 entry, in the same style as the R1/R2/R3/R4 closure
   notes already in that file; leave the R5(a) GTM half's acceptance-criteria items 3/4 status
   accurately described (3 partially addressed — ADR updated but decision not resolved; 4 done via
   the "Contact sales" UI change) — do not mark R5 fully closed since (a) is untouched by design.

### Invariants this order must preserve
- **§4.10 "The server recomputes every total"** (adjacent invariant, not directly touched) — this
  fix is about which plan `org.plan` may be set to, not a total; no total/amount computation
  changes.
- **§4.6 Deny-by-default / structural authz** — `BILLING_MANAGE` (owner-only) dependency on
  `PUT /billing/plan` is untouched; the new check is a business-rule guard inside an already-gated
  route, not an authz change. Proven unchanged by the existing
  `test_checkout_requires_billing_manage`-style tests continuing to pass.
- **No wire-contract break (§4.20)** — no new required/renamed field on `PlanChange`/`BillingOut`;
  the 409 error shape (`{"detail","code"}` + `X-Request-ID`) is the existing global error handler's
  shape, unchanged.

### Database / migration impact
None. No schema change, no new column, no migration.

### Testing requirements
- `backend/tests/test_billing_stripe.py::test_enterprise_self_upgrade_blocked_billing_disabled` —
  fresh org on `trial`, `settings.billing_enabled` at its default (no provider key set),
  `PUT /billing/plan {"plan":"enterprise"}` → asserts `409` and (by re-fetching `GET /billing` or
  inspecting the DB row) that `org.plan` is still `"trial"`, not `"enterprise"`.
- `backend/tests/test_billing_stripe.py::test_enterprise_self_upgrade_blocked_billing_enabled` —
  same org, `monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")` (billing enabled),
  same request → asserts `409` and `org.plan` still `"trial"`.
- Confirm-unaffected (no new test needed, just must stay green as-is):
  `test_paid_plan_switch_blocked_when_billing_enabled` (pro plan, billing enabled → 409, unchanged
  reason/behavior), and any existing test that switches to `trial`/`starter`/`pro` with billing
  disabled (must still succeed at 200 — the fix must not block priced-but-not-None plans).
- This is a business-rule/authorization-adjacent negative case (§8 "malformed input at the
  boundary" family — here, an out-of-catalogue self-service target) rather than a
  money/concurrency/cross-tenant case; no row lock, no cross-tenant, no concurrency dimension
  applies to a plan-string guard, so those adversarial categories are not applicable here (single
  synchronous guard-condition check, no shared mutable resource contended by two requests).

### Acceptance criteria (verifiable checklist)
- [ ] `PUT /api/v1/billing/plan {"plan":"enterprise"}` returns `409` for an org owner when
      `settings.billing_enabled` is `False` (default/no provider key) — proven by
      `test_enterprise_self_upgrade_blocked_billing_disabled`.
- [ ] Same request returns `409` when `settings.billing_enabled` is `True` (Stripe key set) —
      proven by `test_enterprise_self_upgrade_blocked_billing_enabled`.
- [ ] In both cases above, `org.plan` is unchanged after the 409 (re-fetch and assert equality to
      the pre-call value).
- [ ] `PUT /api/v1/billing/plan {"plan":"pro"}` / `{"plan":"starter"}` / `{"plan":"trial"}` behavior
      is byte-identical to before this change (existing tests pass unmodified — no assertion in any
      pre-existing billing test is edited).
- [ ] `Billing.tsx` renders a non-actionable "Contact sales" control (no `onClick` wired to
      `change.mutate`/`checkout.mutate`) for any plan card where `price_eur === null` and it is not
      the org's current plan.
- [ ] `docs/architecture/adr/0013-billing-metering.md` records the fix and explicitly states the
      GTM decision (item 2 in `DECISIONS-NEEDED.md`) remains open.
- [ ] `docs/audit/remediation-roadmap.md` shows R5(b) closed, citing WO-31, with R5(a) still
      explicitly open.

### Rollback strategy
Pure code revert (single commit reverts the backend guard, one commit reverts the frontend change,
docs commits revert independently). No migration, no data written or lost, no one-way effect —
reverting restores exactly the prior (bypassable) behavior with no artifact left behind.

### Documentation to update
- `docs/architecture/adr/0013-billing-metering.md` ("Status of implementation" paragraph).
- `docs/audit/remediation-roadmap.md` (R5 entry — close the (b) half, cite WO-31).
- This file (`docs/plan/plan-a/wo/WO-31-R5b.md`) itself is the record of the order.

### Self-verification block
```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
python -m pytest tests/test_billing_stripe.py -q
python -m pytest -q                                                              # full baseline
grep -n "price_eur is None" app/api/routes/billing.py                            # the fix landed
cd ../frontend && npm run build                                                  # SPA changed
```
