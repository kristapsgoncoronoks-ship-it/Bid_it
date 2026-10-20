# Backend dependency group — Decimal contract and runtime qualification

**18 October 2026 · integrated candidate, pending exact-head CI and Lead review.**
AI-assisted investigation, tests, dependency/snapshot integration and documentation.
No external implementation copied. This continues the
[SQLAlchemy repair](CI-DEPS-SQLA21-2026-10-18.md) on parent `879aeb9`.

[Sanitized validation evidence](evidence/CI-DEPS-BACKEND-REST-2026-10-18.json) and
[complete semantic OpenAPI diff](evidence/CI-DEPS-BACKEND-REST-OPENAPI-2026-10-18.json).

## Dependency scope

The other nine updates from dependency PR #63 are integrated on the repaired
branch. SQLAlchemy stays at the separately qualified 2.1.4.

| Dependency | Before | Candidate |
| --- | --- | --- |
| FastAPI | 0.141.1 | 0.143.0 |
| Uvicorn | 0.52.4 | 0.54.0 |
| asyncpg | 0.31.0 | 0.32.0 |
| Alembic | 1.19.1 | 1.20.0 |
| Pydantic | 2.13.5 | 2.14.0 |
| boto3 | 1.43.88 | 1.43.111 |
| pypdfium2 | 5.13.0 | 5.14.0 |
| Ruff (development/CI) | 0.16.6 | 0.17.0 |
| Mypy (development/CI) | 2.3.1 | 2.4.0 |

Application money, currency, tenant, permission, transaction and retention code is
unchanged. No data migration, new framework, schema override or budget change is
introduced. Existing warning, type, RLS and audit gates remain active.

## OpenAPI change prepared for review

The pre-integration candidate reproduces the existing contract failure:
**one failed / one passed** OpenAPI test. Its entire semantic difference is
**412 removed Decimal `pattern` attributes**, comprising 410 schema locations and
two query-parameter locations. There are two distinct old regexes: unconstrained
Decimal strings and strings constrained to 14 digits / two decimal places.
Removing just those attributes makes the old document equal to the new one.
All **401 paths**, properties, required fields, numeric constraints, data types
and other schema metadata are preserved. The checked-in snapshot is regenerated
from the candidate app for review; the equality gate itself is unchanged.

Pydantic 2.14's `GenerateJsonSchema.get_decimal_pattern()` returns `None` by
default; its separate `build_decimal_pattern()` hook remains available. Keeping
the old regex would misrepresent accepted scientific notation: the actual payment
API accepts `1.05e1` and records the exact decimal **10.50** on both vectors.
The snapshot's less restrictive string schema does not remove server validation.
Nonfinite values, sign, precision, currency and balance checks still apply before
financial effects. No custom schema generator was added to recreate the old
approximation.

Across **66 sampled cases**, unconstrained/nonnegative/positive Decimal adapters
have identical accepted/rejected values and serialization before/after, including
precision boundaries, exponents, malformed strings and nonfinite values. These
probes supplement actual route and persisted-command checks; they are not a proof
of every possible Decimal input.

The SPA's payment entries and captured/issued money fields already use strings.
The response representation remains a decimal string, so its hand-maintained
TypeScript field types need no change. The actual HTTP checks below cover the
422 `detail` locations and exact decimal response/ledger values.

## Financial action contracts

Nine new HTTP regressions pass on the preceding vector and candidate:

- NaN/infinity, negative balance, excess precision and too-large balance inputs
  return **422** with the affected body field. No payment, financial audit,
  webhook delivery or delivery job is written, command lookup returns 404 and
  `amount_paid` remains zero.
- Correcting the input can reuse that same command key; replay returns one
  receipt and creates exactly one of each financial/secondary effect. Response
  and stored Decimal values are **10.50**, with the exact reduced balance.
- Scientific input preserves the exact amount and single-effect replay.

A native upgrade replay goes further: the previous and candidate shipped images
run sequentially against the **same fresh PostgreSQL 16.15 database** with a
NOSUPERUSER/NOBYPASSRLS application role and unchanged signing/storage settings.
The old image commits final-invoice creation, draft creation/issue and a cash
command using scientific input. After switching images, the existing token,
all four command replays and payment-status lookup work. Invoice/payment IDs,
numbers, command fingerprints and the entire financial snapshot remain unchanged:
**two invoices, one payment of 10.50, four financial audits, numbering counter 3**.
The images differ in dependencies; the application implementation is unchanged.

