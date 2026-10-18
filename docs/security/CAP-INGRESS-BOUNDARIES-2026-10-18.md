# CAP-INGRESS — pre-parser request bounds and upload backpressure

AI-assisted implementation, test drafting and documentation; no external code
copied. API ingress controls are implemented, pending exact-head CI and human/Lead
review. Fleet capacity and the separately reproduced development-proxy defect
remain open in the [findings register](../reviews/FINDINGS-REGISTER-2026-10-17.md).

## Problem and proposal

The previous handler repair stopped reads at cap+1 but native tests showed whole
2 MiB files already spooled before a configured 1 MiB cap. Only issued attachments
had an earlier body guard. Against `ef2ce23`, the first 27 new ingress regressions
produced **25 failures and two passes**: the existing issued-attachment guard
already passed its two cases. No exhaustion or customer data was used.

The existing ASGI guard now covers all **13 documented multipart paths**, Mailgun
multipart and generic inbound JSON. It counts received wire bytes before passing
each chunk to Starlette. Excess declared length refuses without reading; missing,
incorrect or chunked lengths remain bounded by actual bytes. Single-file budgets
use their existing general/receipt/logo caps plus the existing 64 KiB envelope.
Batch and mail budgets derive from their existing 25/20 file counts; base64 JSON
pays its 4/3 wire expansion. Escaping and metadata also consume the envelope.

A lightweight observer uses the installed `python-multipart` header callbacks.
It buffers no file data and creates no temporary file. Before a chunk reaches the
real parser, it bounds aggregate headers to 64 KiB and counts file parts using the
same disposition parsing as Starlette. An excess file count returns 422; excessive
wire/header size returns 413. Earlier valid prefixes can already have spools when
a later streamed part violates the count. Those prefixes close; none enter the
upload handler. Invalid framing returns a sanitized 400. Accepted batches and
Mailgun retain per-file rejection/acceptance when inside the request envelope.

## Configuration and compatible browser behavior

| Setting | Default / behavior |
|---|---|
| `UPLOAD_MAX_BODY_MB` | Standalone API: unset derives the legacy file/count envelope. Compose/Kubernetes: **20 MiB**, matching the existing NGINX/ingress ceiling. It can tighten, never enlarge, the derived envelope. |
| `UPLOAD_MAX_ACTIVE_REQUESTS` | **2 per ASGI worker**, validated 1–64. Covers body parsing through inline validation/storage and response completion. |
| Saturation | Immediate **503 `upload_busy`**, `Retry-After: 1`, no-store; no waiting-body queue and no API spool for that request. |

The counter check/increment contains no await and runs on the worker's event
loop. `finally` releases it after success, refusal, exception, disconnect or
cancellation. GET/status/health requests do not consume upload slots. These are
process limits: multiply by workers and replicas for fleet admission. They do
not bound NGINX's earlier buffering or background OCR processes.

Six actual Compose combinations rendered with the intended 20 MiB/two-slot
defaults, including production/image and Hostinger/migration/image overlays.
Kubernetes inherits the same values through its existing ConfigMap. A regression
compares API deployment defaults with both origin configs and the ingress
annotation. No proxy/file/parser cap was raised. Operators changing a ceiling
must coordinate API and proxy settings; a source render is not a live deployment.

`GET /invoices/upload-policy` adds `max_batch_bytes` and serves a conservative
effective per-file cap. It reserves the envelope rather than advertising bytes
that cannot fit on the wire. The browser packs the selected files into sequential
requests under that budget, retaining each source's command key, override and
original outcome order, including same-name files. An unavailable/inconsistent
policy fails closed. Busy/size errors remain visible with unconfirmed outcomes;
lookup/retry preserves existing keys and committed prefixes.

| Action | Expected outcome / development contract |
|---|---|
| Upload selected files | Pack by served byte budget; prove a two-request split preserves source/key/order without duplicate admission. |
| Retry upload after busy | Show capacity guidance, check committed status and reuse the original key; prove no spool/job/usage on initial busy refusal. |
| Retry unconfirmed chunk | Preserve earlier confirmed files; retry only the missing source with its original key/override. |
| Check previous uploads | Remain available while upload slots are full; never count a missing result as permission to replace its key. |

Deploy API policy support before this browser. Older keyless/API clients remain
subject to wire ceilings and should send smaller batches after a whole-request
413. Partial attachment outcomes cannot be promised for an envelope refused
before parsing. File kinds, permissions, workspace identity, scanner, quota,
command/retry, storage and audit owners remain unchanged.

