# Selected-region OCR review delivery

## Problem and resulting workflow

The user reported that only one supplier-name line was correctly read and
approved visual selection and rereading. This change adds a selected-area
recovery workflow to the existing capture review page. It does not establish
accuracy on the customer's invoice, which has not been supplied here.

For a parsed capture that has not created an invoice:

1. Save any current review edits, select a previewed page and rotate the view
   if needed. Draw a rectangle around a header value, line cell or table.
2. Request a read. A persisted job uses the original source bytes, not the
   reduced preview image. A PDF region is rendered at 300 DPI; image regions use
   the EXIF-normalized original image pixels.
3. Inspect raw OCR text, actual engine/settings and proposed values alongside
   current values. No proposal is selected automatically.
4. Select exact fields/cells to apply. Map table proposals to existing rows or
   explicitly append rows. Existing rows are never deleted as a side effect.
   Overwriting a saved manual correction requires explicit acknowledgment.
5. Load the authoritative review and confirm it using its saved fingerprint.
   A stale tab preserves its local values and offers **Reload saved review**
   before another write is allowed.

## Action contracts and development prompts

| Action | Description / completion contract | Prompt for future changes |
| --- | --- | --- |
| Select area | Rectangle coordinates refer to the page after clockwise rotation; the native worker uses the same coordinate convention. Original bytes are untouched. | OCR-REGION-01: Preserve normalized rotated coordinates and original-source geometry across preview, request and native rendering. Qualify EXIF, fractional-size PDF pages, edge crops and rotations with real rendering. |
| Read selected area | Persist one source/generation-bound command and job before native I/O. Show queued/running/ready/failed/stale states and raw text. | OCR-REGION-02: Retain durable command identity, payload binding, command lookup, source/attempt fences and bounded worker admission. Never turn a lost response into a second automatic read. |
| Refresh comparison | Fetch current saved review values and fingerprint without starting work. | OCR-REGION-03: Refresh comparison from the server, preserve unsaved local edits and refuse stale apply. Do not report a failed refresh as an empty or unchanged result. |
| Apply selected changes | Apply only selected fields/cells, preserve original provenance and unrelated corrections, and commit audit/receipt atomically. | OCR-REGION-04: Keep explicit row mapping/append, reviewed-value acknowledgment, current-authority rechecks, replay safety and rollback controls. Never replace a whole table implicitly. |
| Check uncertain outcome | Resolve a stable read/apply command before retrying or announcing success. | OCR-REGION-05: Preserve committed-status lookup and separate an uncertain write from failed readback. Verify invoice confirmation against authoritative server state. |
| Save or confirm review | Bind the submitted values to the saved review loaded by this tab; reject stale edits before side effects. | OCR-CONFIRM-01: Preserve the frozen base fingerprint, exact post-save response token, canonical draft comparison and post-lock authority refresh. Qualify concurrent review/apply, offboarding and organization switching. |

## API and persistence

All new routes are below `/invoices/captures/{run_id}/region-reads`:

| Method / suffix | Result |
| --- | --- |
| `GET /` | Bounded recent metadata and one authoritative source/review envelope. |
| `POST /` | `202`, a stable read command and persisted `capture.region_ocr` job. |
| `GET /by-command/{command_key}` | Actor-scoped recovery lookup after an uncertain request. |
| `GET /{read_id}` | Full result plus current comparison/fingerprint. |
| `POST /{read_id}/apply` | `200`, a durable apply receipt and effective draft. |

Read creation binds a client UUID command key to a canonical payload hash,
source SHA-256 and extraction attempt sequence. Apply has its own stable UUID,
payload binding and expected review fingerprint. Reusing a key with another
payload or applying against changed review/source state returns `409`. Missing
or foreign resources use opaque `404`; admission exhaustion returns `429`.
Malformed path UUIDs return `422`, never a database type error.

