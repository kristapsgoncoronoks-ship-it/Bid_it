# R017 — Bounded capture admission and recoverable local storage faults

AI-assisted implementation, regressions and evidence drafting; human/lead review
and final-head CI remain required. Continues draft PR62 from `e3e368c`. That head's
backend, PostgreSQL, browser, dependency, static and frontend CI completed
successfully; the final-image security gate remained red. This slice does not
resolve or waive the existing image/history/control/production decisions.

## Defects and repair

The supplier capture helper read the entire UploadFile before checking the
configured cap. Both the single-file and batch API now reuse the existing
`filesec.read_bounded` helper: at most cap+1 application bytes are read, in chunks
no larger than 64 KiB, then an oversized file is refused before scanning, storage,
registry, capture, job or usage writes. The file is closed after this read even
when it exceeds the cap. The existing policy remains the only cap definition.
Single-file refusal remains HTTP 413; batches keep ordered per-file outcomes.

Local original-storage failure previously escaped as an unhandled server error.
An OSError or storage-protocol StorageError during original storage now rolls back
the read transaction and returns a generic, actionable 503 for a single upload,
or `storage_unavailable` for that file in a batch. No private path/backend error
is returned. There is no capture, job, registry row or usage increment for the
refused file. Earlier accepted batch files stay committed and later files can
continue; request identity primitives are captured before a rollback can expire
ORM objects. The existing quota, scanner and duplicate-advisory order is retained.

The local object store also used one deterministic `<key>.tmp` path. Concurrent
writers of identical content could remove each other's staging file and make
one upload fail despite a complete object being present. Each writer now owns
a private random sibling staging file. Successful replacement stays atomic on
the same filesystem; ordinary write/replace exceptions remove only that writer's
temporary file and preserve the previous complete object. New files are private
(0600), so API/worker shared local storage must use the same service identity as
the existing non-root deployment. No storage keys or database schema change.

The upload screen shows Retry upload only for a definite storage refusal. It
retains the original browser File and retries that one file without the duplicate
override. Pending disables retries. File/result association follows request order,
so two different sources named the same.csv cannot cause the accepted source to
be resent. Upload anyway remains an explicit duplicate override, and now uses
the correct ordered file too. Failed HTTP retries keep the previous result/file
association; successful results adopt their own request's file list.

## Evidence

Six new backend regressions failed before the fix, then all passed: bounded
single/batch reads; synthetic ENOSPC with no phantom work followed by successful
retry; a middle-of-batch protocol failure with accepted files before/after it;
simultaneous real-filesystem writes of the same object; and failed replacement
preserving the old object without a leftover staging file. **69 related backend
checks passed**, including existing quota, duplicate, security and worker intake
expectations. No assertion, severity, cap or bundle budget was relaxed.

The new browser test failed before the retry control existed, then passed. It
selects two different files with the same name, checks the accepted-file review
link, holds the retry response to prove pending protection, and inspects multipart
bytes to prove only the refused source is resent with no override. **12 affected
browser tests passed**. These UI error cases use mocked responses.

A separate live API → local object store → native worker → source-download check
used a fresh synthetic workspace: HTTP 202, persisted parse status parsed, exact
**167 source bytes**, SHA-256
`f948565e8e5722865f679bc2d05c1f688275e82fb1c4b87cf7230ae892fb8bb5`.
No customer document, provider delivery or production database was used.

Full local backend verification (`pytest -q -n 4`, Python 3.12.14): **3,667 passed, 39 skipped**, 589.19 seconds. PostgreSQL-only checks remain a separate CI gate; no new schema or SQL contract was introduced.
Backend lint/format passed; mypy passed for **418 files**. Frontend lint/build and
bundle gate passed at **435.8 kB raw / 127.6 kB gzip**, unchanged 460/135 budgets.
The collector reports **3,706 tests**; README count updated accordingly.

## Limits, rollout and recovery

This bounds the application file read after multipart parsing. Starlette may
already have spooled request parts, and batch aggregate/disk/network admission
still depends on deployment ingress controls; no whole-process RAM or new hardware
claim is made. S3 delivery was not exercised; fault tests cover local OS errors
and the storage protocol. Catchable errors receive staging cleanup; a process kill,
power loss or a filesystem refusing cleanup can still leave an orphan. This is
not a new fsync/power-loss durability guarantee.

No migration or dependency update is needed. Roll out API/worker code together
for the common local storage behavior and deploy the paired UI recovery control.
After a definite storage refusal, repair availability and retry that file. This
slice does not introduce durable capture command identity or alter capture/job/
usage commit boundaries; an uncertain transport response still requires existing
capture/duplicate review before intentionally overriding anything. Do not report
accepted work as failed merely because another batch file was refused.

Rollback restores the earlier temporary-file race, unbounded application reads
and opaque storage failure; it does not change existing object keys or financial
history. No existing source or temporary files were bulk-deleted.

Action descriptions / engineering prompt: Upload must bound bytes before expensive
work and acknowledge only the actual admitted files. Retry upload resubmits exactly
one definitely refused source, preserving its bytes and ordinary security/quota/
duplicate checks; Upload anyway alone requests a duplicate override. Preserve
ordered outcomes, tenant ownership, native worker linkage and usage evidence.
Test over-cap reads, failure before durable work, mixed batches, same-name sources,
pending protection and concurrent storage writers. Keep ingress, ambiguous-response
and physical-durability limits explicit rather than claiming guarantees not tested.
