# Deploy runbook — the 2026-08-15 release to the Hostinger VPS

> **✅ DEPLOYED 2026-08-23.** Production runs commit `16b91b6` at migration
> head `e9f1a3b5c7d9` — all four containers healthy (the first deploy since
> `15116e1`, ~2 months of work). The deploy surfaced and fixed, in order:
> a backup-verification bug in the deploy script (tail -1 vs pg_dump's
> trailer), the UUID-vs-VARCHAR migration defect in every post-CI-death
> migration (§2 below finally ran, on a real Postgres 16 scratch cluster),
> the worker missing INBOUND_EMAIL_SECRET in compose, and the frontend's
> misleading HTTP healthcheck under the HTTPS redirect. Remaining owner
> steps: §4.1 fee rate, §5 click-through, a reboot for the pending kernel
> update.

**What is being deployed:** the working branch at `HEAD`, which advances
production from `15116e1` by **308 commits** and **38 Alembic migrations** (figures refreshed 2026-08-23, evening; the clone is full again since 2026-08-23, so `git rev-list --count 15116e1..HEAD` is trustworthy — re-measure if you deploy a later commit).

This supersedes `DEPLOY-RUNBOOK-2026-08-12.md`. That runbook was written for
`ec93e4b` and **was never run** — production is still `15116e1`, so its 24
migrations are also still pending and are included in the 30 below. Read this
file, not that one.

**Why a runbook rather than the two-line update in `DEPLOY-HOSTINGER.md#operate`:**
that procedure assumes an incremental update. This applies 34 migrations in one
step to a database that has seen none of them. Migrations are the part of a
deploy that `git reset` does not undo.

---

## 0. Before you start — what has and has not been verified

