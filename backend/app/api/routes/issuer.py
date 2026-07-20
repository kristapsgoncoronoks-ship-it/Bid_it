from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.core.roles import is_admin_or_above
from app.schemas.issuer import IssuerProfileIn, IssuerProfileOut
from app.services import issuer

router = APIRouter(prefix="/issuer", tags=["issuer"])

_ALLOWED_LOGO = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff"}


def _out(profile) -> IssuerProfileOut:
    data = IssuerProfileOut.model_validate(profile)
    data.missing_fields = issuer.missing_fields(profile)
    data.is_complete = not data.missing_fields
    data.has_logo = profile.logo_data is not None
    return data


@router.get("", response_model=IssuerProfileOut)
async def get_issuer(current: CurrentUser, db: DbSession):
    return _out(await issuer.get_or_create(db, current.org_id))


@router.put("", response_model=IssuerProfileOut)
async def update_issuer(body: IssuerProfileIn, current: CurrentUser, db: DbSession):
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can edit company details")
    profile = await issuer.get_or_create(db, current.org_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in ("country", "default_currency") and value:
            value = value.upper()
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return _out(profile)


@router.post("/logo", response_model=IssuerProfileOut)
async def upload_logo(current: CurrentUser, db: DbSession, file: UploadFile):
    if not is_admin_or_above(current):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can edit company details")
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Logo too large (max 2 MB)")
    mime = file.content_type or "image/png"
    magic = _ALLOWED_LOGO.get(mime)
    if magic is None or not content.startswith(magic):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Logo must be a PNG or JPEG image")
    profile = await issuer.get_or_create(db, current.org_id)
    profile.logo_mime = mime
    profile.logo_data = content
    await db.commit()
    await db.refresh(profile)
    return _out(profile)


@router.get("/logo")
async def get_logo(current: CurrentUser, db: DbSession):
    profile = await issuer.get_or_create(db, current.org_id)
    if not profile.logo_data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No logo set")
    return Response(content=profile.logo_data, media_type=profile.logo_mime or "image/png")
