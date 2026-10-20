# CAP-COMMAND — durable per-file capture admission

AI-assisted implementation/test drafting; no external implementation copied.
Implemented on the development branch, pending exact-head CI and human/lead
review. This does not clear the image, historical PII, production restore or
external acceptance blockers in the [findings register](../reviews/FINDINGS-REGISTER-2026-10-17.md).

## Problem, proposal and operation grain

Atomic admission did not identify a caller's request. When the server committed
but its response was lost, another upload hit the duplicate warning or required
an override; neither recovered the original admission. Seven PostgreSQL cases
failed before this repair, including full-quota replay, concurrent replay,
changed-input refusal and recovery of a committed batch prefix.

The operation grain is one **per-file admission command**, scoped to its workspace.
A command binds an opaque key to version-1 SHA-256 fingerprinting of the source
hash, exact submitted filename/MIME and explicit duplicate-override option. The
capture, command receipt, source registry, keyed native job and usage increment
commit in the same per-file transaction. The existing workspace lock serializes
admission. Database uniqueness enforces one workspace/key and at most one capture
for each command; a composite FK prevents a capture using another tenant's key.

Receipts are parents of captures, so purging a capture leaves a durable retired
receipt. A purge cannot make a previously committed key admit another upload.
Workspace deletion retains its existing cascade semantics. There is no invented
receipt TTL, garbage-collection policy or historical backfill.

## API and compatibility

- `POST /invoices/upload` accepts an optional `Idempotency-Key` (8–120 safe ASCII
  characters). Existing keyless clients keep their previous behavior; they do
  **not** gain replay protection. The current browser always supplies keys.
- `POST /invoices/upload/batch` accepts `command_keys`, a bounded JSON array in
  multipart form data, with one distinct valid key for each file in request
  order. Validate the whole key envelope before admitting a file. Names may be
  identical; ordinal source/key pairing is authoritative.
- Read only the bounded original bytes, then revalidate the pinned identity and
  check for a committed payload match **before quota, scanning or storage**.
  Check again after taking the workspace lock, before its fresh quota decision.
  Replay therefore returns the original capture even after it used the last
  quota slot. It creates no additional job, registry entry or usage increment.
- Same key with changed bytes, filename, MIME or override refuses with 409
  `command_payload_conflict`. A retired capture refuses with 409
  `capture_command_retired`. Deliberate new uploads use new keys and the existing
  duplicate advisory/explicit override; replay never implies override consent.
- `GET /invoices/upload-commands/{key}` returns the committed capture ID and its
  **current** state, or `{extraction_run_id:null,status:"retired"}`. It is scoped
  and revalidates the original caller after the database read. Responses are
  private/no-store. Unknown/foreign commands have an opaque 404; revoked access
  refuses. A 404 can race an in-flight commit: it permits replay of the **same
  key**, never automatic replacement with a new one.
- `GET /invoices/upload-policy` serves the existing configured file cap and batch
  size. New paths precede older dynamic invoice routes, including valid command
  keys such as `extraction`. No upload, scanner or resource budget was raised.
- Existing extraction retry generations remain independent: admission replay
  finds the same capture; it does not start another extraction generation.

The checked-in OpenAPI change is additive: optional header/form field, two GET
paths and a lookup result schema. The ERD gains one receipt table and the nullable
capture reference. Generated artifacts and the 113-table count tripwire change
for that semantic reason; these baselines remain subject to human review.

## Browser actions and recovery

The browser keeps only keys, filenames and source fingerprints in session storage,
scoped to account **and** workspace. It never saves file bytes. Sequential hashing
first checks the served per-file limit. An unavailable policy or unavailable
recovery storage blocks a new POST with visible recovery guidance. An account or
workspace transition cancels the old operation before another request.

| Action | Complete result and failure/recovery contract | Follow-up verification prompt |
|---|---|---|
| Upload | Retain every source's key before POST; show per-file outcomes or enter the existing review page for one confirmed file. On response loss, check each command. | Simulate response loss after a real server commit; prove one admission/job/usage and a usable review page. |
| Check previous uploads | Read retained keys after refresh without needing file bytes. Return Review for committed captures, visible retired guidance, or Unconfirmed while lookup fails/404. Never POST from this action. | Refresh after partial batch commit; prove account/workspace isolation and no implicit resubmission. |
| Retry upload | If outcome is uncertain, check first; unavailable lookup blocks a POST. A 404 resends the original source with its existing key. Known storage refusal may start a fresh command. | Drop two same-name files with different bytes; fail one result and prove retry pairs the correct bytes/key. |
| Upload anyway | Only a known duplicate refusal exposes this existing explicit override. It starts a deliberate new command with override bound in its fingerprint. | Prove uncertain requests never present duplicate override as recovery and changed override cannot reuse a committed key. |
| Retry upload limit | Re-read the authoritative cap and enable upload only after a valid result. | Fail/restore the policy request; prove no hashing/POST starts without a known cap. |
| Review | Navigate using the server's committed capture ID; extraction failure/review/save continue through the existing capture page. | Drain a real persisted worker and verify source bytes, current parse state and exact provenance count. |

Changed file bytes cannot silently replace an unresolved request. Reselection
reuses the previously recorded override option; a page refresh cannot reset it.
Pending sources with different recorded options are submitted in separate groups,
retaining per-file keys and original result order. The
original file can be reselected by fingerprint after refresh. Result order and
same-name source identity are preserved. Pending controls prevent repeated clicks;
known quota refusals retain their upgrade guidance. A server error can follow a
committed batch prefix, so the client checks every submitted key rather than
assuming the whole request failed. Unconfirmed outcomes never say Not accepted.

