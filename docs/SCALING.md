# Scaling the Fleet Fuel & VAT Refund System

When the databases and the PDF/ZIP store grow large and the single box gets slow, you
grow **by configuration, not by rewrite**. The architecture was built for this: the
database layer is abstracted (`db.py`), file storage is a pluggable backend
(`document_vault.py`), cross-process coordination uses time-limited leases
(`process_lock.py`), and the intake queue is a durable, lease-based, reclaimable
worker queue (`waiting_room.py`).

This document is the ladder — each rung is more capacity for more operational effort —
plus an **honest list of what still needs doing** for a fully validated multi-server
deployment.

> Just sizing one box for a normal install (e.g. ~100 invoices/day)? See
> **`DEPLOYMENT_SIZING.md`** — you almost certainly stay at Stage 0 below.

---

## What gets slow first (bottleneck order)

1. **The single SQLite writer.** WAL (`db_tuning.py`) gives unlimited concurrent
   *readers* + *one* writer per DB. Heavy ingest/write volume hits this first.
2. **Local disk for `documents/` and `backups/`.** Grows unbounded; competes for the
   same I/O as everything else.
3. **CPU-heavy extraction** (PDF/ZIP → draft) blocking web workers.
4. **Per-request connections** at high concurrency.

Each has an escape hatch below.

---

## The ladder

### Stage 0 — one box, tuned (default)
`python serve.py` (waitress) + WAL + the in-process background intake worker.
Vertical scaling (more CPU/RAM/SSD) takes this a long way. Nothing to change.

### Stage 1 — move the database tier to Postgres  ⟵ highest leverage
Removes the one-writer ceiling: concurrent writers, real pooling, a DB server on its
own machine (or managed RDS / Azure DB for PostgreSQL).

```
pip install "psycopg[binary]"
createdb fuelvat                      # and schemas: customers suppliers fuel_history security
export DB_ENGINE=postgres DB_DSN=postgresql://user:pass@db-host/fuelvat
python db.py --migrate                # copy every table from the .db files into Postgres
# restart the app
```

DB separation is preserved — each logical DB becomes a Postgres **schema**, so the
legal/financial isolation survives. `db.connect()` wraps the Postgres connection in a
paramstyle shim (`_PgShim`) that translates the modules' SQLite `?` placeholders to
psycopg's `%s` automatically. **See "Remaining blockers" before flipping in production.**

### Stage 2 — move file storage off the app server
`document_vault.py` already supports a pluggable, backend-agnostic store (local disk +
SharePoint/Graph today; S3/Azure Blob is a one-class addition following the same
pattern). Locators are prefix-tagged (`sp://…` vs local) so historical documents keep
resolving after you switch the default.

```
export DOC_BACKEND=sharepoint          # + the SP_* / Graph credentials
python -c "import document_vault as v; v.migrate_local_to_sharepoint()"   # move history once
```

Now PDF/ZIP storage no longer grows the app server's disk, and every node reaches the
same documents.

### Stage 3 — split web nodes from a worker fleet ("delegate tasks per server")
Run the **same codebase** in different roles, set per node with `FFS_ROLE`:

| Role | What it runs | How to launch |
|------|--------------|---------------|
| `web` | HTTP only; does **not** drain the queue | `FFS_ROLE=web python serve.py` (behind nginx) |
| `worker` | background extraction only | `python waiting_room.py --work` |
| `all` (default) | web **and** the in-process worker | `python serve.py` |

The intake queue (`waiting_room.py`) is **lease-based**: a worker claims a job for a
TTL; if it dies, the lease expires and another worker reclaims the job. That is exactly
what lets **N worker servers drain one shared queue** safely. The backup scheduler
self-elects a single **leader** across all processes (`process_lock`), so it is safe to
leave running everywhere — only one node ever snapshots.

### Stage 4 — load-balance the web tier
Put nginx / a cloud LB in front of the `web` nodes. **No sticky sessions needed**:
sessions are signed cookies, so any node validates any node's cookie — **provided every
web node shares one signing key**:

```
export FFS_SECRET_KEY=<the-same-32+-byte-secret-on-every-web-node>
```

(When unset, each box generates its own `.secret_key` file — fine for one server, wrong
for a fleet.) Add PgBouncer in front of Postgres for connection pooling.

---

## Target multi-server topology

