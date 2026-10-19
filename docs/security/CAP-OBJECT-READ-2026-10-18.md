# CAP-OBJECT-READ — bounded capture sources and parallel PDF/OCR qualification

**18 October 2026 · implemented, pending exact-head CI and human/Lead review.**
Part of CAP-CAPACITY. AI-assisted investigation, code, tests and documentation.

## Confirmed failures and behavior

Stored UI/email capture sources were loaded in full before parsing. Admission
limits therefore did not bound a restored/replaced object or a source admitted
under an earlier, larger setting. Oversized CSV sources could still parse after
the configured processing cap was reduced. S3 response bodies also remained open
on successful reads and read errors. Four pre-fix regressions reproduced these
failures.

The storage protocol now accepts an optional caller-supplied `max_bytes` budget.
Local/S3 reads consume positive chunks of at most **64 KiB** and stop at **cap +
one byte**; oversized results raise `StorageReadTooLarge`, never partial success.
Short reads do not imply EOF. Local files and S3 response bodies close on success,
size refusal and read failure. Memory storage has the same boundary semantics.
No remote Content-Length or local size precheck substitutes for counting bytes.

Both capture workers obtain their budget from **filesec.max_bytes()**, the existing
upload owner. A limit failure becomes `stored_file_too_large` in the existing
failed-capture worklist, with administrator guidance and no futile same-input
Retry button. The original object, tenant/source references and upload command are
retained. The job completes as a handled capture failure and the worker continues.
UI failure publication still locks and refreshes the durable attempt; a delayed
old size failure cannot replace a newer result. Unrelated storage failures retain
their existing behavior; this repair does not relabel every provider fault as a
missing object.

Other `documents.load` callers retain their existing reads unless they supply a
budget. Downloads/exports and aggregate concurrent API reads remain open work.
No dependency, schema, upload cap, worker count or deployment resource limit changed.

## Verification

[Sanitized measurements, image/source identities and hashes](evidence/CAP-OBJECT-READ-2026-10-18.json).

| Check | Result |
| --- | --- |
| Credited pre-fix reproductions | **4 failed**: two S3 closure and two worker-size failures |
| Related backend suites | **122 passed / 113 warnings** |
| Entire required PostgreSQL CI step, fresh migrated non-superuser/NOBYPASSRLS database | **129 passed / 199 warnings**, including delayed read-failure fencing |
| Shipped Python 3.14.8 storage/worker checks | **27 passed / 5 warnings** |
| Cap-ownership/PG-in-CI controls | **17 passed / 6 warnings**, including a seeded cap-control counterexample |
| Worklist browser regressions | **8 unique cases passed twice with traces: 16 executions** |
| Ruff / format / mypy | Passed; **421** application sources type checked |
| Frontend lint/build/bundle | Passed, **435.8 kB raw / 127.6 kB gzip**, unchanged 460/135 limits |
| Collection only | **4,027 backend / 598 browser** in 65 files |
| Fresh final API and unchanged origin SBOM/HIGH/CRITICAL audits | Both clean, zero advisory rows |

The first browser file run had **1 existing selection timeout / 7 passes** waiting
for `#sel-run-2`, including a pass for the new size-remediation case. The unchanged
file subsequently passed both traced repetitions with one worker. No timeout,
retry budget, fixture or assertion was loosened. The original failure details are retained in the private run log and logged
under **SYS-09**; concurrent native
execution is context, not a demonstrated cause. Passing repeats do not turn that
initial run green or establish the cause. The initial failure log and all 16
passing traces are archived privately. The generated initial error-context.md was
inspected but not archived before the repeated run reset Playwright's output
directory; it must not be represented as retained.

### Native two-worker profile

Real shipped API/workers, PostgreSQL **16.15**, fresh migrations, a NOSUPERUSER/
NOBYPASSRLS application role and a shared **owned synthetic local volume** were
used. API: one Uvicorn worker, **1 CPU / 768 MiB**. Two independent extraction
workers: **1 CPU / 1 GiB each**, UID 10001, read-only root, dropped capabilities,
no-new-privileges, separate 128 MiB temporary filesystems. The existing extract-lane
Kubernetes ceiling is 1 GiB; no deployment budget was raised. Native child limits,
Tesseract, real queue claims, parsing and preview rendering stayed enabled.

The queue held UI and email sources: one text PDF and one image-only scanned PDF
per channel, plus one oversized stored CSV per channel. Only the fixture's own
objects were expanded to sparse **128 MiB** files after normal admission,
simulating an oversized restore/replacement. Instrumentation counted actual reads
and handle closure; it did not inject read failures or replace parsing/OCR.

- Workers overlapped in time and processed **4 and 2 jobs**. All four PDF sources
  reached their expected **text-layer / ocr** result; both oversized sources
  reached the failed worklist with `stored_file_too_large`.
