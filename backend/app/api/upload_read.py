"""HTTP upload read boundary; metadata remains usable after closing the spool."""

from fastapi import HTTPException, UploadFile, status

from app.services import filesec


async def read_upload(upload: UploadFile, purpose: str | None = None) -> bytes:
    try:
        return await filesec.read_bounded(upload, purpose)
    except filesec.FileTooLarge as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    finally:
        await upload.close()
