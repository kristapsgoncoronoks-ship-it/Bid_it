# Security review

## Scope and limits

Reviewed: `c2948cb..HEAD` only — three new services, three routers, two migrations, two new
tenant tables, four frontend files.

**Limit that must be stated up front:** the dedicated independent security reviewer did not
complete (killed by an account session limit before producing findings). What follows is my
own review of my own code, which is a weaker instrument than an adversarial second reader.
Treat the conclusion accordingly.

## Tenant isolation

This repository defends tenancy in three layers (`app/core/tenant.py`): per-query `org_id`
filters, a `do_orm_execute` hook that ANDs the current-org predicate onto every SELECT
touching a registered model, and Postgres `FORCE ROW LEVEL SECURITY`.

| Check | Result |
|---|---|
| `capture_acknowledgements` in `TENANT_MODELS` | Yes — `app/core/tenant.py:137` |
| `inbound_channel_health` in `TENANT_MODELS` | Yes — `app/core/tenant.py:138` |
| RLS enabled + forced + policy'd in the creating migration | Yes — both migrations |
| Real parity probe (not an EXEMPT row) | Yes — both, added in the same commit as the table |
| Probes use overlapping business values so only tenancy discriminates | Yes — identical unparseable file in both orgs; identical channel key with different cadences |

**One finding: F-03** — the mailgun route performed its health writes outside the active
tenant context, disabling layer 2 for those statements. Not a leak (layers 1 and 3 both held,
and the writes carry an explicit `org_id`), but the belt was off. **Fixed.**

## Opaque 404 (§4.4)

`POST /invoices/captures/failures/{channel}/{ref_id}/acknowledge` returns a plain 404 for a
reference that is not a failed capture *in the caller's tenant*. The service returns `None`
rather than raising a distinguishable error, and the route maps that to one 404. A
cross-tenant id and a nonexistent id are indistinguishable, verified by
`test_acknowledging_an_unknown_reference_is_an_opaque_404` and by the cross-tenant leg of the
parity probe. A capture that exists but did **not** fail also returns the same 404 — correct,
since the alternative would confirm the id exists.

## Inbound webhook authentication

The change set did **not** weaken the inbound endpoints. The shared-secret check
(`hmac.compare_digest`) and Mailgun HMAC + freshness window still run *before* tenant
resolution, and every failure mode still returns the single `_inbound_auth_failed()`
response, so a prober still cannot distinguish "wrong secret" from "no such tenant".

**A question I asked deliberately and answered:** does the new error classification let a
caller distinguish failure modes it previously could not? Partially — the module-gate path
already returned a distinguishable 403 before this change, and still does; that is
pre-existing. The new `ERR_MALFORMED` path returns the same 422 as before with the same body.
No new discriminator was introduced.

**Deliberate non-recording:** authentication failures are *not* written to
`inbound_channel_health`, because at that point the tenant is genuinely unknown — the secret
is checked before the token is resolved, on purpose. Attributing them to a guessed org would
be a fabrication. This is documented in `inbound_health.py` and is the right call; the
consequence is still visible via `last_success_at` going stale.

## Information disclosure

- `GET /invoices/captures/failures` returns a `detail` field carrying raw library error text
  (e.g. a CSV parser message). It is derived from the tenant's own uploaded document and is
  tenant-scoped. **`NEEDS VERIFICATION`:** I did not exhaustively enumerate every exception a
  third-party parser can raise, so I cannot promise no library ever embeds an absolute
  server path in its message. The frontend already hides it behind a disclosure element. If
  a stricter posture is wanted, whitelist `detail` to the classified code and drop the raw
  text for non-admin roles.
- `GET /vendors/resolve` echoes supplier names from the caller's own tenant only
  (`select(Vendor).where(Vendor.org_id == org_id)`), gated by router-level `INVOICE_READ`.
  It creates nothing — proven by `test_resolving_creates_nothing`.

## Injection / unsafe operations

Checked and clean in scope: no `eval`, `exec`, `shell=True`, subprocess, `pickle`, dynamic
import, raw SQL string interpolation, or path construction from user input in any new file.
All queries are SQLAlchemy expression-language.

**Regex DoS considered:** `vendor_resolution.py` compiles `_PUNCT = [^\w\s]` and
`_SPACE = \s+`, both linear with no nested quantifiers or backtracking — not vulnerable.
Unicode normalisation (`NFKD`) is applied to supplier names bounded by the DB column length.

**Unbounded memory considered:** `POST /email/inbound` now base64-decodes all attachments
into a list before writing any. Peak memory is unchanged — the entire JSON body, including
every base64 payload, was already fully parsed into memory by the request layer before the
handler ran. The upload size cap (`filesec`) still applies per attachment.

## Audit integrity

Two new audit actions: `capture.failure_acknowledged` and `inbound.cadence_changed`, plus
`vendor.auto_resolved`. All three take `actor` from the session, never from the request body
(the acknowledge body carries only an optional `note`). Meta contains a channel key, an
operator note, counts, a captured supplier name and candidate names — no IBAN, token,
password, or document content. Each is written in the same transaction as the mutation it
describes (§4.16).

## Conclusion

**No security vulnerability was identified within the reviewed scope.**

That is not the same statement as "the application is secure", and it is weaker than usual
here because the independent adversarial security review did not run. One defense-in-depth
gap (F-03) was found and fixed. Two verification gaps remain open: no test proves the new
mutating routes reject a caller lacking `INVOICE_WRITE` (F-08), and the `detail` passthrough
carries a residual, unquantified disclosure question flagged above.
