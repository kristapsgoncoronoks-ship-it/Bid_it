# WO-AD — billing go-live wiring: retention rides the ladder

**Shipped 2026-09-05.** No migration.

## Three decisions, taken before a line was written

The order said "the software that was gated on the decision, not the activation
itself". Three of the gated items turned out to be decisions rather than code,
and they were put to the owner and answered the same day:

| Question | Decision | What it changed in code |
|---|---|---|
| Does longer archive retention ride the plan ladder or sell standalone? (§1.B, open since the ladder was settled) | **Rides the ladder.** Business/Enterprise carry 7 years. | A plan attribute, a MAX, one re-stamp. No add-on price, no new payment flow, no new correlation table. |
| Block at the monthly cap, or allow-and-meter overage? | **Keep blocking.** | Nothing. `access.py`'s policy stands; the upload meter stays dormant. |
| Reconcile the one-allowance-two-counters over-grant? | **Leave it**, documented as intentional. | Nothing. `plans.py` departure 3 stays, now marked decided. |

The pricing doc had already been pointing at the first answer — retention as
the up-tier lever, and no archive add-on in its à-la-carte list — but the
question was the owner's to close, and it is now closed in
`DECISIONS-NEEDED.md` rather than inferred.

## What shipped

- **`Plan.archive_retention_years`** (3 everywhere; 7 on Business and
  Enterprise). `practice` stays at 3 deliberately — a partner plan's retention
  is its own commercial question and was not part of the decision.
- **`archive.retention_years()` is a MAX** of the included floor, the plan and
  any staff override. Never a min: a misconfigured plan or a cleared override
  must never quietly shorten what a client was told. A downgrade re-stamps
  nothing — rows keep the longer date they were promised.
- **One re-stamp implementation.** `restamp_to_effective` is called from every
  path a plan can change through — the Stripe webhook, the EveryPay settle, the
  in-app switch — and by the staff override. An upgrade that protected only
  invoices deleted afterwards would be worthless at the moment it is bought,
  which is right after a pre-expiry notice about records already archived. The
  loop that used to live inside `apply_retention_override` is that function
  now, so there is one and not two that could disagree.
- **The notice names the upgrade** ("move to a plan with longer archive
  retention — the longer period then applies to these records too") instead of
  "ask us about extending". **The archive screen** says what an upgrade buys,
  from the server's `longest_plan_retention_years`, never a number typed into
  the page.

## Two go-live gaps the premise check found

Neither was in the order's text. Both would have surfaced on the first day
billing went live.

**Business was a 502 button.** §2a chose the ladder on 2026-08-15, including
Business at €249. `config.stripe_price_for` knew `starter` and `pro`. The SPA
offered "Subscribe to Business" for any plan with a price, so the click would
have reached `StripeProvider.start_checkout`, raised `BillingError("No Stripe
price configured…")`, and come back as a 502. `STRIPE_PRICE_BUSINESS` now
exists; `settings.plan_purchasable` says whether the active provider can sell a
plan; `PlanOut.purchasable` carries that to the SPA, which renders "Not yet
available" and never a checkout; and the route answers a 400 the screen can
read instead of a gateway fault.

**Stripe Tax was decided and not wired.** §2 records "enable Stripe Tax"; the
checkout session never passed `automatic_tax`. It is now `STRIPE_AUTOMATIC_TAX`,
**off by default even with a live key** — collecting tax is a filing
commitment the owner makes explicitly, not a side effect of configuring a
secret.

## A test that had been passing for the wrong reason

`test_checkout_returns_url_when_enabled` set a Stripe key and no price id, and
passed because `FakeProvider` swallowed the missing price. Against real Stripe
that request would have raised. The new `plan_purchasable` gate failed it,
which is the gate doing its job against the test rather than the code. It now
configures the price the way a real deployment must, and a sibling test
asserts the 400 for a plan with none.

## Certification

- Retention MAX-of-three, proven in all four directions (floor / plan / override
  above / override below).
- Re-stamp on the Stripe path (row extended to `archived_at + 7y`, notice stamp
  cleared) and on the in-app switch; a downgrade re-stamps nothing; clearing a
  staff override on a Business org extends to the plan's 7, never shortens.
- `plan_purchasable` matrix across no-provider / Stripe / EveryPay; `GET
  /billing` carries `purchasable` and `archive_retention_years`; `GET /archive`
  carries `longest_plan_retention_years`; Stripe Tax parameter present only
  when the flag is on; the notice body names "Plan & billing" and not "ask us".
- **Seeded violations, restored by inverse edit:** ignoring the plan in
  `retention_years` fails 4 tests; forgetting to clear the notice stamp in the
  re-stamp fails exactly the one test that asserts it.
- Frontend: plan cards state their retention and render "Not yet available"
  disabled for an unpurchasable plan; the archive nudge appears only when a
  higher plan offers more, with the server's figure. 4 new e2e specs, 20/20
  across both files.

## Still owner-side, and said so

Live credentials (now including the Business price id), the Billing Meter
`event_name` (`STRIPE_METER_UPLOAD`), and the seller-of-record VAT
registration and filing. The software does not pretend otherwise.
