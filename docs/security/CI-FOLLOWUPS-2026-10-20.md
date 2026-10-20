# Backup and browser CI follow-ups — 20 October 2026

The isolated candidate combines dependency PRs #68/#69 with two repairs:

- **DEPLOY-CUSTOM-DB-BACKUP:** use the effective, nonempty database-container
  identity for backups. Quoted `PGDATABASE` preserves literal names containing
  `=`, which a `--dbname` connection-string argument does not.
- **CI-PW-OWN-PIN:** compare both named browser jobs independently with the
  Playwright lock graph. The earlier guards could accept a mismatch in the
  second job because both inspected the first image reference.

The implementation preserves deployment quiescence/recovery, runtime browser
guards, existing jobs, permissions and unchanged visual baselines. It adds no
runtime dependency. Dependency updates retain their original commits and are
combined through a normal merge; their earlier separate CI results do not
qualify this combined candidate.

## Local qualification

[Machine-readable evidence](evidence/CI-FOLLOWUPS-2026-10-20.json) binds source
and local logs. A fresh environment using the combined requirements passed
**118 focused tests**, Ruff checks/formatting and mypy for 423 source files.
An independent review repeated all 60 backup/pin tests successfully. The suite
collects **4,577 cases**; collection does not mean those cases were executed.
Fresh owned SQLite migration upgrade/check passed with one unchanged head.

The frontend build, 19 proxy tests, 67 browser-spec structural checks and 148
label associations passed. The bundle is **434.8 kB raw / 128.4 kB gzip**, within
the existing 460/135 limits. Local Node 24.19.0 differs from hosted Node 22.
The tree PII scan passes structural checks; the owner denylist remains empty.

The fresh local compiler rebuild failed at Alpine package-index DNS resolution.
The build instead used retained previously qualified compiler bytes, with the
current installer's checksum, source/toolchain/dependency identity and license
checks before execution. Its SHA-256 is
`09f762da200042fa4ccd3d28dd6e0109c38333840e52e5a3a80c2b4395e3864a`.
No Dockerfile, TLS or provenance rule was weakened. A prematurely started local
build is excluded; qualification uses the repeated build after validated
installation. Other discarded local setup/command attempts are recorded in
the JSON. Hosted CI still rebuilds the pinned compiler normally.

[Backup failure-first and native restore evidence](CI-CUSTOM-DB-BACKUP-2026-10-20.md)
and [own-pin failure-first/mutation evidence](CI-PLAYWRIGHT-OWN-PIN-2026-10-20.md)
describe their distinct populations and boundaries. Counts overlap and must
not be added together.

## Hosted and deployment status

Combined-head hosted qualification is pending. Main remains
`d772a366f4e2f2e9a17faf7e4d3cc6643d0c3030`; its earlier successful checks do not
qualify this candidate. Normal integration requires the seven CI jobs and four
PR supply-chain checks to pass for this head and its stated synthetic merge.

The native database probe executes the exact extracted backup stage, not a full
VPS rollout. Earlier main deployment-job success did not establish public
readiness, the authoritative live wrapper or served revision. Historical PII
classification, trusted SSH host identity and the qualified deployment revision/
cancellation protocol remain open. Original-workspace UI/auth/directory/AP
changes are excluded from this candidate.

Rollback the corresponding script/checker with its tests and documentation.
Reverting the backup repair restores the custom-identity refusal; reverting the
checker restores the second-job blind spot. No schema or financial contract
changes are included.

AI-assisted: investigation, implementation, adversarial tests, review and
verification documentation. No external implementation copied or customer data
used.
