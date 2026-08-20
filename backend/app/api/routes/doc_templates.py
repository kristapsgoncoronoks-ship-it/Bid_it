"""Document templates — the tenant surface (lifecycle phase 5 machinery).

The trust model lives in `services/doc_templates.py`'s docstring: platform
masters are the operator's; a client's SAVED versions are the client's, as many
as they like, chosen per document. Reads ride INVOICE_READ (anyone who works
with documents may generate from a template); saving/adjusting template TEXT is
org configuration — SETTINGS_MANAGE — because the words of a contract are a
decision an org makes once, not per invoice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.services import audit, doc_templates
from app.services.project_profit import NotFoundError, ProjectProfitError

router = APIRouter(
    prefix="/templates",
    tags=["templates"],
    dependencies=[Depends(require_perm(authz.Permission.INVOICE_READ))],
)
_MANAGE = [Depends(require_perm(authz.Permission.SETTINGS_MANAGE))]


class PlatformTemplateOut(BaseModel):
    id: str
    key: str
    kind: str
    name: str
    description: str | None = None
    body: str
    active: bool


class OrgTemplateOut(BaseModel):
    id: str
    kind: str
    name: str
    body: str
    active: bool
    source_platform_id: str | None = None
    created_by: str | None = None
    created_at: str


class TemplateListOut(BaseModel):
    platform: list[PlatformTemplateOut]
    own: list[OrgTemplateOut]


class OrgTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str | None = None
    body: str | None = None
    source_platform_id: str | None = None


class OrgTemplatePatch(BaseModel):
    name: str | None = None
    body: str | None = None
    active: bool | None = None


class RenderPreviewIn(BaseModel):
    template_scope: str  # 'own' | 'platform'
    template_id: str
    project_id: str
    customer_id: str | None = None


def _own_out(t) -> OrgTemplateOut:
    return OrgTemplateOut(
        id=t.id,
        kind=t.kind,
        name=t.name,
        body=t.body,
        active=t.active,
        source_platform_id=t.source_platform_id,
        created_by=t.created_by,
        created_at=t.created_at.isoformat(),
    )


def _raise_dt(exc: ProjectProfitError):
    if isinstance(exc, NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=TemplateListOut)
async def list_templates(current: CurrentUser, db: DbSession):
    data = await doc_templates.org_list(db, current.org_id)
    return TemplateListOut(
        platform=[
            PlatformTemplateOut(
                id=m.id,
                key=m.key,
                kind=m.kind,
                name=m.name,
                description=m.description,
                body=m.body,
                active=m.active,
            )
            for m in data["platform"]
        ],
        own=[_own_out(t) for t in data["own"]],
    )


@router.post(
    "", response_model=OrgTemplateOut, status_code=status.HTTP_201_CREATED, dependencies=_MANAGE
)
async def save_template(body: OrgTemplateIn, current: CurrentUser, db: DbSession):
    """Save an adjusted version — from a platform master or from scratch."""
    try:
        row = await doc_templates.org_create(
            db,
            current.org_id,
            name=body.name,
            kind=body.kind,
            body=body.body,
            source_platform_id=body.source_platform_id,
            created_by=current.email,
        )
    except ProjectProfitError as exc:
        _raise_dt(exc)
    await audit.record(
        db,
        "template.save",
        target_type="org_template",
        target_id=row.id,
        meta={"name": row.name, "kind": row.kind, "source": row.source_platform_id},
    )
    await db.commit()
    await db.refresh(row)
    return _own_out(row)


@router.patch("/{template_id}", response_model=OrgTemplateOut, dependencies=_MANAGE)
async def update_template(
    template_id: str, body: OrgTemplatePatch, current: CurrentUser, db: DbSession
):
    try:
        row = await doc_templates.org_update(
            db,
            current.org_id,
            template_id,
            name=body.name,
            body=body.body,
            active=body.active,
        )
    except ProjectProfitError as exc:
        _raise_dt(exc)
    await audit.record(
        db,
        "template.update",
        target_type="org_template",
        target_id=template_id,
        meta={"name": row.name, "active": row.active},
    )
    await db.commit()
    await db.refresh(row)
    return _own_out(row)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_MANAGE)
async def delete_template(template_id: str, current: CurrentUser, db: DbSession):
    try:
        row = await doc_templates.org_delete(db, current.org_id, template_id)
    except ProjectProfitError as exc:
        _raise_dt(exc)
    await audit.record(
        db,
        "template.delete",
        target_type="org_template",
        target_id=template_id,
        meta={"name": row.name, "kind": row.kind},
    )
    await db.commit()


@router.post("/render-preview")
async def render_preview(body: RenderPreviewIn, current: CurrentUser, db: DbSession):
    """The filled TEXT, before anything is stored — so the person sees exactly
    what the document will say (unknown tokens visibly unreplaced) and fixes
    gaps BEFORE a PDF exists."""
    from app.models.document_template import PlatformTemplate

    try:
        if body.template_scope == "own":
            tpl = await doc_templates._own(db, current.org_id, body.template_id)
            source = tpl.body
        elif body.template_scope == "platform":
            master = await db.get(PlatformTemplate, body.template_id)
            if master is None or not master.active:
                raise NotFoundError("Template not found")
            source = master.body
        else:
            raise ProjectProfitError("template_scope must be 'own' or 'platform'")
        ctx = await doc_templates.build_context(
            db, current.org_id, body.project_id, customer_id=body.customer_id
        )
    except ProjectProfitError as exc:
        _raise_dt(exc)
    return {"text": doc_templates.render(source, ctx)}
