# Frontend dependency qualification and page-load recovery

**18 October 2026 · integrated candidate, pending exact-head CI/Lead review.**
AI-assisted investigation, implementation, browser tests and documentation.
No external implementation copied. Backend dependency qualification is separately
recorded in [CI-DEPS-BACKEND-REST](CI-DEPS-BACKEND-REST-2026-10-18.md), commit `94d2233`.

[Sanitized evidence](evidence/CI-DEPS-FRONTEND-2026-10-18.json).

## Dependency and bundle scope

The exact package lock from frontend PR #61 (`8d597cd`) is preserved, SHA-256
`e7daa8fd1a1d02bd71f144e04672e8648c47779e3815b92107ddc4902be0083a`.
This is integration onto the repaired application; the stale dependency branch is
not merged. The earlier experiment floating its ranges remains separate evidence.

Direct changes are TanStack Query 5.102.8→5.104.1, React/React DOM
19.2.8→19.3.0, React Router 7.18.3→7.18.4, Playwright 1.62.1→1.63.0,
React types 19.2.18→19.3.0 / React DOM types 19.2.7→19.3.0,
Vite React plugin 6.1.1→6.1.2, PostCSS 8.5.28→8.5.29 and Vite 8.2.2→8.3.3.
Other direct dependency declarations remain unchanged. Both browser CI jobs use
the matching official `mcr.microsoft.com/playwright:v1.63.0-jammy` image, verified
available for amd64/arm64. Workflow action SHAs, permissions and library/image
agreement checks remain intact.

On the repaired eager-shell source, the exact candidate builds but fails the
unchanged **460/135 kB** gate at **464.3 kB raw / 136.0 kB gzip**. Deferring the
protected workspace shell reduces first load to **434.3 / 128.2 kB** across four
assets. Chart isolation, production fixture checks and the same budget gate pass.
This measures download size, not browser RSS or fleet/server capacity.

## Loading and recovery contracts

`ProtectedRoute` still resolves authorization before the shell renders. Public
sign-in remains eager and does not request the protected shell. Its loading state
announces **Loading workspace…** with `role="status"`. A failed shell displays a
clear explanation and **Reload workspace**. Reload preserves the requested URL
and stored session credential and fetches the asset again.

**CI-LAZY-PAGE-RETRY / confirmed button defect:** after a routed-page import fails,
React caches its rejected lazy loader. The existing **Try again** merely resets
its error boundary; it renders the same rejection without another asset request,
even after transport recovers. A compiled-SPA probe reproduces that dead end.

The small `lazyPage` wrapper preserves component/props inference and marks a
loader rejection with `PageLoadError`, retaining its original cause for developer
diagnostics. Error handling uses this marker, not browser-specific message text.
The routed-page boundary offers **Reload page** for these failures; the shell and
navigation remain visible. Ordinary render errors retain the existing page-only
remount behavior, covered by FE-003. No new dependency, auth/cache ownership rule,
API money type, financial mutation or permission is introduced.

Five browser regressions cover:

1. Sign-in renders and accepts input while shell requests are blocked; no shell
   request occurs. This check fails against the old eager shell.
2. A held shell request announces loading, then opens the intended page after
   release with its navigation and query string intact.
3. An aborted shell request explains failure; the explicit reload obtains a new
   document, requests the shell again and restores the page/session.
4. An aborted page request leaves navigation available; **Reload page** obtains
   a new document and restores the exact page/URL/session. The preceding compiled
   code's **Try again** fails this recovery and never refetches the page.

5. A failed public portal import also explains failure and reloads successfully
   without requiring an account session. Before the added public boundary it
   blanks the page. Recovery keeps the token-bearing URL, makes one read and
   performs no offer-decision POST. The failure text does not echo the token.

Reload fixtures seed the credential only once per tab, so a lost session cannot
be hidden by the fixture recreating it on every reload. These strengthened
fixtures pass. All five flows also pass against the compiled SPA. API responses in these browser
checks are synthetic fixtures: they establish session/loading behavior and do not
prove a financial job completed. Native backend/worker completion is separate
[evidence](CI-DEPS-BACKEND-REST-2026-10-18.md).

## Qualification and attempts

