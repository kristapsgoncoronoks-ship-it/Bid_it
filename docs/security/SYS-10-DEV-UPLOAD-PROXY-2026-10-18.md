# SYS-10 — truthful development upload refusals

**Status:** implemented with local protocol and native workflow evidence; exact-head
CI and human/Lead review pending. This changes Vite development serving only.
The release image gate and aggregate CAP-CAPACITY finding remain open.

## Problem and repair

The existing Vite 8.2.2 proxy eagerly streams request bodies. When an API ingress
check responds before consuming a large upload, its request-side `write EPIPE`
can win the race against reading the response. Vite then substitutes a generic
502 for the API's actionable 413 or 503. A real-Vite/synthetic-HTTP regression
reproduced **four failures and one pass** before the repair: both statuses failed
with both declared and chunked bodies; accepted upload behavior already passed.

`frontend/dev/upload-proxy.mjs` installs a middleware through Vite's supported
`configureServer` hook, before its ordinary proxy. It handles API POST multipart
requests and generic `/email/inbound` JSON intake. Other requests retain Vite's
existing proxy. Both paths use the same `VITE_API_PROXY` target.

For these uploads, native Node HTTP/HTTPS sends headers with **Expect: 100-continue**
and waits before piping the incoming body. Uvicorn sends Continue when its ASGI
application first calls receive. The existing ingress guard can therefore send
its declared-size or busy refusal without receiving file bytes. There is no
fallback that sends the body on a timer. A missing handshake produces an explicit
504 after ten seconds, with no-store and status-check guidance. This is a
development handshake deadline, not a new API parsing or worker deadline.

Every proxied upload requests **Connection: close upstream**. Native qualification
found this necessary: after a refusal without sending the declared body, a reused
connection could interpret the next request as the previous body's missing bytes.
The first prototype displayed the error but stalled the following valid import.
The separate connection repairs that recovery path. Ordinary API requests keep
their current connection behavior.

Bodies remain native streams with backpressure; there is no whole-body buffer,
temporary file or explicit drain of rejected client bodies. When a final response
arrives, forwarding stops. An unfinished downstream request closes after the
response is flushed. Disconnects cancel the upstream request/response and clear
the handshake timer. Connection-specific headers are removed and each connection
gets its own framing. End-to-end authentication, cookies, command keys, identity
headers, repeated Set-Cookie, redirect status/Location and compressed bytes are
preserved. HTTPS retains certificate verification.

An available response retains its actual status/body. A transport error before
response headers are obtained cannot establish whether the API admitted work; it
returns sanitized 502 `development_proxy_unavailable`, with status-check guidance.
An abruptly closed mid-stream refusal can have this outcome. EPIPE alone is never
translated into an invented 413/503 or success. A truncated response fails its
stream rather than becoming a completed download. Capture command lookup and
same-key retry continue to own admission truth.

## Evidence and repeatable checks

Run `cd frontend && npm run test:proxy`. CI runs the same command in the existing
frontend job. The suite starts the **actual repository Vite configuration**, not
a reimplementation of the middleware. Its **19 passing tests** cover:

- Twenty repeated early refusals across declared/chunked 413/503, zero upstream
  body bytes, preserved no-store/Retry-After/body and separate upload connections.
- Accepted declared/chunked bytes and original command/authentication/identity
  headers; generic inbound JSON also negotiates before forwarding.
- Known mid-stream response preservation and an abrupt-close uncertainty control;
  connection refusal, transport-only loss, cancellation and restart recovery.
- Repeated cookies, unfollowed redirects, exact gzip bytes/encoding/length,
  incremental streaming, truncated downloads and unchanged ordinary JSON/GET paths.
- The real ten-second handshake timeout; an ephemeral HTTPS fixture is rejected
  without trust and accepted after explicitly trusting only its test CA.

TLS fixtures use OpenSSL, available on the existing Ubuntu CI runner, and are
removed after the test. No TLS verification is disabled. No dependency, lockfile,
API body/file limit, test timeout, assertion budget or existing security gate was
weakened. One new mid-stream test distinguishes a deliberately available response
from a socket-close race; neither fabricates an upstream outcome.

