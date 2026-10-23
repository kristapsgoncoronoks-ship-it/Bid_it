"""Irreversibly remove calendar capabilities when their access ends."""

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar_connection import CalendarConnection, CalendarOAuthAttempt
from app.models.calendar_token import CalendarFeedToken


async def revoke(db: AsyncSession, org_id: str, user_id: str | None = None) -> None:
    statement = delete(CalendarFeedToken).where(CalendarFeedToken.org_id == org_id)
    if user_id is not None:
        statement = statement.where(CalendarFeedToken.user_id == user_id)
    await db.execute(statement)
    # Access restoration must not silently revive an old external-calendar
    # credential. Already exported events are owned by the external account;
    # stop future exports and require a new explicit connection.
    connections = update(CalendarConnection).where(CalendarConnection.org_id == org_id)
    attempts = delete(CalendarOAuthAttempt).where(CalendarOAuthAttempt.org_id == org_id)
    if user_id is not None:
        connections = connections.where(CalendarConnection.user_id == user_id)
        attempts = attempts.where(CalendarOAuthAttempt.user_id == user_id)
    await db.execute(
        connections.values(
            status="disconnected",
            sealed_tokens=None,
            error=None,
            generation=CalendarConnection.generation + 1,
        )
    )
    await db.execute(attempts)