Migration `d4e6f8a01223`, parent `c129fa806b32`, adds tenant-scoped
`capture_region_reads`, composite capture ownership, command uniqueness and
forced PostgreSQL row-level security. The table participates in tenant
registration and workspace export; the worker lease token is redacted from
exports. Stop old workers/writers during migration and rollout. A rollback
must retain the additive table once an apply exists; downgrade deliberately
refuses to delete applied evidence.

This branch is based directly on OCR/navigation PR #75. Work-order planning
PR #76 introduces a sibling migration from the same parent. Integrating both
requires an explicit combined migration graph and combined qualification;
neither feature branch alone qualifies that integration.

The capture and region record lock order is consistent. Create/apply refresh
session, membership and permission after acquiring locks. The worker checks
current owner authority, source and attempt both before and after native I/O.
An original source read, a changed review or an offboarded account cannot
publish stale OCR changes. Original draft and field values remain evidence;
chosen changes enter reviewed values. Newly appended missing cells are not
falsely labeled extracted.

Parallel applications exposed a pre-existing vendor correction-memory
SELECT/INSERT race. PostgreSQL and SQLite now use an atomic upsert/increment
for observation and correction counters. Per-command learning is once per
changed field; supplier bank instructions remain excluded. Deterministic field
ordering avoids a reversed multi-row lock order.

The admission lock also needed repair: organization `FOR UPDATE` followed by
the capture lock could deadlock with an append, manual review or worker result
holding the capture lock while inserting an organization-owned child. Admission
now uses `FOR NO KEY UPDATE`, preserving serialization while allowing the
foreign-key key-share checks. Three actual PostgreSQL failure-first controls
cover all three interleavings (OCR-LOCK-01).

### Saved-review and final-confirm consistency

Manual review accepts `expected_review_fingerprint`; capture-linked invoice
creation accepts `capture_review_fingerprint`. Once a region result has been
applied, omitting either required base is refused. A supplied base is always
checked, including legacy captures. Clients without region applications retain
the prior optional-token contract for staged compatibility.

The fingerprint is checked under the same capture lock as review/apply. Final
confirmation also compares captured headers and complete line values with the
authoritative saved draft: attaching a fresh token to old local values is
refused. Decimal-equivalent values compare equally; notes and cost dimensions
retain their existing behavior. Errors distinguish missing review identity,
changed review and changed draft (`409`) before invoice/vendor/audit effects.

A successful review response returns `X-Capture-Review-Fingerprint` for its
exact saved state; CORS exposes it to allowed clients. The browser advances its
base from that response, rather than fetching a newer token to attach to older
values. Conflicts preserve local edits and disable writes until explicit saved
review reload. Unknown save/readback states retain read-only recovery. A held
save cannot continue into invoice creation after navigation changes the route.

Direct and inbound confirmations revalidate their existing session, membership,
organization and permission policy after waiting for the capture lock and
before resolving a vendor. Actual blocked-confirm controls reproduce and
refuse both offboarding and a same-session organization switch. This closes
OCR-CONFIRM-01 for region-aware clients; it does not claim mandatory optimistic
concurrency for every historical client.

## Bounded behavior and limits

The first version supports English only and the first six previewed pages.
The actual worker validates installed language data. It records the actual
engine version, segmentation mode, language, crop geometry and PDF render DPI.
It does not display an invented accuracy percentage.

Admission permits two active reads per organization, thirty reads per hour,
and forty per capture. History returns at most twenty metadata entries without
duplicating invoice drafts or raw results. Native recognition has a 45-second
deadline, 30-second CPU budget and isolated process/resource limits. Central
byte policy caps memory at 512 MiB, temporary native files at 48 MiB, transport
at 256 KiB, text at 64 KiB and persisted result at 128 KiB. Existing original
file and page-pixel limits still apply. These are enforced bounds, not measured
production capacity.

Table proposals reuse the existing conservative parser and require review of
column roles, amounts, category and tax defaults. Ambiguous header values
remain raw text instead of becoming guessed date, supplier or currency values.
The preview is a selection aid; the original-quality read may still require
tighter cropping or a manual correction.

