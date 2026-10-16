# ECC account-cache isolation — 2026-10-15

Local implementation for review; no push, merge or deployment. AI-assisted
analysis, implementation, tests and documentation. No external code copied and
no new dependency. Source: the user-supplied ECC review of 2026-10-05, finding 1.

## Confirmed defect and repair

The singleton QueryClient survived sign-out/sign-in. In the real app shell,
Owner B appeared above INVOICE-A after A signed out and B signed in, without a
document reload. The existing 30-second freshness interval also suppressed B's
request. Stale data could continue rendering during subsequent refetches.

AuthProvider now owns identity outside the query provider. A keyed child owns a
fresh QueryClient for each user/org/session revision and effective permission
set. The whole account UI, forms and toast provider remount at that boundary.
Retiring a client cancels its query work and clears query/mutation state. Existing
unscoped domain keys remain usable because ownership, rather than a convention
requiring every query author to remember a prefix, enforces separation.

Each API request records the credential and local generation at dispatch. On
completion, the transport rejects superseded successes/errors as cancellation
before exposing their data or processing a 401. An old 401 therefore cannot
remove B's token or navigate B to login; stale mutation errors cannot disclose
A's error text via global toasts. Cancellations do not retry or raise unexpected
failure toasts. Request credentials and fingerprints are never logged.

Logout clears local ownership before awaiting best-effort server revocation,
which uses the captured old credential. The shell navigates immediately. A late
revocation reply never clears a newer token. Initial identity restoration checks
its generation and effect lifetime; a delayed success or failure cannot overwrite
a newer login. A storage event for a shared credential change/clear ends local
ownership and reloads the document. Identity/permission changes observed through
refresh also change cache ownership. The existing organization switcher retains
its full-document navigation; it does not reuse a previous document's cache.

## Follow-on implementation

The remaining session, calendar and OIDC findings described below were subsequently repaired locally; see [the follow-on implementation and evidence](ECC-SECURITY-2026-10-15.md). Review and CI remain pending.

## Scope and limits

This is client isolation, not a substitute for server authorization or RLS.
The ECC session/org, calendar and OIDC findings required independent backend
repairs. They are now implemented locally with PostgreSQL, two-session and browser
evidence in [the follow-on report](ECC-SECURITY-2026-10-15.md). This client patch
alone does not establish those server guarantees.

Canceling cache work and rejecting a response do not undo a server mutation
already accepted under A's session. Financial commands retain their existing
durable identity/status-lookup mechanisms. Revocation failure ends local access
but does not prove the remote session was revoked. Scoped command recovery data
already stored in sessionStorage is not rewritten or globally deleted here.
No schema, financial rows, dependencies, cookies or token format change.

## Design review record

- Proposal: move query ownership below authentication and reject superseded
  transport responses, preserving the current React/TanStack/Axios architecture.
- Alternative: clear only the singleton client. Rejected because callbacks and
  new observers would still share the same client object after a transition.
- Alternative: add identity to every query key. Useful locally, but leaves
  omissions and local component/mutation state as independent exposure paths.
- Adversarial objection: client recreation alone cannot stop an old Axios 401
  redirect, a global error toast, or server work already in progress. The
  transport generation check addresses the first two; remote completion remains
  explicit and the existing command-recovery contracts are retained.
- Complexity/cost: one client lifetime boundary, an in-memory weak request map
  and monotonic generation. No cache history is retained for quick account reuse.
  Expected benefit: one account cannot inherit another's cached financial UI.
- Lead Developer approval/CI/merge remain pending. This record explains the
  local implementation decision; it does not invent a human approval.

## Actions and engineering prompts

| Action | Result | Maintenance prompt |
|---|---|---|
| Sign in | Applies the new session and mounts fresh account state. | Verify A → sign out → B in one document; dashboard and invoice markers must come from B within and beyond staleTime, including delayed A responses. |
| Register | Applies the created workspace/session through the same boundary. | Preserve the common auth transition; registration must not inherit signed-out or previous-account query/mutation state. |
| Sign out | Ends local ownership immediately and attempts old-session revocation. | Hold or fail the revocation response, sign in as B, then release it; never clear B's token, block login on network latency, or retain A's forms/toasts. |
| Organization picker | Uses the existing full-document switch flow. | Preserve a fresh identity restore; separately fix authoritative session tenancy and expected-org checks before claiming safe independent session switching. |
| Session restoration / refresh | Applies only a current restore; observed identity/permission changes get fresh ownership. | Delay the previous restore through a newer login and return success/failure; neither result may replace or clear the new session. |
| Account change in another tab | Invalidates this tab and reloads its identity. | Use two browser pages with real storage events; both replacement and removal of the credential must remove the old account's cached UI. |

## Evidence

- Initial reproduction displayed B's account menu with A's cached invoice row.
- Counterfactual: temporarily replacing the identity key with a constant shared
  client caused the corrected invoice-isolation regression to fail (exit 1).
  The identity key was restored immediately afterwards.
- Final dedicated Chromium suite: **11 passed**. Covers fresh and stale cache,
  dashboard/invoice markers, failed and slow revocation, late success/401,
  late mutation success/500, delayed initial restore success/500, and real
  cross-tab credential replacement/removal. Same-document sentinels distinguish
  SPA sign-out/sign-in from a cache-clearing browser reload.
- Relevant browser groups passed: **65** (initial seven identity cases plus
  recovery/suspended-workspace), **19** (nine identity cases plus dashboard/auth
  resilience), and **30** (nine identity cases plus billing activation, R011,
  R002 and R012). These runs overlap; their counts are not additive.
- Unmocked local SQLite API and Chromium: created two synthetic accounts and
  source invoices, signed in as A, viewed A's invoice, signed out, signed in as B,
  verified only B's invoice in the same document.
- Frontend lint/type checks, build and bundle gate passed during implementation.
  Full CI, visual baselines and PostgreSQL security gates were not run for this
  frontend-only slice; the follow-on report records the completed local backend evidence.

Rollback the scoped frontend changes together; no data migration is necessary.
Restoring the singleton reintroduces the demonstrated disclosure. Existing
capture/payment/export development changes must be preserved independently.
