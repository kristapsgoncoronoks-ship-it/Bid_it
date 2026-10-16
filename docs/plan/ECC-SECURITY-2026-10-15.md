# ECC remaining security repairs — 2026-10-15

Local implementation for review, continuing the user-authorized automatic development sequence. No commit, push, merge or deployment. AI-assisted analysis, implementation, tests and documentation; no external application code copied. Follows [account-cache isolation](ECC-IDENTITY-CACHE-2026-10-15.md) and implements the remaining three findings from the supplied ECC review.

## Session-selected organization and role

The live Session.org_id selects the tenant. Its active Membership supplies role and expense-approver permissions through a frozen core.identity.Principal. Requests never write a tenant projection onto the shared User. A switch updates only that session; independently authenticated sessions retain their organization, permissions and financial destination. User.org_id remains the login/bootstrap default. Password login prefers that default when usable; if it was offboarded or suspended, it selects the newest active membership (with a stable org-id tie-break), returns that workspace and role explicitly, and starts only the new session there. Shared defaults and other sessions remain unchanged. With no usable membership, login still fails closed; the existing platform-operator inactive-default exception is preserved. The switcher list marks current from the Session. Login and invitation responses project the selected membership; a new invitation session starts in the invited organization without repointing existing sessions.

Switch locks and refreshes the session row before checking the source precondition. Two concurrent switches carrying the same old workspace cannot both consume it. Inactive memberships/organizations are refused opaquely; region checks remain. Switch audit attribution includes the actual session.

The SPA sends X-Expected-Org-ID and X-Expected-User-ID from its displayed identity. Scoped requests verify provided headers before business execution; mismatches return 409 / identity_changed. Switch also checks its source after locking. The SPA reloads on that code and restores context; it never automatically replays the write in a different company. Explicit financial-command headers are preserved. Headerless legacy clients remain compatible; integrations showing editable tenant context should send these preconditions. No financial data is rewritten.

## Calendar and tenant offboarding

A feed token alone no longer authorizes a poll. Resolution requires a live global account, the token organization's active membership, active organization and invoice-read permission. The User default org is irrelevant. Denied/unknown URLs return the same opaque 404. The public route applies PostgreSQL tenant context before loading assignments and serves private/no-store responses. Own-assignment narrowing, ICS escaping and money-field exclusions remain.

Membership suspension/reactivation deletes old calendar capabilities in the same transaction. Team and SCIM offboarding use this service; platform/billing org suspension/cancellation delete org capabilities. Reactivation requires a newly obtained URL. Rotation still invalidates the previous URL. Previously downloaded calendar copies cannot be recalled; manual database edits bypass application lifecycle hooks.

The checks exposed tenant deactivation mutating shared User.is_active. Team/SCIM now change only the local membership and revoke its sessions/capabilities. Unrelated memberships/sessions remain usable. SCIM active state is projected from membership plus global account status, using the existing join without an N+1 list query. Global account disable remains an independent gate. Historical global disables are not silently backfilled or undone; an operator must review previously disabled multi-org identities.

## Browser-bound one-use OIDC

Authorize creates a durable short-lived attempt and a random HttpOnly cookie. Only SHA-256 hashes of state and browser secret are stored. Separate cookie names preserve concurrent login tabs. Cookies are host-only, SameSite=Lax, callback-path limited, ten minutes, and Secure on HTTPS or in production.

Callback validates signed state, connection and active organization, verifies the browser binding, and consumes the attempt with a conditional UPDATE. Consumption commits before external IdP I/O/session issuance. Replays/concurrent callbacks cannot consume one attempt twice. A missing/wrong cookie is refused before exchange and cannot consume the initiator's attempt. Expiry fails closed. A bound provider error consumes the attempt; responses clear only its own cookie. Exchange/validation failure requires a new sign-in. The existing login error return now displays that recovery instruction.

