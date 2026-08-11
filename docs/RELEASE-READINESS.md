# Release readiness — go/no-go gate

**Commit:** `ce37708` · **Branch:** `claude/bidit-invoice-data-analytics` · **Assessed:** 2026-08-11

This is the release gate for InvoiceIQ. It records what is **verified by
evidence**, what is **not verified and why**, and the criteria each release
stage must meet. It deliberately does not claim production readiness: the
programme's own charter forbids claiming it without evidence, and the evidence
for some claims does not exist yet.

Nothing in this document is a substitute for the owner actions in §5. Several
of them cannot be performed by engineering at all.

---

## 1. Verdict

**Ready for a supervised pilot with a client who knows they are a pilot.
Not ready for self-serve release.**

The engineering is in good order. The gap is not code quality — it is that
the system has never been validated against real data, and that its
verification pipeline is currently blind.

---

## 2. Verified at `ce37708`

Every line below was executed against this commit. Where a claim could not be
executed, it is in §3 instead.

| Check | Result | How |
|---|---|---|
| Backend suite | **2403 passed, 10 skipped** — unchanged across every dependency bump in WO-96 | `pytest -q`, full run, 31m 37s |
| Browser suite | **270 passed** | `npm run test:e2e`, the CI list |
| Lint | clean | `ruff check app tests` (ruff 0.16.2) |
| Format | 563 files clean | `ruff format --check` |
| Types | no issues, 328 files | `mypy app` |
| Migrations | single head `d4c7b1e93f27` | `alembic heads` (alembic 1.19.1) |
| Migration drift | none | `alembic check` on Postgres 16 |
| Postgres gates | **6 passed** | RLS + numbering + lock concurrency, NOSUPERUSER role |
| PII quarantine | clean | `scripts/pii_scan.py --tree` |
| Dependency currency | **every backend and frontend dependency at latest** | WO-96, five staged commits |
| Playwright pair | library 1.62.1 = CI image `v1.62.1-jammy` | the `ci.yml` guard, reproduced locally |
| `npm audit` | **0 vulnerabilities** (was 1 moderate + 3 high) | `npm ci` |

**Dependencies (WO-96, 2026-08-11).** Every pin is now at the latest release,
converging on what `main` already carried. Two backend majors (reportlab 4→5,
pypdf 5→6) and two frontend majors (vite 6→8 with `@vitejs/plugin-react` 4→6;
React 18→19 with both type packages) each landed in their own commit with their
own full verification. The suites did not move: 2403/10 and 270 before and after
each stage, with **no test weakened, skipped or deleted**.

The document renderers were verified by **parsing generated output**, not by
trusting a green suite: page geometry, extracted text in order, the money edge
cases `1234567.89 / 0.01 / -100.00 / 0.00 / 2.005`, and the embedded
`factur-x.xml` attachment bytes were all identical before and after. This
matters because those documents go to a tax authority and to suppliers.

**One behaviour change is open and is a performance regression, not a
correctness one** — see §3.8.

**Structural guarantees** (each proven by a test that fails when violated,
several with seeded-violation self-tests):

- Tenant isolation — org filter, ORM guard, and Postgres FORCE RLS; the
  probe/exempt registry must equal the tenant-model registry exactly.
- Route authorization — every route classified; a new unclassified route
  fails CI.
- Money — Decimal throughout, no float arithmetic on any amount, no
  cross-currency sums, FX refuses rather than guesses provenance.
- Advisory vs binding — an overpay figure cannot reach a demand letter; an
  excise figure asserts no eligibility; a client never sees an internal code
  or a fee.
- One canonical query per analytic shape; one predicate for synthetic lines;
  one upload size cap.

---

## 3. NOT verified — and why it matters

**3.1 No validation against real data.** Every test is authored against
fixtures derived from a harvested specification. They prove internal
consistency, not correspondence with reality. No real supplier statement has
been parsed, no claim filed, no refund received, no demand letter sent.

This is the single largest unknown, and it is not theoretical. WO-84 found
that `net_eur_eff` was silently identical to `net_eur`, so the platform was
demanding money from suppliers who had already paid it — and that defect
passed every test in the suite at the time.

**3.2 CI has no runners.** Every workflow run currently fails in ~1 second
with `runner_id: 0` and no logs — an account-level GitHub Actions condition,
not a code fault. Consequence: the only verification is a developer running
the suite locally. No independent check, no routine Postgres gate, no docker
build per change.

This is now load-bearing rather than merely inconvenient. WO-96 moved four
majors across both halves of the stack with **no independent confirmation
available at all** — every figure in §2 comes from one machine, including the
Postgres gates, which had to be run against a hand-built scratch cluster with a
`NOSUPERUSER` role rather than the CI service container. A reviewer cannot
currently check any of it by clicking a green tick.

**3.3 The branch has never merged.** 96 work orders on one pull request. The
`manualChunks` fix `main` needs still lives only here — but the gap has narrowed
in the other direction: WO-96 brought this branch onto the same versions `main`
already carried (reportlab 5, pypdf 6, vite 8, plugin-react 6, both minor/patch
groups) and past it on react/react-dom, so the merge is a smaller event than it
was. Long-lived divergence remains a release risk.

