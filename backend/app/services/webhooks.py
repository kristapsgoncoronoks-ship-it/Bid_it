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
import json
import logging
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import webhook as wh
from app.models.webhook import WebhookDelivery, WebhookEndpoint

log = logging.getLogger("invoiceiq.webhooks")

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


async def emit(db: AsyncSession, org_id: str, event_type: str, data: dict) -> int:
    """Fan an event out to every active, subscribed endpoint (best-effort).

    Returns the number of deliveries enqueued. Never raises — a webhook problem
    must not break the business action that produced the event.
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
            )
            db.add(delivery)
            await db.flush()  # assign delivery.id before enqueuing the job
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
