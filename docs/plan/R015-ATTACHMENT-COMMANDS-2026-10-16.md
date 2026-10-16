# R015 / INV20–21 / E16-04 — Recoverable attachment commands

AI-assisted implementation/test/evidence drafting; human review and exact-head CI
pending. Source: [uploaded report reconciliation](../reviews/RECONCILIATION-2026-10-16.md).

## Upload and storage contract

The issued-invoice attachment endpoint now requires a client Idempotency-Key.
The key identifies an upload intent independently of its content-addressed blob.
A tenant-unique key binds invoice, byte hash, filename, MIME and note. Retrying the
same command returns its committed attachment (200); new intent creates one (201).
Changed inputs conflict. An intentional second command may attach identical bytes
as a distinct record. A tenant-scoped lookup returns committed status after a lost
response, including a later Trash state, without resurrecting the attachment.
The parent invoice lock and database unique constraint protect PostgreSQL races.
Attachment row, document registry and audit commit together. Storage uses the existing
content-addressed writer; a failed DB transaction can leave an unreferenced blob for
existing integrity/retention handling, never permission to delete a shared object.

The configured central upload cap controls both bounded file reads (at most cap+1
bytes) and a narrow attachment multipart ASGI guard (cap plus 64 KiB envelope).
The guard checks declared and streamed body bytes before form spooling can consume
an unbounded body. Exact-cap files remain accepted. Scanning runs in the existing
threadpool and remains fail-closed for rejection/unavailable configured scanners.
After scanning, authentication/membership/workspace/write permission are reloaded
outside the old read snapshot before locking/writing. Filename/MIME/note sizes validate.

The browser fetches the configured cap, refuses unknown/invalid limits, checks size
before hashing and stores only a scope-bound command key/digest in sessionStorage.
It retains the original File in memory for an explicit retry; after refresh, Check
previous upload resolves the committed result before any new write. Changed unresolved
inputs cannot replace the old intent. The hidden chooser is disabled while uploading
and through list readback, and for users without invoice-write access.

Downloads have independent per-file pending/error/retry state. The endpoint verifies
stored byte length and SHA-256, reloads current read authority and checks the parent
and attachment remain visible before returning inert attachment/nosniff bytes. Missing,
unavailable or corrupt storage produces a visible retry/recovery error, not a corrupt
successful download. Identity changes remount controls and existing download generation
checks prevent stale responses reaching another account/workspace.

## Actions and maintenance prompts

| Control | Expected result / recovery | Engineering prompt |
|---|---|---|
| Add file / Retry original upload | Submit one payload-bound command; disable duplicate selection and reconcile uncertainty. | Bound multipart and file reads, offload fail-closed scan, recheck authority, persist one metadata/audit result; distinguish intent identity from content hash. |
| Check previous upload | Read a saved command after uncertain response/refresh; explain a committed Trash result or retain the unresolved original key. | Never start another upload to discover whether the first committed; bind status lookup to tenant/invoice and keep original inputs until resolved. |
| Filename download | Save authorized verified bytes, with pending state and a scoped failure/retry message. | Verify byte count/hash and fresh visibility; prove browser-saved bytes equal source and response. Do not claim saving from request initiation. |
| Remove / file-list retry / cap retry | Use existing confirmation/Trash semantics or safely retry the failed read. | Retain permission checks and clear pending through readback; never silently restore an uploaded result found in Trash. |

## Evidence and rollout

The original route independently reproduced duplicate rows on replay and scanning on
the event-loop thread. Eight new focused regressions cover replay/audit identity,
different intents with identical bytes, changed input, Trash lookup, bounded reads,
chunked-body refusal, scanner offload/failure, scan-time offboarding, lost response,
corrupt storage, required command key and exact/over-cap files (several in one test).
The complete focused group passed **29 tests**, including existing file/security/bin
contracts. A real migrated non-superuser PostgreSQL test in that group raced two
identical uploads: one 201 and one 200, one row/audit, changed-payload 409, exact
verified download bytes, and offboarding committed by another session before write.
The existing cross-tenant attachment probe passed separately; required CI registration
and migration checks also passed.

Six affected browser tests passed, including download pending/error/retry and upload
chooser disabling with lost-response reconciliation. These mock API error branches.
Separately an unmocked Chromium → Vite → FastAPI → PostgreSQL + object-storage journey
uploaded and downloaded **60 identical bytes**. Saved bytes equaled source and HTTP
response, SHA-256 `d1132a9d4f06c4458e5b2be927a554eefdd709c9acfc19012fc8cce3996ea6de`,
attachment/nosniff headers and zero browser errors. Only the current API-process run
is credited; stale-process/harness readiness attempts were discarded. Synthetic text
only; actual ClamAV/provider operation and the complete E16-05 pilot are not claimed.

Ruff/format, whole-app mypy (418 files), frontend lint/build/bundle passed. Bundle is
435.8 kB raw / 127.6 kB gzip within the unchanged 460/135 budget. OpenAPI was regenerated
and reviewed; contract/documentation/PII/upload-cap checks passed (38 tests).

Migration `0d546afada3c` adds nullable command fields/unique tenant key, without backfill
or new tables. Deploy schema then paired API/UI; older API writers must supply a
stable key before upload (428 otherwise). Roll back the app with columns retained;
schema downgrade refuses keyed rows rather than discarding committed identity.
No source file, historical row or financial invoice is rewritten by this repair.
