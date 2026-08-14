# WO-100 — H-2, inbound-channel health

**Priority:** BUILD NOW (second harvest work order) · **Source:** `docs/harvest/
CANDIDATES.md` §BUILD NOW, from paperless-ngx scouts S2-1 and S2-7.

## The hole

An inbound channel can die completely while every dashboard stays green. The
mechanism is always the same: success is inferred from a DOCUMENT COUNT, so
"no invoices arrived today" and "nothing could get through today" produce
identical output, and the operator's natural heuristic — it's quiet, must be a
slow week — never fires. We had `email_intake` and a `/health/queue` SLO, but no
per-channel health state at all.

## What was built

`inbound_channel_health`, one row per (org, channel), written on every delivery
attempt independently of whether documents came out of it, and
`GET /email/health` over it.

**Pessimistic-first ordering is the whole guarantee.** `begin_attempt`
increments `consecutive_failures` and **commits before any document work**.
Success is recorded afterwards and joins the caller's transaction, so it lands
only if the documents themselves land. If the request crashes, times out, or is
rolled back halfway, the health row still says the attempt did not succeed. The
naive ordering — record the outcome at the end — is silent about exactly the
deliveries that went most wrong, because the code that would record them is the
code that did not run.

**A rejected document is not a broken channel.** A delivery whose attachments
were all refused by the security gate is recorded as a channel SUCCESS: the
message reached us. Counting it as a channel failure would raise an alarm about
the pipe when the problem is the contents, and send the operator to the wrong
screen. Refused documents are H-1's subject (`capture_failures`), and the two are
kept apart deliberately.

**Sticky vs transient.** A rotated credential or a switched-off module does not
fix itself, so one occurrence is already the alarm. Everything else may be a
blip and alarms after three. Waiting for N occurrences of a failure that will
never stop is waiting forever.

**Quiet is never guessed at.** `expected_cadence_days` is NULL until someone
states it, and NULL means the health view reports the elapsed time as a FACT and
declines to call the channel broken. One customer's fortnight of quiet is
another's outage; a guessed threshold produces alarms nobody trusts, and an alarm
nobody trusts is worse than none. Setting it is audited old→new, because it
decides when the workspace will be warned, and a later "why did nobody tell us?"
deserves an answer.

**A malformed payload is now classified, not merely abandoned.** Attachments are
decoded up front, so a bad base64 part is a clean classified failure with nothing
half-written. The 422 and the "nothing is stored" behaviour are unchanged.

## What we deliberately do NOT record

A delivery rejected at AUTHENTICATION has no tenant. The inbound endpoint checks
the shared secret **before** resolving the address token and returns one
indistinguishable 401 for a bad secret and an unknown token, so the endpoint
cannot be used to enumerate live addresses. Recording those against a guessed org
would be a fabrication, so they are not written to this table at all. The
tenant-visible consequence is still caught from the other side: a channel whose
secret was rotated simply stops succeeding, and `last_success_at` going stale is
exactly what the health view reports.

Direct upload is deliberately not a channel here. A human is watching a browser
tab when it fails, so it cannot die silently, and listing it would pad the screen
with a row that is always green.

## S2-7 (timeouts) — a confirmed NON-gap

The harvest found no timeouts anywhere in paperless-ngx's remote I/O. Ours all
have them already: `oidc.py` (15s), `mailer.py` (20s), `fx.py`, `webhooks.py`,
`billing_provider.py` (20s). So there was nothing to fix — and rather than invent
work, this shipped as a **structural test** that scans `app/` for `httpx` client
construction, `smtplib.SMTP`, and `urlopen` without a timeout. It is a guard
rail against the next one, not a repair of this one.

## Verification

- 14 tests in `backend/tests/test_inbound_channel_health.py`.
- **Four seeded violations, all caught**: (1) the attempt recorded at the END
  instead of pessimistically first, (2) a refused attachment counted as a broken
  channel, (3) a guessed 7-day cadence default, (4) a new `httpx.AsyncClient()`
  with no timeout. Each was applied, the suite run, the expected test went red,
  and the source restored.
- A real tenancy probe for `inbound_channel_health` — both orgs state a DIFFERENT
  cadence for the SAME channel key, so only tenancy can discriminate.
- Migration `d3b8c05f7a41`; migration and tenant-registration guards pass.
- Email-intake and Mailgun suites: 38 passed. Frontend builds and typechecks.

## Known limits

- **One channel exists.** The service is channel-keyed and the route lists every
  known channel including unused ones, so a future pull-based channel is a row,
  not a rewrite — but today `CHANNELS == ("email",)` and the health route lives
  under `/email` because that is where the channel is.
- **No push notification.** This is a screen you visit, not an alert that finds
  you. A daily digest belongs with the existing AP-alerts scheduler and is a
  separate increment; shipping the record first means the alert has something
  truthful to read when it is built.
- **The unattributed-auth-failure count is not surfaced anywhere.** It goes to
  platform logs. Making it visible needs a platform-operator view, which is a
  different audience from this tenant-scoped screen.
