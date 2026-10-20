# CAP-DISK-FULL — truthful upload staging failures and capture recovery

**18 October 2026 · implemented, pending exact-head CI and human/Lead review.**
Part of CAP-CAPACITY; aggregate resource qualification remains open.
AI-assisted investigation, implementation, tests and documentation.

## Problem and repair

A real multipart spool write on an exhausted filesystem raised `ENOSPC`, but
FastAPI wrapped it as HTTP 400, `There was an error parsing the body`. Users could
not distinguish unavailable server storage from a malformed file. Starlette
already closed the partial spool; no upload command or parsing job was admitted.

The upload admission middleware now marks its classified POST requests internally.
The HTTP exception handler converts only FastAPI's generic parsing 400 with a
**direct OSError cause of ENOSPC or EDQUOT** on those requests to:

- HTTP **503**, stable code `upload_storage_full`;
- `Cache-Control: no-store`, `Retry-After: 1`;
- guidance to check upload status before retrying and ask the administrator to
  restore capacity if the problem continues.

All other HTTP exceptions delegate to FastAPI's handler. Authentication challenges,
ordinary malformed multipart, unrelated I/O faults, explicit route errors and
unclassified forms retain their contracts. Diagnostics and private paths are not
included. The SPA displays the new capacity message, checks command status and
retains the original per-file key for retry. It does not resubmit automatically.

Supplier capture object-write failures retain their existing `storage_unavailable`
outcome, transaction rollback and per-file batch reporting. No shared object is
removed in request cleanup. No schema, dependency, resource limit, retention or
storage initialization policy changed.

## Evidence

[Sanitized results and source/artifact hashes](evidence/CAP-DISK-FULL-2026-10-18.json).
The corrected pre-fix regression run produced **6 failures / 7 passes**. An initial
single-file fixture mistakenly supplied two files and hit the existing count cap;
that harness error was corrected before the credited baseline. A first command
used the wrong working-directory path and collected no tests; it is not evidence.

| Check | Result |
| --- | --- |
| Related backend checks, including 15 new staging/error controls | **112 passed / 12 warnings** |
| Shipped Python 3.14.8 image: staging and local atomic-write controls | **17 passed** |
| Capture-command browser regressions | **14 passed**, one Chromium worker |
| Ruff / format / mypy | Passed; **421** application source files type checked |
| Frontend lint / build / bundle | Passed; **435.8 kB raw / 127.6 kB gzip**, unchanged 460 / 135 limits |
| Collection only, not execution | **3,998 backend; 597 browser in 65 files** |
| Fresh final API/origin image SBOM and HIGH/CRITICAL audits | Both clean, **zero** advisory rows |

The browser file was run directly with `npx playwright test`; an earlier npm
invocation expanded the repository's full test list and was intentionally
interrupted. Its partial results are not counted as a completed suite. No gate or
assertion was weakened. The source-only handler change needs no migration.

### Native bounded-filesystem qualification

Baseline and repaired runs used the shipped API, real PostgreSQL **16.15**, a fresh
migrated database, an application role with **NOSUPERUSER/NOBYPASSRLS**, local object
storage, and synthetic CSV bytes. The API ran **one Uvicorn worker, 1 CPU, 768 MiB**,
UID 10001, read-only root, dropped capabilities and no-new-privileges. `/tmp` and
object storage were separate **4 MiB tmpfs** mounts; these deliberately small test
mounts are fault fixtures, not proposed deployment sizes.

An owned filler left **256 KiB** available on the target mount. A **1,242,083-byte**
valid CSV crossed Starlette's real 1 MiB spool threshold. No parser or storage
write was replaced with a synthetic failure in these runs; read-only fixture
instrumentation recorded actual errno and handle closure.

- Baseline real spool **ENOSPC → 400**; repaired real spool **ENOSPC → 503** with
  the new JSON/header contract. Command lookup was 404 and readiness stayed 200.
  Removing only the fixture's filler allowed the **same command and bytes → 202**;
  repeating it returned the same capture.
- Separately exhausting the object mount produced real **ENOSPC** and the existing
  batch `storage_unavailable` outcome, **0 accepted / 1 rejected**, with lookup 404.
  There were **zero `.upload-*.tmp`** files after the failed write. The earlier
  accepted original still matched its SHA-256 after capacity restoration, and the
  failed file's original key then admitted one capture.
- Real Chromium used the compiled SPA through actual NGINX: visible storage-full
  guidance and **0 of 1 confirmed**, then a manual retry after fixture capacity
  restoration. Wire statuses were **[503, 202]**, both requests used the same key,
  and committed-status lookup matched the capture opened by the UI. No API route
  mocks or synthetic success responses were used.
- Final database totals were exactly **3 commands / 3 captures / 3 extraction
  jobs**. **7 native spools opened / 7 closed**; instrumentation observed two real
  staging ENOSPC faults and one real object-write ENOSPC fault. All three captures
  were queued; this run did **not** execute parsing/OCR workers.
- API cgroup peak was **171,114,496 bytes (163.1875 MiB)**, with memory max/oom/
  oom_kill events all zero. This is one fault-recovery workload, not hardware
  acceptance. EDQUOT is covered by injected route regressions, not a native quota
  filesystem. Multi-worker shared-disk exhaustion remains unqualified.

Owned containers/networks and synthetic filler/credential files were removed.
Private diagnostics remain outside the checkout; sanitized evidence contains no
credentials or document bytes.

## Alternatives, risks and review

**Proposal:** classify this narrow parser failure at the existing HTTP boundary;
retain existing parser cleanup and caller-owned capture transactions.

**Alternatives:** returning a capacity response for every parsing exception would
misclassify bad input and permissions. Monkeypatching the parser in production or
introducing a custom request parser for every upload route adds maintenance and
cleanup risk. Prechecking free space alone races concurrent writes and cannot
prove that an upload fits. Deleting stored objects or increasing quotas is outside
this repair and could destroy shared evidence or exhaust the host.

**Adversarial objection:** classification depends on the framework's wrapping
contract, including its generic detail string. Correct: an upgrade can change that
contract. Tests exercise actual FastAPI file parsing and the installed production
handlers in both the development interpreter and shipped runtime; an incompatible
upgrade fails these checks. The narrow fallback preserves ordinary error behavior
instead of guessing from private exception text. Registering the HTTP handler is
global, so explicit unrelated-route and authentication-header regressions matter.

**Expected benefit / complexity:** users can identify a temporary capacity failure
and recover using their existing command. One scoped marker and one delegating
handler; no new service, queue, middleware body buffering or dependency.

**Rollback:** revert this handler/marker and SPA message classification together;
no data migration, object deletion or receipt rewrite. The old generic 400 returns.
**Lead decision: pending** on HTTP contract, framework coupling and deployment
acceptance. Local success does not clear the repository's full image release gate
or human review requirement. Previous head `253206d` has successful PostgreSQL,
frontend/browser, lint, PII and supply-chain checks; backend was still running at
publication preparation. Its full image gate reports vulnerable builders/PostgreSQL
and unavailable MinIO/PgBouncer references. New-head CI must be checked separately.

**Next:** parallel OCR/PDF and stored-object reads; shared filesystem pressure across
workers/replicas; fleet/controller admission and deployment headroom. Storage faults
on other document writes, native quota/inode exhaustion, S3 recovery, power-loss
and restore/reference-lifecycle acceptance remain open. This is not a global
storage or resource-closure claim.
