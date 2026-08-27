"""Outbound webhooks: notify tenant-registered endpoints of domain events.

`emit()` is called from domain actions (invoice created, payment recorded, …). It
records a `WebhookDelivery` per matching endpoint and enqueues a durable
`webhook.deliver` job — so delivery inherits the queue's retry/backoff/dead-letter
guarantees for free. The handler POSTs the signed JSON and records the outcome.

Every request is signed HMAC-SHA256 over the exact body with the endpoint's
secret (header `X-InvoiceIQ-Signature: sha256=<hex>`), so a receiver can verify
authenticity. emit() is best-effort and never raises into the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import webhook as wh
from app.models.webhook import WebhookDelivery, WebhookEndpoint

log = logging.getLogger("invoiceiq.webhooks")


class UnsafeWebhookUrl(ValueError):
    """The endpoint URL points at a private/reserved address (SSRF guard)."""


def _addr_is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_url(url: str) -> None:
    """Reject a webhook URL that targets the internal network (SSRF defense).

    Blocks non-http(s) schemes, `localhost`, and any host that IS or RESOLVES TO a
    private / loopback / link-local / reserved address (e.g. 127.0.0.1, 10.x,
    169.254.169.254 cloud metadata, ::1). DNS resolution FAILS OPEN — an
    unresolvable host (offline/test) is allowed through; the delivery simply won't
    connect. Called on create/update AND again at delivery time (defense in depth
    against DNS rebinding). Raises UnsafeWebhookUrl."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise UnsafeWebhookUrl("url must use http or https")
    host = p.hostname
    if not host:
        raise UnsafeWebhookUrl("url has no host")
    h = host.lower()
    if h == "localhost" or h.endswith(".localhost"):
        raise UnsafeWebhookUrl("url host is not allowed")
    # IP literal → classify directly (no DNS).
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _addr_is_public(literal):
            raise UnsafeWebhookUrl("url targets a private or reserved address")
        return
    # Hostname → resolve; fail OPEN on resolution error, CLOSED on any private hit.
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        return
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not _addr_is_public(addr):
            raise UnsafeWebhookUrl("url resolves to a private or reserved address")


# Known event types (documentation + the /webhooks/events catalog).
EVENT_TYPES = (
    "invoice.created",
    # AP review & approval lifecycle (Phase 08)
    "invoice.submitted",
    "invoice.approved",
    "invoice.rejected",
    "invoice.returned",
    "invoice.reassigned",
    "invoice.scheduled_for_payment",
    "invoice.paid",
    "issued.payment",
    "issued.credit_note",
    "expense.submitted",
    "expense.approved",
    "expense.rejected",
    "expense.returned",
    "expense.reassigned",
    "expense.reimbursement_created",
    "expense.reimbursed",
    # WO-W: an automation rule reached an external system. ONE type for every
    # rule — which rule fired is in the payload, not the event name, so a
    # receiver can subscribe to "automation" once instead of guessing at names
    # a workspace invented.
    "automation.fired",
    "ping",
)

WEBHOOK_DELIVER = "webhook.deliver"
_TIMEOUT_SECONDS = 10


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _subscribed(endpoint: WebhookEndpoint, event_type: str) -> bool:
    spec = (endpoint.events or "*").strip()
    if spec == "*":
        return True
    wanted = {e.strip() for e in spec.split(",") if e.strip()}
    return event_type in wanted


