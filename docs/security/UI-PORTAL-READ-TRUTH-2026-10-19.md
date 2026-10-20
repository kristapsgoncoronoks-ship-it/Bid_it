# Public portal recovery and fresh origin-runtime repair

**19 October 2026 · UI-PORTAL-READ-TRUTH · implemented, pending new-head CI/Lead review.**
AI-assisted investigation, implementation, regression tests and documentation.
External implementation copied: none. [Sanitized evidence](evidence/UI-PORTAL-READ-TRUTH-2026-10-19.json).

## Problem and resulting behavior

A failed `GET /portal/{token}` previously displayed “This link is no longer valid”
for every HTTP or transport failure. A valid link affected by a service outage had
no recovery action. The new 503 regression failed against that implementation.

The portal now distinguishes these outcomes:

| Outcome | Visible explanation and action |
| --- | --- |
| 404 / 410 | Link no longer valid; ask the contact for a fresh link. |
| 401 / 403 | Access refused; ask the contact to check access or send a new link. |
| 429 | Too many requests; wait before retrying. Valid Retry-After seconds and HTTP dates disable the button until the server deadline. Missing/invalid/overflowing values leave an explicit manual retry. |
| Network failure / 5xx | Could not load the portal; Try again, then contact guidance if the problem persists. |
| Stalled read | A 15-second Axios deadline ends with an explained timeout and manual retry. |

The actual backend returns 404 for unknown/revoked credentials. The 401/403/410
cases qualify frontend handling of refusal responses; they do not assert that the
current route generates those statuses. No response detail or credential URL is
rendered in these read-error states. The explicitly handled query opts out of the
global raw-error toast, while other queries and mutation reporting retain their
existing behavior.

## Read recovery and consequential actions

Try again performs one summary GET. It is disabled while fetching; an explicit
handler guard and `cancelRefetch: false` prevent overlapping requests. There is no
automatic retry/reconnect loop. `networkMode: always` lets an attempted read settle
while offline instead of leaving the query paused indefinitely. The query passes
TanStack's cancellation signal to Axios. Loading/reloading is announced through
`role=status`. A clamped timer honors a valid 429 cooldown. A backward clock adjustment before
a subsequent 429 must not leave an enabled button whose handler refuses the click;
the current clock controls both the rendered state and the handler.

Failed refreshes hide stale offer controls. An offer decision still uses the
existing POST and returns the invalidation promise. If that refresh fails after
the decision succeeded, recovery reads the committed state; it never submits the
decision again. Money, tenant scoping, authorization, audit and offer transition
implementation remain unchanged. No application dependency, migration, deployment manifest,
security-gate or visual-baseline change is part of this slice. The origin OS patch
below is separately qualified on the rebuilt image.

A portal GET can commit the existing first-view stamp. It is not write-free.
Sequential retries preserve that timestamp; concurrent first-view behavior is not
qualified by this repair.

## Fresh origin finding and package repair

Refreshing the expired scanner database found **CVE-2026-88806 / HIGH** in
`libx11` **1.8.13-r0** in the first rebuilt origin. This CVE already appears in
the historical Debian/native inventory; this is a newly observed Alpine final-origin
occurrence. It is tracked as **SYS-04-ORIGIN-LIBX11**, with the rejected image
identity and advisory retained in the evidence. The existing runtime APK update
now requires `libx11>=1.8.14-r0`, the reported fixed version, alongside libexpat.
The pinned NGINX base, non-root/read-only operation and severity enforcement stay
in place. The resulting image installs **1.8.14-r0** and its fresh audit has zero
HIGH/CRITICAL rows. Native NGINX/SPA/API recovery below runs against that repaired
image. This fixes this final-origin occurrence; it does not clear the complete
builder/service gate or the historical Debian inventory.

## Validation