```
                       ┌─────────────┐
        clients ──────▶│  nginx / LB │
                       └─────┬───────┘
              ┌──────────────┼──────────────┐
         ┌────▼────┐   ┌─────▼───┐    ┌──────▼──┐      FFS_ROLE=web
         │ web #1  │   │ web #2  │ …  │ web #N  │      FFS_SECRET_KEY=<shared>
         └────┬────┘   └────┬────┘    └────┬────┘
              └──────────────┼──────────────┘
                 shared queue │ (intake)         ┌──────────────┐
              ┌───────────────┼──────────────┐   │ worker #1..M │ FFS_ROLE=worker
              │               │              │   │  (extraction)│ waiting_room.py --work
        ┌─────▼─────┐   ┌─────▼──────┐  ┌─────▼───────┐ └──────────────┘
        │ Postgres  │   │ object store│  │ off-machine  │
        │ (+PgBouncer)│ │ (SharePoint │  │ backup sync  │
        │  master +  │  │  / S3 docs) │  │ (OneDrive/   │
        │  queue +   │  └─────────────┘  │  S3 versioned)│
        │  locks)    │                   └──────────────┘
        └────────────┘
```

---

## Configuration reference (env vars)

| Variable | Purpose | Default |
|----------|---------|---------|
| `DB_ENGINE` | `sqlite` or `postgres` | `sqlite` |
| `DB_DSN` | Postgres DSN (when `postgres`) | — |
| `DOC_BACKEND` | `local` or `sharepoint` | `local` |
| `FFS_ROLE` | `all` / `web` / `worker` | `all` |
| `FFS_SECRET_KEY` | shared session signing key for the web fleet | per-box file |
| `INTAKE_WORKER` | `0` forces the in-process worker off | `1` |
| `INTAKE_DB` / `INTAKE_INBOX` | queue DB / inbox location | local files |
| `BIND_HOST` / `BIND_PORT` | waitress bind | `127.0.0.1:8050` |

---

## Remaining blockers to a fully-validated 100% (be honest)

What already works and is tested on SQLite:
- ✅ Node roles (`FFS_ROLE`) — web nodes don't drain the queue; workers do.
- ✅ Shared sessions (`FFS_SECRET_KEY`) — no sticky sessions on the LB.
- ✅ Pluggable file storage (local / SharePoint) with stable historical locators.
- ✅ Lease-based queue + leader-elected scheduler (multi-process safe today).
- ✅ Paramstyle shim `db.qmark_to_pyformat()` (`?` → `%s`) — unit-tested.
- ✅ Mechanical dialect shim `db.translate_dialect()` — `datetime('now')` (no-modifier)
  → `now()` and `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`, unit-tested.

What still needs doing **before** a production Postgres cutover (needs a live Postgres
to validate — not available in CI):
1. **Exercise `_PgShim` against a real psycopg** — the translator is unit-tested, but
   the connection/cursor wiring needs an integration pass on a live DB.
2. **Dialect functions.** The two MECHANICAL dialect-isms are now auto-translated at the
   shim (`db.translate_dialect()`, unit-tested): `datetime('now')` [no-modifier] → `now()`
   and `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`. What REMAINS as per-site
   ports (no mechanical rewrite — must be validated on a live Postgres):
   `datetime('now', <modifiers>)` → interval math (e.g. `now() - interval '1 day'`),
   `INSERT OR REPLACE` → `ON CONFLICT(<target>) DO UPDATE SET`, and the `json_object`
   **audit triggers** (`audit.py`) → a Postgres trigger function.
3. **Migrate the operational DBs too.** `db.py --migrate` copies the four master DBs;
   for a shared fleet the **queue** (`intake`) and the **cross-node leases**
   (`process_lock`) must also live on the shared Postgres, or workers/leaders won't
   coordinate across machines.
4. **Postgres-native backups.** `backup.py` snapshots SQLite files; on Postgres use
   `pg_dump` / managed snapshots, and sync `backups/` (or object-store versioning)
   off-machine so a disk loss can't take the data.
5. **Ops glue (no code):** nginx/LB, PgBouncer, and per-node env wiring.

Items 2–4 are the genuine engineering remainder; 1 and 5 are validation/ops. Until
those land and are validated on a live Postgres, run Stage 0–2 (one app box + Postgres +
offloaded storage), which already scales well past a single-box-SQLite deployment.
