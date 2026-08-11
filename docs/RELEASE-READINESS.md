# Release readiness — go/no-go gate

**Commit:** `97fc5e3` · **Branch:** `claude/bidit-invoice-data-analytics` · **Assessed:** 2026-08-09

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

## 2. Verified at `97fc5e3`

Every line below was executed against this commit. Where a claim could not be
executed, it is in §3 instead.

| Check | Result | How |
|---|---|---|
| Backend suite | **2403 passed, 10 skipped** — independently re-run against this commit and confirmed, matching WO-95's reported figure exactly | `pytest -q`, full run, 52m |
| Browser suite | **270 passed** | `npm run test:e2e`, the CI list |
| Lint | clean | `ruff check app tests` |
| Format | 563 files clean | `ruff format --check` |
| Types | no issues, 328 files | `mypy app` |
| Migrations | single head `d4c7b1e93f27` | `alembic heads` |
| Migration drift | none | `alembic check` on Postgres 16 |
| Postgres gates | **6 passed** | RLS + numbering + lock concurrency, NOSUPERUSER role |
| PII quarantine | clean | `scripts/pii_scan.py --tree` |

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

**3.3 The branch has never merged.** 95 work orders on one pull request, and
`main` cannot currently build (it needs the `manualChunks` fix that lives on
this branch). Long-lived divergence is itself a release risk.

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
5. **Land react/react-dom (#28/#27) together, or neither.** They are a matched
   pair; one alone breaks `main` exactly as vite did.
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