PKCE, state expiry, RSA signature, nonce, issuer and audience validation remain. Role sync updates the authoritative membership and cannot demote its owner. SAML assertion consumption and a live enterprise IdP trial remain external prerequisites.

Additive migration d9e0f1a2b3c4 follows c8d9e0f1a2b3: sso_login_attempts, unique state hash, org/expiry index, org FK, forced tenant RLS with the standard null/empty/scoped predicate. Registry and migration coverage include it. Expired attempts are removed per org on later authorize requests; dormant rows contain hashes/timestamps, not IdP tokens or browser secrets. No existing rows are converted. Downgrade drops only attempts; users restart in-progress SSO.

## Actions and maintenance prompts

| Action | Outcome | Engineering prompt |
|---|---|---|
| Organization picker | Changes only this session and reloads context. | Test two sessions with different roles and invoice writes; race source-precondition switches on PostgreSQL; preserve audit attribution and opaque refusals. |
| Scoped reads/writes | Verify the displayed user/org before execution. | Preserve explicit headers; prove a stale tab gets 409, reloads and performs no replacement write. |
| Member Deactivate/Reactivate | Ends local access; restoration needs fresh local capabilities. | Offboard in A while B remains active; A's session/feed die, B stays usable, and A's old feed URL never revives. |
| SCIM replace/patch/delete | Changes the local membership status. | Preserve the global account and other tenants; revoke local sessions/feeds and verify active=false in list/get without an N+1 query. |
| Calendar URL / Regenerate | Polls authorized own assignments; rotation kills old URL. | Cover suspended/removed membership, disabled account, inactive org, rotation/reactivation, escaping and financial-field exclusion on SQLite and PostgreSQL. |
| Start SSO | Establishes a browser transaction and redirects with PKCE. | Verify cookie protections, hashed attempt metadata and concurrent tabs; never log browser secrets, state or PKCE secrets. |
| SSO callback | Consumes one bound attempt and validates before session issuance. | Transfer code/state to a fresh browser, replay the original cookie and race consumers; reject unauthorized/replayed attempts while preserving RSA/nonce/issuer/audience checks. |
| SSO error recovery | Explains how to restart. | Preserve visible recovery on the login return path; never reuse a consumed attempt or silently retry exchange. |

## Review record

Uses existing Session/Membership, SQLAlchemy and browser-cookie mechanisms. A mutable shared User projection was rejected because concurrent requests could persist each other's roles. Cache clearing cannot protect backend write destination. Signed state alone cannot identify a browser; cookie deletion alone cannot prove single consumption under concurrency. The database attempt supplies that proof without a new framework or worker.

Adversarial concern: transport cancellation cannot undo an already accepted server mutation. Existing financial-command identity/status lookup remains necessary. Cookie/session protection does not prove a real IdP deployment works. Lead Developer approval and CI/merge remain pending; no human approval is claimed.

## Local evidence

- Session/auth/membership group: 79 passed.
- Initial calendar/OIDC group: 74 passed, including six new browser-binding/replay cases and eight calendar cases. Follow-on scoped-offboarding group: 39 passed, including the added unrelated-session/feed case.
- Non-superuser PostgreSQL group: 9 passed, including serialized switches, three OIDC consumers with one success, cross-org calendar revocation, raw RLS, connection reuse and predicate shape.
- Migration/tenant-registration/boundary/authorization coverage: 26 passed. SQLite and PostgreSQL upgraded; SQLite alembic check reported no new operations.
- Real Chromium/local API: foreign-browser callback rejected, initiator accepted, original-cookie replay refused. External IdP I/O was simulated; RSA signing/validation ran. Independent sessions retained their org; a stale displayed workspace sent expected headers, received 409, reloaded and created no invoice. The local frontend test proxy forwarded actual backend responses.
- Frontend auth/financial regressions, lint/type/build/bundle checks and whole-app mypy/Ruff ran. Final results are recorded below. Focused groups overlap and must not be summed.

