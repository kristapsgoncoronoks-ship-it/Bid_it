# Executive Summary — engineering review of the harvest change set

**Repository:** `/home/user/Bid_it` (InvoiceIQ)
**Branch:** `claude/bidit-invoice-data-analytics`
**Base ref:** `origin/main` @ `46ea0b2` — a clean linear ancestor (branch is 355 ahead, 0 behind)
**Reviewed change set:** `c2948cb..HEAD` — the four unreviewed code commits of this session
(P0 retry guard, H-1 failed-capture worklist, H-2 inbound-channel health, H-3 automation
provenance). The remaining 351 commits were reviewed as they were built and are out of scope.
**Operating mode:** inferred `REVIEW_AND_FIX_DEFECTS` — the task variable arrived unfilled;
review is the deliverable, and defects *introduced by this change set* (plus one CI-blocking
break inherited from an earlier commit on the branch) were fixed rather than left knowingly
broken.

## Tooling honesty

**Code-review-graph was NOT available and NO graph analysis was performed.** Verified: no
`code-review-graph` binary on PATH, no `code_review_graph` Python module, nothing in npm
globals, no MCP tools exposed to this session, no pre-existing graph artifacts in the repo.
The user explicitly cancelled the request to add the skill, so it was not installed.
Structural review was performed by repository inspection (git, grep, import tracing, running
the real test suites) instead.

**The multi-agent review did NOT complete.** Four independent reviewers (security,
skeptical-correctness, QA, architecture/performance) were launched and all four died
simultaneously on an account session limit before producing findings. The reviews below were
therefore performed directly, and are narrower than a full six-agent pass would have been.
One agent left an untracked scratch file (`backend/tests/test_zz_probe.py`) which was removed.

## Repository health

Scored for **the reviewed change set**, not the whole repository. Justification follows each.

| Area | Score | Status | Basis |
|---|---|---|---|
| Functional correctness | 82/100 | Good | Full suite green; two real defects found and fixed; one latent fragility recorded |
| Architecture | 78/100 | Good | Coherent, additive, respects the service/route boundary; one sibling-route asymmetry found and fixed |
| Security | 80/100 | Good | No vulnerability found in scope; tenant registration + RLS + probes correct. **Independent security review did not complete** |
| Test quality | 62/100 | Adequate | 41 new tests, 11 seeded-violation proofs — but the email channel and route authorization are untested, and the frontend has no unit tests at all |
| Maintainability | 85/100 | Strong | Closed vocabularies, documented rationale, no duplication of existing query definitions |
| Performance | 70/100 | Adequate | One per-keystroke defect found and fixed; the worklist is unpaginated by design choice, which bites at scale |
| Reliability | 80/100 | Good | Pessimistic-first health ordering is sound; counter drift under concurrency unproven either way |
| Production readiness | 68/100 | Conditional | Code is ready; **deployment is not** — production is on `15116e1`, two migrations behind, and CI has never run on this branch |

Scores are judgement calibrated to the evidence below, not measurements. No coverage
percentage was computed, so "test quality" is an assessment, not a metric.

## Major risks

1. **CI has never run on this branch.** Every result in this review is from local execution.
   The GitHub Actions runners are billing-blocked. Two CI gates were found red locally
   (`mypy app`, `npx playwright test e2e/nav.spec.ts`) that nobody would have discovered
   until the runners came back.
2. **Production is two migrations behind** (`c7e1a94d5b02`, `d3b8c05f7a41`) and 355 commits
   behind. The deployment runbook predates both.
3. **The failed-capture worklist is unpaginated.** Correct at pilot scale; degrades linearly.
4. **The email channel of the worklist is untested.** The code path exists and is reachable;
   no test exercises it.

## Critical findings

**P0: none.**

**P1 (both FIXED in this review):**

- **F-01 — `mypy app` was red; the branch could not pass CI.** Inherited from `b6d12db`
  (earlier in this programme, not this change set).
- **F-02 — this change set broke `e2e/nav.spec.ts`.** The new nav label "Unread documents"
  substring-matched the admin-gated "Documents" item a test asserts is hidden from base users.

Full findings in `03-code-quality.md`.

## Recommendation

**GO WITH CONDITIONS.**

The change set is well-constructed and now passes every quality gate that can be executed
locally. It does not go to production as-is, for reasons that are about *process*, not this
code:

1. CI must actually run on this branch before merge. Two gates were red locally; there may be
   environment-specific failures neither of us has seen (the Postgres RLS job in particular
   cannot be reproduced here).
2. The deploy runbook must be regenerated for the two new migrations.
3. The untested email channel and the missing route-authorization tests should land before
   this is trusted with a real customer's mail.

The scores above are held down primarily by verification gaps, not by defects in the code.