## Verification and limits of evidence

The preceding full backend run passed 4,524 tests with 176 skips before the
final confirmation/lock repairs. Its receipt binds that earlier source; it is
not full-suite qualification of the final guard changes. Final focused
backend, PostgreSQL, browser, production-runtime and connected-journey counts
are recorded in the [delivery receipt](OCR-REGION-DELIVERY-2026-10-21.json). The native renderer cohort includes
actual Tesseract, rotated images and fractional-size A4 PDF geometry.

At the final product bytes, 93 focused backend cases, 24 actual PostgreSQL
cases and 44 host renderer cases pass. The final Python 3.14.8 Alpine backend
image passes all 66 renderer/region/confirmation cases with Tesseract 5.5.2,
UID 10001, read-only filesystem, network disabled and a one-CPU/1-GiB limit.
Its three original-quality PDF/PNG/rotated-region reads and a blank-region
control pass without source mutation. A synthetic native fixture observed
195.7 MiB cgroup peak; this is not a deployment capacity benchmark.

The complete functional browser suite passes 747 cases with no retries,
skips or flakes. Final frontend lint/build and the unchanged bundle gate pass
at 435.0 kB raw / 128.4 kB gzip. OpenAPI, documentation, migration, authorization,
tenant and central-cap checks pass 145 cases; compiler/resource governance
passes 79 cases. These populations overlap with other focused checks.

All thirteen local visual pixel checks differ from the stored CI baselines.
Running the same unchanged checks against the unchanged PR #75 parent produces
byte-identical actual PNGs in all thirteen cases (zero different pixels between
parent and candidate). Baselines, tolerance, visual specs, lockfile and parent
dependencies are unchanged. This isolates an inherited host/baseline mismatch;
it is not a local visual pass. The hosted visual check remains required.

Two earlier constrained-image runs exposed a 0.3-second synthetic child-start
deadline in the process-cleanup test. That test-only deadline is now two
seconds; timeout/descendant cleanup assertions remain, as does the production
45-second budget. Earlier failed runs are preserved in the runtime receipt.

PostgreSQL qualification uses a non-superuser, non-bypass-RLS role and covers
parallel commands/applications, source/attempt fences, current authority,
rollback, raw tenant isolation, correction memory, workspace-export redaction,
lock order and final-confirm consistency. Failure-first logs are retained for
the correction-memory race, admission deadlocks and stale-authority confirms.

A separate connected synthetic browser/API/PostgreSQL/worker/native-OCR
journey selected an invoice number from an image-only PDF, applied it, retained
an unselected saved supplier correction, verified unchanged original SHA-256,
resolved command replays, and created the invoice (`201`). A repeated create
was refused (`409`). A second actual journey rotated a table region by 90
degrees, appended one explicitly selected row from four to five, and verified
that replay did not append again. Its baseline whole-document extraction had
two spurious rows, retained as finding OCR-TABLE-01; no invoice was created from
that table fixture. Detailed source hashes, logs and database checks are in
`/workspace/artifacts/ocr-region-2026-10-21/`; this local path is task evidence,
not a production application dependency.

Hosted CI, the Hostinger rollout and the customer's affected invoice remain
separate acceptance steps. Extra languages,
supplier templates, full-page/mixed-page retry policies, better table-column
classification and optional Paddle/VLM benchmarks remain in the OCR backlog.

### Build-tool security follow-up

A fresh scan of the parallel planning candidate found CVE-2026-111943 /
GO-2026-6809 in inherited Go 1.27.2 binaries. This branch includes the same
narrow Go 1.27.3 digest pin in frontend/PostgreSQL builds and the native
TypeScript compiler installer. The compiler is built from the same TypeScript
7.0.2 revision, validated with the existing provenance controls. Scanner gates
and severity policy are unchanged. Four affected planning image targets were
rebuilt and scanned clean against the current feed; hosted scanning of this
OCR candidate remains required. No new application dependency is introduced.
