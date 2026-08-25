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
| **CI (GitHub runners)** | run #468 at WO-H `56f80a3`: 6/8 jobs green incl. the full SQLite suite (31m) and the real-Postgres RLS job; its two failures were both fixed before certifying — the nav regression (`fb61138`) and a format drift in the docs tripwire CI caught that local gates missed (this tree). Prior: **run #465 SUCCESS, all 8 jobs** at `46d3167` |
| Backend suite | **2761 passed, 11 skipped, 0 failed (33:36)** on 2026-08-25 at the WO-H tree — adds CRM light (customer notes, lifecycle, timeline, offer pipeline + stage events) on top of WO-D/E/F/G. Production is four additive migrations behind (`f0a2b4c6d8e0`, `a1b3c5d7e9f1`, `b2c4d6e8f0a2`, `c3d5e7f9a1b3`, all proven on scratch Postgres 16) |
| `ruff check` / `ruff format --check` | clean |
| `mypy app` | clean, 360 files |
| Alembic | single head `c3d5e7f9a1b3` (CRM light) — production is at `e9f1a3b5c7d9`, four additive migrations behind |
| Browser suite | **372 passed (3.9m)** on 2026-08-25 at the WO-H tree, no flakes. Round 1 caught a real nav regression (ungated Pipeline entry broke the empty-Receivables-disappears behavior), fixed in `fb61138` before certifying |
| Prior certified runs | 2754 / 369 browser 2026-08-25 at `46d3167` (+ CI #465 all-green); 2748 / 366 browser 2026-08-24 at `31e0e0b`; 2745 / 363 browser 2026-08-24 at `3d0f3cb` (one vat-claims flake re-ran clean 45/45; the WO-E round caught a real Customer-card crash, fixed before certifying); 2737 passed 2026-08-23 at `df9642a`; 2729 passed 2026-08-23 at the deployed tree `ee37037`; 2720 at `60e1faf`; 2714 at `2c5e93a`; 2705 2026-08-20 at `d2ba5b0`; 2694 2026-08-16 at `56bcab7` (single environmental failure — tesseract reinstalled, OCR 2/2) |

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
