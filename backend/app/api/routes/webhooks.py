from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.schemas.webhook import (
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryOut,
    WebhookOut,
    WebhookUpdate,
)
from app.services import webhooks

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _require_admin(current: CurrentUser) -> None:
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can manage webhooks")


@router.get("/events")
async def list_event_types(current: CurrentUser):
    """The event types a webhook can subscribe to."""
    return list(webhooks.EVENT_TYPES)


@router.get("", response_model=list[WebhookOut])
async def list_endpoints(current: CurrentUser, db: DbSession):
    rows = await db.scalars(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.org_id == current.org_id)
        .order_by(WebhookEndpoint.created_at.desc())
    )
    return [WebhookOut.model_validate(r) for r in rows]


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_endpoint(body: WebhookCreate, current: CurrentUser, db: DbSession):
    """Register an endpoint. The signing secret is returned ONCE here."""
    _require_admin(current)
    ep = WebhookEndpoint(
        org_id=current.org_id,
        url=body.url,
        events=body.events or "*",
        description=body.description,
        secret=webhooks.new_secret(),
        active=True,
    )
    db.add(ep)
    await db.commit()
    await db.refresh(ep)
    return WebhookCreated.model_validate(ep)


async def _load(db: DbSession, org_id: str, endpoint_id: str) -> WebhookEndpoint:
    ep = await db.scalar(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == endpoint_id, WebhookEndpoint.org_id == org_id
        )
    )
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
    return ep


@router.patch("/{endpoint_id}", response_model=WebhookOut)
async def update_endpoint(
    endpoint_id: str, body: WebhookUpdate, current: CurrentUser, db: DbSession
):
    _require_admin(current)
    ep = await _load(db, current.org_id, endpoint_id)
    for field in ("url", "events", "description", "active"):
        val = getattr(body, field)
        if val is not None:
            setattr(ep, field, val)
    await db.commit()
    await db.refresh(ep)
    return WebhookOut.model_validate(ep)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(endpoint_id: str, current: CurrentUser, db: DbSession):
    _require_admin(current)
    ep = await _load(db, current.org_id, endpoint_id)
    await db.delete(ep)
    await db.commit()


@router.post("/{endpoint_id}/ping", status_code=status.HTTP_202_ACCEPTED)
async def ping_endpoint(endpoint_id: str, current: CurrentUser, db: DbSession):
    """Send a test `ping` event to this endpoint (enqueued like any other)."""
    _require_admin(current)
    await _load(db, current.org_id, endpoint_id)
    n = await webhooks.emit(db, current.org_id, "ping", {"message": "InvoiceIQ webhook test"})
    await db.commit()
    return {"enqueued": n}


@router.get("/{endpoint_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def list_deliveries(
    endpoint_id: str,
    current: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
):
    await _load(db, current.org_id, endpoint_id)
    rows = await db.scalars(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.endpoint_id == endpoint_id,
            WebhookDelivery.org_id == current.org_id,
        )
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return [WebhookDeliveryOut.model_validate(r) for r in rows]
