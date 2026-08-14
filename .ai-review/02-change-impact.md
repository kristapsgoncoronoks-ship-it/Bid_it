# Change impact

**Base:** `origin/main` @ `46ea0b2` (clean linear ancestor; branch 355 ahead / 0 behind).
**Reviewed:** `c2948cb..HEAD` — 33 code files, 4063 insertions, 45 deletions.

No graph tooling was available, so blast radius was established by import tracing, caller
grep, and running the affected suites. Depth-2 dependents were checked; deeper transitive
effects rely on the full suite, which is stated below.

## Changed components

| Component | Kind | Risk | Why |
|---|---|---|---|
| `services/capture_failures.py` | new | LOW | Read-only over two tables; writes only its own ack rows |
| `services/inbound_health.py` | new | **MEDIUM** | Writes on a public webhook path and **commits mid-request** |
| `services/vendor_resolution.py` | new | LOW | Pure read; creates nothing (test-proven) |
| `services/extraction_provider.py` | modified | **MEDIUM** | Changes the exception *type* raised by the shared parse choke point |
| `services/extraction.py` | modified | LOW | Adds `failure_code`; clears it on success/retry |
| `services/email_intake.py` | modified | LOW | Adds `failure_code` on two existing failure paths |
| `api/routes/email.py` | modified | **MEDIUM** | Restructured both public inbound handlers |
| `api/routes/invoices.py` | modified | LOW-MED | New routes + an audit write on the invoice-create path |
| `api/routes/vendors.py` | modified | LOW | One new read-only route, declared before `/{vendor_id}` |
| 2 migrations | new | LOW | Purely additive: 2 nullable columns, 2 new tables |
| 4 frontend files | modified/new | LOW | One nav regression, found and fixed (F-02) |

## Highest-risk symbol: `extraction_provider.select()` / `PdfProvider` / `ImageProvider`

These now raise `CaptureError` instead of a bare `ValueError`.

**Blast radius traced:**

```
extraction_provider.select/extract
  → parser._dispatch_parse → parser.parse_invoice_file        (the single parse choke point)
      → extraction.extract_upload         (worker: broad except — now classifies)
      → email_intake.extract_inbound      (worker: except ValueError — now classifies)
      → 36 other `except ValueError` sites across the app
```

**Why this is safe:** `CaptureError` subclasses `ValueError`, so every one of those 36 sites
catches it exactly as before. The change is purely additive information on the exception
object. Verified by grep of all catch sites and by the extraction/email suites passing.

## Second-highest risk: `inbound_health.begin_attempt` commits mid-request

This is the one genuinely novel runtime behaviour in the change set: a `db.commit()` in the
middle of a public webhook handler, before the document work.

**Traced:** at the commit point, nothing else is pending on the session — the module gate and
org resolution are reads, and no `InboundInvoice` row has been added yet. So the commit
flushes only the health row. On the failure path (`record_failure`) the handler raises
immediately after, so nothing is left half-written.

**`NEEDS VERIFICATION`:** behaviour under *concurrent* deliveries for the same org is
unproven in either direction. Two simultaneous deliveries both read the same
`consecutive_failures`, both increment, and one write is lost — the counter could under-count.
This affects only the *transient* alarm threshold (3), never a sticky failure (which alarms
at 1), and never a success reset. I did not write a concurrency test and am not claiming it
is correct.

## Affected execution flows

1. **Direct upload → parse → review**: unchanged on success; on failure now also records a
   classified code and surfaces on a new worklist.
2. **Email delivery → security gate → parse → review inbox**: restructured. Attachments are
   now decoded up front (same 422, same "nothing stored" behaviour — test-proven), and the
   channel's health is recorded before and after.
3. **Invoice create from a supplier name**: now additionally writes a `vendor.auto_resolved`
   audit event. **This is the one flow where an existing, widely-used path gained a write.**
   Verified across 428 tests in the vendor/invoice/audit/capture/review suites.
4. **Capture review screen load**: now additionally calls `GET /vendors/resolve`.

## Compatibility

- **API:** purely additive. Three new endpoints; no existing response shape changed, no field
  removed, no status code altered.
- **Database:** purely additive. Two nullable columns and two new tables. Nothing back-filled
  — deliberately, since a failure recorded before the contract existed genuinely has no
  classified cause and deriving one from an old message would manufacture a fact.
- **Rollback:** both migrations have working `downgrade()`. Rolling back drops the
  acknowledgement history and the health record; no pre-existing data is touched.

## Regression risks

| Risk | Assessment |
|---|---|
| Nav e2e break | **Materialised.** Found and fixed (F-02) |
| `mypy` gate | **Materialised** (inherited). Found and fixed (F-01) |
| Exception-type change breaking a catch site | Checked all 36 — none affected |
| New audit event breaking a test that counts events | Checked — 428 passed |
| Route-order shadowing (`/captures/failures` vs `/captures/{run_id}`; `/vendors/resolve` vs `/vendors/{vendor_id}`) | Both static routes declared first; verified by tests hitting them |
| Mid-request commit corrupting the audit hash-chain | Traced — nothing audit-related is pending at that point |
