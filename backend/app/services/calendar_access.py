"""Irreversibly remove calendar capabilities when their access ends."""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar_token import CalendarFeedToken


async def revoke(db: AsyncSession, org_id: str, user_id: str | None = None) -> None:
    statement = delete(CalendarFeedToken).where(CalendarFeedToken.org_id == org_id)
    if user_id is not None:
        statement = statement.where(CalendarFeedToken.user_id == user_id)
    await db.execute(statement)
