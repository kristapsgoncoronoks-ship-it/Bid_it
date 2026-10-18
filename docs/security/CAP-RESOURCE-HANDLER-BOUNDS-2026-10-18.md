# CAP-RESOURCE — bounded handler reads and measured ingress gap

AI-assisted implementation, test drafting and documentation; no external code
copied. Handler repair implemented, pending exact-head CI and human/Lead review.
Subsequent API controls are recorded under **CAP-INGRESS**; aggregate resource
qualification remains open as **CAP-CAPACITY** in the
[findings register](../reviews/FINDINGS-REGISTER-2026-10-17.md).

## Problem and resulting behavior

Eleven remaining upload paths called `read()` without a size. Most refused large
files only after buffering them. Mailgun trusted the parsed size hint and read
unknown-length attachments whole. Safe synthetic regressions against `ae6a8a5`
failed **44 cases** before this repair; no exhaustion or customer input was used.

`filesec.read_bounded` now accepts the existing purpose parameter. It requests
positive chunks of at most 64 KiB and consumes at most the purpose cap plus one
byte. Reported size cannot permit a larger read. The API's small `read_upload`
adapter maps size refusal to the existing 413 sentence and closes the spool in
`finally`, including on read failure and cancellation. Metadata remains available
for downstream validation, storage and audit. Accepted byte buffers can still
require more memory than their source size; this is a read bound, not a constant
memory streaming parser.

| Surface | Existing file budget | Refusal/recovery |
|---|---|---|
| Reconciliation import | General configured cap | 413 before scanner/parser/import; reselect a smaller file |
| Expense bank import | General | 413 before scanner/parser/transaction writes |
| Receipt suggestions and saved receipt attachment | Receipt purpose | 413 before OCR/storage; existing suggestions/attachment workflows retained |
| Issuer logo | Logo purpose | 413 before scanner/profile/storage changes |
| Received-invoice working attachment | General | 413 before content gate/storage; existing empty-file refusal retained |
| Project document | General | 413 before document service/storage |
| Transport statement, estimate and claimant document | General | 413 before scanner/ingest/estimate/storage; existing pre-read checks retained |
| Mailgun attachment | General, per attachment | Oversized part counted as rejected; supported later parts still queued |

The general configured default remains 15 MiB; receipt 5 MiB and logo 2 MiB are
clamped by it. No size, parser, worker, retry or security budget was raised. File
kind, scanner, permission, tenant, storage and audit policies retain their existing
owners. This does not add command identity to legacy non-capture uploads or change
Mailgun's existing transaction/replay semantics.

Mailgun now owns the parsed form through an async context, so all parts close
after authentication/module/count refusal, deduplication, successful processing
or exceptions. Accepted/rejected parts also close immediately after their read.
The reconciliation input disables while pending and resets its native value on
selection, allowing the same named file to be selected after a refusal. Errors
remain visible until an import succeeds.

## Evidence and its limits

[Machine-readable evidence](evidence/CAP-RESOURCE-2026-10-18.json) records source,
image, measurement and log hashes.

- **159 focused/backend parity checks passed**: ten real route functions exercise
  cap+1 refusal, exact-cap bytes, I/O error and cancellation; their pre-read
  authorization/load collaborators are stubbed. Real Mailgun tests exercise
  unknown/understated/oversized metadata, partial acceptance and form cleanup.
  An AST tripwire detects new whole-file API reads, with seeded violations.
  The complete tenant-parity matrix runs real authenticated query paths.
- **177 existing related checks passed**, including accepted imports, receipt OCR,
  logo/invoice/expense/project/transport workflows, file security and layering.
  **31 authorization/schema/ERD/documentation/PG-list checks passed.** Full backend
  execution is delegated to exact-head CI. Collection: **3,906 tests**.
- Frontend lint, build and unchanged bundle gate passed: **435.8 kB raw / 127.6 kB
  gzip**, limits 460/135. Three reconciliation browser cases passed, including
  delayed refusal, pending disabling and successful same-file reselection. The
  final combined capture/reconciliation run passed **13 browser cases**; the
  corrected capture fixture also passed **20 repetitions with two workers**.
- A freshly built Alpine API image matches all **eleven changed backend source
  hashes** and has zero HIGH/CRITICAL findings in its fresh SBOM audit. The full
  eight-target deployment/build-image gate remains blocked by the existing
  [vendor/service findings](SYS-04-IMAGE-COVERAGE-2026-10-17.md).

Native execution used PostgreSQL 16.15 with an application role created as
NOSUPERUSER/NOBYPASSRLS, the built API image with a read-only root and dropped
capabilities, and a **1 CPU / 512 MiB API cgroup** on the 4 CPU cloud host. A temporary
instrumentation entrypoint counted read sizes and spool state without recording
file bytes or credentials. The browser used the current Vite source and real API;
streaming proxy checks used the existing final NGINX image whose default config
matched the current source hash. This is not production TLS/cluster certification.

