# SYS-02 — Compose readiness without curl

AI-assisted diagnosis, configuration repair, regression checks and evidence drafting.
Implemented locally, pending exact-head CI and human/lead review. Continues SYS-02
from the [full-system scan](FULL-SYSTEM-SCAN-2026-10-17.md); the scan retains its
historical observations. No production deployment or release clearance is claimed.

## Problem and resulting behavior

The hardened backend image has Python but no curl. Base and Hostinger Compose
overrode its healthcheck with a curl command, so worker/frontend startup could
remain blocked even when the API and database were ready. Generic production and
the Hostinger migration/image overlays inherited the broken command.

Both definitions now execute Python's standard-library HTTP client against
`http://localhost:8000/health/ready`. HTTP failures, refused connections and socket
timeouts exit unsuccessfully. The response is closed on success. The HTTP socket
timeout is three seconds; Docker's five-second process timeout remains the outer
bound. Interval 10 seconds, 12 retries and 30-second start period are unchanged.
No executable or package is added to the image.

The database readiness endpoint remains distinct from `/health` liveness. Worker
and frontend retain `service_healthy` dependencies. Migration overlays still wait
for successful one-shot migration completion, disable the migration healthcheck
and retain the worker's own loop-heartbeat probe.

## Evidence

[Sanitized machine-readable evidence](evidence/SYS-02-COMPOSE-READINESS-2026-10-17.json)
records immutable image/module identities, rendered healthchecks and measured results.

- **Six new regressions failed first** against the curl configuration. **45 focused
  deployment, queue-health and inbound-channel checks then passed** in 24.29 seconds.
  The six variants cover base, base plus production, Hostinger alone, Hostinger plus
  images, Hostinger plus migrations, and Hostinger plus migrations plus images.
- **Docker Compose 2.40.3 rendered all six variants** with synthetic required values
  and `--env-file /dev/null`. Each retained the same readiness command, timing,
  dependency conditions and worker/migration probe separation. Rendered configuration
  containing synthetic credentials remains outside Git; the evidence includes only
  healthcheck/variant results.
- **Native runtime probe passed with Python 3.14.8, UID 10001**, dropped capabilities,
  no-new-privileges and a read-only container root. The actual image has no curl.
  It is the existing scan image `bidit-fullscan-backend:716153c`, immutable ID
  `sha256:4aef757e3b4a5786d756bbcd41862e50fa53d5f44dbb8f88eff8ded7b6e0a573`;
  its `app/main.py` and database module hashes exactly match current source.
  This is a configuration repair tested with that image, not a newly built release.
- An isolated real PostgreSQL 16 container backed the actual API. Docker's installed
  exec-form healthcheck exactly matched the rendered command with all production
  timings retained. Startup became healthy in **10.874 seconds**. Stopping PostgreSQL
  made the probe exit 1 with HTTP 503 in **0.722 seconds**, while `/health` remained
  HTTP 200. Native health became unhealthy after **123.895 seconds**, exercising
  the unchanged failure threshold. Compose then refused to start either dependent:
  both containers remained created with no start timestamp.
- After PostgreSQL restarted, native health became healthy in **2.374 seconds** and
  Compose started both dependents. Their Python startup sentinels exited 0. These
  sentinels prove Compose dependency gating, not full worker or NGINX behavior.
  The API used development-mode schema creation and one worker, with writable
  temporary storage; this does not validate production migrations or SYS-03 storage.
- A separate network-isolated runtime container executed the exact probe against
  its own loopback HTTP fixture. HTTP 200 exited 0; HTTP 503 and refused connections
  exited 1. A response stalled before headers exited 1 in **3.341 seconds**, within
  the existing five-second Docker timeout. Every received request used `/health/ready`.
- **Nine docs/PG-registration checks passed**, Ruff and format checked **808 files**,
  and **3,800 backend tests collected**. Workflow pins and generated security-control
  checks passed. Existing synthetic HMAC-key warnings remain visible (36 in the
  focused run). The full backend/browser/PG suites were not repeated locally for
  this configuration-only repair; fresh exact-head CI is required.
- Current-tree structural PII scanning, document links and source/probe hash
  consistency passed. The owner-held denylist is empty; this check does not clear
  the separately recorded historical PII findings.

Only owned synthetic containers, their anonymous database volume and the isolated
network were removed after the check. No customer data, external provider or
production service was accessed. Source hashes and fixture hashes are in the JSON.

## Rollout, recovery and remaining boundaries

After review, apply the updated Compose files with the deployment's existing overlay
order and recreate the backend to install its new container healthcheck. An already
created container retains its previous healthcheck until recreated. No migration,
dependency upgrade, image-pin change or data backfill is needed for this repair.

If readiness fails, inspect the API/database and migration status; restore the
underlying dependency before starting waiting services. Do not switch the probe to
liveness or disable it. Docker marks unhealthy containers but does not automatically
restart them on that status; initial Compose dependencies do not stop already-running
workers/frontend or change subsequent traffic routing when the database fails.

SYS-03 fresh-volume ownership is next. This probe deliberately used temporary
storage so that separate confirmed defect remains visible. Native image findings,
unavailable service-image references, historical PII, external deployment/restore
acceptance and human/lead review remain open. The preceding source-head CI still
had backend/browser runs pending and docker-build failed when checked; it does not
clear this candidate or the unchanged image security gate.