## Evidence and practical limits

Detailed outcomes, source/image identities and log hashes are in the
[repair evidence](../security/evidence/CAP-COMMAND-2026-10-18.json). PostgreSQL tests
use separate sessions and a migrated NOSUPERUSER/NOBYPASSRLS role, including raw
RLS and composite-FK/uniqueness checks. Browser interception tests prove UI
behavior only; the separate native browser/API/worker run must prove real commits,
response loss, source bytes, provenance and metering.

Validation completed:

- **126 required PostgreSQL checks passed**, including **18 new command cases**
  (193 existing synthetic-key warnings). Seven of eight initial cases failed
  against `7e747d0` before implementation; the rollback case already passed.
- **87 related backend checks passed, two PG-only checks skipped** in that SQLite
  run (their PostgreSQL execution passed separately); 21 API/schema/authz/docs
  contract checks passed. Collection is 3,859 tests. Full final backend execution
  is delegated to exact-head CI, not claimed as a local full-suite pass.
- **591 browser cases ran: 586 passed, five timed out.** All nine new command
  cases passed. The five failures passed unchanged in a one-worker isolation run
  (12.3 s): access/dunning/invoice-review failure states, delayed old-account 401
  setup, and bulk-delete undo-path setup. No assertion, timeout, retry or budget
  was weakened. Their repeatability cause is unestablished and logged as SYS-09;
  this is not a green single full-suite result. A later targeted run passed
  **23 cases**, including all **10 command cases**. An added override/refresh
  regression failed first, then passed after retaining the original override
  option when the source is reselected; no new key or consent is inferred.
- **Native browser/API/workers passed** in Alpine image `3ee582ace422`: lost
  committed responses, partial lookup failure on a same-name batch, refresh,
  Check previous uploads and reselection of an explicitly authorized duplicate.
  Five browser POST attempts (four reached the API; one was stopped before I/O)
  yielded four commands/captures/jobs, three distinct source records and usage
  four. The fourth admission was the deliberate duplicate with retained override,
  not accidental replay. All parses have exact provenance and original download
  bytes match. UID/GID 10001, read-only root, dropped capabilities and
  nonprivileged PG16.15. The browser used a local Vite proxy; this is not deployed
  frontend acceptance.
- The rebuilt backend SBOM/HIGH+CRITICAL audit is clean; all eight changed runtime
  and migration source hashes match that image. Migration upgrade/new-revision
  roundtrip and populated downgrade refusal pass. A synthetic dump/restore matched
  row counts and ordered data hashes for all 114 tables, retaining retired
  receipts; no production key/document-volume restoration is implied.
- Ruff/format (813 app/test files), mypy (420 source files), frontend lint/build,
  action pins and the 15-control generated view pass. First load is 435.8 kB raw /
  127.6 kB gzip within unchanged 460/135 limits. Structural current-tree PII is
  clean with an empty owner denylist; history is not cleared. Owned database,
  API/worker containers, network, volume and Vite fixture server were removed.

No dependency/framework was added. There is no new financial posting, document
retention duration, cross-currency rule, scanner exception or outbound processor.
This repair does not establish global ingress/spooling/memory/concurrency ceilings
(CAP-RESOURCE), object lifecycle/production restore (CAP-STORAGE), or a generation
precondition on a stale completed review form (CAP-REVIEW-VERSION).

## Adversarial review and rollout

**Alternatives:** a source hash cannot distinguish deliberate same-file admissions
or bind filename/options; a key stored only on the capture disappears on purge;
a batch-level key loses per-file partial recovery; an in-memory/browser-only key
has no committed server lookup. A standalone command parent adds one table and
one optional reference, avoiding a new queue or pretending the transaction covers
external object storage.

**Objection/risk:** hashing uses a file-sized browser buffer and replay still
transfers/reads bounded bytes; the command is not a global resource limiter.
A queued lookup may return 404 before commit, so same-key retry remains mandatory.
Session storage belongs to the tab and may be cleared/unavailable. If the user
loses all recovery metadata, use Captures and the existing duplicate advisory;
this is not a cross-device upload-history protocol. Receipt cleanup needs an
explicit retention/reference policy. Storage rollback can still orphan a shared
blob; never delete that blob as request cleanup. The native job framework's
underlying lease behavior is unchanged.

**Lead decision:** pending review of receipt lifecycle, tenant/FK guarantees,
transaction ordering, generated schema semantics and browser recovery. AI-authored
proof is not self-approval under the [engineering policy](../architecture/AI-ENGINEERING-POLICY.md).

Quiesce upload writers, back up database/documents, apply additive migration
`238cd0be715f`, then deploy compatible API before the browser. Legacy captures keep
NULL references; no command is invented for them. Retain additive schema for
application rollback; roll the browser back before an older API that ignores
command identities. Mixed versions cannot provide this recovery contract.
Schema downgrade refuses while any receipt exists,
including retired receipts. Do not erase receipts to make a downgrade pass.
Historical full PostgreSQL downgrade remains separately blocked by SYS-08.

## Next prompt

> Continue CAP-RESOURCE: measure proxy/API multipart spooling, bounded object reads
> and parallel native-parser use under a stated hardware profile. Reproduce the
> eleven logged unbounded upload reads and failure/recovery; implement only missing
> evidenced limits with visible UI recovery, preserving all document workflows,
> command identity, tenant isolation and current safety budgets.
