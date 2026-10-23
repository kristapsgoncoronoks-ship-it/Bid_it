# Planning / selected-region OCR integration — 23 October 2026

## Decision and review boundaries

New no-op merge e5f7091a2345 joins c3d5e7f90112 and d4e6f8a01223.
Keep all existing migration revisions and dependencies unchanged. Reparenting
would obscure independent feature histories and affect databases already stamped
at either head.

Inputs: main 90891d8d; PR #75 0bbeb26e; PR #76
24abfa67bc0a05633970a449196c9e2a9f4e4cda; PR #77
60e1c2238f9be03f98eeeb2da26340b71f0af080. Feature PRs remain drafts.
This candidate must not bypass separate #75/#76/#77 reviews and acceptance.
Requalify the final merge commit after any source or base change.
No production deployment or real provider/email activation is included.

## Complete graph, dependencies and model inspection

153 revisions, one root, two merge points, no explicit depends_on edges.
The earlier c129fa806b32 joins capture admission 238cd0be715f and expense
commands b018e9f4c021 and is current main's schema head. The new fork is:

- c129fa806b32 -> b2c4d6e8f010 -> c3d5e7f90112 (planning)
- c129fa806b32 -> d4e6f8a01223 (OCR)
- (c3d5e7f90112, d4e6f8a01223) -> e5f7091a2345

Planning adds assignment time zones/preferences/revision fences and three
calendar connection/OAuth/mapping tables. OCR adds capture_region_reads with
composite tenant/source FK and persistent before-draft/application evidence.
Their DDL does not overlap; neither depends on the other's schema. The merge
contains no DDL, data normalization, replay or credential/provider operations.
All four new tables retain their original ENABLE/FORCE RLS, tenant policies,
model registration and parity probes. Unscoped system-worker sessions remain
permitted by the existing policies; that contract is unchanged.

Conflict resolutions retain both tenant probes, worker handlers, PostgreSQL and
browser test entries. README/architecture counts, generated ERD and OpenAPI
describe the combined application.

## Upgrade and deployment constraints

One migration writer at a time. Verify alembic current and prove database and
document backups. Never stamp over missing migrations. Upgrade head from empty,
main c129fa806b32, either feature head or both parents applies missing branches.

Stop API/scheduling writers, capture/OCR, email/delivery and optional calendar
workers, including external replicas and one-off containers. The VPS script
enumerates the calendar profile even after opt-out, verifies no writer remains
running/restarting/paused, proves backups and starts prepared images. External
workers and Kubernetes deployments require separate operator quiescence; the
Compose script cannot verify them. Do not resume old workers automatically
after a migration/start attempt.

No migration activates a provider. Leave credentials unset and real accounts
disconnected during qualification. Tests use synthetic data and mock I/O.

## Rollback restrictions

Prefer application rollback retaining additive schemas. Old assignment writers
ignore opt-outs/revision fences and must remain disabled.

Unqualified alembic downgrade -1 refuses an ambiguous walk at the merge.
Explicit alembic downgrade c3d5e7f90112 removes only the marker, leaving BOTH
parent versions and all feature tables/records. Upgrade head restores one head.
Regression tests compare row/schema snapshots across that reversal.

Earlier feature rollbacks are not equivalently safe:

- Assignment downgrade refuses changed revisions/nondefault preferences.
- OCR downgrade refuses applied evidence; it can remove unapplied commands.
- Calendar downgrade has NO populated-data refusal: it drops connection,
  OAuth and mapping metadata. Disconnect, stop workers, export/retain evidence
  and separately review any destructive schema removal. Remote events are not
  deleted by that downgrade.
- A broad downgrade can drop unrelated branch objects before a later guard
  refuses, particularly with nontransactional SQLite DDL. Guards do not make a
  broad rollback atomic. Do not use broad downgrade for application rollback.

## Qualification and limits

New regression covers complete graph resolution, empty/main/planning/OCR upgrade
paths, seeded assignment/extraction preservation, drift, marker-only reversal/
reapplication and assignment/OCR downgrade refusal. PostgreSQL cases create
disposable databases with an explicit CI admin URL; migrations/assertions use
the non-superuser app role. Existing tenant/concurrency, deployment shutdown/
refusal, backend, browser, image and supply-chain gates remain enabled.

Local Python 3.12 has pinned backend/dev dependencies. PostgreSQL/Docker are
unavailable locally; package installation is blocked by host user/locking
constraints. Hosted PostgreSQL and combined CI are required on this integration
commit; previous feature-branch CI does not qualify it. No visual baseline,
security threshold or existing gate is relaxed. Consult the integration PR's
checks for final hosted status.


## Combined CI follow-up

Initial integration head 9019d7d0 ran CI 39933109925 and supply-chain
39933109890. Migration single-head/apply/drift gate passed. Security gates
found inherited pypdf 6.21.0 CVE-2026-113786 and moving setup-python v7 comment
mismatches. The minimal follow-up pins pypdf 6.21.1, the upstream JPEG-plugin
restriction patch, and corrects setup-python comments to v7.0.0, which resolves
to the already-pinned 5fda3b95 commit. Action code/permissions are unchanged;
no advisory is ignored. Upstream evidence checked on 23 October 2026:

- https://github.com/py-pdf/pypdf/security/advisories/GHSA-5hx5-vpf9-5wcv
- https://github.com/py-pdf/pypdf/releases/tag/6.21.1
- https://api.github.com/repos/actions/setup-python/git/refs/tags/v7.0.0

Final CI must run against the follow-up integration revision; results from the
initial head do not qualify changed dependency bytes.


## PostgreSQL drift contract

Run 39933663796 on 6a0bb21 passed upgrades and legacy-row assertions, then
raw PostgreSQL alembic check reported exactly the four intentional DB-017/018
column-specific SET NULL foreign keys from c4d6e8f0a2b4. The portable models
declare plain SET NULL because SQLite cannot compile PostgreSQL's column list.
The new PostgreSQL parity assertion copies ORM metadata, sets those exact four
FK actions to the documented PostgreSQL values, verifies the reflected database
has those values, then compares the complete schema without ignoring any diff.
It does not mutate app metadata, rewrite a constraint or change migration DDL.
The existing behavioral DB-017/018 PostgreSQL deletion test remains required.
SQLite continues to run unmodified alembic check.
