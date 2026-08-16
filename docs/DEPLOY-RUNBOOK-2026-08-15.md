# Deploy runbook — the 2026-08-15 release to the Hostinger VPS

**What is being deployed:** the working branch at `HEAD`, which advances
production from `15116e1` by **262 commits** and **29 Alembic migrations**.

This supersedes `DEPLOY-RUNBOOK-2026-08-12.md`. That runbook was written for
`ec93e4b` and **was never run** — production is still `15116e1`, so its 24
migrations are also still pending and are included in the 29 below. Read this
file, not that one.

**Why a runbook rather than the two-line update in `DEPLOY-HOSTINGER.md#operate`:**
that procedure assumes an incremental update. This applies 29 migrations in one
step to a database that has seen none of them. Migrations are the part of a
deploy that `git reset` does not undo.

---

## 0. Before you start — what has and has not been verified

**CI has not verified this commit, or any commit since 2026-08-12.** Every
GitHub Actions run fails within about a second with no logs, on every branch —
the runners are not starting, which is an account/billing condition rather than a
code fault. Twelve pushes have triggered nothing. So the checks the deploy job
would normally gate on have not run.

Verified LOCALLY at this tree (executed, not recalled):

| Check | Result |
|---|---|
| Backend suite | see §0.1 — re-state the figure from the run that gated this deploy |
| `ruff check` / `ruff format --check` | clean |
| `mypy app` | clean, 339 files |
| Alembic | single head `a4d7e0c16b93` |
| Browser suite | 293 passed at `1bbb154` — **stale**, see below |

**Known gaps in that evidence, stated plainly:**

- **The browser suite has not been re-run since the consent dialog changed.** It
  passed at `1bbb154`; the delete-confirmation dialog was reordered afterwards.
  Re-run `npm run test:e2e` before deploying, or accept that one dialog is
  unverified in a browser.
- **The Postgres-only gates have not been run on this tree.** Production is
  Postgres 16; the local suite is SQLite except where a scratch cluster is used.
  This release adds a new tenant table (`archived_invoices`) with an RLS policy,
  so `tests/test_rls.py` under a NOSUPERUSER role is **not optional** — see §2.
- **Nothing here has been validated against real data.** Every test is
  self-authored over fixtures. Four money defects once passed 2,445 tests; a
  review on 14/15 August found two P0s that 2,633 passing tests did not. The
  suite catches a wrong shape, not a wrong figure.

### 0.1 Re-state the suite figure

Do not copy a number from an older document. Run it and paste what you get:

```bash
cd backend && python -m pytest tests/ -q -p no:randomly | tail -2
```

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
docker compose -f docker-compose.hostinger.yml exec -T backend \
  tar czf - /data/documents > ~/pre-deploy-docs-$(date +%F-%H%M).tar.gz
ls -lh ~/pre-deploy-docs-*.tar.gz

# 4. Record the commit you are rolling back TO.
git rev-parse HEAD > ~/pre-deploy-commit.txt && cat ~/pre-deploy-commit.txt
```

**Do not proceed until all four have produced output you have looked at.**

---

## 2. The Postgres gate — required for this release

`archived_invoices` is a new tenant table carrying a row-level-security policy.
On SQLite the coverage test proves only that the model registry and the migration
agree; it cannot prove the policy itself. Run this against a real Postgres 16
under a **NOSUPERUSER** role (RLS is bypassed by superusers, so a superuser run
proves nothing):

```bash
# On a scratch cluster, NOT production.
createuser --no-superuser appuser && createdb -O appuser invoiceiq_gate
DATABASE_URL=postgresql+asyncpg://appuser@localhost/invoiceiq_gate \
  alembic upgrade head && alembic check          # expect no drift
DATABASE_URL=... python -m pytest tests/test_rls.py \
  tests/test_numbering_concurrency.py tests/test_transport_lock_concurrency.py -q
```

Then confirm the new table is actually protected:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity
  FROM pg_class WHERE relname = 'archived_invoices';
-- expect: archived_invoices | t | t
SELECT polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
  WHERE c.relname = 'archived_invoices';
-- expect: tenant_isolation
```

`relforcerowsecurity` must be `t`. Without FORCE the policy does not apply to the
table owner, and the application connection is frequently the owner — the layer
would be present in the schema and absent in practice.

---

## 3. Deploy

```bash
cd /root/Bid_it
git fetch origin && git checkout claude/bidit-invoice-data-analytics && git pull
docker compose -f docker-compose.hostinger.yml up -d --build   # migrations run on boot
docker compose -f docker-compose.hostinger.yml ps
docker compose -f docker-compose.hostinger.yml logs -f backend  # watch the migrations
```

Expect 29 migrations to apply. If any fails the container will not become
healthy — **stop and go to §6 rather than retrying**.

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
5. **Check the plan matrix** (`/api/v1/access/matrix`): six plans plus Practice,
   Free reported as `paid: false`, Starter €39 with a 150 cap.

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

**Capture-failure worklist, inbound channel health, vendor resolution** — the
H-1/H-2/H-3 work.

**Four money defects and the merge that repaired `main`** — from 2026-08-12,
never deployed, and the reason the pre-flight above is not optional.

---

## 8. After the deploy

- [ ] Set the fee rate (§4.1) and verify it read back.
- [ ] Re-run the browser suite if you did not before deploying.
- [ ] Get one real supplier statement, redacted, through the system. This is the
      highest-value open item on the board and no amount of testing substitutes
      for it.
- [ ] Chase the GitHub Actions billing condition. Until it is fixed every
      release rests on one person's local runs.
