# ADR-0018 — Notifications via webhooks + email on the durable queue

**Status:** Accepted

## Context
The platform must notify external systems (integrations) and people (email: invoices, reminders) reliably, with retries, without blocking request paths or losing messages.

## Selected approach
All notifications flow through the **durable job queue** (ADR-0007):
- **Outbound webhooks** — tenants register signed endpoints; domain actions call `webhooks.emit`, which records a delivery row and enqueues a `webhook.deliver` job. Delivery is a **signed (HMAC-SHA256) POST**, retried with backoff, dead-lettered, with a per-endpoint delivery log. Producers don't know consumers (event fan-out).
- **Email** — invoice/reminder sends go through `mailer` (SMTP relay when configured, else recorded to an outbox), also queue-driven for retries.

`emit` is **best-effort** and never breaks the business action; delivery + retries are the worker's job.

## Alternatives considered
- **Synchronous in-request delivery** — couples the user action to a slow/failing external call; drops on failure.
- **A dedicated event bus (Kafka/SNS)** — powerful pub/sub + replay, but a broker to run for a need the DB queue already covers at our scale.
- **Third-party webhook service (Svix)** — good product, but an external dependency + residency; our queue gives durability + signing already.

## Why appropriate
Reusing the durable queue gives retries/backoff/dead-letter/idempotency for free; signing gives receivers authenticity; best-effort emit protects the core action; the delivery log gives observability. One mechanism for all async side effects.

## Risks
- Slow/hostile receivers → per-delivery timeout, backoff, dead-letter, and (later) per-endpoint rate limiting/circuit-breaking.
- Email deliverability → SMTP relay + SPF/DKIM/DMARC (ops), outbox fallback.

## Revisit when
Cross-service streaming/replay or very high event volume is needed (→ dedicated bus for those event kinds), or webhook fan-out per event grows large enough to need batching/rate controls.