- `PW_CHROMIUM_PATH=/usr/bin/chromium npx playwright test e2e/portal.spec.ts --workers=2`: **20 passed**, exit 0.
- `PW_CHROMIUM_PATH=/usr/bin/chromium npm run test:e2e -- --workers=2`: **620 passed**, exit 0, no retries; final application/test sources unchanged throughout. Runs use isolated artifact directories and retained failure traces.
- Existing backend `tests/test_portal.py`: **4 passed / 11 warnings**, exit 0. No backend source or dependency changed in this slice.
- `npm run lint`, `npm run build` and `node scripts/check-bundle.mjs`: pass. First load **434.6 kB raw / 128.3 kB gzip** across four assets, under unchanged **460 / 135** budgets. The baked image passes the same gate.
- Rebuilt origin distribution: **86 files match** the checkout byte for byte. Reused qualified API: **421 application files match** the unchanged checkout.
- Staged-tree PII scan: clean for structural patterns; the owner denylist is empty/pending and history remains unresolved.
- Image audit/supply-chain control regressions: **43 passed**, exit 0.
- Fresh final-origin SBOM/HIGH/CRITICAL audit: **zero advisory rows**, exit 0; complete build/service gate remains blocked separately.
- Native origin/API peaks: **5.72 / 160.80 MiB** under **128 / 768 MiB**, each one CPU, non-root/read-only/capabilities dropped, zero max/OOM events. PostgreSQL uses the existing pinned 16.15 image. Owned fixture containers/network/volume and temporary secrets are removed.

Browser route fixtures use synthetic data. They establish loading, refusal,
error-message and request behavior, not completion of a server-side decision.
The native run described in the evidence uses the rebuilt compiled NGINX image,
unchanged qualified API sources and a fresh PostgreSQL 16 database with a
NOSUPERUSER/NOBYPASSRLS application role. Its fixture proxy replaces two actual
committed HTTP 200 responses with 503: the first portal view and the summary
refresh after a real accepted-offer POST. The browser recovers both reads, keeps
its URL and needs no account session. Database checks confirm one decision audit,
one seeded invoicing plan, unchanged first-view time, zero issued invoices and
zero payments. Revoking the actual token then produces the real 404/contact state.

The native fixture disables rate limiting and sets the image's global NGINX
worker count to two for its one-CPU quota. Application configuration and production
worker defaults are unchanged. Small synthetic origin/API measurements do not
establish minimum-hardware capacity or worst-case fleet headroom.

The first scanner attempt failed to update an expired vulnerability database over
direct container networking. The pinned scanner refreshed it through the environment
proxy with trusted TLS, then the unchanged image audit was rerun. No stale-database
skip or severity exception was used.

Initial failures are retained as evidence and are not counted as passing checks:
wrong initial test-file path/no tests; the original 503 regression failure; a
clock-adjustment regression against the first repair; an
accepted-status selector that incorrectly expected a standalone text node; and an
overlapping focused/broad test invocation that removed shared artifacts and stopped
the shared development server. The first native file control also rejected the
base NGINX 50x.html despite all 86 application assets matching. The final control
verifies that sole inherited extra against the pinned base bytes. A later partial broad run was superseded by the
clock correction and actual-offline regression. The final runs use separate artifact directories
and run sequentially. No browser timeout, snapshot allowance or behavioral assertion was widened to
pass the change.

## Review and rollback

Proposal: use the existing TanStack/Axios/UI components for classified public read
failures, a per-read deadline and explicit recovery. Reloading the whole portal for
every API failure adds document/module requests and loses local state. Automatic
retries obscure server refusals and rate limits. Keeping a generic invalid-link
message leaves valid customers stranded during an outage. No new library is needed.

Adversarial objection: retrying after an uncertain response may repeat a decision,
show stale actionable controls, or leak the URL credential through global errors.
The POST-then-failed-refresh regression, real committed native flow, hidden stale
controls, one-GET pending guard, terminal-status cases and sanitized error text
address those boundaries. The query's handled-error opt-out must remain limited
to pages providing their own complete error surface. Returning online does not
automatically retry; the visible Try again button is the recovery contract.

**Lead review pending:** public failure wording, 15-second read deadline,
Retry-After handling and per-query error ownership. Revert the Portal, QueryCache and portal-spec changes together to roll back
the UI. Retain the independently patched origin runtime; the preceding origin
image now has the reported libx11 advisory and is not a security-qualified rollback. There is no data rollback or migration.

At preceding head `9fa0bf9`, functional CI, PostgreSQL, browser, frontend, lint, PII
and supply-chain checks passed. The complete image gate remains red on the
recorded builder/PostgreSQL advisories and unavailable service references; this
slice does not close them. New-head CI and human acceptance remain required.
Next unblocked: remaining CAP-CAPACITY API/download/verification reads, followed
by worst-case decoding, shared-disk and fleet qualification.
