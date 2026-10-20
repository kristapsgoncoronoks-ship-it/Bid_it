# Configurable database backup identity — 20 October 2026

**DEPLOY-CUSTOM-DB-BACKUP is implemented; native backup-stage qualification
passes.** This report records the bounded native checks below. Hosted candidate
CI, a full VPS rollout and the authoritative live host entrypoint are separate
qualification steps. Earlier uncommitted application/UI work is outside this
candidate.

## Problem and repair

Hostinger Compose exposes `POSTGRES_USER` and `POSTGRES_DB` for the database,
health check, API and worker. The shipped backup command instead selected
`invoiceiq` for both values, so valid custom identities could refuse backup.
The original native custom-role/database command failed with exit 1 at source
SHA-256 `557a156eecf7b0d860b9d655ebd7fa853834ce93a93a40b79dbb7ca503850ccb`.
No live-host failure or data loss is inferred.

The repair reads the effective `POSTGRES_USER`/`POSTGRES_DB` inside the DB
container, refuses missing or empty values, exports quoted `PGUSER` and
`PGDATABASE`, then executes `pg_dump`. It does not interpolate those values into
a shell command. The existing gzip pipeline, pipefail, private backup files,
completion-marker verification and surrounding deployment recovery remain.

Using an otherwise quoted `--dbname` argument is insufficient: real libpq treats
a name containing `=` as connection information. Native probes show the valid
synthetic database `native=literal` fails with that argument, while `PGDATABASE`
preserves its literal identity. Ordinary names and names with spaces/semicolons
also work through the environment. This is why the repair uses libpq's environment
defaults rather than narrowing supported database names.

## Executable deployment harness

[Failure-first evidence](evidence/CI-CUSTOM-DB-FAILURE-FIRST-2026-10-20.json)
records **5 failed / 23 passed** on the original script, including all 20
existing deployment cases passing. The original 28-test source is retained;
new empty-value controls are not retroactively counted in that baseline.

The [final 30-case harness](evidence/CI-CUSTOM-DB-HARNESS-2026-10-20.json)
passes, including default/custom/literal identity, real shell quoting, missing
and empty values, stream exit preservation, truncated-success refusal, ordered
quiescence/backups, locks, signals and resuming only prior writers. Its fake DB
container executes the actual shell command in a fixture-only environment;
ambient host credentials/libpq defaults cannot mask it. This harness does not
model the complete PostgreSQL connection parser; native evidence below does.

[Separate empty-value controls](evidence/CI-CUSTOM-DB-EMPTY-CONTROLS-2026-10-20.json)
reject the original script and a temporary no-colon parameter-validation mutation.
[Independent review](evidence/CI-CUSTOM-DB-REVIEW-2026-10-20.json) found no blocker;
removing both required-value checks makes all four missing/empty preclient cases
reject the mutation. No real candidate source was changed by those controls.
All counts overlap with the full candidate suite and must not be summed.

## Native PostgreSQL evidence

[Machine-readable proof](evidence/CI-CUSTOM-DB-BACKUP-2026-10-20.json) binds the
actual backup-stage bytes, helpers, private logs, archives and result hashes.
The tested script SHA-256 is
`c49648a1199ab94bffad19778100b4e7026714a807da3d9c908de05de325d98c`.
The helper extracts the database-backup stage directly from that script and
executes it unchanged. A controlled Compose-exec routing function sends its DB
command to the owned container; this is not execution of the entire VPS script.

The native image reports **PostgreSQL 16.15**, image ID
`sha256:81bd698b4594e751a3269e4dcd3e03a4a0ec0daf7b72e7aa1abd43cce9887542`,
with digest `postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`.
That is the PostgreSQL base pinned by the candidate; the full rebuilt final
image/application stack is not claimed executed by this probe.

| Native case | Actual backup stage | Dump and restore evidence |
| --- | --- | --- |
| Default `invoiceiq` identity | Exit 0 | Valid gzip/completion marker; exact row/schema readback |
| Custom role and database | Exit 0 | Source unchanged; exact row/schema readback |
| Database containing `=` | Exit 0 | Literal intended DB selected; exact row/schema readback |
| Role/database with spaces and semicolons | Exit 0 | Literal values preserved; exact row/schema readback |
| Empty user or empty database (two controls) | Exit 2 | No completion marker; source witness unchanged |

Each positive case seeds three synthetic rows with text, Unicode, bytea, arrays
and NULL values. Ordered row and schema hashes match before/after the dump and
after restoring into a fresh owned database. Each archive is mode `0600`. The
proof records the exact archive hashes; it does not republish row contents.

Containers ran sequentially with **1 CPU / 384 MiB**, a read-only root filesystem,
network `none` and tmpfs data. No host ports or customer/production volumes were
used. All six owned containers, including the initial failed setup fixture,
were removed; no global prune or unowned cleanup was performed. Resource fields
are enforced ceilings, not measured peak RSS or minimum-hardware acceptance.

The first acceptance fixture attempted setup after `pg_isready` alone and failed.
Its cleanup/metadata are retained, but the original stderr was not retained, so
the exact cause remains unattributed. A temporary initialization server was a
possible explanation. The corrected fixture waits for PID 1 to be `postgres`
and for a successful SQL query before setup; all four acceptance cases then
passed. This fixture correction is separate from product-source behavior.

## Boundaries and rollback

This native probe qualifies the exact database-backup stage and synthetic
restoration. It does not execute full build/stop/migration/recovery ordering,
prove external-writer quiescence, certify customer restores or size production.
The executable deployment harness and fresh hosted checks retain their own
source/population boundaries. The live wrapper/public readiness, trusted SSH
host identity and exact qualified revision protocol remain separate backlog items.

Reverting this change restores the hard-coded custom-identity refusal. Preserve
writer quiescence, private backups and the refusal to revive old consumers after
any startup/migration attempt; this repair does not authorize schema, role,
retention, provider or live-setting changes.

AI-assisted: native investigation, isolated reproduction, helper drafting and
verification documentation. No external implementation copied and no production
or customer data used.
