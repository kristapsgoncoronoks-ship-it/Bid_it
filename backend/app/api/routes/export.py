from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.core.errors import ConflictError
from app.core.security_headers import content_disposition
from app.models.issuer import IssuerProfile
from app.schemas.accounting_export import (
    AccountingDownload,
    AccountingEntity,
    AccountingPreview,
    AccountingScope,
)
from app.services import accounting_preview, erp_export

# Structural authorization (ADR-0024): the export surface is EXPORT_RUN.
router = APIRouter(
    prefix="/export",
    tags=["export"],
    dependencies=[Depends(require_perm(authz.Permission.EXPORT_RUN))],
)


@router.get("/formats")
async def list_formats(current: CurrentUser):
    """Accounting/ERP export formats available from the ledger."""
    return [
        {
            "key": f,
            "enabled": f == "generic",
            "contract": "generic-v2" if f == "generic" else None,
            "reason": None
            if f == "generic"
            else "A reviewed product/region importer contract and account/tax mappings are required.",
            "label": {
                "generic": "Generic accounting CSV",
                "xero": "Xero (Bills)",
                "quickbooks": "QuickBooks (Bills)",
            }[f],
        }
        for f in erp_export.FORMATS
    ]


@router.get("/accounting")
async def export_accounting(
    current: CurrentUser,
    db: DbSession,
    fmt: str = Query("generic"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
):
    """Retired direct downloads require an explicit approved-population preview."""
    if fmt not in erp_export.FORMATS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown format '{fmt}'. Available: {', '.join(erp_export.FORMATS)}.",
        )
    if fmt != "generic":
        raise ConflictError(
            "This adapter is unconfigured. Obtain a reviewed product/region importer contract and account/tax mappings first.",
            code="export_adapter_unconfigured",
        )
    raise ConflictError(
        "Choose a legal entity, period and currency, then preview the approved invoice population before downloading.",
        code="export_preview_required",
    )


@router.post("/accounting/preview", response_model=AccountingPreview)
async def preview_accounting(body: AccountingScope, current: CurrentUser, db: DbSession):
    return await accounting_preview.prepare(db, current.org_id, current.id, body)


@router.get("/accounting/entities", response_model=list[AccountingEntity])
async def accounting_entities(current: CurrentUser, db: DbSession):
    # Expose only entity identity, never the issuer's bank/settings block.
    rows = await db.execute(
        select(IssuerProfile.id, IssuerProfile.name, IssuerProfile.legal_name)
        .where(IssuerProfile.org_id == current.org_id)
        .order_by(IssuerProfile.name, IssuerProfile.id)
    )
    return rows.mappings().all()


@router.post("/accounting/download")
async def download_accounting(body: AccountingDownload, current: CurrentUser):
    filename, content = accounting_preview.download(body.token, current.org_id, current.id)
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": content_disposition(filename),
            "X-Export-Contract": accounting_preview.CONTRACT,
        },
    )
