# Deploy runbook — the 2026-08-12 release to the Hostinger VPS

**What is being deployed:** `main` at `ec93e4b`, which advanced from `15116e1`
by **223 commits** — the whole transport vertical, the VAT claim engine, the
invoice PDF redesign, and WO-96's dependency modernisation.

**Why this needs a runbook rather than the two-line update in
`DEPLOY-HOSTINGER.md#operate`:** that procedure assumes an incremental update.
This one applies **22 new Alembic migrations** in a single step, on a database
that has never seen any of them. Migrations are the part of a deploy that a
`git reset` does not undo.

---

## 0. Before you start — read this

CI has not verified this commit. Every GitHub Actions run on this repository
currently fails within about a second with no logs, on **every** branch
including Dependabot's own — the runners are not starting, which is an
account/billing condition, not a code fault. So the checks the deploy job would
normally gate on have not run.

What HAS been verified, locally, at this exact tree:

| Check | Result |
|---|---|
| Backend suite | **2445 passed / 10 skipped** |
| Frontend production build | exit 0 |
| Frontend typecheck (`tsc --noEmit`) | exit 0 |
| Backend targeted subset, post-merge | 253 passed |
| API smoke (health, authz 401, PDF render) | pass |

What has NOT been verified: anything on real data, anything under load, and the
Postgres path beyond the test harness. Production runs Postgres 16; the local
suite runs SQLite except where a scratch cluster is used.

---

## 1. Pre-flight — back up first, and prove the backup exists

**Do not skip this.** 22 migrations run automatically when the backend
container starts. If one fails halfway, the rollback in §4 needs this dump.

```bash
ssh root@srv1760867.hstgr.cloud
cd /root/Bid_it

# database → gzipped SQL dump
docker compose -f docker-compose.hostinger.yml exec -T db \
  pg_dump -U invoiceiq invoiceiq | gzip > backup-db-$(date +%F).sql.gz

# document bytes (uploaded originals, receipts, logos)
docker run --rm -v invoiceiq_storagedata:/data -v "$PWD":/out alpine \
  tar czf /out/backup-docs-$(date +%F).tar.gz -C /data .

# PROVE they are non-empty before going further
ls -lh backup-db-$(date +%F).sql.gz backup-docs-$(date +%F).tar.gz
gzip -t backup-db-$(date +%F).sql.gz && echo "db dump integrity OK"
```

Record the commit you are rolling back TO:

```bash
git rev-parse HEAD    # expect 15116e1… — write it down
```

Copy both archives off the VPS. A backup that only exists on the box being
changed is not a backup.

---

## 2. Deploy

```bash
cd /root/Bid_it
git fetch origin main
git reset --hard origin/main          # → ec93e4b
docker compose -f docker-compose.hostinger.yml up -d --build
docker image prune -f
```

The backend container runs `alembic upgrade head` before serving. Watch it:

```bash
docker compose -f docker-compose.hostinger.yml logs -f backend
```

You are waiting for the migrations to complete and then `Application startup
complete`. If a migration raises, **stop** and go to §4.

---

## 3. Verify — do not declare success on a 200 alone

```bash
# the app is up
curl -fsS https://srv1760867.hstgr.cloud/health && echo

# the schema really reached head
docker compose -f docker-compose.hostinger.yml exec -T backend alembic current
#   expect: d4c7b1e93f27 (head)

# stored documents still hash to what the database says they do
#   log in as an admin, then:
#   POST /api/v1/integrity/documents/verify
```

Then open the UI and confirm, by eye:

- an existing invoice still opens and its figures are unchanged;
- downloading an invoice PDF gives the new layout with `factur-x.xml` attached;
- the VAT and recovery screens load (they are new to production).

---

## 4. Rollback

Application code rolls back cleanly. **The database does not** — the 22
migrations will already have applied.

```bash
cd /root/Bid_it
git reset --hard 15116e1
docker compose -f docker-compose.hostinger.yml up -d --build
```

If the schema must also go back, restore the dump from §1:

```bash
gunzip -c backup-db-YYYY-MM-DD.sql.gz | \
  docker compose -f docker-compose.hostinger.yml exec -T db psql -U invoiceiq invoiceiq
```

Restoring the dump discards everything written since the backup. Take the box
out of service first if that matters.

---

## 5. Known-open after this deploy

These ship in this release and are not fixed by it:

- **No claim can be filed until a contingency fee rate is configured.** The
  gate is deliberate; the value is a business decision that has not been made.
- **Late-payment interest can be billed twice.** Generating a penalty invoice
  a second time re-bills from the original due date — reproduced at
  €73.32 → €146.64. Do not run penalty invoicing on this release.
- **`penalty_summary` sums across currencies**, labelling the total with
  whichever row the database returned last.
- **camt.053 import mis-reads four cases**: reversals (`RvslInd`) are booked as
  real credits, `Ccy` is dropped, `PDNG` entries are treated as settled, and
  batched `TxDtls` collapse into one line. On a five-entry test statement this
  overstated cash by €2,277. Do not rely on bank-statement reconciliation on
  this release.
- **The sign-in form's labels are not programmatically associated** with their
  inputs (no `htmlFor`/`id`), so a screen reader announces unlabelled fields.
- **The SPA's first-load payload roughly doubled under Vite 8** (~329 kB →
  ~773 kB critical path). Correctness is unaffected.

Each is tracked in `TODO.md`; the first four involve money and should be
closed before the platform bills or reconciles for a real customer.
