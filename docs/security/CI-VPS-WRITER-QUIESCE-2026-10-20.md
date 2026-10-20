# Quiesce capture writers before VPS migrations — 20 October 2026

**Implemented; local qualification recorded below. Fresh hosted qualification
and main integration remain pending.** This follows the user's instruction to
continue the CI repair train. Existing merge authorization is preserved; an
unreadable deployment variable is not assumed false.

## Confirmed rollout defect

Capture migrations `127bc9ad604e` and `238cd0be715f` explicitly require API/extract
writers to be quiesced. Old writers cannot enforce the new generation and
admission-command fences. Candidate `22c9b3af` retained main's unchanged
`scripts/vps-deploy.sh`, which ran ordinary `compose up -d --build`. Its worker
is recreated only after the new backend becomes healthy, so the script did not
establish the required migration window. This is a source-confirmed rollout
gap, not evidence of observed production data corruption.

Read-only deployment review also corrected the earlier blocker framing:
`docker-compose.hostinger.yml` uses existing local shared `storagedata`. It has
no bundled S3 bootstrap services. Decision §29's production S3/provider refusal
does not itself prevent using this separate Hostinger stack. No provider choice
or data-store cutover is part of this repair.

The current `DEPLOY_ENABLED` value remains unreadable (GitHub 403). The latest
main push at `7608c1e` skipped deployment because Docker CI failed; it does not
prove the variable is false. The earlier successful main push `34783822395` at
`02a4e17` ran deployment successfully. Its public health assertion warned that
`DEPLOY_HEALTH_URL` was unset then; today's setting is unknown. Normal main
integration can therefore invoke the existing CI-gated deployment path.

## Resulting sequence

1. Preserve existing environment/disk preflights. Acquire a private `flock`
   shared by invocations using the same backup directory.
2. Record the existing backend, worker, frontend and database image identities
   before builds retag local images. Treat checkout HEAD as checkout evidence;
   the SSH wrapper may already have reset it to the candidate.
3. Pull and build while existing containers still run. A failed build does not
   stop writers. Existing bind-mounted frontend configuration can nevertheless
   change with the checkout; no immutable-serving-deployment claim is made.
4. Remember which API/worker services were running; stop both with a bounded
   timeout. Query **all** their containers, including one-offs, for running,
   restarting or paused states. Any leftover or failed query refuses migration.
5. Take and verify the database dump and document archive while writers are
   stopped. Backup files are private under `umask 077`.
6. Start prepared images with `up -d --no-build`. The backend migrates before
   the new worker starts. Preserve the existing backend health checks.

Before a startup attempt, failure or catchable HUP/INT/TERM resumes only the
previously running **existing** containers with `compose start`. It does not
recreate containers from the newly built tags. Recovery failure preserves the
primary exit status and explains manual recovery.

After startup is attempted, migrations may have run. Old writers are never
automatically restarted. Verified backups and capture-job reconciliation are
required before rollback; the migrations' downgrade refusals and durable
command/generation evidence remain intact.

## Qualification

The initial 13-case Bash harness, against the old ordering with only a private
backup-directory redirect, recorded **10 failures / three passes**. Backups ran
while both synthetic writers were active. The original full output, transient
baseline source hash and fixture events were not retained; no hashes for those
missing bytes are invented.

The expanded executable Bash harness has **20 passing cases**. It verifies
success ordering, build/stop/status/backup/start/health failures, leftover
one-off writer statuses, image evidence, private backups, real lock exclusion,
signal recovery and preservation of primary failures. Docker, Git, disk and
clock commands are controlled fixtures; gzip/tar and file locking are real.
No HOME override, live host, customer volume or credentials are used.

The combined affected deployment/security/docs group passed **66 tests**;
existing source counts collect **4,537 tests**. Bash syntax, Ruff lint/format
and whitespace checks pass. These results overlap, rather than adding into a
synthetic suite total.

Native Docker 28.4.0 / Compose 2.40.3 also verifies the stop/refusal/resume
commands on an owned, network-isolated synthetic project. Stopping the regular
services left a one-off worker running; the exact guard found it and would
refuse migration. After that fixture was explicitly stopped, byte counts and
digests stayed stable for 1.2 seconds. `compose start` resumed identical regular
container/image IDs and writing, without resuming the one-off. Cleanup removed
all owned containers and the synthetic volume. An initial bare-Python PID1
fixture timeout is preserved separately; the passing fixture uses `init:true`
to mirror the shipped backend's tini signal forwarding. This native proof does
not execute the complete application migration or certify the live host.
Source hashes, limitations and fresh hosted status are in
[the evidence](evidence/CI-VPS-WRITER-QUIESCE-2026-10-20.json).

## Scope and rollback

This is the documented single-VPS Compose project. External writers require
separate operator quiescence. The script lock does not cover a host wrapper's
earlier checkout reset; those operations must also be exclusive. GitHub's
existing production-job concurrency remains. SIGKILL, host outages and stopped
external processes require operator recovery, not an automatic success claim.

The actual live host wrapper and settings were not changed or independently
certified. Historical PII classification, production sizing, provider/cutover
approval and external acceptance remain separate. The app's financial/source
code and published migrations are unchanged by this script repair.

Reverting the script restores the migration-ordering defect. After any startup
attempt, follow the runbook and reconcile capture commands/jobs before restoring
an older consumer; do not automatically downgrade or erase generation identity.
The Hostinger guide now uses this script for upgrades instead of suggesting
ordinary `up --build` as an equivalent shortcut.

AI-assisted: implementation, executable regressions, independent deployment
review and adversarial ordering review. No external implementation copied; no
audit, tenant, money, retention, provider or deployment-setting policy changed.
