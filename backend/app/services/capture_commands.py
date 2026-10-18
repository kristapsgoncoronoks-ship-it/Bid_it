"""Per-file command identity; the upload workspace lock owns admission writes."""

import hashlib
import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.capture_command import CaptureCommand
from app.models.extraction_run import ExtractionRun

KEY_PATTERN = r"^[A-Za-z0-9._:-]{8,120}$"


def valid_key(key: object) -> bool:
    return isinstance(key, str) and re.fullmatch(KEY_PATTERN, key) is not None


def fingerprint(sha256: str, filename: str, mime: str | None, override: bool) -> str:
    payload = {"v": 1, "sha256": sha256, "filename": filename, "mime": mime, "override": override}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def lookup(
    db: AsyncSession, org_id: str, key: str, *, expected: str | None = None
) -> tuple[CaptureCommand, ExtractionRun | None] | None:
    command = await db.scalar(
        select(CaptureCommand).where(
            CaptureCommand.org_id == org_id, CaptureCommand.command_key == key
        )
    )
    if command is None:
        return None
    if expected is not None and command.payload_hash != expected:
        raise ConflictError(
            "This upload command committed with different file inputs. Check its result before starting another upload.",
            code="command_payload_conflict",
        )
    run = await db.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.org_id == org_id, ExtractionRun.admission_command_id == command.id)
        .execution_options(populate_existing=True)
    )
    if expected is not None and run is None:
        raise ConflictError(
            "This upload committed, but its capture is no longer available. Check Captures before intentionally uploading again.",
            code="capture_command_retired",
        )
    return command, run