**3.4 No backup/restore tooling** (audit item **R14**, decision-gated). The
system would hold client invoice documents and VAT claims with no tested
restore path. **A restore drill must pass before any client data enters it.**

**3.5 No load or large-dataset testing** (audit item **R15**). Performance is
untested beyond current fixture scale. `expected_rebate` loads a tenant's
whole transaction history into memory to learn medians — fine now, unmeasured
at scale.

**3.6 The PII quarantine is structural-only.** The deny-list is empty and the
salt is unset, so it catches values *shaped* like identifiers, not actual
Fleet Fuel values.

**3.7 No filing is possible until a fee rate is configured** (WO-95, by
design — fail-closed rather than invent a charge).

**3.8 The SPA's first-load payload roughly doubled under Vite 8** (WO-96 Stage
B, open). Vite 8 bundles with rolldown, which reassigns the shared React runtime
regardless of what `manualChunks` asks for. The consequence is that the 416 kB
`recharts` chunk is now `modulepreload`ed on **every** page, including pages
with no chart; under Vite 6 it was fetched only where charts render.
Critical-path JS measured from `index.html`: **~329 kB → 772,780 B**.

Correctness is unaffected — 270 browser specs green, both named chunks still
emitted, `vite.config.ts` byte-identical. Two traps worth recording: total
emitted bytes *fell* 1.1% and the file count fell 96→79, so an aggregate size
check scores this an improvement; and React 19 restored the chunk *sizes* to
near their Vite 6 values, which reads like a fix but is not one — the preload
set is unchanged.

Not fixed in WO-96 deliberately: the only levers are rolldown-specific
(`rolldownOptions.output.codeSplitting`, `advancedChunks`) and adopting one
would destroy the one-config-builds-on-either-bundler property bought at
`9540ab3`, under cover of a version bump. It needs its own order. Measurements
are in `docs/plan/plan-a/wo/WO-96-dependency-modernisation.md`.

---

## 4. Gates

### Stage 0 — unblock (must all be true before anything else)
- [ ] GitHub Actions runners restored; a full CI run green on this branch
- [ ] Branch merged to `main`; `main` builds and CI green there
- [ ] Fee percentage and minimum configured
- [ ] Backup **and restore** drill executed and documented (R14)

### Stage 1 — supervised pilot
- [ ] Stage 0 complete
- [ ] **Shadow run:** one real client, one real quarter — parse their actual
      statements, compute our figures, reconcile against what was actually
      filed and actually recovered
- [ ] Discrepancies explained, not averaged away
- [ ] Nothing transmitted to a tax authority, supplier or customs authority
      until the shadow figures reconcile
- [ ] Production secrets: KEK provider off `local`; `INBOUND_EMAIL_SECRET` set
- [ ] PII deny-list + salt loaded

### Stage 2 — commercial (can take money)
- [ ] Billing credentials live (§2); plan ladder reconciled (§2a);
      seller-of-record VAT process owned (§2b)
- [ ] Audit item **R5** closed (self-serve billing collects real payment)

### Stage 3 — general / enterprise
- [ ] SSO/SCIM/SAML completed against a real IdP (§1)
- [ ] Data residency (§4), production key custody (§5), public API GA (§6)
- [ ] SOC 2 / ISO 27001 path started (§7)
- [ ] Load/perf harness (R15); onboarding wizard (R19)

---

## 5. Owner actions — ordered, none performable by engineering

1. **Restore GitHub Actions** (billing / spending limit). Everything else
   waits on this: without CI there is no independent verification of anything.
2. **Merge this branch to `main`** — or apply the `manualChunks` fix directly.
   `main` cannot build until one of these happens.
3. **Set the fee percentage and minimum.** No filing is possible without it.
4. **Decide R14** — infrastructure DR runbook, or app-owned backup/restore —
   then run the drill.
5. ~~**Land react/react-dom (#28/#27) together, or neither.**~~ **Done on this
   branch** (WO-96 Stage E, `0377b66`): react, react-dom and both `@types`
   packages moved to 19.2.8/19.2.x in one commit, with no application code
   change needed and 270 browser specs green. It still has to reach `main` via
   action 2.
6. **Provide the PII deny-list and salt** from the offline archive.
7. **Confirm §13's scope** — does freeze-until-partial-rejection apply to
   supplier overcharge claim-backs, or only to VAT claims?
8. **Fleet Fuel archive counsel review — due 30 September 2026.** A legal
   clock, not a build task.

---

## 6. Deployment

Runbooks: `docs/DEPLOYMENT.md`, `docs/DEPLOY-HOSTINGER.md`,
`docs/DEPLOY-TLS.md`. Neither a production deploy nor a rollback has been
rehearsed against this commit; treat the first deployment as a drill, on data
you can afford to lose, before any client data exists.

**Rollback position:** migrations are single-head with `alembic check` clean
and downgrade paths present, so a schema rollback is available. There is no
tested data restore (see 3.4) — which is why the drill in Stage 0 is a gate
and not a nice-to-have.
