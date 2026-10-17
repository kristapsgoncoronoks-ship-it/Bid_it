# V06a — Supported dependency review, control change for review

AI-assisted implementation/tests/documentation. Implemented locally; human/lead
review and exact-head hosted CI remain outstanding. This record is the proposed
control decision, not an invented approval. PR #62 remains draft and merge blocked.

## Problem, choice and objection

GitHub run 38913882442 rejected dependency-review as unsupported for this private
repository. It produced no vulnerability result. The owner authorized continuing
the security backlog and fixes; no account purchase or visibility change is made.

Retain the existing required `Dependency review` job and read-only token. Replace
the unavailable service call with pinned pip-audit 2.10.1 and npm's supported audit,
checking full head graphs rather than only introduced vulnerabilities. Save resolved
Python base/head differences and npm lock-path/version/license-declaration changes.
A base audit may return findings; only valid resolved JSON with advisory evidence
explains that exit code. Missing graphs, skipped dependencies, invalid base SHA,
network/tool/config errors fail. The head audit never excuses existing findings.

Alternatives: enabling the hosted service remains an owner/admin option (§28);
skipping it, making the job optional, changing repo visibility or adding ignores
would remove protection and were rejected. The objection is that ecosystem audits
are not all features of the hosted product. The coverage mapping below states the
actual replacement and limits; a reviewer must accept it before merge. No legal
license policy is manufactured, and no claim of universal feature equivalence is made.

## Coverage mapping

| Former configured control | Supported replacement / limit |
|---|---|
| PR dependency changes | Resolved Python runtime+developer base/head deltas; complete npm v3 locked package-path deltas, including transitive/dev packages. Full-history authenticated checkout fetches base without persisting credentials. |
| Moderate-or-higher vulnerabilities | pip-audit blocks any reported vulnerability (stronger); npm blocks moderate/high/critical, including dev/transitives. Full head graphs cover existing as well as introduced findings. |
| License policy | Former action configured no allow/deny license policy. None is invented. npm declared license changes are evidence; Python license review remains manual. These declarations are not legal certification. |
| Private repo / forks | No hosted security entitlement/API or secret needed. Read-only contents permission, persist-credentials=false, no pull_request_target, no provider credentials. |
| Scanner/service/config failure | Normal nonzero pipeline exit blocks; no continue-on-error, conditional private-repo skip, new ignore or threshold relaxation. |
| Reproducible evidence | Action SHAs and pip-audit version pinned; manifests/npm integrity lock and resolved base/head versions recorded. Python transitive resolution is observed at run time, not a historical transitive lock replay. |

## Verification

Six real scanner fixtures behaved as required: affected pypdf 6.16.2 failed,
6.21.0 passed; missing Python manifest failed; old source-map-js npm lock failed,
corrected complete lock passed; unavailable npm registry failed with retries off.
They are isolated synthetic/index tests, not production probes. An additional
unit regression verifies transitive advisory/version deltas and refuses empty or
skipped resolution. The actual current comparison against main reports six Python
changes and one npm locked package change. Full current Python and npm audits pass.
Workflow action-pin checks and zizmor passed with zero findings; existing credential,
PII and control-register jobs are retained. Hosted CI must confirm the delivered head.

Cost/complexity: one stdlib comparison script and existing audit tools; a base
resolution adds network/time and can block during an index outage. This is deliberate
fail-closed behavior. No new runtime dependency/service or app migration. Rollback
must restore a working reviewed control, not simply the unsupported call or an
optional gate. The separate final-image/history controls still block release.
