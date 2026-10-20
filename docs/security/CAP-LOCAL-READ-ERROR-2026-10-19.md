# Explain local storage failures and preserve complete export recovery

**19 October 2026 · CAP-LOCAL-READ-ERROR · implemented, pending new-head CI and Lead review.**
AI-assisted investigation, implementation, tests and documentation. Independent
agents reviewed storage/caller contracts and final source, implemented export
regressions and qualified PostgreSQL. No external implementation copied.
[Sanitized evidence](evidence/CAP-LOCAL-READ-ERROR-2026-10-19.json).

## Failure and repair

`LocalStorage.get` translated only `FileNotFoundError`. Other local filesystem
open/read/close failures escaped the application's storage recovery contract.
The initial real-file HTTP regressions have **seven failures / four passes**:
permission, read and close faults return generic 500 responses. The adapter
regression has **27 failures / 12 passes**, including a read-time missing error
being classified as an absent object and secondary close errors hiding the primary
refusal/interruption. Faults are injected around actual local descriptors; a real
directory occupying an object key also produces a genuine non-missing OS error.
These are synthetic faults, not a physical-disk or provider certification.

The local adapter now treats an absent object only at open/lookup. Non-missing
open failures and read/close `OSError` become the existing `StorageReadError`
with static text. An acquired source gets one close attempt. A secondary close
`OSError` cannot replace an existing oversize refusal, read error, cancellation
or programming exception. A close failure after otherwise successful reading
refuses the entire result. Original causes stay available internally; public
messages exclude keys, paths and private diagnostics. Unrelated programming
exceptions remain exceptions rather than being disguised as I/O errors.

The HTTP boundary maps an otherwise unhandled `StorageReadError` to **503** with
`code=storage_read_unavailable`, retry guidance and `Cache-Control: private,
no-store`. Existing request IDs remain. Capture-source/page and fuel-statement
handlers rethrow this subtype before their genuine-missing 404 branches. Issued
attachments retain their existing sanitized 503 handling. Actual missing objects
retain their prior response contracts; successful responses retain original bytes
and inert headers. No partial download is returned.

Shared subtype handling must not turn a local read failure into an absent file:
workspace/archive builds rethrow it before the permitted missing-document manifest
branch. The tested first failed worker attempt stays queued for retry, with no ready ZIP or mailed link
published; restoration and the real retry path produce the complete ZIP. Export
downloads propagate the failure before successful audit/commit, preserving the
one-time token and reference. The same link downloads once after restoration;
subsequent use is refused. Claim packs retain complete-bundle 409 refusal with
unavailable/retry guidance; actual missing evidence retains its existing code and
wording. Existing invoice-delivery failure handling was already fail-closed.

Two existing storage/page file observers now expose the standard file `close()`
method. Their byte-budget, consumed-byte and closure assertions are preserved.
No schema, dependency, authorization, financial calculation, admission limit or
retention policy is changed.

## Validation

- Adapter **39 passed**, against the 27-failure baseline.
- Final HTTP/storage/digest/integrity selection **98 passed / 53 existing test-key warnings**; preceding attachment/preview selection **33 passed / 69 warnings**.
- Export/claim-pack controls **59 passed / 64 warnings**. After strengthening response/cache/tempfile assertions, all **eight final new cases passed / 13 warnings**; production code was unchanged between runs.
- Boundary/file-security/foundation/authorization/invoice-command controls **56 passed / 32 warnings**.
- All **59 new cases pass on shipped Python 3.14**, zero skips.
- Required fresh PostgreSQL **140 passed / zero skips / 221 test-key warnings**.
- Full Ruff/format (**831 files**) and Mypy (**421 source files**) pass; docs truth **seven passed**.
- Backend collection **4,181 cases**, not a claim of 4,181 executed passes.
- Rebuilt final API SBOM/HIGH/CRITICAL audit **zero advisory rows**. Full build/service gate remains separate and blocked.

The native test run uses the rebuilt shipped Python 3.14 image, actual ASGI HTTP
routes/local files and synthetic SQLite databases. It runs as image user UID10001,
with one CPU/384 MiB, a read-only root, all capabilities dropped and no network.
Test fixtures are copied into an owned volume mounted read-only. All **421**
application files match the checkout, and the selected test sources match too.
This is not live TCP/native PostgreSQL deployment acceptance, real S3/provider
qualification, peak-memory measurement or a minimum-hardware claim.

PostgreSQL 16.15 uses an actual NOSUPERUSER/NOBYPASSRLS app role. Single-head
migration/upgrade and all required tests pass, with stable source hashes and
owned-resource cleanup. Optional `alembic check` retains already registered
**SYS-06** column-subset SET NULL reflection drift (exit255); required steps and
the helper's required-check outcome pass. Unchanged performance-growth and
informational concurrency checks are explicitly not rerun. Tenant-preserving
constraints are unchanged.

An initial validation command selected nonexistent `test_errors.py`, ran zero
tests and exited4. It is retained as uncredited; the corrected real control
selection passed. The export agent's preliminary log named "before" was already
passing with the repaired adapter loaded; it is not claimed as a baseline.
The actual negative controls are the adapter and HTTP baseline runs above.
Owned containers/volumes and temporary credential files are removed.

## CI, review and remaining work

Predecessor **f27b8488ba32c580daf3577988122f7226db91e4**, [CI
39220231379](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39220231379),
completed backend/frontend/browser/PostgreSQL/lint/PII successfully; supply-chain
**39220231368** passed. Its expanded image audit still failed and deployment was
skipped. Artifact **12171184168** confirms clean final API/origin, backend builder
**4 HIGH**, frontend builder **18 HIGH**, PostgreSQL **24 HIGH + 1 CRITICAL**, and
three unavailable PgBouncer/MinIO references. All **47 advisory/package/version
rows** exactly match the preceding dated evidence, with all ten report hashes
verified. This is predecessor evidence; new-head CI remains pending.

Proposal: use the existing storage subtype and a static HTTP boundary, preserving
missing lookup and export transaction behavior. A blanket broad catch would hide
programming errors; normalizing local failures without auditing callers would
make exports treat them as absent. Independent review checked subtype ordering,
primary error/cleanup precedence, no partial output, missing compatibility,
restoration, tenant checks and token/audit rollback. **Lead review remains pending**
for these semantics and the exact new-head checks. No merge or deployment.

This repair is specific to local `get` failures. Separate source gaps are logged:
**CAP-DIGEST-ERROR-CLASS** (post-open missing and cleanup precedence) and
**CAP-S3-READ-ERROR-CLASS** (provider/read errors flattened into missing). Their
HTTP/job reproduction and repair remain pending; real-provider acceptance is
separate. Do not generalize this local qualification to those paths.

The **15 uncapped original/download/render/delivery/export calls** remain. Reads
still buffer admitted payloads; this error-contract change does not add a memory
ceiling, timeout, hardware guarantee or global disk/fleet bound. Full image-gate
qualification, restore/history/provider acceptance, SYS-06 and human release
review remain open. Earlier dated inventories/evidence are preserved.

Rollback storage normalization, caller subtype branches and the HTTP handler as
a group; otherwise local read errors could be collapsed into missing/partial
exports. There is no data or financial migration. Rollback restores the generic
I/O-error behavior; the preceding attachment/preview read budgets remain separate.
