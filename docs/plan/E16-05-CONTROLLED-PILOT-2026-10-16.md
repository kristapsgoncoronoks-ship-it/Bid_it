# E16-05 — Controlled synthetic finance and recovery pilot

AI-assisted harness, investigation and evidence drafting; human review and external
pilot/accountant acceptance remain required. This is bounded engineering evidence,
not a production release or a minimum-hardware certification.

## Reproduce safely

From the repository root, install the existing backend/frontend dependencies and
local Chromium. Build both existing Dockerfiles with OCI revision labels and
`org.bidit.backend-tree` / `org.bidit.frontend-tree` labels matching
`git rev-parse HEAD:backend` / `HEAD:frontend`. Select the images with
`PILOT_BACKEND_IMAGE` and `PILOT_FRONTEND_IMAGE`, then run
`backend/.venv/bin/python scripts/pilot/controlled_finance.py`.

The harness refuses dirty application sources and mismatched application-tree
labels, resolves tags to immutable image IDs, and creates uniquely labelled
containers, private internal Docker network and separate synthetic database/object
volumes. It never targets an existing database. A loopback-only bridge on port 8002
(default, configurable with `PILOT_BRIDGE_PORT`) lets the real browser reach NGINX.
For a direct Docker build without a custom proxy/CA setup:

```bash
pilot_revision=$(git rev-parse HEAD)
pilot_backend_tree=$(git rev-parse HEAD:backend)
pilot_frontend_tree=$(git rev-parse HEAD:frontend)
docker build --label "org.opencontainers.image.revision=$pilot_revision" --label "org.bidit.backend-tree=$pilot_backend_tree" -t bidit-pilot-backend:review backend
docker build --label "org.opencontainers.image.revision=$pilot_revision" --label "org.bidit.frontend-tree=$pilot_frontend_tree" -t bidit-pilot-frontend:review frontend
backend/.venv/bin/python scripts/pilot/controlled_finance.py
```

Docker, local Playwright/Chromium and sufficient temporary disk space are required;
Docker's VFS driver copies image files for each container. Completed migration and
old worker containers are removed before the second deployment to reduce temporary
storage pressure. Cleanup affects only this run's labelled resources.

All source data and identifiers are synthetic. SMTP is unconfigured, the billing
provider is disabled, and the internal network has no external provider access.
Credentials are generated per run. Private logs, tokens, source bytes and backups
remain outside Git under `/workspace/.cache/bidit-pilot-*`, directory mode 0700 and
files mode 0600. Do not attach state.json, database dumps, raw logs or configuration
to a PR. Only the aggregate report is suitable for review.

## Outcomes exercised

- Real stored CSV source → persisted capture/job → native upload worker → parsed
  capture. The harness abandons a claimed native job, advances its lease-expiry
  check through the existing reclamation function and retries through the jobs API.
  This tests persisted lease/retry behavior, not an actual operating-system kill.
- Browser confirms the capture into an AP invoice, submits for approval, and a
  separate authenticated actor approves it. A later edit is refused as locked.
- Real API setup issues one EUR100 AR invoice, one EUR10 credit and one EUR90
  payment. Replays preserve each financial identity. The browser matches a real
  imported bank line to that payment, then previews and downloads the approved
  invoice's accountant CSV. Critical actions/readbacks have no browser route mocks.
- Quiesced database dump and local-object archive are restored into separate fresh
  volumes. Before restore, deliberate supporting-file corruption must return an
  integrity refusal. After restore, source/attachment bytes, frozen CSV hash,
  financial totals, bank/payment linkage, command replay IDs, audit hash chain and
  the credit event's actual document link must all match.
- A new source is admitted after restore and a newly started native worker must
  finish it. Read-only restore checks alone are insufficient.

The credit replay exercise found R016; semantic audit checks then found that a
new credit event could hash correctly while lacking its document ID. Both fixes
and regression evidence are in [R016](R016-CREDIT-COMMANDS-2026-10-16.md).
An earlier restore write returned 500 because the disposable VFS test host ran out
of disk space; private server logs identified ENOSPC. That failed run is not
accepted evidence. The harness removes its no-longer-needed containers sooner.

## Profile and limits

Server containers have enforced CPU quotas totalling 2 CPUs and memory ceilings
of 1,920 MiB with no swap: API 512 MiB, upload worker 768 MiB, PostgreSQL 512 MiB
and NGINX 128 MiB. Runtime cgroup counters and process UID/capabilities/no-new-
privileges are checked, rather than inferred from configuration. There are two
identities, one active user workflow, one API process, one upload worker, with
parallel browser requests enabled. Host OS, Docker daemon, browser, bridge and
control client are outside these ceilings. Summed individual memory peaks are not
an instantaneous machine-wide RAM measurement.

Only one AP invoice, one AR invoice plus credit, one payment, one bank line and
one supporting attachment are exercised. CSV parsing does not benchmark OCR,
large PDFs, high volume, multiple tenants or concurrent users. The private origin
uses HTTP; production TLS has separate security smoke evidence. Application
configuration/signing key is retained across the drill; cold secret/KEK recovery,
provider delivery, external accountant import, legal/tax acceptance and production
backup storage/access controls remain unproven. Measured restore verification
starts after fresh database bootstrap and is not a production RTO guarantee.

Action/prompt: repeat against the reviewed immutable images using only isolated
synthetic data. Preserve all quotas, authority checks, hash/ledger/audit assertions
and post-restore write/worker checks. Investigate failures without weakening gates.
Expand workload and concurrency before making broader sizing claims; obtain named
accountant/product/operator acceptance independently of this engineering exercise.

## Accepted bounded run

[Aggregate evidence](E16-05-PILOT-EVIDENCE-2026-10-16.json), application commit
`46351730e2f8b85979919820e67916737c04ab0c`, records the immutable images and Git
application-tree IDs. The unmocked browser sequence completed in **33.823 s** with
zero browser errors. The saved one-row accountant CSV was **287 bytes**, net
EUR100 / VAT EUR21 / gross EUR121, and its exact response/download hash survived
restore. All 13 audit events verified, including the credit event's document link.
The application database role was neither superuser nor BYPASSRLS.

The sum of the four servers' individual memory peaks was **490.81 MiB** within the
1,920 MiB aggregate ceiling. The 91 successful origin-API samples had median
**59.12 ms**, nearest-rank p95 **330.308 ms**, maximum **1,170.432 ms**; these exclude
Docker/bridge overhead and are specific to this tiny workflow. No sampled server
reported an OOM event. The post-restore sample covers API/database/frontend; the
original native worker is included in the before-backup samples.

Fresh-database-bootstrap-to-restored-readback verification took **21.531 s**;
quiesce-to-that-readback took **50.335 s**. These timings end before the additional
new-upload/worker check, which also passed; they do not measure a complete
production recovery objective. The successful run followed the ENOSPC investigation
and container-lifecycle correction. No failed discovery run is counted as success.

The accepted run used controller SHA-256
`671a320cf3742e7f9ab966904dfabdde62c2a15784a46f682ea8558aafc824f1`
and browser harness SHA-256
`c0048b2e6c0e7a792865a0bad0d000bf97d123e11f806a4b3d2f41ab7e146b80`.