**✅ CI IS ALIVE AGAIN — and green.** The repo went public on 2026-08-25
(Actions minutes free), and CI run #465 (workflow_dispatch, all 8 jobs)
passed at `46d3167` on the development branch — the first CI verdict since
the billing outage began 2026-08-12. Note the trigger design: push-CI runs
ONLY on main; branch work gets CI via a pull request or a manual dispatch
(run #465 was a dispatch). So merges to main are CI-gated again; branch
pushes stay covered by local certification + dispatch on demand.

Verified at this tree (executed, not recalled):

| Check | Result |
|---|---|
| **CI (GitHub runners)** | **run #502: ALL 8 JOBS SUCCESS — including `deploy`**, 2026-08-27 at `6fe0ac6` on `main` — the WO-T train (the claim payment leg + the refund-claim edge-set pin). Clean first attempt, no re-runs; full pytest 31:46, deploy 45s. **Production is deployed AT `6fe0ac6`** (no migration in this train — `c4e6a8b0d2f5` remains head). Prior: #501 all-8 at `652b70f` — the WO-S train (statement intake) plus the PII-scan fix. **Production is deployed AT `652b70f`** (no migration in this train — `c4e6a8b0d2f5` remains head). The train's first attempt, run #499 at `f0ae24f`, went RED on `pii-scan` and is worth keeping visible: the runbook row written to record WO-S's PII incident QUOTED the offending digits, so the write-up re-introduced the value it was about. `deploy` never ran on #499. Prior: #498 all-8 at `f6e229c` on `main` — the WO-R train (the perf harness, the growth gate, the recorded baseline) plus the WO-Q asyncio-mark cleanup. **First run carrying the R15 growth gate**, which passed on a real GitHub runner in 21 seconds: the ratio-not-milliseconds design was chosen precisely so a shared runner would not move the verdict, and this is the evidence that it does not. Clean first attempt, no re-runs. **Production is deployed AT `f6e229c`** (no migration in this train — `c4e6a8b0d2f5` remains head). Prior: #496 all-8 at `578283b` on `main` — the WO-Q train (the supplier-reliability design doc, the derived reliability board, migration `c4e6a8b0d2f5`, and the entity-picker determinism fix). Clean first attempt, no anomalies, no re-runs; the `backend` job's full pytest took 35 minutes and the deploy landed in 54 seconds. **Production is deployed AT `578283b`** — migration `c4e6a8b0d2f5` (`vat_reliability_thresholds`, RLS in-migration) went live with it. Prior: #491: ALL 8 JOBS SUCCESS at `35f656c` on `main` — the WO-P + docs-truth-up + diagram-matrix train, first run carrying the `test_erd_truth` diagram-drift gate. Two anomalies this train, both handled: GitHub silently created NO run for the `c6e1e74` push (covered by a manual dispatch, itself superseded); #490 failed on one C420 lint finding in the WO-P test file (the one file the local ruff pass missed — fixed in `35f656c`). Prior: #487 all-8 (WO-O, first run with `check-labels` + `check-bundle`); #485 all-8 (WO-N; deploy attempt 1 timed out on an unresponsive VPS window, re-run green in 42s); #483 all-8 (WO-M); #479 all-8 (WO-L, the first CI auto-deploy); #477 7/8 (deploy unreachable, fixed with `ssh -4`) |
| Backend suite | **2866 passed, 12 skipped, 0 failed (49:42)** on 2026-08-27 at the WO-U tree. Net +1 despite three new tests, because WO-U converted the `vat_fee_rates` tenancy EXEMPTION into a real probe — an exemption is a row in a list, a probe is a test. Prior: 2865 passed, 12 skipped, 0 failed (48:23) at the WO-T tree. Prior: 2845 passed, 12 skipped, 1 failed → fixed (50:01) at the WO-S tree. The one failure is worth recording rather than smoothing over: the **PII quarantine gate caught a structural VAT-id shape in WO-S's own e2e mock** — a Belgian-shaped number invented by hand for the learned-entity assertion, which had no business looking allocatable. Resolved the sanctioned way: the value moved to the repo's all-nines convention (structurally impossible for Belgium) and gained a `scripts/pii_allowlist.json` entry with a justification and a named verifier; the job was never disabled. **It then caught a second time, and the second one is the instructive one:** the first draft of THIS runbook row quoted the offending digits while explaining the incident, so the write-up re-introduced the value the write-up was about. An incident report about a quarantined value must describe it, never reproduce it — the scan cannot tell prose from a fixture, and it should not have to. `test_pii_scan.py`, `test_docs_truth.py`, `test_erd_truth.py`, `test_authz_coverage.py` and `test_rls.py` re-run green afterwards — the fix touches one `.ts` file and one allowlist, which no other backend test reads. Prior: 2830 passed, 12 skipped, 0 failed (43:41) on 2026-08-27 at the WO-R tree (SQLite harness, matching CI). The one added skip is the measured half of the perf gate, which needs a Postgres URL and is supplied one by CI's `postgres` job; its four structural siblings run everywhere. Prior: 2808 passed, 11 skipped, 0 failed (42:44) on 2026-08-26 at the WO-P tree |
| Performance (**NEW**, WO-R) | **Measured, not assumed.** `scripts/perf_harness.py` at 400 / 5,000 / 20,000 rows per fact table on a migrated Postgres 16.13: every read is sub-linear in the data (50× the rows costs at most 25× the time). The `expected_rebate` whole-history walk that `RELEASE-READINESS.md` §3.5 flagged grew **17× across 50×** — the feared quadratic is not there. Fastest-growing read is the analytics `explore` group-by at 24.9×. The CI gate is a growth RATIO (`tests/test_perf_shape.py`, ~17s in the `postgres` job), proven to bite by a seeded `O(n²)` caught at 11.35× against a ceiling of 8.0. **Concurrency is NOT measured** — every figure is one sequential caller. Baseline: `docs/perf/BASELINE-2026-08-27.md` |
| Prior backend suite | **2808 passed, 11 skipped, 0 failed (42:44)** on 2026-08-26 at the WO-P tree (SQLite harness, matching CI — an earlier run wrongly carried a DATABASE_URL override and produced 9 dialect-mismatch artifacts, all confirmed harness-only). +2 gate tests since (`test_erd_truth`), CI #491's full pytest green at `35f656c` over all 2821. **Production deployed AT `35f656c` by CI #491's deploy job** — migration `e8f0a2b4c6d8` (onboarding stamp) went live with it |
| `ruff check` / `ruff format --check` | clean (652 files) |
| `mypy app` | clean, 373 files |
| Alembic | single head `c4e6a8b0d2f5` (`vat_reliability_thresholds`, WO-Q) — **production is CURRENT** (CI #496 deployed `578283b`; fresh-Postgres upgrade + `alembic check` verified locally) |
| Browser suite | **401 e2e + 13 visual = 414 passed** on 2026-08-27 at the WO-U tree (5 new reachability specs), fully clean; `tsc --noEmit` clean, `check-e2e` 41 spec files clean, `check-labels` 134 associated, `check-bundle` 416.3 kB raw / 122.4 kB gzip against a 460/135 budget. Prior: **396 e2e + 13 visual = 409 passed** at the WO-T tree (4 new claim-payment specs), fully clean. One of the new specs flaked once under full-suite parallel load while passing 12/12 in isolation, and chasing it found a REAL duplication rather than a timing bug: the page had grown a second "Refund received" label because the totals card has carried that figure since WO-78 and had simply never had a value to show. The duplicate row was removed and the spec now asserts the VALUE (`€312.40`) instead of the label. Prior: **392 e2e + 13 visual = 405 passed** at the WO-S tree; `tsc --noEmit` clean, `check-e2e` 39 spec files clean, `check-labels` 132 associated, `check-bundle` 416.0 kB raw / 122.3 kB gzip against a 460/135 budget. Prior: **401 passed across 38 spec files** on 2026-08-27 at the WO-Q tree, carried forward unchanged to the WO-R tree (which touched no frontend file) and independently re-run green by CI #496 and #498 | (4 new onboarding specs), fully clean; `tsc --noEmit` clean. First load 415 kB raw / 122 kB gzip held by `check-bundle`; `check-labels` green |
| Prior certified runs | 2804 / 393 browser 2026-08-26 at WO-O `7b65a21` (+ CI #487 all-8-green, first `check-bundle` run); 2804 / 393 browser 2026-08-26 at WO-N `32b42bf` (+ CI #485 all-8-green; round 1 carried a single vat-claims registry-mock load flake, 3/3 green isolated, round 2 clean); 2804 / 391 browser 2026-08-26 at WO-M `03e586e` (+ CI #483 all-8-green); 2799 / 390 browser 2026-08-26 at WO-L `23c9b46` (+ CI #479 all-8-green, the first CI auto-deploy); 2791 / 386 browser 2026-08-26 at WO-K `176bbfa` (+ CI #477 code-green); 2782 / 382 browser 2026-08-26 at WO-G2 `edc6c22` (+ CI #473 all-green); 2776 / 380 browser 2026-08-25 at WO-J `7423dca` (+ CI #471 all-green); 2766 / 376 browser 2026-08-25 at WO-I `5d121ee` (+ CI #470 all-green); 2761 / 372 browser 2026-08-25 at the WO-H tree; 2754 / 369 browser 2026-08-25 at `46d3167` (+ CI #465 all-green); 2748 / 366 browser 2026-08-24 at `31e0e0b`; 2745 / 363 browser 2026-08-24 at `3d0f3cb` (one vat-claims flake re-ran clean 45/45; the WO-E round caught a real Customer-card crash, fixed before certifying); 2737 passed 2026-08-23 at `df9642a`; 2729 passed 2026-08-23 at the deployed tree `ee37037`; 2720 at `60e1faf`; 2714 at `2c5e93a`; 2705 2026-08-20 at `d2ba5b0`; 2694 2026-08-16 at `56bcab7` (single environmental failure — tesseract reinstalled, OCR 2/2) |

The browser gap the first draft of this runbook carried is CLOSED: the suite has
been re-run since the consent dialog was reordered, and since the archive screen
landed. It is no longer a pre-deploy chore.

**Known gaps in that evidence, stated plainly:**

- ~~The Postgres-only gates have not been run on this tree.~~ **CLOSED
  2026-08-23, the expensive way:** the first deploy attempt failed exactly
  where this gap predicted — every migration authored after CI died
  (2026-08-12) used VARCHAR(36) for GUID columns and an uncast RLS
  predicate, which only real Postgres refuses. §2 was then executed for
  real on a scratch Postgres 16 under a NOSUPERUSER role: all 107
  migrations clean, `relforcerowsecurity = t` on all 11 new tenant tables,
  `alembic check` clean (after registering seven model modules autogen had
  gone blind to), RLS + concurrency suites 8/8.
- **Nothing here has been validated against real data.** Every test is
  self-authored over fixtures. Four money defects once passed 2,445 tests; a
  review on 14/15 August found two P0s that 2,633 passing tests did not. The
  suite catches a wrong shape, not a wrong figure.

### 0.1 Re-state the suite figure

The figure in the table above was produced at THIS tree on 2026-08-23 and is not
copied from an older document. If you deploy from a later commit, re-run it and
replace the row rather than trusting this one:

```bash
cd backend && python -m pytest tests/ -q -p no:randomly | tail -2
```

Worth knowing what that run cost: the previous full run came back RED — three
failures, two of them caused by a change that had already passed its own
targeted tests. The targeted run is not a substitute for this one.

---

## 1. Pre-flight — back up first, and prove the backup exists

A backup you have not restored is a hypothesis. The restore drill
(`scripts/restore_drill.sh`, evidence in `RELEASE-READINESS.md` §3.4) covers the
database. **It does NOT cover the document-bytes volume** — that gap is open and
this deploy does not close it.

```bash
ssh root@YOUR_VPS_IP
cd /root/Bid_it

# 1. Database dump, timestamped.
docker compose -f docker-compose.hostinger.yml exec -T db \
  pg_dump -U invoiceiq invoiceiq | gzip > ~/pre-deploy-$(date +%F-%H%M).sql.gz

# 2. Prove it is not empty and not truncated.
gunzip -c ~/pre-deploy-*.sql.gz | tail -5     # expect "PostgreSQL database dump complete"
ls -lh ~/pre-deploy-*.sql.gz                  # expect a plausible size, not a few KB

# 3. The document store. This release ARCHIVES invoice PDFs rather than
#    deleting them, so these bytes now outlive the rows that referenced them.
#    The bytes live on the `storagedata` volume, mounted at /app/var/storage
#    (STORAGE_LOCAL_PATH) — an earlier draft of this runbook said
#    /data/documents, which does not exist and produced a 45-byte "backup".
docker compose -f docker-compose.hostinger.yml exec -T backend \
  tar czf - /app/var/storage > ~/pre-deploy-docs-$(date +%F-%H%M).tar.gz
ls -lh ~/pre-deploy-docs-*.tar.gz
# If the backend container is not running, back the volume up directly:
#   docker run --rm -v invoiceiq_storagedata:/data -v "$HOME":/out alpine \
#     tar czf /out/pre-deploy-docs-$(date +%F-%H%M).tar.gz -C /data .

# 4. Record the commit you are rolling back TO.
git rev-parse HEAD > ~/pre-deploy-commit.txt && cat ~/pre-deploy-commit.txt
```

**Do not proceed until all four have produced output you have looked at.**

---

## 2. The Postgres gate — required for this release

This release adds NINE new tenant tables carrying row-level-security policies:
`archived_invoices`, `invoice_project_splits`, `project_cost_entries`,
`project_documents`, `project_offers`, `invoicing_plan_rows`, `org_templates`,
`project_assignments`, `calendar_feed_tokens`
(plus org-less `platform_templates`, deliberately NOT tenant-scoped — operator
master documents, the `ecb_rates` pattern). On SQLite the coverage test proves
only that the model registry and the migration agree; it cannot prove the policy
itself. Run this against a real Postgres 16 under a **NOSUPERUSER** role (RLS is
bypassed by superusers, so a superuser run proves nothing):

```bash
# On a scratch cluster, NOT production.
createuser --no-superuser appuser && createdb -O appuser invoiceiq_gate
DATABASE_URL=postgresql+asyncpg://appuser@localhost/invoiceiq_gate \
  alembic upgrade head && alembic check          # expect no drift, head d8e0f2a4b6c8
DATABASE_URL=... python -m pytest tests/test_rls.py \
  tests/test_numbering_concurrency.py tests/test_transport_lock_concurrency.py \
  tests/test_usage_counter_concurrency.py -q
```

Then confirm the new table is actually protected:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
 WHERE relname IN ('archived_invoices','invoice_project_splits',
   'project_cost_entries','project_documents','project_offers',
   'invoicing_plan_rows','org_templates','project_assignments',
   'calendar_feed_tokens');
-- expect: every row  t | t
SELECT c.relname, polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
  WHERE c.relname IN ('archived_invoices','invoice_project_splits',
   'project_cost_entries','project_documents','project_offers',
   'invoicing_plan_rows','org_templates','project_assignments',
   'calendar_feed_tokens');
-- expect: tenant_isolation on each
```

`relforcerowsecurity` must be `t`. Without FORCE the policy does not apply to the
table owner, and the application connection is frequently the owner — the layer
would be present in the schema and absent in practice.

---

## 3. Deploy

> Since 2026-08-20 the §1 backups + this section + the health wait are one
> command: `./scripts/vps-deploy.sh claude/bidit-invoice-data-analytics`.
> The manual steps below remain the reference for what it does.

```bash
cd /root/Bid_it
git fetch origin && git checkout claude/bidit-invoice-data-analytics && git pull
docker compose -f docker-compose.hostinger.yml up -d --build   # migrations run on boot
docker compose -f docker-compose.hostinger.yml ps
docker compose -f docker-compose.hostinger.yml logs -f backend  # watch the migrations
```

Expect 38 migrations to apply. If any fails the container will not become
healthy — **stop and go to §6 rather than retrying**.

Known first-time snag: `docker-compose.hostinger.yml` now REQUIRES
`INBOUND_EMAIL_SECRET` in `.env` (the email-intake webhook secret). Every
compose command — including the §1 backup `exec`s — fails on interpolation
until it is set:

```bash
echo "INBOUND_EMAIL_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
```

---

## 4. Post-deploy configuration — two steps, both required

### 4.1 Set the VAT contingency fee rate

By design nothing can be filed until this is typed (`fee_rate_not_configured`).
Decided 2026-08-15: **15% of recovered VAT, €50 minimum.**

```bash
curl -X PUT https://YOUR_HOST/api/v1/transport/fee-rates \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"scope":"standard","pct":"15.0","minimum_eur":"50.00"}'
```

Verify with `GET /api/v1/transport/fee-rates` before assuming it took.

### 4.2 Nothing else is required

The deletion chain, the archive and the new plan ladder are all live on deploy
with no configuration. The archive is **empty** until an invoice completes its
30 days in the bin, so expect no rows there for a month.

---

## 5. Verify — behaviour, not just a green container

```bash
curl -fsS https://YOUR_HOST/health
curl -fsS -o /dev/null -w '%{http_code}\n' https://YOUR_HOST/api/v1/invoices   # expect 401
```

Then, signed in, check the things this release actually changed:

1. **Delete a draft invoice.** Expect a toast naming "Deleted invoices" and a
   30-day window. It must NOT vanish silently.
2. **Open `/invoices/trash`.** The deleted invoice is there with days remaining.
3. **Restore it.** It returns to the list.
4. **Delete an approved or paid invoice.** Expect a dialog whose specific
   consequences appear ABOVE the general warning; confirming it should succeed
   and the audit event should carry the warning text verbatim.
5. **Open the Archive** (`/invoices/archive`, linked from Deleted invoices). It
   will be EMPTY and should say so — no invoice can have completed its 30 days
   yet. What is being checked is that the screen loads for the owner, states the
   retention period, and is refused for a role below administrator.
6. **Check the plan matrix** (`/api/v1/access/matrix`): six plans plus Practice,
   Free reported as `paid: false`, Starter €39 with a 150 cap.
7. **Open a project** (Workspace → Cost objects → a project) — the P&L card
   renders and states "live figures".
8. **Open Templates** — the three demo documents are there (they seed on first
   read), each saying it is an example, not legal advice. Adjust one, save it,
   and generate a PDF from a project's Contract card.
9. **Open the Schedule** (Overview → Schedule) — assign yourself to a project
   for tomorrow; the assignment appears in the week grid; a second assignment
   overlapping the first saves WITH an amber warning naming the collision.

---

## 6. Rollback

Migrations are the part that `git reset` does not undo. Rolling back code alone
against a migrated database will fail in ways that look like application bugs.

```bash
cd /root/Bid_it
git checkout $(cat ~/pre-deploy-commit.txt)
docker compose -f docker-compose.hostinger.yml down
gunzip -c ~/pre-deploy-<stamp>.sql.gz | \
  docker compose -f docker-compose.hostinger.yml exec -T db psql -U invoiceiq invoiceiq
docker compose -f docker-compose.hostinger.yml up -d --build
```

Restore the document tarball only if the deploy ran long enough for new uploads
to have landed — restoring it will discard anything uploaded since the backup.

---

## 7. What this release contains

Grouped by what a reader would want to find, not by commit order.

**Deletion, the recycle bin and the platform archive (2026-08-14→15).** Deleting
an invoice used to destroy the row. It now goes to a bin for 30 days, then to a
sealed archive readable by the company owner for three years. Consent to delete
anything past draft is server-enforced, versioned, and recorded verbatim.
Design: `docs/design/deletion-and-archive.md`, `docs/design/platform-archive.md`.

**The plan ladder (§2a, resolved 2026-08-15).** Free €0 · Starter €39 · Team €99
· Business €249 · Enterprise custom · Practice. **Team is no longer unlimited**
— it carries a 750/month cap. Any existing tenant on `pro` is affected on
deploy; there are none in production today, which is why this was safe to do
before the pilot rather than after.

**The project lifecycle (phases 1–5a, 2026-08-16→20).** Per-project P&L
(revenue from issued invoices linked by project, net of credit notes; costs from
allocated supplier invoices — line-level, cent-exact % splits, or whole-invoice
— plus expense links and manual entries), close-freeze (the P&L snapshot
commits in the same transaction as the close; late documents surface as
labelled adjustments), versioned offers/estimates with client-configurable
numbering, invoicing plans tracked against actually-issued, and dynamic
document templates (operator masters a workspace adjusts into frozen own
versions; `{{token}}` render leaves unknown tokens visible; generate → PDF into
the project's documents). Design: `docs/design/project-profitability.md`.

**Capture-failure worklist, inbound channel health, vendor resolution** — the
H-1/H-2/H-3 work.

**Four money defects and the merge that repaired `main`** — from 2026-08-12,
never deployed, and the reason the pre-flight above is not optional.

---

## 8. After the deploy

- [ ] Set the fee rate (§4.1) and verify it read back.
- [ ] Get one real supplier statement, redacted, through the system. This is the
      highest-value open item on the board and no amount of testing substitutes
      for it.
- [x] ~~Chase the GitHub Actions billing condition.~~ RESOLVED 2026-08-25:
      repo public → Actions free → CI run #465 green. Next: set the three
      deploy secrets (`DEPLOY_SSH_KEY`/`DEPLOY_HOST`/`DEPLOY_USER`) +
      `DEPLOY_ENABLED=true` for merge-to-main auto-deploys (§ deploy job).
