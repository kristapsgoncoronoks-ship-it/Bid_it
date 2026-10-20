# CI feedback performance plan — 20 October 2026

**Status: planned measurement and qualification, not an implemented speedup.**
Finding CI-20-FEEDBACK. This does not block the repaired candidate's existing
required checks or authorize weaker security, coverage or resource controls.

## Measured baseline

At repair head `2701ad1550cebbabf8977d896e362d317e8f0e22`, hosted
[CI 39374113766](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39374113766)
recorded a full backend run of 1,801.86 seconds, with 4,361 passes and 156 skips.
The 620-case functional browser run took 14.8 minutes with one worker. The
earlier 15-case R010 check took 10.86 seconds, about 0.6% of full backend time;
its cases overlap the full suite. Removing that early check offers little
measured saving and loses early failure feedback.

The backend currently retains neither per-test/fixture duration evidence nor a
JUnit report or CPU/RSS profile. Buffered log timestamps cannot establish the
cost of an individual test. Database fixtures recreate schema and seed FX, and
authenticated fixtures perform registration and bcrypt work; their shares of
total runtime remain hypotheses until measured.

## Ordered development

1. Retain pytest duration/JUnit evidence and bounded CPU/RSS measurements even
   after failure, preserving the original exit code. Distinguish setup, call and
   teardown. Record source head, runtime, runner resources and collected node IDs.
2. Profile representative database and authentication fixtures. Change fixture
   scope only after proving isolation, foreign-key enforcement, cleanup and
   equivalent authorization outcomes. Preserve production cryptographic settings.
3. Compare browser workers one versus two under the same CPU/memory budget.
   Require identical test identities and outcomes, no new flakiness and measured
   resource peaks before selecting a setting. Keep visual baselines and retries.
4. Consider backend partitioning into separate processes only after those
   measurements. Prove complete, disjoint collection against the unpartitioned
   suite. Seed an omitted and duplicated shard case so the coverage check must
   fail. Preserve PostgreSQL concurrency/growth gates, live-browser admission
   controls and all existing resource ceilings.

## Development prompt

Instrument the existing CI backend and browser runs before optimizing them.
Publish duration/JUnit/resource artifacts with truthful failed, missing and
cancelled states. Compare identical source and test populations under fixed
resources. Propose fixture or worker changes only with measured improvement and
negative controls proving isolation, coverage and failure detection. Do not skip
tests, reduce production crypto, widen retry/time/resource budgets or approve
changed visual baselines. Record results and rollback for Lead review.

AI-assisted: read-only workflow, fixture and completed-log review. No code or CI
settings changed for this investigation; no external implementation copied.