## Validation and native evidence

[Sanitized evidence](evidence/CAP-INGRESS-2026-10-18.json) records exact source,
image and log hashes. The first focused suite covers every route's declared and
chunked refusal, accepted/excess counts, malformed headers/framing, JSON bounds,
prefix/filename semantics, saturation, cancellation/read error/disconnect cleanup,
policy and configuration. An API-contract inventory fails on an unclassified new
multipart route. Real workflow suites retain accepted capture/email/receipt and
attachment behavior. No timeout/assertion/security gate was weakened.

Completed local checks: **219 related backend cases** (including all **48 ingress
cases**), **21 API/schema/ERD/docs/PG-list contracts**, and two capture-command
parity checks passed. **28 targeted browser cases** passed, including byte packing,
busy recovery, duplicate/batch/quota behavior, reconciliation and product flows.
Collection is **3,955 backend / 595 browser cases**; full final suites await
exact-head CI. Ruff/format (816 app/test files), mypy (421 source files), frontend
lint/build and unchanged bundle budgets passed: **435.8 kB raw / 127.6 kB gzip**,
limits 460/135. Action pins and the 15-control generated view pass. Current-tree
structural PII scan is clean; the empty owner denylist and historical PII remain
unresolved. Owned fixture containers, PostgreSQL database, network and volume were
removed.

The built SPA ran through the existing final NGINX origin, with current compiled
assets mounted read-only, against the newly built Alpine API image. PostgreSQL
16.15 used a migrated application role created NOSUPERUSER/NOBYPASSRLS. API and
CSV worker containers each used **1 CPU / 512 MiB**, read-only roots and dropped
capabilities; host resources were 4 CPUs/32 GiB. Temporary instrumentation recorded
spool/read counts, never bytes or credentials. This is not production TLS or a
Kubernetes deployment claim.

- At a 1 MiB wire cap, a real browser saw 413 and recovered with a valid import.
  Two held uploads occupied the two slots; a capture received 503, showed the
  busy message and created no API spool. Health/status stayed available. After
  release, retry reused the same command. Two 700,000-byte same-name sources were
  split into individual bounded requests. Exactly **three commands/captures/jobs**
  resulted; a real worker parsed all three and original bytes matched each source.
- Two concurrent unknown-length 2 MiB forms returned 413 before full spooling.
  All 32 observed API spools closed; the largest prefix was **1,015,808 bytes**,
  and no temporary descriptors or `/tmp` entries remained. An excess 26-part
  request created at most the allowed 25 prefix spools and entered no handler.
  Disconnect cleanup and a later valid import passed. The database contained
  exactly four successful statements/four lines and the three capture commands.
- In that profile API cgroup `memory.peak` was **167,624,704 bytes (159.9 MiB)**.
  This includes cgroup-accounted memory/cache, not process RSS.
- A second API used the deployed **15 MiB file / 20 MiB envelope** settings under
  the same 1 CPU/512 MiB constraint. Two concurrent near-20 MiB synthetic subject
  JSON bodies completed body/schema parsing and reached the expected fail-closed
  **401** authentication refusal; an over-limit stream returned 413. This does
  not test authorized base64 attachment decoding/delivery.
- Two concurrent 21 MiB forms each refused at the derived single-file envelope.
  Their real disk-rolled spool prefixes were **15,761,408 bytes each**, closed
  after refusal, with zero remaining temporary descriptors/files. That API's
  cgroup peak across JSON/form probes was **255,184,896 bytes (243.4 MiB)**; health
  recovered. NGINX retained its existing declared/chunked 20 MiB refusals.
- The fresh backend SBOM/HIGH+CRITICAL audit is clean and all five changed backend
  source hashes match the native image. The complete eight-target image gate
  remains blocked by the recorded build/vendor/service findings.

A final adversarial regression found that an empty boundary (`boundary=""`)
skipped the observer although the installed parser accepts that framing. The
initial safe two-file single-route probe returned 200; it now returns 422. The
observer tests presence rather than truthiness, matching the parser. The rebuilt
native API also refused 26 empty-boundary parts with zero spools/handler work.
The fresh image/audit and measurements above include this follow-up. No deployed
traffic, real exhaustion or customer data was used.

## Prior-head CI and canonical 413 control

