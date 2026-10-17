"""Durable keyed invoice commands, scoped to the tenant and command kind."""

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError
from app.models.issued_invoice import IssuedInvoice


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def lookup(
    db: AsyncSession, org_id: str, key: str, *, issue: bool = False, expected: str | None = None
) -> IssuedInvoice | None:
    column = IssuedInvoice.issue_command_key if issue else IssuedInvoice.create_command_key
    row = await db.scalar(
        select(IssuedInvoice)
        .where(IssuedInvoice.org_id == org_id, column == key)
        .options(selectinload(IssuedInvoice.lines))
    )
    if row is not None and expected is not None:
        actual = row.issue_command_hash if issue else row.create_command_hash
        if actual != expected:
            raise ConflictError(
                "This command already committed with different inputs. Check its result before "
                "starting another invoice.",
                code="command_payload_conflict",
            )
    return row