- A real browser received the 1 MiB configured 413, then successfully imported a
  valid file with the same name. No mocked API response was used.
- Four simultaneous chunked 2 MiB multipart uploads (two direct, two through
  NGINX) each returned 413 after **1,048,577 handler-read bytes**. All observed
  handles closed. A 1,200,000-byte disconnected upload left zero temporary-file
  descriptors and zero `/tmp` entries; valid import still succeeded afterward.
- The database contained exactly two successful statements/two lines, zero
  captures and zero capture commands. Refused requests did not create imports.
- API cgroup `memory.peak` was **163,246,080 bytes (155.7 MiB)** under its 512 MiB
  ceiling. This includes cgroup-accounted memory/cache, not a process RSS metric.
  Open descriptors grew 17→20 with HTTP/DB connections; temporary-file descriptors
  remained 0→0. Total descriptor count alone is not a spool-leak measurement.
- NGINX refused both a declared 21 MiB envelope and a chunked envelope exceeding
  its existing 20 MiB setting. API readiness and a valid import recovered.

## CAP-INGRESS — next unblocked development step

**Subsequent update:** [pre-parser API controls and native qualification](CAP-INGRESS-BOUNDARIES-2026-10-18.md)
are now implemented, pending CI/review. This section preserves the original
measurement/prompt; aggregate qualification continues as CAP-CAPACITY and the
newly reproduced development-proxy error is SYS-10.

The same native measurement found **each entire 2 MiB file already spooled to
disk before its handler ran**, despite the 1 MiB file cap. Existing attachment
middleware bounds issued attachments only. An NGINX request ceiling is not a
direct-API ceiling, a purpose-specific file cap, a global disk budget or a limit
on replicas/concurrent native parsers. No giant allocation, OOM, filesystem-full
test, production traffic or minimum-hardware certification was attempted.

**Development prompt:** inventory every supported multipart/JSON intake and
deployment path; derive pre-parser envelope/part-count limits from the existing
single/batch/mail policies, preserving partial attachment outcomes. Reproduce
declared, missing-length, chunked, slow and disconnected requests; prove temporary
file cleanup before route entry, bounded aggregate memory/disk/concurrency and
visible recovery. Measure API replicas and parallel OCR/PDF worker processes on a
stated constrained profile. Include stored-object reads separately. Reconcile
valid batch sizes with the existing proxy ceiling using a reviewed contract;
never raise budgets or suppress failures to manufacture capacity evidence.

CAP-STORAGE, CAP-REVIEW-VERSION, historical rollback/browser findings, PII history,
production KEK restore and external/human acceptance remain in the register.

## Prior-head CI corrections

Head `ae6a8a5` hosted backend CI failed its matrix-completeness tripwire because
`capture_commands` lacked a tenancy-parity probe (3,749 passed, one failed,
109 skipped). A new real authenticated probe creates overlapping keys in both
tenants, proves each resolves only its own capture, and checks an A-only key is
an opaque 404 for B. No exemption or isolation assertion was weakened.

That head's browser job completed with **591 first-attempt passes and one retry
pass**, plus 13 visual passes. Its failed first attempt changed a mocked 503
lookup to 404 before the prior retry had settled, producing three observed POSTs
instead of two. The test now holds that lookup at a barrier, asserts pending
disabling/no POST, releases the 503 and waits for settlement before switching to
404. The same key/two-POST assertions remain. This repairs a demonstrated fixture
ordering race; it does not establish a production duplicate-admission defect or
resolve the five older SYS-09 timeout investigations.

## Review, alternatives and rollback

The proposal reuses the central reader and caps; a small API adapter owns HTTP
translation/closing, keeping framework exceptions out of services. Repeating ten
try/finally blocks adds drift risk. Relying only on upload metadata or the proxy
does not bound handler reads. Adding a new upload framework is unnecessary here.

Adversarial objection: a reader repair can hide unbounded earlier spooling and
parallel worker cost. The measured counterexample is retained as CAP-INGRESS;
the broader resource item is not declared complete. Closing bytes early could
break later readers: the changed routes consume the returned bytes and metadata,
and existing workflow/native tests validate those paths. I/O failures continue
through existing error handling; no false success or automatic write retry was
introduced.

There is no schema/dependency change. Rollback is a source revert/redeploy and
would restore the unsafe reads; it requires no object deletion or data rewrite.
**Lead decision: pending** on implementation, capacity scope and rollout. Keep
PR #64 draft and preserve the exact-head image/security and human-review gates.