Head `ef2ce23` hosted CI finished with **3,796 backend passes, one failure and
109 skips**. The failure was `test_http_413_symbol` requiring at least nine files
containing the new status constant; the ten-route shared adapter correctly
reduced that to four response-owner files. The global deprecated-symbol ban stays
unchanged. Its replacement checks all four response owners' ASTs and all ten
routes' forwarding to the canonical adapter, with a seeded deprecated/literal
replacement counterexample. The **93-case control/boundary sweep passes**. This
repairs a proven incorrect file-count assumption; no size, status or response
assertion was relaxed. Lead review of this control change remains pending.

That head also passed the required PostgreSQL gate, frontend/lint/PII/supply-chain
checks and **593 browser plus 13 visual cases without retry**. Its image gate
remained blocked. These are dated prior-head results, not final-candidate CI.

## Remaining findings and next steps

**SYS-10 — development proxy refusal truth, subsequently repaired:** the scan-time Vite
HTTP proxy intermittently reports `write EPIPE` and substitutes a generic 502 for
an early API 413 while writing a large body. Native default-proxy execution
reproduced it; the compiled SPA/NGINX path above passed the size/busy/retry checks.
No proxy exception was suppressed or API body drained merely to hide this.
The [subsequent development-only repair](SYS-10-DEV-UPLOAD-PROXY-2026-10-18.md)
uses an Expect handshake and separate upload connections, with real-Vite protocol/
TLS and native same-key recovery evidence; exact-head CI/human review remains pending.

**Prompt:** reproduce early 413/503 and upstream connection failure on a synthetic
server through the actual Vite proxy. Preserve upstream status/body when obtained,
keep truthful failure/recovery when unavailable, and verify uploads, downloads,
redirect/cookie behavior and streaming before selecting a supported proxy repair.
Do not weaken pre-parser refusal or infer a successful admission from transport.

**CAP-CAPACITY — aggregate qualification remains open:** API slots are not a
fleet-wide quota. Origin buffering occurs before upstream backpressure. Defaults
with multiple processes/replicas, authorized near-limit base64 decoding, parallel
OCR/PDF workers, stored-object reads, slow-client deadlines, shared temporary-disk
capacity/fullness and filesystem/power-loss behavior remain unqualified. A
standalone API without an explicit wire cap can still admit much larger derived
batch/JSON envelopes; that is compatibility, not modest-hardware certification.

That paragraph records the ingress baseline. The [subsequent capacity ingress
repair](CAP-CAPACITY-INGRESS-2026-10-18.md) adds body-only deadlines, shared origin
admission and streaming. A four-worker fixture now exercises two authorized
near-envelope JSON deliveries, including real decode/storage; it reaches 718.2 MiB
under 768 MiB, with limited headroom. Fleet/controller, parallel native-worker,
object-read and disk-full/host qualification remain open. A shared metadata race
found during that test is CAP-REGISTRY; its separate transaction repair is linked
there. No general hardware or delivery-replay guarantee follows from the fixture.

**Prompt:** measure the actual multi-process/pod profile and proxy buffering,
introduce justified shared/host and slow-body budgets, then prove saturation,
timeout, disconnect, disk-full recovery and parallel native-worker/object-read
behavior. Keep existing supported single/batch/email contracts, durable command
recovery and all parser/security budgets. No automatic garbage collection or
unapproved retention decision is part of resource cleanup.

## Alternatives, adversarial review and rollback

A proxy-only cap leaves direct API access unbounded. A post-read check is too
late. Enforcing the file cap as a whole-batch cap would discard partial outcomes.
The shared wire budget and header-only observer avoid a new upload framework,
request-body copy or disk buffer; the normal parser retains ownership of files.
The tradeoff is parsing multipart framing twice. Native measurements exercise
this implementation; broader throughput/latency remains CAP-CAPACITY.

Adversarial objection: a finite per-worker envelope can still overwhelm a host
after multiplication, and slow clients can occupy every slot. Correct: this
repair bounds admission and makes refusal/recovery explicit; it does not close
the aggregate finding. Immediate 503 also affects other tenants sharing a busy
worker. Fairness, deadlines and scaling need measured capacity work.

No schema/dependency change. Roll back the browser before an API without the new
policy field; revert/redeploy ingress code/config without deleting stored objects
or rewriting commands. Preserve prior capture generation/receipt migrations for
application rollback. **Lead decision: pending** on limits, compatibility, capacity
scope and rollout. Keep draft PR #64 and existing release gates in place.