Rollback the scoped security code together, preserving earlier capture/export/payment work. Reverting these controls reintroduces demonstrated disclosure, posting and capability risks. Production IdP credentials/deployment, full visual approval and the previously recorded PostgreSQL FK/model drift remain separate review work. pytest-xdist 3.8.0 was installed only in the local development venv to run four test workers on the four-CPU environment; application dependency declarations are unchanged.


### Final validation and compatibility repairs

- Broad final backend execution: **3,516 passed, 33 skipped, one fixture failure** in 8m44s. That fixture read a password hash from the deliberately credential-free SCIM principal. It now inspects the persisted User instead, preserving the unusable-password assertion; the affected password/SCIM group then passed **16 tests**. No application change was needed for that failure.
- Final login-recovery/auth/offboarding compatibility group: **124 passed**, including the two new fallback cases for unavailable default membership/organization. The SQL expression was also type-checked and the final session/login suite rerun after its type-only construction adjustment. No unresolved observed test failures remain. This is not a claim that CI or a final single full-suite rerun ran after that last fixture/new-branch change.
- Final non-superuser PostgreSQL group: **14 passed** (the ten ECC/RLS checks plus four invoice/cash concurrency cases). SCIM offboarding preserved the unrelated session/feed and fresh password login recovered into the remaining organization.
- Final frontend identity/auth/suspension group: **21 passed**. Earlier R011/R012 browser group: **23 passed**, overlapping identity cases. Lint/type/build passed; first load **435.7 kB raw / 127.6 kB gzip**, under unchanged 460 / 135 budgets.
- Whole-app mypy, Ruff and format checks passed. Generated API/ERD/README truth checks: **12 passed**. Both database upgrades and SQLite model parity passed. Development API health, demo login, scoped identity and a deliberate stale-header refusal were checked; temporary simulated-IdP servers were stopped.
- Existing single-org role fixtures now set the authoritative Membership, rather than quietly leaving its owner privileges intact. No test-time automatic synchronization hook was introduced. The new tenant table has a real callback isolation probe, not a parity exemption.
- Existing intake assertions now cover the two captured line-amount fields. Delivery/view lifecycle tests drive the persisted worker with simulated SMTP acceptance before claiming sent/viewed. Raw SQL date fixtures use ISO values rather than Python 3.12's deprecated implicit adapter. These preserve their original safety assertions.
- The unchanged upload-cap structural gate exposed misplaced derived-output/memory bounds in earlier work. Fixed byte budgets were moved to the existing filesec owner; generated snapshot/JPEG buffers have explicit names. Preview input reads the central upload cap clamped to its 32 MiB purpose. Output remains 512 KiB/page and 2 MiB aggregate, child memory 512 MiB, accountant snapshot 2,000,000 bytes. The relevant cap/preview/export group passed **33 tests**. No scanner exemptions or thresholds were widened.
- The table-count literal was independently re-derived as **112** against model declarations and metadata; six additive local migrations bring the current revision-file count to **142**. README now records the current 3,552 collected test cases, including optional PostgreSQL/slow cases. SSO attempt schema, ERD domain membership and API snapshot were reviewed as contract changes.

The previously reported PostgreSQL portable-model versus column-selective ON DELETE SET NULL drift is unchanged. This task did not rewrite those retention/tenant safeguards. Full CI, container images, visual baseline approval, live enterprise IdP credentials and deployment remain outside these local evidence claims.

Final running-environment check also passed against the normal development API:
company A offboarding invalidated only A's calendar URL, company B's session/feed
stayed usable, and a fresh password login selected B with its accountant role.
After the final SQL type-construction cleanup, mypy passed all 411 app modules,
Ruff/format checks passed 772 Python files, and the six session/login regressions
passed again. No application or frontend source changed after the recorded final
checks. The development API was refreshed with that source; no production system
was accessed or deployed.