async def emit(
    db: AsyncSession,
    org_id: str,
    event_type: str,
    data: dict,
    *,
    idempotency_key: str | None = None,
) -> int:
    """Fan an event out to every active, subscribed endpoint (best-effort).

    Returns the number of deliveries enqueued. Never raises — a webhook problem
    must not break the business action that produced the event.

    IDEMPOTENCY (WO-W). Until this, every call created a delivery per endpoint
    unconditionally, so a caller that retried — a client resubmitting after a
    timeout, a job re-running, an operator double-clicking — delivered the same
    event to the customer's system twice. `emit` is called from routes that
    perform real business actions, and "the invoice was approved" arriving twice
    is not a cosmetic problem for whatever is listening.

    Pass `idempotency_key` and a repeat call becomes a no-op FOR THE ENDPOINTS
    ALREADY HOLDING IT. The dedup is a unique index, not a pre-SELECT: two
    concurrent callers would both pass a check-then-insert, and this is exactly
    the shape where they race. Each endpoint is inserted in a SAVEPOINT so a
    collision rolls back that one row and leaves the others — a second endpoint
    added between the two calls still gets its first delivery.

    The key is OPT-IN. A caller with no natural key must not invent one: an
    invented key that collided would SUPPRESS a delivery that should have
    happened, which is worse than the duplicate it was meant to prevent. NULL
    keys are excluded from the index and behave exactly as before.

    Returns the number of deliveries actually ENQUEUED, so a fully-deduplicated
    repeat returns 0 — which is the truth, and what a caller wanting to log
    "already sent" needs.
    """
    try:
        from app.services import jobs  # local import avoids a cycle

        endpoints = list(
            await db.scalars(
                select(WebhookEndpoint).where(
                    WebhookEndpoint.org_id == org_id, WebhookEndpoint.active.is_(True)
                )
            )
        )
        enqueued = 0
        for ep in endpoints:
            if not _subscribed(ep, event_type):
                continue
            payload = {"event": event_type, "data": data}
            delivery = WebhookDelivery(
                org_id=org_id,
                endpoint_id=ep.id,
                event_type=event_type,
                payload_json=json.dumps(payload, sort_keys=True),
                status=wh.PENDING,
                idempotency_key=idempotency_key,
            )
            # SAVEPOINT per endpoint: a unique-index collision must roll back
            # THIS row only. Without it the IntegrityError would poison the
            # caller's whole transaction — and `emit` is called from inside
            # business operations that have already done their real work.
            try:
                async with db.begin_nested():
                    db.add(delivery)
                    await db.flush()  # assign delivery.id before enqueuing
            except IntegrityError:
                # Already enqueued under this key for this endpoint. The
                # duplicate is the point of the key, so this is a success.
                log.info(
                    "webhook emit deduplicated: %s to endpoint %s (key %s)",
                    event_type,
                    ep.id,
                    idempotency_key,
                )
                continue
            await jobs.enqueue(
                db, WEBHOOK_DELIVER, {"delivery_id": delivery.id}, org_id=org_id, commit=False
            )
            enqueued += 1
        return enqueued
    except Exception as exc:  # noqa: BLE001 — emit is best-effort
        log.warning("webhook emit failed for %s: %s", event_type, exc)
        return 0


async def _http_post(url: str, body: bytes, headers: dict) -> tuple[int, str]:
    """The network seam (patched in tests). Returns (status_code, response_text)."""
    import httpx

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, content=body, headers=headers)
        return resp.status_code, resp.text[:500]


async def deliver(db: AsyncSession, delivery_id: str) -> dict:
    """Deliver one recorded event to its endpoint. Raises on non-2xx so the job
    queue retries; the delivery row always reflects the latest attempt."""
    delivery = await db.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return {"skipped": "delivery gone"}
    endpoint = await db.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None or not endpoint.active:
        delivery.status = wh.FAILED
        delivery.last_error = "endpoint removed or inactive"
        await db.commit()
        return {"skipped": "endpoint inactive"}

    # Re-check the target at delivery time — the URL may have been repointed at an
    # internal host (or DNS-rebound) since it was registered. A blocked URL is a
    # terminal failure, not a retry.
    try:
        assert_public_url(endpoint.url)
    except UnsafeWebhookUrl as exc:
        delivery.status = wh.FAILED
        delivery.last_error = f"blocked: {exc}"
        await db.commit()
        return {"skipped": "unsafe url"}

    body = delivery.payload_json.encode()
    headers = {
        "Content-Type": "application/json",
        "X-InvoiceIQ-Event": delivery.event_type,
        "X-InvoiceIQ-Delivery": delivery.id,
        "X-InvoiceIQ-Signature": sign(endpoint.secret, body),
    }
    delivery.attempts += 1
    try:
        code, text = await _http_post(endpoint.url, body, headers)
    except Exception as exc:  # noqa: BLE001 — network error → retry
        delivery.status = wh.FAILED
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        await db.commit()
        raise

    delivery.response_code = code
    if 200 <= code < 300:
        delivery.status = wh.DELIVERED
        delivery.last_error = None
        delivery.delivered_at = datetime.now(UTC)
        await db.commit()
        return {"delivered": True, "code": code}

    delivery.status = wh.FAILED
    delivery.last_error = f"HTTP {code}: {text}"[:2000]
    await db.commit()
    raise RuntimeError(f"webhook returned HTTP {code}")
