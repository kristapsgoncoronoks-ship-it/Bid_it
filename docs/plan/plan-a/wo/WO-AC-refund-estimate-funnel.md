# WO-AC — the refund-estimate funnel (G4.8, R43)

**Shipped 2026-09-05.** No migration — the whole point of this order is that it
writes nothing.

## The premise was queued UNVERIFIED, and the check said build

The arc-4 queue put this order in without verifying it, with instructions to
drop it if the premise had expired the way `advertised_prices` did. It has not.
Nothing superseded R43; it is simply unbuilt, and it turned out to be cheap
because every dependency shipped in the meantime:

| Needs | Already exists |
|---|---|
| in-memory parse, no DB write | `fuel_card_parser.run` — pure, returns a `ParsedStatement` (WO-S) |
| per-country aggregation + minimum flag | `minimum.below_minimum` / `min_for` / `NATIONAL_MINIMUMS` |
| optional prospect handoff | `POST /transport/customers/{id}/prospect` (WO-77) |
| the R53 caveat vocabulary | already carried by `excise.py` / `savings.py` |

The sweep's note that "the only trace is one sentence in `excise.py`" was right
about the trace and wrong to read it as decay — that sentence is the R53
framing quote, not the funnel.

## The check surfaced a decision, which is why it was worth running

§2.3 calls `/estimate` an *acquisition wedge*, and the row directly above it
marks `/value` **LOGIN-ONLY** — so the harvest marks authentication where it
means it, and `/estimate` carries no marker. That reads as a public
marketing-site page.

This deployment is not that one. Every entry in this codebase's public-route
allowlist is either an infrastructure probe that touches no tenant data or
**token-authenticated**, where the token is the credential and serves only its
own owner's data. An anonymous `/estimate` would be the first route here where
an unauthenticated stranger makes the server parse bytes they supply — on a
system that deploys to production automatically on every push to `main`.

**Owner decision, 2026-09-05: authenticated only, no public variant.** Recorded
in `DECISIONS-NEEDED.md` §17 with the controls a public version would have
needed, so the reasoning survives even though the answer was "no".

## What shipped

`estimate.py` (the analysis), `transport_estimate.py` (the wire shapes), a
`VAT_READ` router, and `/refund-estimate` in the SPA — the analysis ships with
its screen, because a shipped analysis nobody can open is the WO-S/WO-U defect.

Three decisions worth keeping:

- **`recoverable_eur = vat_eur`** is §2.3's own generous assumption, and it is
  what makes this a sales preview rather than a filing. Supplier registration,
  receipt control, the document gate, waivers, Art. 17 and the fee can each
  only *reduce* it, so the figure is an upper bound — and `CAVEAT` says so on
  every response, in R53's own "indicative, verify" wording rather than the
  language reserved for a contractual claim-back.
- **`below_minimum` is three-state.** `null` means the Art. 17 comparison could
  not be made in the country's own currency (Sweden and Denmark compare a local
  amount; a country whose lines arrive in mixed currencies has no single one).
  Rendering that as `false` would tell an operator a claim clears a threshold
  nobody checked — the same collapse `excise.py` refuses for a missing rate
  versus a placeholder one. The screen renders all three distinctly.
- **A line `fx` cannot convert is counted and named, never dropped.** Silently
  skipping it would report a smaller opportunity than the file contains and
  give no sign it had done so, which is the failure mode that turns a sales
  tool into a misleading one.

## What the build found

**The parser already refuses a missing country, so my own branch for it was
dead code — and weaker than what already happens.** The first draft counted a
country-less line as "unattributable" and warned. Every shipped parser
validates country and currency codes structurally and raises, which refuses the
*whole file*; quietly excluding money from a total is a softer answer than
that. The branch was removed and the test now asserts the refusal, which is the
real behaviour.

**A fixture I had reasoned about was wrong, and it would have given false
confidence in the assertion that mattered most.** The unconvertible-line test
used SEK on the assumption that no rate was seeded. The harness seeds every
European currency plus the global majors, so SEK converts — the test failed,
and had it not, it would have exercised the conversion path while claiming to
prove the missing-rate path. It now uses ZAR, which is a structurally valid
code with genuinely no entry in `fx.FALLBACK_RATES`, and the test says why.

**The first seeded violation proved nothing because it crashed instead of
writing.** Seeding a `SupplierVatRegistration` with a column name that does not
exist raises a `TypeError` — the test failed, but for the wrong reason, which
is indistinguishable from success at a glance and is exactly how a gate gets
believed without being tested. Re-seeded with a real write, the row-count gate
fails with the right message.

**Three mechanisms were being credited to one, in my own test docstring.** The
route returns 401 to an anonymous caller because of the `CurrentUser`
dependency (authentication). `require_perm(VAT_READ)` is authorization, and
removing it does not change that answer — seeding its removal proved the point:
my route test still passed, and `test_authz_coverage.py` is what failed.
`authz`'s allowlist is a third thing again — the *declaration* that a route may
be public. The docstring claimed my test guarded publicness; it does not, and
it now says which test does.

## Certification

- **The write-free rule, asserted as an absence**: row counts in every table the
  real intake path writes to, before and after, required equal. Seeded a real
  write → the gate fails naming the table.
- Per-country aggregation, the R53 caveat on every response, the three-state
  minimum, and the threshold following the PERIOD (€400 quarterly / €50 annual,
  the same figures flipping on nothing but the period).
- Route: 401 anonymous, 415 for a non-CSV before any parser runs, 422
  `invalid_period` carrying the service's own code, 403 `module_not_enabled`.
- 7 e2e specs covering the two things a screen can get wrong on its own: the
  number appearing without its framing, and `null` rendering as "clears".
- `check-e2e` earned its keep: the new spec existed but was not in
  `package.json`'s `test:e2e`, so it would never have run in CI. That is the
  WO-Y defect the gate was built for, caught on its first opportunity.
