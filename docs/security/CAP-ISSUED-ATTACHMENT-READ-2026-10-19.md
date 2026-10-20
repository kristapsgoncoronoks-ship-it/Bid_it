# Bound issued-attachment reads by recorded admission size

**19 October 2026 · CAP-ISSUED-ATTACHMENT-READ · implemented, pending new-head CI and Lead review.**
AI-assisted investigation, implementation, tests and documentation. Separate agents
reviewed the contract and final source, checked CI artifacts and added PostgreSQL
access regressions. No external implementation copied.
[Sanitized evidence](evidence/CAP-ISSUED-ATTACHMENT-READ-2026-10-19.json).

## Confirmed failure and repair

An issued attachment had exact admitted size and SHA metadata, but downloading it
read the entire stored object before checking either. The baseline HTTP regression
reads a **32 MiB** sparse replacement in one unbounded read before refusing it.
A fake S3 response with false `ContentLength` also reads all **50 bytes** instead
of stopping after the original **29-byte** document plus one. These are allocation
failures; existing integrity checks already prevented corrupt downloads.

The route now supplies the authorized attachment row's recorded size as the read
budget. A positive-size object's growth is refused after **size + one byte**;
the measured replacement now reads **30 bytes**. Local/S3 sources close, no partial
attachment is returned, and the route gives **503** with restoration guidance.
The specific oversize exception is handled before the general storage exception.
Exact original restoration recovers the same download without deleting or rewriting
its reference or upload command.

The budget comes from admission metadata rather than today's upload limit. A
**2 MiB** attachment actually admitted under **3 MiB** remains downloadable after
the upload limit falls to **1 MiB**. Legacy size-zero metadata uses a one-byte
budget (at most two bytes consumed); the existing exact length/SHA checks still
permit an empty object with its correct hash. Negative size returns the existing
integrity refusal before I/O. Missing/truncated/same-size-corrupt objects retain
503 refusal and restoration recovery.

Tenant and invoice/attachment matching, module/permission requirements, the
post-I/O transaction rollback and fresh identity/permission/module checks, invoice
and attachment visibility, exact size/SHA and inert attachment headers remain.
No schema, dependency, financial mutation, audit mutation or new size policy is
introduced. Invisible/other-tenant attachments never touch storage.

## Validation

- Host attachment/storage selection: **43 passed / 39 existing test-key warnings**.
- Issuing/boundary/file-security/foundation/authorization controls: **47 passed / 14 existing test-key warnings**.
- Shipped Python 3.14 runtime: the same **43 focused cases passed**, zero skips.
- Required fresh PostgreSQL selection: **140 passed / zero skips / 221 existing test-key warnings**, including all nine new access cases; **six read-growth checks** pass and eight informational concurrency scenarios have zero errors.
- Ruff/format and Mypy **421 source files** pass; docs truth **seven passed**.
- Backend collection: **4,122 cases collected**, not a claim of 4,122 executed passes.
- Rebuilt final API SBOM/audit: **zero HIGH/CRITICAL advisory rows** against the refreshed pinned scanner DB. The complete build/service image gate remains blocked.

The rebuilt shipped runtime's application source matches all **421** checkout
files. Its native test execution uses actual ASGI HTTP routes, local files and
fake S3 response bodies, with synthetic SQLite databases; this is not live TCP,
native PostgreSQL deployment or real S3-provider acceptance. Tests run as the
image's UID 10001, with a read-only root, dropped capabilities, no network,
one CPU and 384 MiB. These are test constraints, not a measured hardware minimum
or production memory-headroom claim.

The fresh PostgreSQL fixture proves NOSUPERUSER/NOBYPASSRLS, a single migration
head and successful upgrade. Nine new cases commit changes through independent
connections after the actual bounded read: user, membership, session and
organization refusal; permission downgrade and module disable; attachment binning,
attachment deletion and invoice deletion. Every response refuses the bytes. The
new file is in the required CI PostgreSQL selection.

An optional `alembic check` again reports already registered **SYS-06** column-subset
SET NULL reflection drift (exit 255); the helper retains overall exit 1, while all
required steps independently exit zero. No tenant-preserving constraints changed.
The first PG attempt captured a source fingerprint before the route edit; it is
retained but not used for final qualification. The final fresh run has stable
before/after source hashes and its own cleanup proof.

Native fixture attempts with unavailable bind-mounted tests and unreadable copied
test-source permissions are retained as uncredited exit-4 attempts. Only temporary
synthetic test copies receive readable modes; application privileges and gates do
not change. Final fixtures use a private named volume populated through Docker
copy, mounted read-only for the unprivileged test process. Owned containers,
volumes and temporary secret files are removed.

## CI and review

Predecessor **584d2ad6e4e5cb062d998676dfc964befee11bd0**, CI
[39205368962](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39205368962),
passed backend, PostgreSQL, frontend, browser, lint and PII jobs; its supply-chain
run **39205368853** passed. The complete image gate still failed and deployment
was skipped. Artifact **12166140623** confirms final API/origin zero HIGH/CRITICAL,
backend builder **4 HIGH**, frontend builder **18 HIGH**, PostgreSQL **24 HIGH +
1 CRITICAL**, and three unavailable PgBouncer/MinIO references. All **47 advisory
package/version tuples** match the dated SYS-04/V05b evidence. Clean final images
do not clear that gate. The new head still needs CI and human review.

Adversarial review checked historical/zero/negative size semantics, exception
ordering, tenant and fresh-access boundaries and the absence of new writes.
**Lead decision pending:** recorded-size authority, restoration/error behavior,
actual tests, memory limits and new-head CI. No merge or deployment performed.

## Remaining work and rollback

This bounds growth relative to admitted size. It still buffers the admitted file;
the bytearray-to-bytes conversion can temporarily hold two payload buffers. Large
historical metadata, aggregate concurrency, total I/O/deadlines and fleet memory
remain capacity work. No fixed global memory ceiling is claimed.

There are **17 direct `documents.load` calls: two explicitly bounded and 15 still
uncapped**, listed in the new evidence. Older 18/16-call inventories remain dated
evidence. Remaining originals, receipts, logos, AP attachments, portal files,
delivery/render and generated exports require their own compatible contracts;
128 MiB workspace export rules must stay separate from upload policy.

Independent source review also logged **CAP-LOCAL-READ-ERROR**: non-missing local
filesystem `OSError` may escape `LocalStorage.get` and bypass sanitized recovery.
This is a source error-contract gap; route-level fault reproduction and repair
remain pending. Current oversize tests do not certify generic local I/O recovery.

Rollback is a route-only revert plus removal of the added regression selection if
reverting tests. There is no data migration or financial rollback. Reverting the
route restores the unbounded allocation risk. Full image qualification, provider/
restore/history acceptance, SYS-06, worst-case decoding/shared disk/fleet work and
human release review remain open.