## Runtime and resource evidence

- **Full default backend suite: 3,934 passed / 113 skipped / 3,250 warnings**,
  exit 0, **2,233.69 seconds** on Python 3.12.14. All 4,047 collected tests are
  accounted for; environment-dependent skips are separate from passing evidence.
- **125 focused backend checks passed / 118 warnings**, including the nine new
  route checks, OpenAPI, invoice/payment replay, received VAT evidence, automation,
  storage bounds and upload disk-capacity behavior.
- **All 131 required PostgreSQL checks passed / 203 warnings**, including real
  RLS, document-registry, payment/credit, command and capture concurrency guards.
- **All six PostgreSQL growth checks passed**, preserving the original dataset
  scales and endpoint limits. Fresh SQLite upgrade and drift checks pass with
  no new upgrade operations detected.
- **76 shipped Python 3.14.8 checks passed / one PostgreSQL-only skip / 129 warnings**,
  including payment recovery/replay on the actual candidate image.
- New Ruff/Mypy versions pass; Mypy checks **421 application sources** and Ruff
  format checks **826 files**. The dependency resolver reports no broken requirements.
- The staged-tree PII scan passes structural patterns. Its owner-held denylist
  remains empty/pending; this does not clear historical identifiers.
- Pinned pip-audit 2.10.1 reports zero known advisories across **78 resolved packages**.
  The rebuilt final API image has **zero HIGH/CRITICAL** findings. The complete
  build/service image gate remains blocked independently.
- Native capture replay on the candidate refuses both changed originals before
  parsing, completes four healthy text/OCR PDFs and shows truthful restoration
  guidance in the unchanged compiled SPA/NGINX with Chromium. Exact-original
  restoration completes the existing supported replay paths: **nine succeeded
  jobs, four UI commands/captures and three pending email records**, original
  amount **100** and an empty failure worklist. All nine source handles close.
  Initial extraction-worker peaks are **197.50 / 387.70 MiB** under 1 GiB each.

### Four-worker ingestion profile

The candidate also starts **four API workers at 1 CPU / 768 MiB**, read-only root,
UID 10001 and 128 MiB temporary storage, behind two NGINX workers. Two concurrent
authorized JSON requests, **20,880,385 bytes each**, each contain three attachments
of **5,220,000 bytes**. Two distinct API workers handle them. Both responses are
200 with three received/queued and zero rejected attachments. PostgreSQL contains
six inbound records, six durable queued email jobs and one shared registry object.
Readiness stays 200.

API cgroup peak is **662.11 MiB**, leaving **105.89 MiB** inside this limit, with
zero memory-max/OOM events. Configuration limits were not increased. This narrower
ingestion-only profile is not a performance improvement claim against the older
mixed upload profile. Its six email jobs are deliberately **not executed**; the
completed ordinary extraction/restoration workflow is separate evidence above.
Neither profile establishes worst-case decoding, shared-disk or fleet capacity.

Initial
fixture/command mistakes (a nonexistent test filename, missing fixture import path
and omitted synthetic production inbound secret) are retained and not credited as
application failures or successful validation. The production configuration guard
was satisfied with a proper ephemeral fixture secret, not bypassed.

## Review, alternatives and rollback

Proposal: integrate the reviewed dependency vector, regenerate its derived contract
with the exact semantic diff and retain authoritative financial validation. Keeping
the previous versions would defer the dependency PR. Reinstating old regexes,
ignoring snapshot drift, weakening money/RLS controls or raising resource budgets
would hide the behavior and were rejected.

Adversarial objection: dependency metadata can change while runtime behavior,
persisted fingerprints or memory use regress. The pre/post route checks, actual
same-database image switch, PostgreSQL controls, shipped/native checks and bounded
four-worker profile address those risks. S3 SDK unit checks do not certify a live
provider; no private provider, customer records or production deployment was used.

**Lead decision: pending.** Review the Decimal schema change and its client impact,
financial/idempotency evidence, dependency/runtime compatibility and rollback.
Revert this dependency/snapshot commit or use the preceding qualified image; no
data migration is involved. Approval of a generated baseline and release remains
human work. No dependency branch was merged and no deployment was performed.

Next: frontend PR #61's exact lock still exceeds **460/135 kB** at **464.3/136.0 kB**;
repair its initial graph and qualify matching Playwright library/container versions
without increasing budgets. TEST-GC-ISOLATION and the remaining capture/resource,
history, provider/restore and image findings remain in the register.
