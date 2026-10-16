# Report-driven development — combined verification

AI-assisted implementation, regression testing and evidence drafting. Application
source is `46351730e2f8b85979919820e67916737c04ab0c`; the final evidence/harness commit
changes no backend/frontend application files. Exact-head CI and human/lead review
remain required before merge. Main was independently read at
`02a4e17ec8bd313153faddd6213377d0345e2f91`; no merge or deployment occurred.

## Implemented report work

Both uploaded reports are reconciled with source hashes, preserved IDs and
per-item prompts in [the intake/follow-through record](RECONCILIATION-2026-10-16.md).
[The backlog](../BACKLOG.md) links all repair evidence: JWT/PDF/dependency controls,
FX-aware project reporting, complete-or-refused ZIP, immutable/validated automation,
lossless editing, truthful preview, durable delivery and attachment command/recovery.
The controlled pilot found and repaired legal-credit replay and audit-link defects.
The external extraction-provider item is now explicitly owner-blocked under
[Decisions §31](../DECISIONS-NEEDED.md#31-external-extraction-provider-l1).

## Executed verification

- `cd backend && .venv/bin/pytest -q -n 4` (Python 3.12.14): **3,661 passed, 39 skipped**, 632.48 s. PostgreSQL-only cases are exercised separately by the required real-PostgreSQL CI group.
- GitHub application-source CI run [38946075404](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/38946075404):
  **581 browser tests**, **13 visual tests**, **56 required PostgreSQL tests** and
  **6 query/performance-shape tests** passed. The PostgreSQL job also completed the
  existing scale/concurrency performance harness. Backend static/type checks,
  frontend build/bundle, current-tree PII and security workflows passed. Backend
  full-CI job was still running at this evidence capture and is not counted as
  passed here. Final-head CI remains required; subsequent evidence-only pushes
  can supersede older runs under the existing concurrency policy.
- Focused new regressions and real PostgreSQL races are recorded per slice; original
  monetary, tenant, replay and bundle expectations remain enforced. Whole-app mypy
  passed for 418 files. Latest frontend bundle: 435.8 kB raw / 127.6 kB gzip, within
  the unchanged 460/135 limits.
- `backend/.venv/bin/ruff check scripts/pilot/controlled_finance.py`, matching format
  check, and `node --check scripts/pilot/browser_finance.mjs` passed. Nine focused
  documentation/PG-registration checks and relative-link target validation passed.
- `python scripts/pii_scan.py --tree` passed. A fresh private all-ref inventory at
  the application head scanned 7,378 blobs across 19 refs; the same 872 masked rows
  in 196 blobs remain unresolved. No historical clearance or shared-history change.
- The [accepted isolated synthetic pilot](../plan/E16-05-CONTROLLED-PILOT-2026-10-16.md)
  passed capture, separate-user approval, bank/payment match, exact CSV download,
  persisted lease/retry, corruption refusal, fresh-volume database/object restore,
  financial/audit/hash replay checks and processing a new post-restore upload.
  Server ceilings total 2 CPUs / 1,920 MiB, no swap; summed individual memory peaks
  490.81 MiB. This is a one-workflow CSV exercise, with explicit measurement limits,
  not production capacity, external accountant acceptance or a production RTO.

## Remaining gates

Final-image scanning fails closed locally and in CI. The rebuilt backend image has
75 HIGH + 1 CRITICAL rows / 33 advisory IDs with no listed fixed versions; the
final frontend image has zero HIGH/CRITICAL findings. Exact images, unchanged
finding population and SBOM/audit hashes are in [V05 evidence](../security/V05-IMAGES-2026-10-16.md).
There is no suppression, risk waiver or release clearance.

Owner/lead-controlled image disposition, historical PII verification/denylist,
security-control acceptance and unknown historical SMTP evidence remain Decisions
§29/24/28/30; historical credit audit linkage remains §32. External processor choice, production IdP/KEK/provider verification,
accountant import acceptance and broader business/personal-finance product scope
remain separate. This candidate stays a draft; passing application tests does not
resolve those gates. The pilot's private resources were removed after success;
its synthetic raw artifacts and backups remain protected outside Git.