A real Chromium → current Vite → Uvicorn → supported development SQLite → real
CSV worker run passed without mocking network outcomes:

| Action | Observed result / development prompt |
|---|---|
| Import an oversized statement, then reselect a valid file with the same name | Browser sees **413 then 201**; one persisted statement/line. Preserve actionable refusal and immediately usable recovery. |
| Upload while two direct API requests hold both ingress slots | Browser sees **503 `upload_busy`**, no admission, and visible capacity guidance; health stays 200. Preserve early backpressure without sending rejected bytes upstream. |
| Retry after releasing those slots | Browser sees **202**, uses the original command key and lookup returns its capture. Exactly one command and one capture exist; the real worker reaches `parsed` and stored source bytes match. Preserve status lookup and same-key retry on uncertainty. |

The native run uses synthetic data and the supported development database profile;
it is not new PostgreSQL/RLS, production TLS, NGINX or fleet-capacity qualification.
Temporary trace hooks observed only status, byte counts and connection events.
Fixture processes were stopped. The first fixture attempts also exposed a reserved
email-domain validation error and a cancellation-wait error in the harness; those
were corrected before the successful run, without changing application contracts.

Also passed: **20 existing browser regressions**, **47 deployment/supply-chain/PG-list/
documentation controls**, frontend lint/build, action pins, the 15-control generated
view and current-tree structural PII scanning. First load stays **435.8 kB raw /
127.6 kB gzip**, within unchanged 460/135 limits. The empty owner denylist and
historical PII remain unresolved. [Sanitized evidence and hashes](evidence/SYS-10-DEV-UPLOAD-PROXY-2026-10-18.json).

Prior head `265b086` [CI run 39112353301](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/39112353301)
completed: **3,846 backend passed / 109 skipped / 3,220 warnings**, PostgreSQL,
**595 browser and 13 visual tests**, frontend and lint passed. The image artifact
confirms both final app images clean, backend builder 4 HIGH, frontend builder
18 HIGH, PG 24 HIGH + 1 CRITICAL, and three unavailable service images. Its overall
failure remains valid; it is not exact-head CI for this subsequent proxy repair.

## Alternatives, risk and review

**Proposal/benefit:** a narrowly scoped development upload transport preserves
known refusals and recovery without weakening early API admission.

**Alternatives:** direct API origins require coordinating browser base URLs/CORS;
suppressing Vite errors cannot recover a response already lost; draining API bodies
defeats the ingress boundary. The bundled proxy's optional fetch path introduces
redirect/decompression/repeated-cookie compatibility questions. Replacing all API
proxying or adding another proxy library expands the scope. None was selected.

**Adversarial objection:** custom transport has lifecycle and HTTP compatibility
cost, and Expect is not honored by every upstream. Native tests caught an actual
connection-reuse defect before publication. Real-Vite framing/streaming/TLS tests
now cover this boundary, the ten-second handshake fails explicitly, and ordinary
traffic keeps the existing path. An operator-selected intermediary that returns
100 itself can still forward into a later refusal; unavailable responses remain
uncertain and must use command lookup. No universal HTTP-delivery guarantee is made.

**Complexity/rollback:** one dependency-free development middleware and one protocol
suite; no schema/transaction changes. Remove its Vite hook to restore the previous
development path, retaining API limits and capture receipts. The reproduced masked
refusal then returns. No production deployment is performed by this change.

**Lead decision: pending.** Review handshake/connection lifecycle, proxy compatibility,
uncertain outcomes and test evidence. AI-assisted investigation, implementation,
tests and documentation; no external implementation copied. Next unblocked work
is CAP-CAPACITY, while owner/vendor/release gates stay visible.

References consulted: [Vite configureServer](https://vite.dev/guide/api-plugin.html#configureserver),
[Node Continue event](https://nodejs.org/api/http.html#event-continue),
[Node flushHeaders](https://nodejs.org/api/http.html#requestflushheaders),
and the installed Uvicorn HTTP protocol implementation's receive/Continue handling.