- Each oversized read stopped at **15,728,641 bytes**, the unchanged 15 MiB cap +
  one. Across the parallel run and subsequent healthy capture, **7 source reads /
  7 closed handles**, no unbounded read and no request above 65,536 bytes.
- A subsequent healthy CSV parsed successfully. Final totals: **7 succeeded jobs,
  4 durable UI upload commands, 4 UI captures and 3 inbound rows**. The handled
  size failures remain failed captures, not successful invoice extraction.
- Worker peaks were **428.703125 MiB** and **396.87109375 MiB**, below each 1 GiB
  ceiling, with max/OOM/oom_kill events all zero. Job-processing intervals were
  **12.69 s / 9.22 s** for different job mixes. These are individual cgroup peaks,
  not a simultaneous host-memory measurement or a throughput promise.
- Real Chromium used the unchanged compiled SPA through NGINX: both failed
  filenames and administrator guidance were visible, originals were described
  as retained, no Retry button was offered, and readiness remained 200. No API
  route mocks were used in this workflow.

This small ordinary-document profile does not qualify worst page geometry,
maximum-page/near-limit PDFs, all tenant mixtures or fleet headroom. S3 behavior is
covered with instrumented bodies and a real botocore StreamingBody truncation
control; no unavailable MinIO/S3 service was represented as tested. No original
was deleted by refusal handling. Owned containers/networks/volume and synthetic
credential files were removed after the run.

## Cap-control correction and adversarial review

The existing AST cap-owner scan flagged the two generic `len(data) > max_bytes`
checks as competing upload policies (**1 failed / 15 passed**). The core storage
module cannot import service-layer policy; its budget is a caller parameter.
The gate now recognizes only `_read` and `MemoryStorage.get` in that exact module,
requires an actual parameter with no non-None default or reassignment, and exempts
only the comparison signal with that parameter. Megabyte literals and other
size gates remain scanned, including literals on the same source line. Adversarial
review of an unpublished prototype reproduced a same-line-literal hole (**one
seeded failure**); the final policy now filters the signal type as well as the
location. Seeded literal/default/reassignment/same-line mutations fail the control; worker regressions prove that lowering filesec's owner controls both
channels. No line-number waiver, cap increase or blanket storage exemption was
introduced. **Lead review of this control correction remains pending.**

**Proposal / benefit:** bound capture source allocation before native parsing,
close storage resources, and publish an explained failure without losing command
or source identity. Reuse the existing failure worklist and transactions.

**Alternatives:** relying only on admission/Content-Length misses restored objects
and races; changing the default for every stored download risks unrelated archive
contracts; silently truncating corrupts documents; deleting shared objects is not
valid cleanup. A new queue or global memory coordinator is unnecessary for this
boundary repair.

**Adversarial objection / risk:** the optional protocol budget leaves unrelated
callers unbounded, and a byte cap does not bound decoded PDF/OCR memory. Correct:
these remain explicit backlog items. Buffer assembly/copying and native decoding
consume more RAM than the input byte count. The measured profile is qualification
of two ordinary workers, not global resource closure. The cap-control exception
adds test complexity; its strict scope and seeded controls must remain reviewable.

**Rollback:** revert the optional storage contract, worker adapter, failure kind
and control change together. No data migration or object/receipt deletion; old
reads become unbounded and the former failure behavior returns.
**Lead decision: pending** on protocol/error semantics, control correction,
transaction/resource evidence and deployment acceptance. Prior head `14a3da7`
passed hosted backend, PG, frontend/browser, lint, PII and supply-chain checks;
its full image gate remains blocked. New-head CI and human review are required.

**Next:** remaining API/download/verification object-read bounds; worker source
hash verification; worst-case parallel PDF/OCR, shared-disk pressure across
workers, fleet/controller admission and deployment headroom. S3 outages/restores,
reference lifecycle and retention need their existing acceptance/policy work.

## 19 October follow-up: remaining stored-read inventory

[Static source inventory](evidence/CAP-READ-INVENTORY-2026-10-19.json) records
18 direct `documents.load` calls without a `max_bytes` argument: ten in API
routes and eight in services. They include originals, thumbnails, logos,
attachments, project/portal documents, delivery, integrity verification and export
construction/download. The optional load contract forwards these calls to an
uncapped storage read. This is a candidate inventory, not proof of runtime
reachability, an authorization bypass or a measured allocation failure. Existing
upload and export limits remain relevant; the capture worker adapter stays bounded.

Next reproduce oversized restored objects on the authorized paths, then apply
existing purpose-specific limits or an appropriate streaming contract. Ordinary
documents and generated export archives have different supported size contracts;
a blanket capture-size cap would risk rejecting valid archives. Preserve source
integrity, tenant/permission checks, supported historical downloads, clear
refusal/retry and resource closure. Native aggregate measurements remain required.
No application behavior was changed by this inventory.
