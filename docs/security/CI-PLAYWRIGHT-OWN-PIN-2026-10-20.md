# Validate both Playwright job references — 20 October 2026

**CI-PW-OWN-PIN: implemented and locally qualified; fresh hosted CI pending.**
This change sits on combined dependency candidate `fbd4c851`, not on either
previously qualified dependency head alone. It does not establish main merge,
full application/browser execution, deployment or production acceptance.

The existing runtime guards grep the first Playwright image reference globally.
A failure-first fixture using those exact committed Bash guards proves that
changing only `vr-baselines` from 1.64 to 1.63 is accepted by both guards, while
changing only the first job is rejected. The fixture supplies an owned `npx`
stub reporting 1.64; no browser or production service is invoked for that proof.

The new `scripts/check_playwright_pair.py` reads each named job through the
already pinned safe PyYAML parser and compares its version tag with the actual
lock records for `@playwright/test`, `playwright` and `playwright-core`. It checks
their pinned dependency edges and refuses a divergent nested Playwright record.
Missing/malformed sources, duplicate YAML keys, missing jobs and mutable or
unrecognized image references fail with bounded diagnostics. Comments and an
unrelated first image cannot substitute for either named job's own reference.
No new dependency is introduced.

The backend invokes the real checker after installing requirements, before its
migration and full test steps. The 30 mutation cases also join the existing
fast supply-chain pytest step. Other jobs still run in parallel; this is not a
claim that all build work waits behind a new workflow job. Both existing runtime
guards, job permissions, resource limits, visual baseline files and browser
commands are unchanged.

## Local verification

- Both first-only and second-only pin mutations independently return failure
  and identify the correct job. The actual combined source returns success.
- **81 focused tests passed in 3.55 seconds:** 30 new cases plus 51 existing
  supply-chain, dependency-audit and deployment-hygiene cases. These overlap the
  full backend suite and must not be added to its count.
- Ruff, actionlint, offline zizmor (zero findings), whitespace and current-tree
  PII checks passed. The owner-held PII denylist remains empty; history is not
  classified by the current-tree result.
- Independent source review found no current blocker. Its two bounded notes
  (image-digest claims and decoy ordering) are reflected in the implementation,
  fixtures and evidence.

[Source hashes, exact baseline/negative controls and verification scope](evidence/CI-PLAYWRIGHT-OWN-PIN-2026-10-20.json)
record Python 3.12.14 and PyYAML 6.0.3. Local checks use the existing workspace
interpreter against the clean isolated candidate; they do not qualify the whole
new dependency set or shipped Python runtime. Fresh hosted checks remain required.

The checker validates source version-tag/lock consistency. Optional digest syntax
is parsed, but digest content, image availability, signatures and actual browser
rendering are not verified by this static tool. Existing runtime/browser/visual
checks remain necessary. No visual baseline refresh is performed or approved.

Rollback removes the checker and early invocation/tests together. It restores
the old global-first-reference gap; it does not alter financial data, migrations,
app authorization, retained artifacts or dependency versions.

AI-assisted: native implementation, mutation-regression drafting, independent
source review and local qualification. No external implementation copied; no
human release approval or owner policy decision asserted.