The main checkout installs the exact lock with `npm ci`; build, lint and all
structural gates pass. The upload proxy's 19 protocol/TLS checks pass, npm audit
reports zero advisories, and all 17 workflow/governance checks pass.
The broad browser run passes **603 cases** in the matching Playwright container,
with two workers at 2 CPU / 2 GiB. It precedes only the final public-portal boundary
and seed-once fixture tightening. **All 22 final focused cases pass** on those
changes: five loading/recovery cases, all four existing portal cases and all
13 visual snapshots. The 100-pixel allowance and snapshots are unchanged.

The rebuilt origin image passes the same bundle gate and has **zero
HIGH/CRITICAL** findings. Its **86 compiled distribution files match the main
checkout byte for byte**. All five compiled loading/recovery flows pass through
that image with UID 101, read-only root, no capabilities, two NGINX workers,
1 CPU and 128 MiB. The synthetic fixture derives its global nginx.conf from the
image and sets only worker_processes to 2 for its quota; no production default
is changed. Peak is **6.45 MiB**, with zero max/OOM events. This is a small static
asset test with mocked API reads, not application/server capacity acceptance.
Owned containers and the temporary matched-browser image are removed. The
staged-tree PII scan passes structural patterns; the owner denylist remains
empty/pending and history is not cleared.

Initial attempts are preserved and do not count as passing qualification:

- Initial full host-browser run: **599 passed / three failed**. The suspension
  nav fixture returned `[]` as billing data, producing a page render error and
  an ambiguous alert locator. It now supplies a complete plan/status fixture,
  asserts actual billing content and absence of the error boundary, and targets
  the shell's suspension guidance. Navigation absence assertions are retained.
  The first corrected assertion also matched two legitimate “Current plan”
  elements; its final locator targets the actual disabled plan button.
- The other two host failures (schedule and document upload) have recorded
  `net::ERR_NETWORK_CHANGED` failures on application entry dependencies while
  Docker lifecycle work was concurrent. Their traces are retained. This is a
  concrete mechanism observed in those two runs, not a global explanation or
  closure of SYS-09. The complete rerun uses a stable container network namespace.
- A four-worker browser-container trial at **2 CPU / 2 GiB** hit its cgroup limit
  (19 OOM events, four kills observed before stop). It was interrupted and is
  unqualified. The rerun uses two workers at the same resource limit; repository
  timeouts, retries, pixel allowances and application budgets remain unchanged.
  These are test-harness limits, not production-capacity measurements.
- The first matching-container command ran as root with all capabilities dropped
  against owner-only fixture executables and correctly received permission denied.
  Running as their UID 1000 resolves it without broadening file permissions or
  restoring capabilities. An initial helper used ES2022 `ErrorOptions` against
  the existing ES2020 target; retaining an explicitly typed cause fixes compilation
  without raising the browser target. The first bundle invocation named a
  nonexistent npm script; the actual unchanged Node gate was subsequently run.

## Review, alternatives and rollback

Proposal: integrate the exact dependency vector, defer the protected shell and
provide real recovery for rejected page downloads. Retaining the prior dependencies
would defer maintenance. Raising bundle limits would leave the oversized graph.
Resetting only the boundary cannot retry React's cached lazy rejection. Generic
render-error recovery remains available for that separate failure kind.

Adversarial objection: moving the shell creates an extra network failure boundary
and may alter auth or suspended-workspace behavior. The public/delayed/failed
asset regressions, identity and permission suites, corrected billing fixture,
compiled checks and unchanged visual snapshots address those risks. Reload is
explicit and can discard browser-only unsaved state; URL and credential retention
are tested, not an assertion that every transient field survives. No global
network-error suppression, larger timeout or self-approved visual baseline is used.

**Lead review pending:** dependency behavior, deferred-shell accessibility,
reload semantics and the exact new-head CI results. No data migration is involved.
Revert the frontend dependency/recovery commit together with both Playwright image
tags, or use its preceding qualified frontend image; keeping the new lock with the
old eager shell would reinstate the bundle failure.

**UI-PORTAL-READ-TRUTH** is a separate next finding: Portal's query-error branch
labels all failed reads as an invalid link, including service/transport failures.
Its source evidence and HTTP refusal/retry repair prompt are in the register;
this module-download repair does not claim to fix data-read outcomes.

The complete build/service image gate, historical PII, provider/restore acceptance,
remaining API/download bounds, worst-case decoding/shared-disk/fleet evidence,
email-state/replay races, TEST-GC-ISOLATION and broader SYS-09 investigation remain
in the findings register. No merge or production deployment is performed.
